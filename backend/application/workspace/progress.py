"""Read-only presentation of durable progress events; never execution state."""

from datetime import datetime, timezone


STAGES = {
    "material_parsing": ("research", "查找资料"),
    "material_reading": ("research", "查找资料"),
    "material_searching": ("research", "查找资料"),
    "case_searching": ("research", "查找资料"),
    "case_reading": ("research", "查找资料"),
    "law_searching": ("research", "查找资料"),
    "source_reading": ("research", "查找资料"),
    "researching": ("research", "查找资料"),
    "file_writing": ("files", "生成文件"),
    "file_compiling": ("files", "生成文件"),
    "execution": ("execution", "执行任务"),
    "writing": ("writing", "撰写回答"),
    "memory": ("model", "模型处理中"),
    "perception": ("model", "模型处理中"),
    "reasoning": ("model", "模型处理中"),
    "reflection": ("model", "模型处理中"),
    "model_processing": ("model", "模型处理中"),
    "context_compaction": ("model", "模型处理中"),
    "context_compaction_done": ("model", "模型处理中"),
    "finalizing": ("delivery", "整理结果"),
}


def project_progress(run, events, now=None):
    now = now or datetime.now(timezone.utc)
    stages = {}
    active = False
    known = False
    current = None
    previous_at = None
    last_label = None

    def accrue(at):
        if active and current and previous_at:
            stages[current]["elapsed_ms"] += max(0, int((at - previous_at).total_seconds() * 1000))

    for event in events:
        at = datetime.fromisoformat(event["emitted_at"].replace("Z", "+00:00"))
        accrue(at)
        previous_at = at
        stage = event["stage"] or ""
        if stage.startswith("execution:"):
            boundary = stage.split(":", 1)[1]
            interrupted = active and boundary == "snapshot"
            if boundary == "running":
                active = True
                known = True
            elif boundary != "pausing":
                active = False
            # A process restart cannot reconstruct the end of an unfinished interval.
            if interrupted:
                known = False
                for item in stages.values():
                    item["timing_known"] = False
            current = None if boundary == "running" else current
            continue
        if stage in {"waiting_user", "stage_delivered"}:
            continue
        if event["type"] == "delta":
            key, label = STAGES["writing"]
        else:
            key, label = STAGES.get(stage, ("model", "模型处理中"))
        if key not in stages:
            stages[key] = {"id": key, "label": label, "elapsed_ms": 0, "timing_known": known, "details": []}
        elif not known:
            stages[key]["timing_known"] = False
        current = key
        detail = event.get("label")
        if detail and detail != last_label:
            stages[key]["details"].append(detail)
            last_label = detail

    # A live clock is based on a server observation, not the browser's wall clock.
    live = active and run.status == "running"
    if live:
        accrue(now)
    result = []
    for key, item in stages.items():
        result.append({
            "id": key, "label": item["label"],
            "elapsed_ms": item["elapsed_ms"] if item["timing_known"] else None,
            "active": live and current == key,
            "details": list(dict.fromkeys(item["details"])),
        })
    return result
