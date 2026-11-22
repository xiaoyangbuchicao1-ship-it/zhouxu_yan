import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.ZHOUXU_TEST_URL || "http://127.0.0.1:18180";
const email = process.env.ZHOUXU_TEST_ADMIN_EMAIL;
const password = process.env.ZHOUXU_TEST_ADMIN_PASSWORD;
assert.ok(email && password, "请通过本次进程环境提供管理员邮箱和密码");

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on("pageerror", error => errors.push(error.message));

try {
  await page.goto(`${base}/admin/login`);
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录管理台", exact: true }).click();
  await page.waitForURL(/\/admin\/overview$/);

  await page.goto(`${base}/admin/models`);
  await page.getByRole("heading", { name: "模型", exact: true }).waitFor();
  await page.getByRole("tab", { name: /模型/ }).waitFor();
  await page.getByRole("tab", { name: /服务/ }).waitFor();
  await page.locator(".admin-model-index").waitFor();
  assert.ok(await page.getByText("思考档位", { exact: true }).count(), "模型编辑器应显示思考档位");
  assert.ok(await page.locator(".admin-model-index > button").count(), "模型页应显示已登记模型");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "模型页桌面不应横向溢出");

  await page.goto(`${base}/admin/operations`);
  await page.getByRole("heading", { name: "能力与状态", exact: true }).waitFor();
  await page.getByRole("heading", { name: "账户权限", exact: true }).waitFor();
  await page.getByRole("heading", { name: "最近任务", exact: true }).waitFor();
  await page.getByRole("heading", { name: "资料索引", exact: true }).waitFor();
  await page.getByRole("heading", { name: "服务状态", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "能力页桌面不应横向溢出");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "networkidle" });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "能力页手机不应横向溢出");
  await page.goto(`${base}/admin/models`);
  await page.getByRole("heading", { name: "模型", exact: true }).waitFor();
  await page.getByRole("tab", { name: /模型/ }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "模型页手机不应横向溢出");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks: ["模型目录", "思考档位", "能力与状态", "手机布局"] }));
} finally {
  await browser.close();
}
