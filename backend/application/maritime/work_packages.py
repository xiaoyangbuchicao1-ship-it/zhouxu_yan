"""Private run deliverables with field, citation and arithmetic checks."""

import json
import re
from collections import OrderedDict
from datetime import date
from pathlib import Path
from uuid import uuid4

from backend.application.maritime.calculator import calculate_claim
from backend.application.maritime.catalog import builtin_skills
from backend.application.maritime.citations import (
    expand_citations,
    invalid_citations,
)
from backend.application.maritime.service import now
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus, connection

DEFAULT_STORE = Path(__file__).resolve().parents[2] / ".runtime/maritime-work.sqlite3"


def configured_store(settings=None):
    path = (
        Path(settings.application.get("work_store_path", DEFAULT_STORE))
        if settings is not None
        else DEFAULT_STORE
    )
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return WorkPackageStore(path)


def validate_work(value, corpus=None):
    if not isinstance(value, dict) or set(value) - {
        "skill_id",
        "title",
        "sections",
        "notes",
    }:
        raise ValueError("工作包字段应为 skill_id、title、sections、notes")
    method = next(
        (s for s in builtin_skills() if s["id"] == value.get("skill_id")), None
    )
    if not method:
        raise ValueError("请选择一个基础海事工作包类型")
    title = value.get("title")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        raise ValueError("工作包需要1至160字标题")
    sections = value.get("sections")
    specs = method["deliverables"]
    if isinstance(sections, list):
        converted = {}
        for section in sections:
            if not isinstance(section, dict) or set(section) != {"key", "rows"}:
                raise ValueError("每个section只包含key和rows；notes放在最外层")
            spec = next((s for s in specs if s["key"] == section["key"]), None)
            if spec is None or section["key"] in converted:
                raise ValueError("表key未知或重复，请使用当前技能规定的表")
            if not isinstance(section["rows"], list):
                raise ValueError("rows应为行数组")
            data = []
            for row in section["rows"]:
                if not isinstance(row, list) or len(row) != len(spec["columns"]):
                    raise ValueError(
                        section["key"]
                        + "每行按顺序需要"
                        + str(len(spec["columns"]))
                        + "格："
                        + "、".join(spec["columns"])
                    )
                data.append(dict(zip(spec["columns"], row)))
            converted[section["key"]] = data
        sections = converted
    example = {s["key"]: [{column: "待核" for column in s["columns"]}] for s in specs}
    if not isinstance(sections, dict) or set(sections) != {s["key"] for s in specs}:
        raise ValueError(
            "sections只能包含规定的表key，不要增加其他表。备注放notes。每个value直接是行对象数组，不要包key/name/columns/rows。字段形状（待核仅作示意）："
            + json.dumps(example, ensure_ascii=False)
        )
    corpus = corpus or MaritimeCorpus()
    output = []
    for spec in specs:
        rows = sections[spec["key"]]
        if not isinstance(rows, list) or len(rows) > 80:
            raise ValueError("每个表格最多80行")
        for row_index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != set(spec["columns"]):
                raise ValueError(
                    spec["key"]
                    + "列名及行结构有误：每个元素直接是单行数据，不要包key/name/columns/rows。单行字段形状："
                    + json.dumps(example[spec["key"]][0], ensure_ascii=False)
                )
            for column, text in row.items():
                if not isinstance(text, str) or not text.strip() or len(text) > 5000:
                    raise ValueError("单元格需要非空文本，未知填写待核，每格最多5000字")
                expanded = expand_citations(text, corpus)
                if invalid_citations(expanded, corpus) or ("[[ZX:" in expanded):
                    markers = re.findall(r"\[\[ZX:[^\]\r\n]{1,100}\]\]", expanded)
                    raise ValueError(
                        f"{spec['key']}第{row_index}行【{column}】引用未能解析："
                        + "、".join(markers[:5])
                        + "。逐字核对真实检索回执的citation，必要时重新检索；不得猜改标记。"
                    )
        output.append({**spec, "rows": rows})
    if not any(s["rows"] for s in output):
        raise ValueError("工作包至少包含一行实际内容")
    notes = value.get("notes", [])
    if (
        not isinstance(notes, list)
        or len(notes) > 20
        or any(not isinstance(n, str) or len(n) > 2000 for n in notes)
    ):
        raise ValueError("notes最多20条，每条最多2000字")
    for note in notes:
        expanded = expand_citations(note, corpus)
        if invalid_citations(expanded, corpus) or "[[ZX:" in expanded:
            raise ValueError("备注引用未能解析")
    checks = {
        "structure": "passed",
        "citations": "checked_when_present",
        "professional_review": "pending",
    }
    if method["id"] == "evidence-review":
        previous = None
        for row in sections["timeline"]:
            # Only unambiguous complete dates; never infer unknown dates or reorder claims.
            match = re.fullmatch(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", row["时间"].strip())
            if not match:
                continue
            try:
                current = date(*(int(part) for part in match.groups()))
            except ValueError:
                raise ValueError("时间线含无效完整日期，请核对原文，不要猜测") from None
            if previous is not None and current < previous:
                raise ValueError("时间线已知完整日期未按先后排列；请移动整行，保留原日期、事件与陈述主体，不改写事实")
            previous = current
        checks["complete_date_order"] = "checked_when_parseable"
    if method["id"] == "claim-calculation":
        amounts = sections["amounts"]
        computed = calculate_claim(
            [
                {
                    "name": r["项目"],
                    "quantity": r["数量"],
                    "rate": r["单价"],
                    "currency": r["币种"],
                }
                for r in amounts
            ]
        )
        for row, calculated in zip(amounts, computed["items"]):
            from decimal import Decimal, InvalidOperation

            try:
                matches = Decimal(row["金额"]) == Decimal(calculated["amount"])
            except InvalidOperation:
                matches = False
            if not matches:
                raise ValueError("金额与确定性复算不一致：" + row["项目"])
        checks["arithmetic"] = "recomputed"
        checks["totals"] = computed["totals"]
    result = {
        "skill_id": method["id"],
        "skill_name": method["name"],
        "method_version": method["version"],
        "title": title.strip(),
        "sections": output,
        "notes": notes,
        "checks": checks,
    }
    if len(json.dumps(result, ensure_ascii=False)) > 100000:
        raise ValueError("工作包超过长度限制，请按争点拆分")
    return result


class WorkPackageStore:
    def __init__(self, path=DEFAULT_STORE):
        self.path = Path(path)

    def save(self, run_id, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        package = {**payload, "id": uuid4().hex, "run_id": run_id, "created_at": now()}
        with connection(self.path, timeout=15) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS work_packages(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS packages_run ON work_packages(run_id)"
            )
            db.execute(
                "INSERT INTO work_packages VALUES (?,?,?)",
                (package["id"], run_id, json.dumps(package, ensure_ascii=False)),
            )
        return package

    def list(self, run_id):
        if not self.path.is_file():
            return []
        with connection(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM work_packages WHERE run_id=? ORDER BY rowid DESC",
                    (run_id,),
                )
            ]


class WorkPackageTool:
    def __init__(self, store=None, corpus=None):
        from backend.contracts.modules.loop import ToolSpec

        self.store = store or WorkPackageStore()
        self.corpus = corpus or MaritimeCorpus()
        self.failures = OrderedDict()
        contracts = [
            {"skill_id": s["id"], "sections": s["deliverables"]}
            for s in builtin_skills()
        ]
        self.spec = ToolSpec(
            name="prepare_maritime_work",
            read_only=False,
            description="保存当前运行的私人海事工作包。用户需要完整表格/工作包时使用，简短问答不强制。只保存本次运行交付物，不改案件、不发送。sections是对象数组，每项{key,rows}，每行按规定列顺序填字符串数组；notes仅放最外层。连续3次校验失败将停止本次运行。结构/引用/金额校验不等于专业结论正确。各表的列顺序："
            + json.dumps(contracts, ensure_ascii=False),
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "enum": [s["skill_id"] for s in contracts],
                    },
                    "title": {"type": "string", "maxLength": 160},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {
                                    "type": "string",
                                    "enum": [
                                        d["key"]
                                        for s in builtin_skills()
                                        for d in s["deliverables"]
                                    ],
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                            "required": ["key", "rows"],
                            "additionalProperties": False,
                        },
                        "description": "每行按列顺序填值，未知填待核。每项只放key与rows。",
                    },
                    "notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["skill_id", "title", "sections"],
                "additionalProperties": False,
            },
        )

    async def execute_context(self, request):
        from backend.contracts.modules.loop import ActionResult

        try:
            payload = validate_work(request.input.arguments, self.corpus)
            package = self.store.save(request.run_id, payload)
            self.failures.pop(request.run_id, None)
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                result={
                    "status": "saved",
                    "package_id": package["id"],
                    "title": package["title"],
                    "checks": package["checks"],
                    "notice": "工作包已保存到本次运行，未发送，未改变案件复核状态。",
                },
                side_effects=["saved_private_run_deliverable"],
            )
        except (ValueError, LookupError) as exc:
            attempts = self.failures.pop(request.run_id, 0) + 1
            if attempts >= 3:
                from backend.contracts.schemas.core import Error, PortError

                raise PortError(
                    Error(
                        code="work_package_validation",
                        message="工作包连续三次未通过校验，已停止本次运行；没有保存成功的表格不能视为交付完成。请修改输入或工作规范后重新发起。",
                        retryable=False,
                    )
                ) from None
            self.failures[request.run_id] = attempts
            if len(self.failures) > 1024:
                self.failures.popitem(last=False)
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="error",
                result={"status": "validation_failed", "correction": str(exc)},
            )
