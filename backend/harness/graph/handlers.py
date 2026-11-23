"""Graph adapters for the existing Harness modules and workspace provider.

Nodes use the same Skill registry, account scope, tool permissions and model
clients as ordinary Runs. No second agent loop or execution service is opened.
"""

import asyncio
import hashlib
import json
import mimetypes
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import (
    ActionRequest,
    PerceptionInput,
    ReasoningInput,
    RunInput,
    mode_tool_specs,
    resolve_tool_spec_for_mode,
    tool_allowed_in_mode,
)
from backend.contracts.modules.memory import MemoryRecall
from backend.contracts.modules.perception import ContextItem, ContextLink
from backend.contracts.ports import ModelContinuationPort
from backend.contracts.runtime import Artifact, GraphNodeResult, NodeSpec, Ref
from backend.contracts.schemas.core import Error, ModuleRequest, PortError
from backend.harness.lifecycle.control import RunControl, StopRequested
from backend.infrastructure.sources import source_refs_from_action


class _NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SkillConfig(_NodeConfig):
    skill_id: str = Field(min_length=1)
    version: int | str | None = None
    resource: str | None = None


class _ToolConfig(_NodeConfig):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any]


class _ModelConfig(_NodeConfig):
    instruction: str = Field(min_length=1)


class _FileConfig(_NodeConfig):
    path: str = Field(min_length=1)
    source_node_id: str | None = None
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _error(code: str, message: str) -> PortError:
    return PortError(Error(code=code, message=message))


def _config(schema, node):
    try:
        return schema.model_validate(node.config)
    except ValidationError as exc:
        raise _error("invalid_graph_node_config", f"节点“{node.label}”的配置无效") from exc


def _module_output(response, code, message):
    if response.status != "ok" or response.output is None:
        raise PortError(response.error or Error(code=code, message=message))
    return response.output


def _ancestors(node: NodeSpec, run: RunEnvelope) -> set[str]:
    dependencies = {item.id: set(item.depends_on) for item in run.graph.nodes}
    for edge in run.graph.edges:
        dependencies[edge.to_node].add(edge.from_node)
    found, pending = set(), list(dependencies[node.id])
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(dependencies[current])
    return found


class HarnessGraphHandlers:
    """Real node operations plus the shared workspace's Run lifecycle."""

    def __init__(self, harnesses, *, default_mode: str):
        self.harnesses = dict(harnesses)
        self.default_mode = default_mode
        self.handlers = {
            "skill": self.skill,
            "tool": self.tool,
            "model": self.model,
            "file": self.file,
        }

    def _harness(self, run):
        mode = RunInput.model_validate(run.payload).thinking_mode or self.default_mode
        if mode not in self.harnesses:
            raise _error("unsupported_thinking_mode", "任务的模型思考模式不可用")
        return self.harnesses[mode]

    @staticmethod
    def _tool_specs(harness, mode):
        """Return the one model-facing tool catalog used by every node.

        ``build_harness`` keeps material tools out of ``Harness.tools`` because
        they are injected by the same action dispatcher with a separate
        visibility policy. Graph model nodes must still see that exact catalog;
        rebuilding a second graph-only list was the source of the old
        ``tools=[]`` split.
        """
        specs = list(harness.tools)
        known = {item.name for item in specs}
        for tool in (harness.material_tool, harness.material_search_tool):
            if tool is not None and tool.spec.name not in known:
                specs.append(tool.spec)
                known.add(tool.spec.name)
        action_module = harness.modules.get("action") if isinstance(getattr(harness, "modules", None), dict) else None
        return mode_tool_specs(specs, mode, action_module=action_module)

    async def prepare(self, run, state, control):
        harness = self._harness(run)
        bind_run = getattr(harness.model_context, "bind_run", None)
        if callable(bind_run):
            value = RunInput.model_validate(run.payload)
            bind_run(
                run.run_id,
                value.model_profile_id,
                value.model_snapshot,
            )
        # Establish the provider snapshot before any tool can create files.
        if harness.execution_tools is not None:
            if state.get("workspace_baseline") is None and hasattr(harness.execution_tools, "snapshot_baseline"):
                state["workspace_baseline"] = await asyncio.to_thread(
                    harness.execution_tools.snapshot_baseline,
                    run.run_id,
                    account_id=run.account_id,
                    conversation_id=run.conversation_id or run.session_id,
                )
            await self._action(run, state, control, "read", {"path": ".", "max_chars": 1})

    async def collect_artifacts(self, run, state, *, completed: bool) -> None:
        """Project graph workspace files through the normal artifact collector."""

        harness = self._harness(run)
        provider = harness.execution_tools
        if provider is None:
            return
        baseline = state.get("workspace_baseline")
        if baseline is None and hasattr(provider, "snapshot_baseline"):
            baseline = await asyncio.to_thread(
                provider.snapshot_baseline,
                run.run_id,
                account_id=run.account_id,
                conversation_id=run.conversation_id or run.session_id,
            )
            state["workspace_baseline"] = baseline
        owned_paths = tuple(
            item["relative_path"]
            for item in state.get("artifacts", [])
            if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
        )
        state["artifacts"] = await asyncio.to_thread(
            provider.collect_artifacts,
            run.run_id,
            completed=completed,
            account_id=run.account_id,
            conversation_id=run.conversation_id or run.session_id,
            baseline=baseline,
            owned_paths=owned_paths,
        )

    async def close(self, run):
        harness = self._harness(run)
        try:
            if harness.execution_tools is not None:
                await harness.execution_tools.close_run(run.run_id)
        finally:
            if isinstance(harness.model_context, ModelContinuationPort):
                harness.model_context.close_run(run.run_id)

    async def _action(self, run, state, control: RunControl, name, arguments, *, action_id=None):
        if control.cancelled:
            raise StopRequested
        harness = self._harness(run)
        action = harness.modules["action"]
        specs = {spec.name: spec for spec in harness.tools}
        for tool in (harness.material_tool, harness.material_search_tool):
            if tool is not None:
                specs[tool.spec.name] = tool.spec
        if name not in action.tools or name not in specs:
            raise _error("unknown_tool", f"任务工具未注册：{name}")
        spec = specs[name]
        value = RunInput.model_validate(run.payload)
        spec = resolve_tool_spec_for_mode(spec, value.mode, action_module=action)
        if not tool_allowed_in_mode(spec, value.mode):
            raise _error("work_mode_required", "请在工作模式运行需要写入文件或执行操作的节点")
        counts = state.setdefault("graph_tool_counts", {})
        counts[name] = counts.get(name, 0) + 1
        action_request = ActionRequest(tool_name=name, arguments=arguments)
        if action_id is not None:
            action_request.action_id = action_id
        request = ModuleRequest(
            run_id=run.run_id,
            call_id=uuid4().hex,
            input=action_request,
            context={
                "account_id": run.account_id,
                "conversation_id": run.conversation_id or run.session_id,
                "mode": value.mode,
                "user_request": value.text,
                "user_facts": value.facts,
                "user_constraints": value.constraints,
                "tool_call_index": counts[name],
                "tool_result_chars": harness.settings.context_policy.tool_resource_chars,
                "material_read_chars": harness.settings.context_policy.material_excerpt_chars,
                "image_allowed": False,
            },
        )
        operation = action.handle(request)
        response = await control.read(operation) if spec.read_only else await operation
        result = _module_output(response, "graph_tool_failed", f"工具执行失败：{name}")
        if result.outcome == "error" or result.error is not None:
            raise PortError(result.error or Error(code="graph_tool_failed", message=f"工具执行失败：{name}"))
        payload = result.result
        if isinstance(payload, dict) and (
            payload.get("status") in {"failed", "error", "cancelled"}
            or (name == "bash" and payload.get("exit_code") != 0)
        ):
            raise _error("graph_tool_failed", f"工具执行未成功：{name}")
        state.setdefault("graph_actions", []).append({
            "request": request.input.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        })
        refs = {item["source_id"]: item for item in state.get("source_refs", [])}
        refs.update({item["source_id"]: item for item in source_refs_from_action(result)})
        if refs:
            state["source_refs"] = list(refs.values())
        return result

    @staticmethod
    def _store(node, state, output, *, summary, media_type="application/json", text=None):
        body = text if text is not None else json.dumps(output, ensure_ascii=False, sort_keys=True)
        ref = Ref(name=node.id, media_type=media_type, sha256=hashlib.sha256(body.encode("utf-8")).hexdigest())
        state.setdefault("node_outputs", {})[node.id] = {
            "kind": node.kind, "ref_id": ref.ref_id, **output,
        }
        return GraphNodeResult(output_refs=[ref], summary=summary)

    async def skill(self, node, run, state, control):
        config = _config(_SkillConfig, node)
        result = await self._action(run, state, control, "read_skill", config.model_dump(exclude_none=True))
        return self._store(
            node, state, {"action": result.model_dump(mode="json")},
            summary=f"已加载 Skill：{result.result['name']}", media_type="text/markdown", text=result.result["content"],
        )

    async def tool(self, node, run, state, control):
        config = _config(_ToolConfig, node)
        result = await self._action(run, state, control, config.tool_name, config.arguments)
        summary_labels = {
            "information_card_search": "论文资料已找到",
            "information_card_read": "论文资料已读取",
            "dynamic_rag_search": "法规资料已核对",
            "read_dynamic_rag": "法规原文已读取",
            "search_cases": "案例资料已找到",
            "read_case": "案例原文已读取",
            "web_search": "公开资料已找到",
            "web_read": "公开资料已读取",
        }
        summary = summary_labels.get(config.tool_name, "这一步已完成")
        if result.outcome == "partial":
            summary = f"{summary}（部分结果）"
        return self._store(
            node, state, {"action": result.model_dump(mode="json")},
            summary=summary,
        )

    async def model(self, node, run, state, control):
        config = _config(_ModelConfig, node)
        harness = self._harness(run)
        value = RunInput.model_validate(run.payload)
        facts = dict(value.facts)
        if run.answers:
            facts["user_answers"] = dict(run.answers)
        current = value.model_copy(update={
            "text": f"用户目标：\n{value.text}\n\n当前节点任务：\n{config.instruction}",
            "facts": facts,
        })
        required = _ancestors(node, run)
        outputs = state.get("node_outputs", {})
        candidates = [ContextItem(
            id=f"graph-output:{key}", type="resource", source="graph",
            kind=f"graph.{outputs[key]['kind']}", origin_run_id=run.run_id,
            body={"node_id": key, "output": outputs[key]},
        ) for key in outputs if key in required]
        tool_specs = self._tool_specs(harness, value.mode)
        action_context: list[ContextItem] = []
        max_turns = getattr(getattr(harness, "settings", None), "runtime", None)
        max_turns = max(1, int(getattr(max_turns, "max_rounds", 8)))
        memory = MemoryRecall(
            scope=run.run_id, session_id=run.session_id,
            run_id=run.run_id, task_id=run.task_id,
        )
        prefix = "\n\n".join(item["text"] for item in outputs.values() if item["kind"] == "model")
        for _ in range(max_turns):
            visible_candidates = candidates + action_context
            perception = await control.read(harness.modules["perception"].handle(ModuleRequest(
                run_id=run.run_id, call_id=uuid4().hex,
                input=PerceptionInput(
                    current=current,
                    memory=memory,
                    candidates=visible_candidates,
                    required_candidate_ids=[item.id for item in visible_candidates],
                    tools=tool_specs,
                ),
            )))
            perceived = _module_output(perception, "graph_perception_failed", "模型上下文组装失败")
            if perceived.needs_compaction:
                raise _error("graph_context_too_large", "节点依赖的完整内容超出模型上下文，请拆分任务图")
            response = await control.read(harness.modules["reasoning"].handle(ModuleRequest(
                run_id=run.run_id, call_id=uuid4().hex,
                input=ReasoningInput(
                    perceived=perceived,
                    tools=tool_specs,
                    purpose="main",
                    delivered_prefix=prefix + "\n\n" if prefix else "",
                ),
            )))
            proposal = _module_output(response, "graph_model_failed", "模型节点执行失败")
            if proposal.kind == "action":
                if proposal.incomplete or not proposal.action_requests:
                    raise _error("graph_model_incomplete", "模型未完成当前节点的工具调用")
                call_item_id = f"graph-tool-call:{uuid4().hex}"
                action_context.append(ContextItem(
                    id=call_item_id,
                    type="tool_call",
                    source="reasoning",
                    role="assistant",
                    origin_run_id=run.run_id,
                    body={
                        "text": proposal.commentary,
                        "calls": [item.model_dump(mode="json") for item in proposal.action_requests],
                    },
                ))
                for action_request in proposal.action_requests:
                    result = await self._action(
                        run, state, control, action_request.tool_name, action_request.arguments,
                        action_id=action_request.action_id,
                    )
                    action_context.append(ContextItem(
                        id=f"graph-tool-result:{action_request.action_id}",
                        type="tool_result",
                        source="action",
                        role="tool",
                        origin_run_id=run.run_id,
                        links=[ContextLink(relation="responds_to", target_id=call_item_id)],
                        body=result.model_dump(mode="json"),
                    ))
                continue
            if proposal.kind != "answer" or proposal.incomplete or not proposal.complete or proposal.blocked:
                raise _error("graph_model_incomplete", "模型未完成当前节点任务，不能将其标记为成功")
            if not proposal.answer or not proposal.answer.strip():
                raise _error("graph_model_empty", "模型未返回当前节点的正文结果")
            return self._store(node, state, {"text": proposal.answer}, summary=proposal.answer, media_type="text/plain", text=proposal.answer)
        raise _error("graph_model_round_limit", "模型节点的工具调用轮次已达到上限")

    async def file(self, node, run, state, control):
        config = _config(_FileConfig, node)
        path = PurePosixPath(config.path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in str(path) or str(path) == ".":
            raise _error("execution_invalid_path", "交付文件必须位于当前会话工作区")
        relative = path.as_posix()
        expected = config.expected_sha256
        content = None
        if config.source_node_id is not None:
            if config.source_node_id not in _ancestors(node, run):
                raise _error("graph_source_not_dependency", "文件来源必须是当前节点的上游模型节点")
            source = state["node_outputs"][config.source_node_id]
            if source["kind"] != "model":
                raise _error("graph_source_not_model", "文件来源必须是已完成的模型正文")
            content = source["text"]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if expected is not None and expected != digest:
                raise _error("graph_file_hash_mismatch", "模型正文与指定的文件哈希不一致")
            expected = digest
        try:
            result = await self._action(run, state, control, "read", {"path": relative, "max_chars": 1})
        except PortError as exc:
            if content is None or exc.error.code != "execution_file_not_found":
                raise
            await self._action(run, state, control, "write", {"path": relative, "content": content})
            result = await self._action(run, state, control, "read", {"path": relative, "max_chars": 1})
        file = result.result
        if file["kind"] != "file":
            raise _error("graph_artifact_not_file", "交付路径不是文件")
        if expected is not None and file["sha256"] != expected:
            raise _error("graph_file_hash_mismatch", "交付文件与预期正文或指定哈希不一致")
        artifact_id = hashlib.sha256(f"{relative}:{file['sha256']}".encode()).hexdigest()[:24]
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metadata = {
            "artifact_id": artifact_id, "name": path.name, "relative_path": relative,
            "media_type": media_type, "size_bytes": file["bytes"], "sha256": file["sha256"],
            "version": 1, "kind": "document" if path.suffix.lower() in {".docx", ".xlsx", ".pptx"} else "file",
        }
        state["artifacts"] = [item for item in state.get("artifacts", []) if item["relative_path"] != relative] + [metadata]
        state.setdefault("node_outputs", {})[node.id] = {"kind": "file", "artifact": metadata}
        return GraphNodeResult(
            output_refs=[Ref(ref_id=artifact_id, name=path.name, kind="artifact", media_type=media_type, sha256=file["sha256"])],
            artifacts=[Artifact(
                artifact_id=artifact_id, run_id=run.run_id, node_id=node.id, name=path.name,
                media_type=media_type, size_bytes=file["bytes"], sha256=file["sha256"],
            )],
            summary=f"已生成文件：{path.name}",
        )


def build_graph_handlers(harnesses, *, default_mode: str) -> HarnessGraphHandlers:
    return HarnessGraphHandlers(harnesses, default_mode=default_mode)
