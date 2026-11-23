"""Read-only presentation of durable progress events; never execution state."""

from datetime import datetime, timezone


STAGES = {
    "skill_reading": ("skills", "加载技能"),
    "material_parsing": ("research", "查找资料"),
    "material_reading": ("research", "查找资料"),
    "material_searching": ("research", "查找资料"),
    "case_searching": ("research", "查找资料"),
    "case_reading": ("research", "查找资料"),
    "law_searching": ("research", "查找资料"),
    "paper_searching": ("research", "查找资料"),
    "paper_reading": ("research", "查找资料"),
    "web_searching": ("research", "查找资料"),
    "source_reading": ("research", "查找资料"),
    "researching": ("research", "查找资料"),
    "work_package": ("files", "生成文件"),
    "file_writing": ("files", "生成文件"),
    "file_compiling": ("files", "生成文件"),
    "calculating": ("execution", "执行任务"),
    "execution": ("execution", "执行任务"),
    "writing": ("writing", "撰写回答"),
    "synthesizing": ("synthesis", "综合分析与推进"),
    "memory": ("model", "模型处理中"),
    "perception": ("model", "模型处理中"),
    "reasoning": ("model", "模型处理中"),
    "reflection": ("model", "模型处理中"),
    "model_processing": ("model", "模型处理中"),
    "context_compaction": ("model", "模型处理中"),
    "context_compaction_done": ("model", "模型处理中"),
    "finalizing": ("delivery", "整理结果"),
}


def _append_detail(details: list[str], detail: str) -> None:
    """Keep chronological step receipts while replacing an active step with its completion receipt."""
    clean = detail.strip()
    if not clean:
        return
    if details:
        prev = details[-1]
        if clean == prev:
            return
        # e.g. "检索法规 · X" -> "检索法规 · X（命中 6 条）"
        base_clean = clean.split("（", 1)[0]
        base_prev = prev.split("（", 1)[0]
        if "（" not in prev and base_clean == base_prev and "（" in clean:
            details[-1] = clean
            return
        # e.g. "加载技能 · X" -> "已加载技能 · X", "撰写文稿 · X" -> "已保存文稿 · X", "编译..." -> "已生成..."
        pending_to_done = (
            ("加载技能", "已加载技能"),
            ("撰写文稿", "已保存文稿"),
            ("修改文稿", "已修改文稿"),
            ("续写文稿", "已保存文稿"),
            ("编译生成", "已生成"),
            ("精读论文", "已读取"),
            ("核对案例", "已核对"),
            ("读取来源正文", "已读取来源正文"),
            ("读取上传材料", "已读取"),
            ("测算海事索赔", "已完成"),
            ("生成结构化", "已保存工作包"),
            ("核验 ", "已完成"),
            ("检查", "已完成"),
        )
        for p_prefix, d_prefix in pending_to_done:
            if prev.startswith(p_prefix) and clean.startswith(d_prefix):
                details[-1] = clean
                return
    details.append(clean)


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
            _append_detail(stages[key]["details"], detail)
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
