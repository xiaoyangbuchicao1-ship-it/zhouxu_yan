from __future__ import annotations

import re

from backend.config.settings import MaterialSettings
from backend.contracts.modules.materials import (
    MaterialInput,
    MaterialLocator,
    MaterialManifest,
    MaterialUnit,
    ParsedMaterial,
)
from backend.infrastructure.parsing.adapters.common import (
    infer_media_type,
    local_path,
    material_id,
    material_name,
    unit_id,
)


class TextParser:
    name = "text"
    _suffixes = {".txt", ".md", ".markdown", ".json", ".log", ".csv"}

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        path = local_path(material)
        return (
            infer_media_type(material).startswith("text/")
            or path.suffix.lower() in self._suffixes
        )

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        path = local_path(material)
        try:
            raw = path.read_bytes()
            if len(raw) > self.settings.max_file_bytes:
                raise ValueError("file_too_large")
            try:
                text = raw.decode("utf-8-sig")
                decode_errors = False
            except UnicodeDecodeError:
                text = raw.decode("utf-8-sig", errors="replace")
                decode_errors = True
            units = []
            # Structural paragraphs with exact source character ranges. A very
            # long paragraph stays intact in storage; the reader can seek within.
            for index, match in enumerate(re.finditer(r"[^\n]*(?:\n|$)", text)):
                if not match.group():
                    continue
                units.append(
                    MaterialUnit(
                        unit_id=unit_id(key, "text", "全文"),
                        material_id=key,
                        kind="text",
                        locator=MaterialLocator(
                            kind="text",
                            value=str(index + 1),
                            label=f"第{index + 1}段",
                            char_start=match.start(),
                            char_end=match.end(),
                        ),
                        text=match.group(),
                        metadata={"characters": len(match.group())},
                    )
                )
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="partial" if decode_errors else "available",
                    missing_ranges=["UTF-8 解码存在替换字符，原件仍完整保存"]
                    if decode_errors
                    else [],
                    parser=self.name,
                    summary=text[: self.settings.preview_chars],
                    unit_count=len(units),
                    available_units=[unit.unit_id for unit in units],
                ),
                units=units,
            )
        except Exception as exc:
            return failed_text(
                material, key, self.name, "parse_failed:" + type(exc).__name__
            )


def failed_text(
    material: MaterialInput, key: str, parser: str, error: str
) -> ParsedMaterial:
    return ParsedMaterial(
        manifest=MaterialManifest(
            material_id=key,
            name=material_name(material),
            uri=material.uri,
            media_type=infer_media_type(material),
            status="failed",
            parser=parser,
            errors=[error],
        )
    )
