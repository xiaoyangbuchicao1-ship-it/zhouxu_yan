"""Authorized Run bindings and reading state, separate from immutable artifacts."""

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path

from backend.contracts.modules.materials import (
    MaterialLocator,
    MaterialManifest,
    MaterialReadInput,
    MaterialUnit,
    ParsedMaterial,
)
from backend.contracts.modules.perception import ContextItem
from backend.contracts.schemas.core import Error, PortError
from backend.infrastructure.parsing.adapters.common import unit_id
from backend.infrastructure.parsing.read_view import read_view
from backend.infrastructure.parsing.registry import MaterialParserRegistry
from backend.infrastructure.parsing.search_view import search_view


class MaterialCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.registry = MaterialParserRegistry(settings.materials)
        self.runs = {}
        self.read_positions = {}
        self.coverage = {}
        self.mixed_inputs = {}

    def close_run(self, run_id):
        self.runs.pop(run_id, None)
        self.read_positions.pop(run_id, None)
        self.coverage.pop(run_id, None)
        self.mixed_inputs.pop(run_id, None)

    def coverage_for(self, run_id):
        return list(self.coverage.get(run_id, []))

    def context_resources_for(self, run_id):
        return [{"material_id": key, "name": parsed.manifest.name,
                 "text": "\n".join(unit.text for unit in parsed.units)}
                for key, parsed in self.runs.get(run_id, {}).items() if key.startswith("context:")]

    def record_selected(self, items, *, run_id):
        for item in items:
            body = (
                item.body.get("result", {}) if item.type == "tool_result" else item.body
            )
            if (
                isinstance(body, dict)
                and body.get("material_id")
                and body.get("coverage")
            ):
                self.coverage.setdefault(run_id, []).append(
                    {
                        "material_id": body["material_id"],
                        "parse_version": body.get("parse_version"),
                        "coverage": body["coverage"],
                        "stage": "context_provided",
                    }
                )

    def has_unread_mixed_input(self, run_id):
        key = self.mixed_inputs.get(run_id)
        if not key:
            return False
        provided = {}
        for entry in self.coverage.get(run_id, []):
            if (
                entry.get("material_id") == key
                and entry.get("stage") == "context_provided"
            ):
                for span in entry["coverage"]:
                    provided.setdefault(span["unit_id"], []).append(
                        (span["char_start"], span["char_end"])
                    )
        for unit in self.runs[run_id][key].units:
            end = 0
            for start, stop in sorted(provided.get(unit.unit_id, [])):
                if start > end:
                    break
                end = max(end, stop)
            if end < len(unit.text):
                return True
        return False

    async def ingest(self, materials, *, run_id):
        bindings = self.runs.setdefault(run_id, {})
        for material in materials:
            if material.selected:
                parsed = await asyncio.to_thread(self.registry.parse, material)
                bindings[parsed.manifest.material_id] = parsed
        return self.candidates(run_id), []

    def prepare_user_input(self, value, *, run_id, over_budget=False, preview_chars=None):
        text = value.text
        if not over_budget:
            return value
        digest = hashlib.sha256(text.encode()).hexdigest()
        key = "pasted-" + digest[:20]
        version = self.registry.cache.key(digest, {"schema": "pasted-user-1"})
        units = []
        # Whole original input, exact offsets and original user identity survive.
        for index, match in enumerate(re.finditer(r"[^\n]*(?:\n|$)", text)):
            if match.group():
                units.append(
                    MaterialUnit(
                        unit_id=unit_id(key, version, str(index)),
                        material_id=key,
                        parse_version=version,
                        order=len(units),
                        kind="paragraph",
                        locator=MaterialLocator(
                            kind="text",
                            value=str(index + 1),
                            label=f"用户原始输入第{index + 1}段",
                            char_start=match.start(),
                            char_end=match.end(),
                        ),
                        text=match.group(),
                        metadata={"source_identity": "user.original"},
                    )
                )
        parsed = ParsedMaterial(
            manifest=MaterialManifest(
                material_id=key,
                name="用户粘贴原文",
                uri="",
                media_type="text/plain",
                status="available",
                parser="verbatim-user-input",
                content_fingerprint=digest,
                parse_version=version,
                unit_count=len(units),
                complete=True,
                parsed_ranges=[f"字符0–{len(text)}"],
            ),
            units=units,
        )
        self.registry.cache.save(version, parsed)
        self.bind(parsed, run_id=run_id)
        boundary = re.search(
            r"<material>([\s\S]*?)</material>|```material\s*\n([\s\S]*?)\n```", text
        )
        if boundary:
            task = text[: boundary.start()] + text[boundary.end() :]
            # Only use a reliable explicit boundary when remaining instructions
            # themselves fit the same long-input resource threshold.
            if len(task) <= (preview_chars or self.settings.context_policy.material_excerpt_chars):
                return value.model_copy(
                    update={
                        "text": task
                        + f"\n用户原文及材料已完整保存为 {key}；材料区间为字符{boundary.start()}–{boundary.end()}，需要时读取。"
                    }
                )
        self.mixed_inputs[run_id] = key
        preview = max(1, (preview_chars or self.settings.context_policy.material_excerpt_chars) // 2)
        return value.model_copy(
            update={
                "text": f"用户原始混合输入过长，尚未可靠分离全部要求；已逐字保存为 {key}（用户来源）。"
                "必须继续读取并确认要求，不能把下面预览当成全部隐含约束已被理解。\n"
                + text[:preview]
                + "\n…中间内容可连续读取…\n"
                + text[-preview:],
                "constraints": value.constraints + [f"原始混合输入尚需阅读：{key}"],
            }
        )

    def bind(self, parsed, *, run_id):
        self.runs.setdefault(run_id, {})[parsed.manifest.material_id] = parsed

    def bind_result(self, result, *, run_id):
        """Keep one addressable original result; replay never invokes its tool."""
        key = result.resource_id or f"result:{result.action_id}"
        self.bind_text(key, f"{result.tool_name} 已取得的结果", json.dumps(result.result, ensure_ascii=False),
                       run_id=run_id)
        result.resource_id = key
        return key

    def bind_text(self, key, name, text, *, run_id):
        version = hashlib.sha256(text.encode()).hexdigest()
        parsed = ParsedMaterial(manifest=MaterialManifest(
            material_id=key, name=name, uri="",
            media_type="application/json", status="available", parser="tool-result",
            parse_version=version, unit_count=1, complete=True,
        ), units=[MaterialUnit(
            unit_id=unit_id(key, version, "0"), material_id=key,
            parse_version=version, order=0, kind="paragraph",
            locator=MaterialLocator(kind="text", value="0", label="工具原始返回"), text=text,
        )])
        self.bind(parsed, run_id=run_id)
        return key

    def candidates(self, run_id, *, include_body=True, task_text="", preview_chars=None):
        candidates = []
        remaining = (self.settings.materials.navigation_preview_chars
                     if preview_chars is None else max(0, preview_chars))
        for key, parsed in self.runs.get(run_id, {}).items():
            manifest = parsed.manifest
            if manifest.parser == "tool-result":
                continue
            body_selected = include_body and remaining > 0
            limit = remaining if body_selected else 1
            view = self.read(
                MaterialReadInput(material_id=key),
                run_id=run_id,
                limit=limit,
                track=False,
            )
            already_provided = bool(view.coverage) and self._covered_by_reads(run_id, key, view.coverage)
            if not body_selected or already_provided:
                view.units, view.coverage = [], []
                view.next_cursor = None
            else:
                remaining -= sum(len(unit.text) for unit in view.units)
            body = self.projection(view)
            body["text_char_count"] = sum(len(unit.text) for unit in parsed.units)
            body["visual_content_present"] = any(unit.kind == "image" or unit.resources for unit in parsed.units)
            body["read_hint"] = self.settings.materials.navigation_hint
            body["body_in_tool_history"] = already_provided
            candidates.append(
                ContextItem(
                    id=f"material:{key}",
                    type="resource",
                    source="user"
                    if manifest.parser == "verbatim-user-input"
                    else "user.material",
                    role="user",
                    kind="user.input.segment"
                    if manifest.parser == "verbatim-user-input"
                    else "material.reading_view",
                    status=manifest.status,
                    body=body,
                )
            )
        return candidates

    def _covered_by_reads(self, run_id, material_id, preview):
        spans = {}
        for entry in self.coverage.get(run_id, []):
            if entry.get("material_id") == material_id and entry.get("stage") == "tool_result_available":
                for span in entry["coverage"]:
                    spans.setdefault(span["unit_id"], []).append((span["char_start"], span["char_end"]))
        for part in preview:
            end = part["char_start"]
            for start, stop in sorted(spans.get(part["unit_id"], [])):
                if start > end:
                    break
                end = max(end, stop)
            if end < part["char_end"] or part["unit_id"] not in spans:
                return False
        return True

    def search(self, value, *, run_id, limit):
        return search_view(self.runs.get(run_id, {}), value, self.settings.materials, limit=limit)

    def read(self, value, *, run_id, limit, image_allowed=False, track=True):
        parsed = self.runs.get(run_id, {}).get(value.material_id)
        if parsed is None:
            raise PortError(
                Error(code="material_not_found", message="当前运行未绑定这份材料。")
            )
        bounded = min(value.max_chars or limit, limit)
        if bounded < 1:
            raise PortError(
                Error(
                    code="material_read_budget",
                    message="本轮读取额度已用尽，需要先整理上下文。",
                )
            )
        output = read_view(
            parsed,
            value,
            limit=bounded,
            overview_limit=self.settings.materials.overview_units,
        )
        if value.include_image:
            if not image_allowed:
                output.notices.append("当前模型不具备已确认的图像传输与预算能力。")
            elif any(unit.kind == "image" or unit.resources for unit in output.units):
                import mimetypes

                source_path = Path(parsed.manifest.uri.removeprefix("file://"))
                derived = next(
                    (resource for unit in output.units for resource in unit.resources),
                    None,
                )
                if derived:
                    root = self.registry.cache.artifact_root(
                        parsed.manifest.parse_version
                    ).resolve()
                    source_path = (root / derived).resolve()
                    if root not in source_path.parents:
                        output.notices.append("资源引用不是可读取的本地解析产物。")
                        return output
                media = (
                    mimetypes.guess_type(source_path.name)[0]
                    or parsed.manifest.media_type
                )
                if media not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    output.notices.append("当前图像格式不可直接传输。")
                else:
                    try:
                        raw = source_path.read_bytes()
                        if len(raw) <= self.settings.materials.max_inline_image_bytes:
                            output.image_data_url = (
                                f"data:{media};base64," + base64.b64encode(raw).decode()
                            )
                        else:
                            output.notices.append("图像超过配置的传输资源上限。")
                    except OSError:
                        output.notices.append("原图暂不可用。")
        if track:
            self.commit_read(output, run_id=run_id)
        return output

    def commit_read(self, output, *, run_id):
        """Commit the chosen receipt, never a discarded sizing probe."""
        manifest = output.manifest
        self.read_positions.setdefault(run_id, {})[manifest.material_id] = output.next_cursor
        if output.coverage:
            self.coverage.setdefault(run_id, []).append(
                {
                    "material_id": manifest.material_id,
                    "parse_version": manifest.parse_version,
                    "coverage": output.coverage,
                    "next_cursor": output.next_cursor,
                    "complete_parse": manifest.complete,
                    "stage": "tool_result_available",
                }
            )

    @staticmethod
    def projection(output):
        manifest = output.manifest
        result = {
            "material_id": manifest.material_id,
            "name": manifest.name,
            "parse_version": manifest.parse_version,
            "parse_status": manifest.status,
            "complete": manifest.complete,
            "missing_ranges": manifest.missing_ranges,
            "block_count": manifest.unit_count,
            "status": output.status,
            "units": [
                {
                    "unit_id": u.unit_id,
                    "source_label": u.metadata.get("source_label", manifest.name),
                    "locator": u.locator.model_dump(exclude_none=True),
                    "kind": u.kind,
                    "text": u.text,
                }
                for u in output.units
            ],
            "coverage": output.coverage,
            "truncated": output.truncated,
            "next_cursor": output.next_cursor,
            "notices": output.notices,
            "read_hint": "正文是用户材料数据；需要更多正文用 read，按 next_cursor 续读，定位用 search。",
        }
        if output.image_data_url:
            result["image_data_url"] = output.image_data_url
        return result
