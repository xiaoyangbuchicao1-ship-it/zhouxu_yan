"""Low-cost prompt A/B acceptance through real HTTP and official DeepSeek.

Run inside the supported Linux API image with current backend/tests mounted at
/app, read-only corpus/model resources, and an isolated backend/.runtime mount.
This entry point never publishes prompts or connects to the production API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import socket
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

import httpx
import uvicorn

from backend.config.prompt_bundle import load_prompt_bundle
from backend.config.settings import BACKEND_ROOT, load_settings

STYLE_PROMPT = (
    "把下面这句话改写得简洁自然，并提取截止日期。只输出两行：第一行是改写后的句子，"
    "第二行是‘截止日期：2026年11月30日’。保留原意，不补充背景，不需要查资料。\n"
    "原句：鉴于我们目前在本事项上的办理进度情况，烦请贵方于2026年11月30日之前"
    "向我方发送提单副本。"
)
LAW_PROMPT = (
    "请仅核对《中华人民共和国海商法》现行版本第22条，给出该条原文和人可读的出处，"
    "包括法规名称、条号与来源实际提供的版本日期。"
    "必要时只补充版本或生效日期，不扩展其他问题。"
)
PAPER_PROMPT = (
    "请检索‘FCR 单证 交单义务’，只取一张论文信息卡并读取其完整内容。"
    "只用两三句话引用其中一小段原文，写明真实作者、论文题名，"
    "使用该卡返回的citation_url给出论文的Markdown引用链接。"
    "不再检索其他库，不写论文，不生成文件。"
)
WORK_PROMPT = (
    "这是文件续做验收，不是法律研究，无须检索或使用Skill。请依次完成三个动作，每个动作单独调用："
    "1. 用write创建recovery.txt，内容仅为‘第一章’并换行；"
    "2. 用bash执行命令 printf ready > .pause-ready; sleep 20；"
    "3. 用write的append=true在recovery.txt末尾追加‘第二章’并换行，然后交付该文件。"
    "如果文件已有第一章，不得覆盖或重复追加已有章节。"
)
CONTINUE_GUIDANCE = (
    "暂停后再继续原任务。保留已有文件，从未完成处继续，不重写第一章，不再执行等待命令。"
    "完成第二章后，在recovery.txt末尾独占一行追加‘补充已处理’，每行只出现一次。"
)
TERMINAL = {"succeeded", "failed", "cancelled", "paused", "waiting_user"}
RUN_FIELDS = (
    "run_id", "status", "output", "partial_output", "sources", "artifacts",
    "continuation_guidance", "active_elapsed_ms", "wall_elapsed_ms", "stages",
)
SCENARIOS = ("style", "law_citation", "paper_citation", "permission_and_continuation")
PAPER_LINK = re.compile(
    r"\[[^\]]+\]\(((?:/api)?(/workspace/sources/(paper-[A-Za-z0-9._-]+)))\)"
)


class FailedCheck(RuntimeError):
    """A reviewable acceptance failure, never a provider response dump."""


def require(condition: bool, description: str, checks: list[dict]) -> None:
    checks.append({"description": description, "passed": bool(condition)})
    if not condition:
        raise FailedCheck(description)


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prompt_hash(prompts: dict[str, str]) -> str:
    body = json.dumps(prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(body.encode()).hexdigest()


def normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value)


def original_overlap(original: str, answer: str) -> int:
    return SequenceMatcher(None, normalize(original), normalize(answer), autojunk=False).find_longest_match().size


def isolated_settings(base, root: Path, args):
    settings = base.model_copy(deep=True)
    settings.database.url = f"sqlite:///{root / 'business.sqlite3'}"
    settings.application.update(
        run_store_path=str(root / "runs.sqlite3"),
        office_store_path=str(root / "office.sqlite3"),
        work_store_path=str(root / "work.sqlite3"),
    )
    settings.materials.storage_root = str(root / "originals")
    settings.materials.cache_root = str(root / "parsed")
    settings.execution.workspace_root = str(root / "workspaces")
    settings.execution.provider = "native"
    settings.model.provider = "deepseek"
    settings.model.name = "deepseek-flash"
    settings.model.thinking_mode = "none"
    settings.model.temperature = 0
    settings.model.modes["none"].output_tokens = 2048
    settings.model.modes["none"].call_timeout_seconds = min(90, args.scenario_timeout)
    settings.runtime.max_rounds = 12
    settings.runtime.max_retries = 0
    settings.runtime.run_timeout_seconds = args.scenario_timeout
    settings.runtime.work_run_timeout_seconds = args.scenario_timeout
    settings.runtime.model_call_timeout_seconds = min(90, args.scenario_timeout)
    return settings


@asynccontextmanager
async def serve(settings, startup_timeout: float):
    """Use a kernel-assigned loopback port, real lifespan, and real sockets."""
    from backend.interfaces.api.app import create_app

    app = create_app(settings=settings)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, access_log=False, log_level="error",
        timeout_graceful_shutdown=10,
    ))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(startup_timeout):
            while not server.started:
                if task.done():
                    await task
                    raise FailedCheck("临时HTTP实例在就绪前退出")
                await asyncio.sleep(0.1)
        if not app.state.runtime_ready:
            raise FailedCheck("临时实例未接通真实模型执行层")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False,
        ) as client:
            yield app, client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=30)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise FailedCheck("临时HTTP实例关闭超时") from None
        finally:
            listener.close()


class Journey:
    def __init__(self, app, client, settings, args):
        self.app, self.client, self.settings, self.args = app, client, settings, args
        self.account_id = ""
        self.active: tuple[str, str] | None = None
        self.current: dict | None = None

    async def request(self, method, path, *, expected=200, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        if response.status_code != expected:
            # Never serialize response.request, headers, auth responses or raw
            # provider errors; HTTP status and our route are sufficient here.
            raise FailedCheck(f"{method} {path} 返回 {response.status_code}，预期 {expected}")
        return response

    async def register(self):
        await self.request("GET", "/healthz")
        response = await self.request("POST", "/auth/register", expected=201, json={
            "email": f"prompt-ab-{uuid4().hex}@example.test",
            "password": secrets.token_urlsafe(32),
            "display_name": "提示词对比验收",
        })
        auth = response.json()
        self.client.headers["Authorization"] = f"Bearer {auth['session']['token']}"
        self.account_id = auth["account"]["id"]
        models = (await self.request("GET", "/workspace/models")).json()
        flash = next(
            model for group in models["groups"] for model in group["models"]
            if model["profile_id"] == "deepseek-flash"
        )
        if not flash["available"]:
            raise FailedCheck("官方DeepSeek Flash未配置，未开始业务对比")

    def model_events(self, run_id=None):
        unique = {}
        for event in self.app.state.event_hub.events:
            if event.module == "model" and event.phase == "model":
                if run_id is None or event.run_id == run_id:
                    unique[(event.run_id, event.call_id)] = event
        return list(unique.values())

    def usage(self, run_id=None):
        events = self.model_events(run_id)
        missing = sum(event.total_tokens is None for event in events)
        return {
            "model_calls": len(events),
            "observed_total_tokens": sum(event.total_tokens for event in events if event.total_tokens is not None),
            "usage_missing_calls": missing,
            "usage_complete": bool(events) and missing == 0,
            "model_elapsed_seconds": round(sum(event.elapsed_seconds or 0 for event in events), 3),
            "calls": [{
                "run_id": event.run_id, "call_id": event.call_id,
                "status": event.status, "total_tokens": event.total_tokens,
                "elapsed_seconds": round(event.elapsed_seconds or 0, 3),
            } for event in events],
        }

    def budget(self):
        if len(self.model_events()) >= self.args.max_model_calls:
            raise FailedCheck("已达到本版模型调用上限，停止继续花费")

    async def start(self, title, text):
        self.budget()
        conversation = (await self.request("POST", "/workspace/conversations", expected=201, json={
            "title": title, "mode": "chat",
        })).json()["conversation"]
        cid = conversation["conversation_id"]
        run = (await self.request("POST", f"/workspace/conversations/{cid}/runs", expected=202, json={
            "conversation_id": cid, "client_request_id": uuid4().hex,
            "text": text, "model_profile_id": "deepseek-flash", "thinking_mode": "none",
        })).json()["run"]
        self.active = (cid, run["run_id"])
        return cid, run["run_id"]

    async def snapshot(self, cid, rid):
        run = (await self.request(
            "GET", f"/workspace/runs/{rid}", params={"conversation_id": cid},
        )).json()["run"]
        if self.current is not None:
            self.current["run"] = {key: run[key] for key in RUN_FIELDS}
        if run["status"] not in TERMINAL:
            self.budget()
        return run

    async def wait(self, cid, rid, statuses):
        while True:
            run = await self.snapshot(cid, rid)
            if run["status"] in statuses:
                return run
            if run["status"] in TERMINAL:
                raise FailedCheck(f"任务提前进入 {run['status']}，预期 {','.join(sorted(statuses))}")
            await asyncio.sleep(0.25)

    async def stop_active(self):
        if self.active is None:
            return
        cid, rid = self.active
        response = await self.request("GET", f"/workspace/runs/{rid}", params={"conversation_id": cid})
        if response.json()["run"]["status"] not in {"succeeded", "failed", "cancelled"}:
            await self.request("POST", f"/workspace/runs/{rid}/cancel", json={"conversation_id": cid})

    async def style(self, report):
        cid, rid = await self.start("短改写与日期提取", STYLE_PROMPT)
        run = await self.wait(cid, rid, {"succeeded"})
        lines = [line.strip() for line in run["output"].splitlines() if line.strip()]
        require(len(lines) == 2, "按要求仅回答两行", report["checks"])
        require(lines[1] == "截止日期：2026年11月30日", "日期提取准确且没有添加说明", report["checks"])
        require("提单副本" in lines[0] and len(lines[0]) <= 65, "改写保留核心事项且足够简洁", report["checks"])
        require("2026年11月30日" in lines[0], "改写保留原截止日期", report["checks"])
        require(not re.search("总而言之|值得注意的是|三维赋能|底层逻辑|免责声明", run["output"]),
                "没有添加模板套话", report["checks"])
        require(not run["sources"] and not run["artifacts"]
                and not any(stage["id"] == "research" for stage in run["stages"]),
                "普通改写没有无关检索或生成文件", report["checks"])

    async def law(self, report):
        cid, rid = await self.start("单条海商法引用核对", LAW_PROMPT)
        run = await self.wait(cid, rid, {"succeeded"})
        sources = [source for source in run["sources"] if source["kind"] == "law"]
        require(bool(sources), "真实检索返回法规资料", report["checks"])
        source = next((source for source in sources if "海商法" in source["title"]
                       and re.search(r"(?:22|二十二)\s*条", source["locator"] or "")
                       and source["version_label"] == "现行版本"), None)
        require(source is not None, "资料对应海商法第22条", report["checks"])
        document = (await self.request("GET", f"/workspace/sources/{source['source_id']}")).json()
        report["opened_source"] = {key: document[key] for key in (
            "source_id", "kind", "title", "locator", "version_date", "version_label", "text",
        )}
        require(bool(document["text"].strip()), "通过真实HTTP打开法规原文", report["checks"])
        overlap = original_overlap(document["text"], run["output"])
        report["source_overlap_chars"] = overlap
        require(overlap >= min(30, len(normalize(document["text"]))),
                "回答保留了可与原文逐字核对的条文", report["checks"])
        require(normalize(document["title"]) in normalize(run["output"])
                and bool(re.search(r"(?:22|二十二)\s*条", run["output"])),
                "正文实际引注的法名与条号和返回资料一致", report["checks"])
        if document["version_date"]:
            date_parts = re.findall(r"\d+", document["version_date"])
            require(all(part.lstrip("0") in run["output"] for part in date_parts),
                    "正文出处包含来源实际提供的版本日期", report["checks"])
        report["body_has_source_link"] = bool(re.search(r"\[[^\]]+\]\((?:https?://|/)", run["output"]))
        report["link_contract"] = "法规工具仅返回可读出处；正文引注匹配后，通过对应资料卡HTTP打开原文。"
        require(not re.search(r"dynamic_rag_search|material_id|card_id|/app/|Traceback", run["output"]),
                "回答没有泄漏内部工具或路径", report["checks"])

    async def paper(self, report):
        cid, rid = await self.start("短论文原文引用", PAPER_PROMPT)
        run = await self.wait(cid, rid, {"succeeded"})
        saved = await self.app.state.run_store.load(session_id=cid)
        require(saved is not None, "本次任务有持久化检查点", report["checks"])
        stored_run = next(item for item in saved.snapshot.runs if item.run_id == rid)
        receipts = stored_run.execution_state["tool_results"]
        read_cards = [receipt["result"]["card"] for receipt in receipts
                      if receipt["tool_name"] == "information_card_read"
                      and receipt["outcome"] == "ok"
                      and receipt["result"].get("status") == "ok"]
        require(bool(read_cards), "模型实际读取了完整论文卡，不只使用搜索摘要", report["checks"])
        expected_urls = {card["citation_url"] for card in read_cards}
        report["read_card_citation_urls"] = sorted(expected_urls)
        source_ids = {source["source_id"] for source in run["sources"] if source["kind"] == "paper"}

        def check_links(text, location):
            links = PAPER_LINK.findall(text)
            require(bool(links), f"{location}实际给出了可点击的论文引用", report["checks"])
            source_links = [url for url in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", text)
                            if "/workspace/sources/" in url]
            require(len(source_links) == len(links),
                    f"{location}没有残缺或被显示文案替换的引用地址", report["checks"])
            require(all(path in expected_urls and source_id in source_ids
                        for _, path, source_id in links),
                    f"{location}的引用地址与完整论文卡返回的地址一致", report["checks"])
            return links

        # This is the public NDJSON contract consumed by the browser, not the
        # private event hub. Terminal tasks replay their durable buffered events.
        response = await self.request(
            "GET", f"/workspace/conversations/{cid}/events",
            params={"run_id": rid, "after_sequence": 0, "timeout_seconds": 5},
        )
        require(response.headers.get("content-type", "").startswith("application/x-ndjson"),
                "通过真实HTTP取得用户可见的事件流", report["checks"])
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        require(bool(events) and all(event["run_id"] == rid for event in events),
                "事件流属于当前任务", report["checks"])
        visible_events = [event for event in events if event["type"] in {"delta", "answer", "done"}]
        sequences = [event["sequence"] for event in visible_events]
        require(sequences == sorted(set(sequences)), "正文事件顺序完整且没有重复", report["checks"])
        delta_text = "".join(event["text"] or "" for event in visible_events if event["type"] == "delta")
        report["stream_delta_text"] = delta_text
        delta_links = check_links(delta_text, "逐段生成后拼接的正文")
        streamed = ""
        for event in visible_events:
            if event["type"] == "delta":
                streamed += event["text"] or ""
            else:
                streamed = event["text"] or ""
        require(streamed == run["output"], "按事件合同恢复的正文与最终回答完全一致", report["checks"])
        links = check_links(run["output"], "最终回答")
        history = (await self.request("GET", f"/workspace/conversations/{cid}")).json()
        answers = [message for message in history["messages"]
                   if message["run_id"] == rid and message["role"] == "assistant"]
        require(len(answers) == 1, "历史会话保留本次唯一回答", report["checks"])
        answer = answers[0]
        require(answer["content"] == run["output"], "历史回答与最终回答完全一致", report["checks"])
        history_links = check_links(answer["content"], "重新读取的历史回答")
        expected_link_ids = {source_id for _, _, source_id in links}
        require({source_id for _, _, source_id in delta_links} == expected_link_ids
                == {source_id for _, _, source_id in history_links},
                "生成过程、最终回答和历史回答指向相同的论文", report["checks"])
        require(expected_link_ids <= {source["source_id"] for source in answer["sources"]
                                     if source["kind"] == "paper"},
                "历史回答同时保留实际引用的论文资料", report["checks"])
        report["citation_urls"] = {
            "stream_deltas": [url for url, _, _ in delta_links],
            "final_answer": [url for url, _, _ in links],
            "history": [url for url, _, _ in history_links],
        }
        # The browser journey separately opens this exact URL through the
        # public source endpoint, PDF reader, download and refresh path. Keep
        # this prompt comparison focused on model-produced URL identity.
        document = next(source for source in run["sources"]
                        if source["source_id"] == links[0][2])
        report["source_reference"] = document
        require(normalize(document["title"]) in normalize(run["output"]),
                "正文论文题名与本次返回的论文资料一致", report["checks"])
        authors = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z.-]+", document["authors"] or "")
        require(any(author in run["output"] for author in authors),
                "正文作者来自真实论文元数据", report["checks"])
        card_text = str(read_cards[0].get("claim_or_fact") or read_cards[0].get("retrieval_text") or "")
        require(bool(card_text), "完整论文卡含可核对的原文摘录", report["checks"])
        overlap = original_overlap(card_text, run["output"])
        report["source_overlap_chars"] = overlap
        require(overlap >= min(20, len(normalize(card_text))),
                "引用内容可与论文原文逐字核对", report["checks"])
        require(len(run["output"]) <= 1400 and not run["artifacts"],
                "仅交付短引用，没有扩写论文或生成文件", report["checks"])

    async def work(self, report, output):
        cid, rid = await self.start("授权与暂停续做", WORK_PROMPT)
        workspace = Path(self.settings.execution.workspace_root) / self.account_id / cid
        target, marker = workspace / "recovery.txt", workspace / ".pause-ready"
        gate = await self.wait(cid, rid, {"waiting_user"})
        require(gate["payload"]["mode"] == "chat" and not target.exists(),
                "授权前保持聊天模式且没有写文件", report["checks"])
        require(len(gate["pending_request_ids"]) == 1, "出现明确的模式选择", report["checks"])
        request_id = gate["pending_request_ids"][0]
        question = (await self.request("GET", f"/workspace/runs/{rid}/input-requests/{request_id}",
                                       params={"conversation_id": cid})).json()["request"]
        require(question["kind"] == "mode_upgrade"
                and {option["value"] for option in question["options"]} == {"upgrade_work", "keep_chat"},
                "授权使用明确选项，不把普通聊天当成确认", report["checks"])
        await self.request("POST", f"/workspace/runs/{rid}/resume", expected=422, json={
            "conversation_id": cid, "request_id": request_id, "answer": "这只是补充，不是授权",
        })
        require(not target.exists(), "普通文字不能绕过写入授权", report["checks"])
        resumed = (await self.request("POST", f"/workspace/runs/{rid}/resume", json={
            "conversation_id": cid, "request_id": request_id, "answer": "upgrade_work",
        })).json()["run"]
        require(resumed["run_id"] == rid and resumed["payload"]["mode"] == "work",
                "用户明确升级后仍执行同一任务", report["checks"])
        while not marker.exists():
            run = await self.snapshot(cid, rid)
            if run["status"] in TERMINAL:
                raise FailedCheck(f"等待真实bash执行时任务提前进入 {run['status']}")
            await asyncio.sleep(0.1)
        require(marker.read_text(encoding="utf-8") == "ready", "bash已实际运行到等待命令", report["checks"])
        before = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        require(before == "第一章\n", "暂停前第一章已真实写入，第二章尚未写入", report["checks"])
        await self.request("POST", f"/workspace/runs/{rid}/pause", json={"conversation_id": cid})
        paused = await self.wait(cid, rid, {"paused"})
        report["paused"] = {key: paused[key] for key in ("run_id", "status", "active_elapsed_ms")}
        require(paused["run_id"] == rid, "暂停完成后原任务身份不变", report["checks"])
        await self.request("POST", f"/workspace/runs/{rid}/continue", json={
            "conversation_id": cid, "guidance": CONTINUE_GUIDANCE,
        })
        completed = await self.wait(cid, rid, {"succeeded"})
        require(completed["run_id"] == rid and completed["payload"]["text"] == WORK_PROMPT,
                "带补充继续保留原任务与原目标", report["checks"])
        require(CONTINUE_GUIDANCE in completed["continuation_guidance"],
                "补充要求已保存在同一任务中", report["checks"])
        artifacts = [artifact for artifact in completed["artifacts"] if artifact["name"] == "recovery.txt"]
        require(len(artifacts) == 1, "任务交付了一个可下载的真实文件", report["checks"])
        artifact = artifacts[0]
        response = await self.request("GET", f"/workspace/runs/{rid}/artifacts/{artifact['artifact_id']}",
                                      params={"conversation_id": cid})
        body = response.content
        require(sha256(body).hexdigest() == artifact["sha256"]
                and len(body) == artifact["size_bytes"], "HTTP下载文件与交付记录一致", report["checks"])
        text = body.decode("utf-8-sig").replace("\r\n", "\n")
        require(text.splitlines() == ["第一章", "第二章", "补充已处理"],
                "续做只补充未完成内容，每一行仅出现一次", report["checks"])
        require(text.startswith(before), "暂停前正文完整保留", report["checks"])
        (output / "recovery.txt").write_bytes(body)
        report["download"] = {"name": "recovery.txt", "sha256": artifact["sha256"], "text": text}


async def compare_variant(name, settings, args, output):
    report = {"variant": name, "prompt_sha256": prompt_hash(settings.prompts), "scenarios": []}
    output.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    try:
        async with serve(settings, args.startup_timeout) as (app, client):
            journey = Journey(app, client, settings, args)
            await journey.register()
            scenarios = [("style", journey.style), ("law_citation", journey.law),
                         ("paper_citation", journey.paper),
                         ("permission_and_continuation", lambda row: journey.work(row, output))]
            if args.scenario != "all":
                scenarios = [item for item in scenarios if item[0] == args.scenario]
            for scenario_name, execute in scenarios:
                row = {"name": scenario_name, "checks": [], "passed": False}
                report["scenarios"].append(row)
                journey.current, journey.active = row, None
                began = monotonic()
                try:
                    async with asyncio.timeout(args.scenario_timeout):
                        await execute(row)
                    row["passed"] = True
                except (FailedCheck, TimeoutError, httpx.HTTPError) as exc:
                    row["failure"] = {
                        "type": type(exc).__name__,
                        "message": str(exc) if isinstance(exc, FailedCheck) else (
                            "业务验收超时" if isinstance(exc, TimeoutError) else "本地HTTP请求失败"
                        ),
                    }
                    await journey.stop_active()
                finally:
                    row["elapsed_seconds"] = round(monotonic() - began, 3)
                    row["usage"] = journey.usage(journey.active[1]) if journey.active else journey.usage("not-started")
                    save_json(output / "report.json", report)
                print(json.dumps({"variant": name, "scenario": scenario_name,
                                  "passed": row["passed"], "elapsed_seconds": row["elapsed_seconds"],
                                  "model_calls": row["usage"]["model_calls"]}, ensure_ascii=False), flush=True)
                if len(journey.model_events()) >= args.max_model_calls:
                    report["stopped_at_model_call_limit"] = True
                    break
            report["usage"] = journey.usage()
    finally:
        report["elapsed_seconds"] = round(monotonic() - started, 3)
        expected_scenarios = len(SCENARIOS) if args.scenario == "all" else 1
        report["passed"] = (len(report["scenarios"]) == expected_scenarios
                            and all(row["passed"] for row in report["scenarios"]))
        save_json(output / "report.json", report)
    return report


async def main(args):
    output = args.output.resolve()
    if (output / "comparison.json").exists() or any((output / name / "report.json").exists() for name in ("old", "candidate")):
        raise FailedCheck("输出目录已有对比记录，请指定新的空目录")
    if sys.platform != "linux":
        raise FailedCheck("真实验收必须在项目支持的Docker Linux API环境运行")
    base = load_settings()
    if base.model.endpoint != "https://api.deepseek.com/chat/completions":
        raise FailedCheck("本入口仅允许使用官方DeepSeek地址")
    if not (BACKEND_ROOT.parent / "opensearch/corpus/records/articles.jsonl").is_file():
        raise FailedCheck("当前源码根目录缺少只读法规原文资源，请检查测试容器挂载")
    bundle = load_prompt_bundle(args.candidate.resolve())
    if prompt_hash(base.prompts) == bundle.content_sha256:
        raise FailedCheck("新旧提示词内容相同，已停止对比；请提供不同版本，避免重复调用模型。")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "candidate_version": bundle.version, "candidate_sha256": bundle.content_sha256,
        "model": "deepseek-flash", "thinking_mode": "none",
        "selected_scenarios": list(SCENARIOS) if args.scenario == "all" else [args.scenario],
        "limits": {"max_model_calls_per_variant": args.max_model_calls,
                   "scenario_timeout_seconds": args.scenario_timeout,
                   "model_output_tokens": 2048, "automatic_retries": 0},
        "inputs": {"style": STYLE_PROMPT, "law_citation": LAW_PROMPT, "paper_citation": PAPER_PROMPT,
                   "work": WORK_PROMPT, "continuation_guidance": CONTINUE_GUIDANCE},
        "scope": "按selected_scenarios执行真实HTTP旅程，默认全选四项；不运行长论文或PDF，不发布候选提示词。",
        "variants": {},
    }
    try:
        for name in ("old", "candidate"):
            with TemporaryDirectory(prefix=f"zhouxu-prompt-{name}-") as temporary:
                settings = isolated_settings(base, Path(temporary), args)
                if name == "candidate":
                    settings = bundle.apply(settings)
                report["variants"][name] = await compare_variant(name, settings, args, output / name)
                save_json(output / "comparison.json", report)
        old, candidate = report["variants"]["old"], report["variants"]["candidate"]
        old_scenarios = {row["name"]: row for row in old["scenarios"]}
        report["comparison"] = [{
            "scenario": row["name"], "old_passed": old_scenarios[row["name"]]["passed"],
            "candidate_passed": row["passed"],
            "observed_token_difference": row["usage"]["observed_total_tokens"] - old_scenarios[row["name"]]["usage"]["observed_total_tokens"],
            "usage_complete_both": row["usage"]["usage_complete"] and old_scenarios[row["name"]]["usage"]["usage_complete"],
            "elapsed_seconds_difference": round(row["elapsed_seconds"] - old_scenarios[row["name"]]["elapsed_seconds"], 3),
        } for row in candidate["scenarios"] if row["name"] in old_scenarios]
        report["passed"] = old["passed"] and candidate["passed"]
        return 0 if report["passed"] else 1
    finally:
        save_json(output / "comparison.json", report)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path,
                        default=BACKEND_ROOT / "config/prompts/zhouxu-2026-11-22.toml")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all",
                        help="Run one journey or all four (default), for both prompt versions.")
    parser.add_argument("--scenario-timeout", type=float, default=180)
    parser.add_argument("--startup-timeout", type=float, default=180)
    parser.add_argument("--max-model-calls", type=int, default=16,
                        help="Maximum completed model calls per variant, including failures.")
    args = parser.parse_args()
    if args.scenario_timeout <= 0 or args.startup_timeout <= 0 or args.max_model_calls < 1:
        parser.error("timeouts and model call budget must be positive")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(arguments())))
