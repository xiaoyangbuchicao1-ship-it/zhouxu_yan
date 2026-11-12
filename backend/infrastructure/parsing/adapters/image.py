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


class ImageParser:
    name = "pillow-optional-ocr"
    _suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        return (
            infer_media_type(material).startswith("image/")
            or local_path(material).suffix.lower() in self._suffixes
        )

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        path = local_path(material)
        try:
            from PIL import Image

            with Image.open(path) as image:
                metadata = {
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                }
            ocr_text = ""
            ocr_status = "ocr_unavailable"
            if self.settings.ocr_enabled:
                try:
                    import pytesseract

                    with Image.open(path) as image:
                        ocr_text = pytesseract.image_to_string(
                            image, lang=self.settings.ocr_language
                        ).strip()
                    ocr_status = "ocr_available"
                except Exception as exc:
                    ocr_status = f"ocr_failed:{type(exc).__name__}"
            unit = MaterialUnit(
                unit_id=unit_id(key, "image", "原图"),
                material_id=key,
                kind="image",
                locator=MaterialLocator(kind="image", value="原图", label="原始图片"),
                text=ocr_text,
                metadata={**metadata, "ocr_status": ocr_status},
            )
            # OCR text is not a claim that diagrams/layout have been understood.
            status = "partial"
            errors = [] if ocr_text else ["image_text_requires_visual_model_or_ocr"]
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status=status,
                    parser=self.name,
                    summary=ocr_text[: self.settings.preview_chars],
                    unit_count=1,
                    available_units=[unit.unit_id],
                    errors=errors,
                    missing_ranges=["原图的非文字视觉信息尚需按需读取"],
                    metadata={
                        **metadata,
                        "visual_readable": True,
                        "visual_read_mode": "on_demand",
                    },
                ),
                units=[unit],
            )
        except Exception as exc:
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="failed",
                    parser=self.name,
                    errors=["parse_failed:" + type(exc).__name__],
                )
            )
