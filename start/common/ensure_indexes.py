#!/usr/bin/env python3
"""Start-up gate for the three independent OpenSearch sources used by ZhouXu.

The script is deliberately small and explicit: it waits for the local
OpenSearch endpoint, checks the published aliases and document counts, and
only runs the existing law/case publishing pipelines when a source is absent.
It never prints credentials and never replaces an existing alias silently.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LAW_ALIAS = "maritime_law_chunks_read"
LAW_INDEX = "maritime_law_chunks_v2"
CASE_ALIAS = "maritime_cases_read"
INFORMATION_ALIAS = "maritime_information_cards_read"
INFORMATION_INDEX = "maritime_information_cards_v4"
PREVIOUS_INFORMATION_INDEX = "maritime_information_cards_v3"
DEFAULT_URL = "https://127.0.0.1:19200"


class StartupError(RuntimeError):
    """An actionable deployment failure."""


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise StartupError(f"缺少 OpenSearch 配置：{path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    if not values.get("OPENSEARCH_INITIAL_ADMIN_PASSWORD") or values[
        "OPENSEARCH_INITIAL_ADMIN_PASSWORD"
    ].startswith("CHANGE_ME"):
        raise StartupError(f"请先在 {path} 设置 OPENSEARCH_INITIAL_ADMIN_PASSWORD")
    return values


class OpenSearch:
    def __init__(self, url: str, password: str, *, allow_remote: bool = False):
        parsed = urllib.parse.urlparse(url)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
            raise StartupError("启动脚本只允许连接 HTTPS OpenSearch")
        if parsed.hostname not in local_hosts and not allow_remote:
            raise StartupError("远程 OpenSearch 必须显式传入 --allow-remote")
        self.url = url.rstrip("/")
        import base64

        token = base64.b64encode(f"admin:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        if parsed.hostname in local_hosts or os.environ.get("ZHOUXU_OPENSEARCH_INSECURE", "").strip() == "1":
            self.context = ssl._create_unverified_context()
        else:
            self.context = ssl.create_default_context()

    def get(self, path: str, *, missing_ok: bool = False):
        request = urllib.request.Request(
            f"{self.url}{path}", headers=self.headers, method="GET"
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.context)
        )
        try:
            with opener.open(request, timeout=8) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise StartupError(f"OpenSearch 返回 HTTP {exc.code}：{detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StartupError(f"无法连接 OpenSearch：{exc}") from None
        return json.loads(payload) if payload else {}


def wait_for_cluster(client: OpenSearch, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = "未知错误"
    while time.monotonic() < deadline:
        try:
            health = client.get("/_cluster/health")
            if health.get("status") in {"yellow", "green"}:
                return
            last_error = f"集群状态为 {health.get('status', 'unknown')}"
        except StartupError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise StartupError(f"OpenSearch 在 {timeout} 秒内没有就绪：{last_error}")


def alias_count(client: OpenSearch, alias: str, expected_index: str) -> int | None:
    aliases = client.get(f"/_alias/{alias}", missing_ok=True)
    if aliases is None:
        return None
    indexes = sorted(aliases)
    if indexes != [expected_index]:
        raise StartupError(
            f"别名 {alias} 指向了 {indexes}，期望只指向 {expected_index}；不会自动改写别名"
        )
    count = client.get(f"/{expected_index}/_count")
    shards = count.get("_shards", {})
    if shards.get("failed", 0):
        raise StartupError(f"索引 {expected_index} 的分片检查失败")
    return int(count.get("count", -1))


def information_source(project_root: Path) -> Path:
    configured = os.environ.get("ZHOUXU_INFORMATION_CARDS_DIR", "").strip()
    return Path(configured).resolve() if configured else (project_root.parent / "信息卡").resolve()


def expected_counts(project_root: Path) -> tuple[int, int, str, int]:
    law_root = project_root / "opensearch"
    law_manifest = law_root / "corpus" / "manifest" / "corpus_manifest.json"
    case_root = project_root / "data" / "cases"
    if not law_manifest.is_file():
        raise StartupError(f"缺少条文库清单：{law_manifest}")
    manifests = sorted(case_root.glob("*/manifest.json"))
    if len(manifests) != 1:
        raise StartupError(
            f"案例快照必须恰好有一个 data/cases/*/manifest.json，当前找到 {len(manifests)} 个"
        )
    law = json.loads(law_manifest.read_text(encoding="utf-8-sig"))
    case = json.loads(manifests[0].read_text(encoding="utf-8-sig"))
    law_count = int(law["article_chunks"]) + int(law["source_chunks"])
    case_count = int(case["chunk_count"])
    if law_count <= 0 or case_count <= 0:
        raise StartupError("数据清单中的文档数必须为正数")
    from opensearch.opensearch_delivery.scripts.information_cards_index import (
        load_cards,
    )

    cards, _ = load_cards(information_source(project_root))
    return law_count, case_count, str(case["snapshot_id"]), len(cards)


def run_checked(command: list[str], *, cwd: Path) -> None:
    printable = " ".join(command[:4])
    print(f"执行数据发布步骤：{printable} …", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def publish_law(project_root: Path, python: Path, url: str) -> None:
    opensearch_root = project_root / "opensearch"
    script = opensearch_root / "opensearch_delivery" / "scripts" / "maritime_index.py"
    if not script.is_file():
        raise StartupError(f"缺少条文发布脚本：{script}")
    run_dir = opensearch_root / ".ai-tmp" / f"startup-law-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    commands = [
        [str(python), "-B", str(script), "validate", "--root", str(opensearch_root), "--run-dir", str(run_dir)],
        [str(python), "-B", str(script), "create", "--root", str(opensearch_root), "--index", LAW_INDEX, "--url", url, "--run-dir", str(run_dir)],
        [str(python), "-B", str(script), "import", "--root", str(opensearch_root), "--index", LAW_INDEX, "--url", url, "--run-dir", str(run_dir)],
        [str(python), "-B", str(script), "verify", "--root", str(opensearch_root), "--index", LAW_INDEX, "--url", url, "--run-dir", str(run_dir)],
        [str(python), "-B", str(script), "activate", "--root", str(opensearch_root), "--index", LAW_INDEX, "--url", url, "--run-dir", str(run_dir)],
    ]
    try:
        for command in commands:
            run_checked(command, cwd=project_root)
    except Exception:
        print(f"条文发布失败；诊断材料保留在：{run_dir}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(run_dir, ignore_errors=True)


def publish_cases(project_root: Path, python: Path) -> None:
    snapshot_dirs = sorted((project_root / "data" / "cases").glob("*/manifest.json"))
    if len(snapshot_dirs) != 1:
        raise StartupError("案例导入前必须准备唯一的 data/cases/* 快照")
    run_checked(
        [str(python), "-B", "-m", "backend.infrastructure.corpus.import_cases"],
        cwd=project_root,
    )


def publish_information_cards(project_root: Path, python: Path, url: str) -> None:
    script = (
        project_root
        / "opensearch"
        / "opensearch_delivery"
        / "scripts"
        / "information_cards_index.py"
    )
    source = information_source(project_root)
    if not script.is_file():
        raise StartupError(f"缺少信息卡发布脚本：{script}")
    commands = [
        [str(python), "-B", str(script), "validate", "--source-dir", str(source)],
        [
            str(python),
            "-B",
            str(script),
            "create",
            "--source-dir",
            str(source),
            "--root",
            str(project_root / "opensearch"),
            "--index",
            INFORMATION_INDEX,
            "--url",
            url,
        ],
        [
            str(python),
            "-B",
            str(script),
            "import",
            "--source-dir",
            str(source),
            "--root",
            str(project_root / "opensearch"),
            "--index",
            INFORMATION_INDEX,
            "--url",
            url,
        ],
        [
            str(python),
            "-B",
            str(script),
            "verify",
            "--source-dir",
            str(source),
            "--root",
            str(project_root / "opensearch"),
            "--index",
            INFORMATION_INDEX,
            "--url",
            url,
        ],
        [
            str(python),
            "-B",
            str(script),
            "activate",
            "--source-dir",
            str(source),
            "--root",
            str(project_root / "opensearch"),
            "--index",
            INFORMATION_INDEX,
            "--alias",
            INFORMATION_ALIAS,
            "--replace-index",
            PREVIOUS_INFORMATION_INDEX,
            "--url",
            url,
        ],
    ]
    for command in commands:
        run_checked(command, cwd=project_root)


def source_status(
    client: OpenSearch, project_root: Path
) -> tuple[dict[str, int | str | None], int, int, str, int]:
    law_count, case_count, snapshot_id, information_count = expected_counts(project_root)
    actual_law = alias_count(client, LAW_ALIAS, LAW_INDEX)
    actual_case = alias_count(client, CASE_ALIAS, snapshot_id)
    # Corpus replacement is an explicit offline cutover, never startup compatibility.
    active_information_index = INFORMATION_INDEX
    actual_information = alias_count(client, INFORMATION_ALIAS, active_information_index)
    if actual_information is not None:
        from opensearch.opensearch_delivery.scripts.information_cards_index import (
            DEFAULT_EMBEDDINGS,
            definition,
            load_cards,
            load_embeddings,
        )

        cards, expected_manifest = load_cards(information_source(project_root))
        mapping = client.get(f"/{active_information_index}/_mapping")[active_information_index]["mappings"]
        for key in ("information_cards_sha256", "source_registry_sha256", "validation_sha256"):
            if mapping.get("_meta", {}).get(key) != expected_manifest[key]:
                raise StartupError(f"信息卡索引与只读源文件版本不一致：{key}；请核对后重新发布索引")
        _, embedding_manifest = load_embeddings(DEFAULT_EMBEDDINGS, cards, expected_manifest)
        for key in ("embedding", "vectors_sha256", "sidecar_sha256", "normalized_cards_sha256", "sources_sha256"):
            if mapping.get("_meta", {}).get(key) != embedding_manifest[key]:
                raise StartupError(f"信息卡索引向量版本不一致：{key}")
        if mapping.get("properties") != definition()["mappings"]["properties"]:
            raise StartupError("信息卡索引字段与发布契约不一致")
        coverage = client.get(f"/{active_information_index}/_count?q=_exists_:vector")["count"]
        if coverage != information_count:
            raise StartupError("信息卡索引缺少部分向量")
    if actual_law is not None and actual_law != law_count:
        raise StartupError(f"条文库条数不匹配：实际 {actual_law} 条，期望 {law_count} 条")
    if actual_case is not None and actual_case != case_count:
        raise StartupError(f"案例库条数不匹配：实际 {actual_case} 条，期望 {case_count} 条")
    if actual_information is not None and actual_information != information_count:
        raise StartupError(
            f"信息卡条数不匹配：实际 {actual_information} 条，期望 {information_count} 条"
        )
    return (
        {
            "law_index": LAW_INDEX,
            "law_documents": actual_law,
            "case_index": snapshot_id,
            "case_documents": actual_case,
            "information_index": active_information_index,
            "information_documents": actual_information,
        },
        law_count,
        case_count,
        snapshot_id,
        information_count,
    )


def verify_sources(client: OpenSearch, project_root: Path) -> dict[str, int | str]:
    status, law_count, case_count, _, information_count = source_status(client, project_root)
    if status["law_documents"] != law_count:
        raise StartupError(f"条文库未就绪：实际 {status['law_documents']} 条，期望 {law_count} 条")
    if status["case_documents"] != case_count:
        raise StartupError(f"案例库未就绪：实际 {status['case_documents']} 条，期望 {case_count} 条")
    if status["information_documents"] != information_count:
        raise StartupError(
            f"信息卡未就绪：实际 {status['information_documents']} 条，期望 {information_count} 条"
        )
    return {key: value for key, value in status.items() if value is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--url", default=os.environ.get("ZHOUXU_OPENSEARCH_URL", DEFAULT_URL))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    configured_env = os.environ.get("ZHOUXU_RUNTIME_ENV_FILE", "").strip()
    env_path = Path(configured_env) if configured_env else project_root / "deploy" / "app" / ".env"
    env = read_env(env_path)
    client = OpenSearch(
        args.url,
        env["OPENSEARCH_INITIAL_ADMIN_PASSWORD"],
        allow_remote=args.allow_remote,
    )
    wait_for_cluster(client, args.timeout)
    status, law_count, case_count, snapshot_id, information_count = source_status(
        client, project_root
    )
    print(
        f"OpenSearch 已就绪；目标条文 {law_count} 条，案例 {case_count} 条（{snapshot_id}），"
        f"信息卡 {information_count} 条"
    )

    if (
        status["law_documents"] == law_count
        and status["case_documents"] == case_count
        and status["information_documents"] == information_count
    ):
        print("三个检索源已经发布，跳过重复导入。")
        print(json.dumps(status, ensure_ascii=False))
        return 0
    if args.check_only:
        raise StartupError("检索源尚未完整发布（请去掉 --check-only 让启动脚本执行发布）")

    python = Path(sys.executable)
    if status["law_documents"] is None:
        publish_law(project_root, python, args.url)
    if status["case_documents"] is None:
        publish_cases(project_root, python)
    if status["information_documents"] is None:
        publish_information_cards(project_root, python, args.url)
    status = verify_sources(client, project_root)
    print("条文库、案例库和信息卡均已通过条数与别名检查。")
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        sys.stderr.reconfigure(encoding="utf-8", errors="strict")
    try:
        raise SystemExit(main())
    except (StartupError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"启动检查失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
