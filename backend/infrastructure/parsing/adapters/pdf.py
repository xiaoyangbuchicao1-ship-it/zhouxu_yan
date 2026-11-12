from __future__ import annotations

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


class PdfParser:
    """Small PDF baseline; MinerU can replace this adapter without contract changes."""

    name = "pdf-pypdf-baseline"

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        return (
            infer_media_type(material) == "application/pdf"
            or local_path(material).suffix.lower() == ".pdf"
        )

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        try:
            from pypdf import PdfReader

            path = local_path(material)
            if path.stat().st_size > self.settings.max_file_bytes:
                raise ValueError("file_too_large")
            reader = PdfReader(str(path))
            units: list[MaterialUnit] = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                units.append(
                    MaterialUnit(
                        unit_id=unit_id(key, "page", str(index)),
                        material_id=key,
                        kind="page",
                        locator=MaterialLocator(
                            kind="page", value=str(index), label=f"第{index}页"
                        ),
                        text=text,
                        metadata={"characters": len(text)},
                    )
                )
            readable = [unit for unit in units if unit.text.strip()]
            missing = [
                f"第{index}页：无可提取正文，视觉能力尚未完成"
                for index, unit in enumerate(units, 1)
                if not unit.text.strip()
            ]
            status = "available" if readable and not missing else "partial"
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type="application/pdf",
                    status=status,
                    parser=self.name,
                    summary="\n".join(unit.text for unit in readable)[
                        : self.settings.preview_chars
                    ],
                    unit_count=len(units),
                    available_units=[unit.unit_id for unit in units],
                    errors=[] if not missing else ["pdf_visual_content_unavailable"],
                    missing_ranges=missing,
                    metadata={
                        "pages": len(units),
                        "layout": "native_text_order_not_visual_layout",
                        "visual_fallback": "configured_adapter_only",
                    },
                ),
                units=units,
            )
        except Exception as exc:
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type="application/pdf",
                    status="failed",
                    parser=self.name,
                    errors=["parse_failed:" + type(exc).__name__],
                )
            )
