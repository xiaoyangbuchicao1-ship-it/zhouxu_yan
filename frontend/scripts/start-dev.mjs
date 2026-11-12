import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workspaceRoot = path.resolve(frontendRoot, "..");
const backendPython = path.join(
  workspaceRoot,
  "backend",
  ".venv",
  ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]),
);
const backendPort = Number(process.env.ZHOUXU_API_PORT || 8010);
const frontendPort = Number(process.env.ZHOUXU_WEB_PORT || 4173);
const healthUrl = `http://127.0.0.1:${backendPort}/healthz`;
const env = {
  ...process.env,
  ZHOUXU_API_TARGET: `http://127.0.0.1:${backendPort}`,
  ZHOUXU_WEB_PORT: String(frontendPort),
};

let backend;
let frontend;
let ownsBackend = false;
let stopping = false;

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

function commandForNpm() {
  return process.platform === "win32" ? "npm.cmd" : "npm";
}

function stopProcess(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  if (process.platform === "win32") {
    // Vite and uvicorn are grandchildren of cmd.exe on Windows.
    spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
  }
}

function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  stopProcess(frontend);
  if (ownsBackend) stopProcess(backend);
  setTimeout(() => process.exit(code), 250);
}

async function waitForBackend() {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`后端进程提前退出，退出码：${backend.exitCode}`);
    }
    try {
      const response = await fetch(healthUrl, {
        signal: AbortSignal.timeout(2_000),
      });
      if (response.ok) return;
    } catch {
      // The model and local encoder warm up before the health route is served.
    }
    await sleep(500);
  }
  throw new Error(`后端在 120 秒内未通过健康检查：${healthUrl}`);
}

try {
  let reusedBackend = false;
  try {
    const response = await fetch(healthUrl, {
      signal: AbortSignal.timeout(2_000),
    });
    reusedBackend = response.ok;
  } catch {
    reusedBackend = false;
  }
  if (!reusedBackend) {
    if (!existsSync(backendPython)) {
      throw new Error("后端环境尚未安装。请先在项目根目录执行 uv sync --project backend --extra rag --extra materials --locked");
    }
    ownsBackend = true;
    backend = spawn(
      backendPython,
      [
        "-m",
        "uvicorn",
        "backend.interfaces.api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        String(backendPort),
      ],
      { cwd: workspaceRoot, env, stdio: "inherit", windowsHide: true },
    );
    backend.once("error", (error) => {
      if (!stopping) {
        console.error("后端进程启动失败：", error.message);
        shutdown(1);
      }
    });
    backend.once("exit", (code) => {
      if (!stopping) {
        console.error(`后端进程已退出：${code ?? "unknown"}`);
        shutdown(code || 1);
      }
    });
  }

  await waitForBackend();
  console.log(`${reusedBackend ? "复用" : "启动"}后端已就绪：${healthUrl}`);

  frontend = spawn(
    commandForNpm(),
    ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(frontendPort)],
    {
      cwd: frontendRoot,
      env,
      stdio: "inherit",
      windowsHide: true,
      shell: process.platform === "win32",
    },
  );
  frontend.once("error", (error) => {
    if (!stopping) {
      console.error("前端进程启动失败：", error.message);
      shutdown(1);
    }
  });
  frontend.once("exit", (code) => shutdown(code || 0));
  console.log(`前端地址：http://127.0.0.1:${frontendPort}/`);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  shutdown(1);
}
