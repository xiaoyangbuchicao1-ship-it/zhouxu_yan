"""Persist the public task view independently of browser subscriptions."""

from backend.application.workspace.conversations import WorkspaceError
from backend.contracts.modules.loop import RunInput
from backend.contracts.events.streaming import StreamEvent


_TOOL_LABELS = {
    "dynamic_rag_search": "海商法规检索",
    "search_laws": "海商法规检索",
    "search_cases": "海事案例检索",
    "case_search": "海事案例检索",
    "research_card_search": "论文资料库检索",
    "read_research_card": "阅读论文卡片",
    "web_search": "网络检索",
    "read_skill": "读取专业技能",
    "read_material": "读取用户材料",
    "python_interpreter": "数据分析计算",
    "bash": "工作区命令",
}


def _safe_tool_traces(execution_state) -> list[dict]:
    if not isinstance(execution_state, dict):
        return []
    raw_items: list[dict] = []
    for entry in execution_state.get("tool_results") or []:
        if isinstance(entry, dict):
            raw_items.append(entry)
    for entry in execution_state.get("graph_actions") or []:
        if isinstance(entry, dict) and isinstance(entry.get("result"), dict):
            result_item = dict(entry["result"])
            if isinstance(entry.get("request"), dict) and "arguments" not in result_item:
                req = entry["request"]
                result_item["arguments"] = req.get("arguments") or req
            raw_items.append(result_item)
    traces: list[dict] = []
    seen: set[tuple[str, str, int | None, str]] = set()
    for item in raw_items:
        tool_name = str(item.get("tool_name") or "").strip()
        if not tool_name:
            continue
        label = _TOOL_LABELS.get(tool_name, tool_name)
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        session_summary = item.get("session_summary") if isinstance(item.get("session_summary"), dict) else {}
        result_obj = item.get("result") if isinstance(item.get("result"), dict) else {}

        query_parts: list[str] = []
        for source_map in (args, session_summary):
            for key in ("query", "keyword", "keywords", "question", "topic", "skill_id", "document_name", "article_no", "title"):
                val = source_map.get(key)
                if isinstance(val, str) and val.strip():
                    clean = val.strip()[:80]
                    if clean not in query_parts:
                        query_parts.append(clean)
                elif isinstance(val, list):
                    joined = " ".join(str(x).strip() for x in val if isinstance(x, str) and x.strip())[:80]
                    if joined and joined not in query_parts:
                        query_parts.append(joined)
        query_text = " · ".join(query_parts)[:160] or None

        result_count: int | None = None
        for container in (result_obj, session_summary):
            if not container:
                continue
            for list_key in ("items", "results", "hits", "cards", "cases", "rows", "source_refs"):
                seq = container.get(list_key)
                if isinstance(seq, list):
                    result_count = len(seq)
                    break
            if result_count is not None:
                break
            for int_key in ("total", "count", "matched"):
                cnt = container.get(int_key)
                if isinstance(cnt, int) and not isinstance(cnt, bool) and cnt >= 0:
                    result_count = cnt
                    break
            if result_count is not None:
                break

        err_obj = item.get("error") if isinstance(item.get("error"), dict) else None
        status = (
            "failed"
            if item.get("outcome") == "error"
            or err_obj is not None
            or result_obj.get("status") in {"failed", "error"}
            else "succeeded"
        )
        summary: str | None = None
        if status == "failed" and err_obj and isinstance(err_obj.get("message"), str):
            summary = err_obj["message"].strip()[:120]
        elif result_count is not None:
            summary = f"命中 {result_count} 条" if result_count > 0 else "未命中结果"
        elif isinstance(result_obj.get("name"), str) and result_obj["name"].strip():
            summary = result_obj["name"].strip()[:120]

        dedupe_key = (tool_name, query_text or "", result_count, status)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        traces.append({
            "tool_name": tool_name,
            "tool_label": label,
            "query": query_text,
            "result_count": result_count,
            "status": status,
            "summary": summary,
        })
    return traces


class RunProjection:
    def __init__(self, workspace, events):
        self.workspace, self.events = workspace, events
        self._versions = {}
        self._statuses = {}

    def __call__(self, run):
        if self.workspace is None or not run.account_id:
            return
        previous_status = self._statuses.get(run.run_id)
        if previous_status != run.status:
            # Add observation boundaries to the existing event log. No Run or
            # checkpoint schema changes; historical missing intervals stay unknown.
            stage = run.status if previous_status is not None or run.status == "queued" else "snapshot"
            self.events.emit_stream(StreamEvent(run_id=run.run_id, type="progress", stage=f"execution:{stage}"))
            self._statuses[run.run_id] = run.status
        text, sequence = self.events.visible(run.run_id)
        if sequence and (run.first_event_at is None or run.first_delta_at is None):
            items = self.events.stream_events_since(run_id=run.run_id)
            if items:
                run.first_event_at = run.first_event_at or items[0].emitted_at
                delta = next((item for item in items if item.type == "delta"), None)
                if delta:
                    run.first_delta_at = run.first_delta_at or delta.emitted_at
        run.last_sequence = max(run.last_sequence, sequence)
        if text and run.status != "succeeded":
            run.partial_output = text
        source_refs = run.execution_state.get("source_refs", []) if isinstance(run.execution_state, dict) else []
        if not isinstance(source_refs, list):
            source_refs = []
        # The public projection only accepts the stable, human-readable fields
        # produced by the Harness. A malformed checkpoint must not block the
        # conversation from being saved.
        source_refs = [item for item in source_refs if isinstance(item, dict) and item.get("source_id")]
        artifacts = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        if not isinstance(artifacts, list):
            artifacts = []
        artifacts = [item for item in artifacts if isinstance(item, dict) and item.get("artifact_id")]
        tool_traces = _safe_tool_traces(run.execution_state)
        has_checkpoint = bool(
            run.partial_output
            or (run.execution_state or {}).get("records")
            or (run.execution_state or {}).get("tool_results")
            or (run.execution_state or {}).get("delivered_stages")
            or (run.execution_state or {}).get("task_state")
        )
        key = (
            run.revision,
            run.status,
            run.output,
            run.partial_output,
            run.last_sequence,
            has_checkpoint,
            tuple(item.get("source_id") for item in source_refs),
            tuple(item.get("artifact_id") for item in artifacts),
            tuple((item["tool_name"], item.get("query"), item.get("result_count"), item.get("status")) for item in tool_traces),
            tuple(run.continuation_guidance),
            run.context_compaction_count,
        )
        if self._versions.get(run.run_id) == key:
            return
        value = RunInput.model_validate(run.payload)
        if run.answers:
            facts = dict(value.facts)
            facts["user_answers"] = {**facts.get("user_answers", {}), **run.answers}
            value = value.model_copy(update={"facts": facts})
        try:
            self.workspace.record_run(
                account_id=run.account_id,
                conversation_id=run.conversation_id or run.session_id,
                run_id=run.run_id, question=value.text, status=run.status,
                answer=run.output, partial_output=run.partial_output,
                revision=run.revision, client_request_id=run.client_request_id,
                retry_of_run_id=run.retry_of_run_id,
                payload=value.model_dump(mode="json"), material_ids=value.material_ids,
                error=run.error.message if run.error else None,
                error_code=run.error.code if run.error else None,
                model_profile_id=(value.model_snapshot.profile_id if value.model_snapshot else value.model_profile_id),
                model_profile_revision_id=(getattr(value.model_snapshot, "profile_revision_id", None) if value.model_snapshot else None),
                provider_revision_id=(getattr(value.model_snapshot, "provider_revision_id", None) if value.model_snapshot else None),
                model_snapshot=(value.model_snapshot.model_dump(mode="json") if value.model_snapshot else None),
                last_sequence=run.last_sequence, first_event_at=run.first_event_at,
                first_delta_at=run.first_delta_at, last_event_at=run.completed_at,
                progress={
                    "completed_units": run.completed_units,
                    "total_units": run.total_units,
                    "current_unit": run.current_unit,
                    "has_checkpoint": has_checkpoint,
                    "source_refs": source_refs,
                    "tool_traces": tool_traces,
                    "artifacts": artifacts,
                    "continuation_guidance": list(run.continuation_guidance),
                    "context_compaction_count": run.context_compaction_count,
                },
                created_at=run.created_at, completed_at=run.completed_at,
                include_archived=True,
            )
        except WorkspaceError:
            return  # Conversation was explicitly removed, never recreate it.
        self._versions[run.run_id] = key
