from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

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

VisionDescriber = Callable[[Path, str, str], tuple[str, str]]

_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _prepare_image_for_ocr(image):
    """Composite alpha onto white, upscale small images, and sharpen edges for OCR."""
    from PIL import Image, ImageFilter, ImageOps

    prepared = ImageOps.exif_transpose(image)
    if prepared.mode in ("RGBA", "LA") or (
        prepared.mode == "P" and "transparency" in prepared.info
    ):
        rgba = prepared.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        prepared = canvas
    elif prepared.mode != "RGB":
        prepared = prepared.convert("RGB")
    else:
        prepared = prepared.copy()

    w, h = prepared.size
    shorter = min(w, h)
    longer = max(w, h)
    if shorter > 0 and shorter < 1800 and longer < 3200:
        scale = min(2.0, 1800.0 / shorter, 3200.0 / longer)
        if scale > 1.05:
            prepared = prepared.resize(
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                Image.Resampling.LANCZOS,
            )

    gray = ImageOps.grayscale(prepared)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def _is_garbage_token(token: str, conf: float) -> bool:
    stripped = token.strip()
    if not stripped:
        return True
    if not _MEANINGFUL_CHAR_RE.search(stripped):
        return conf < 70 or len(stripped) <= 2
    if len(stripped) == 1 and conf < 45:
        return True
    if conf < 30:
        return True
    return False


def _ocr_pass(pytesseract, image, lang: str, psm: int) -> tuple[str, float]:
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config=f"--oem 3 --psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    n = len(data.get("text", []))
    lines: dict[tuple[int, int, int], list[str]] = {}
    weighted_score = 0.0
    for i in range(n):
        raw_text = str(data["text"][i] or "").strip()
        if not raw_text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0 or _is_garbage_token(raw_text, conf):
            continue
        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        lines.setdefault(key, []).append(raw_text)
        meaningful_len = len(_MEANINGFUL_CHAR_RE.findall(raw_text))
        weighted_score += meaningful_len * (conf / 100.0)

    rendered = []
    for key in sorted(lines):
        line_text = " ".join(lines[key]).strip()
        if line_text:
            rendered.append(line_text)
    return "\n".join(rendered).strip(), weighted_score


def _extract_clean_ocr(path: Path, lang: str) -> tuple[str, str]:
    import pytesseract
    from PIL import Image

    with Image.open(path) as raw:
        prepared = _prepare_image_for_ocr(raw)

    best_text = ""
    best_score = -1.0
    for psm in (11, 3):
        try:
            text, score = _ocr_pass(pytesseract, prepared, lang, psm)
            if score > best_score and text:
                best_text = text
                best_score = score
        except Exception:
            continue

    if not best_text:
        best_text = pytesseract.image_to_string(prepared, lang=lang).strip()
    return best_text, "ocr_available"


class ImageParser:
    name = "pillow-optional-ocr"
    _suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}

    def __init__(
        self,
        settings: MaterialSettings,
        *,
        vision_describer: VisionDescriber | None = None,
    ):
        self.settings = settings
        self.vision_describer = vision_describer

    def can_handle(self, material: MaterialInput) -> bool:
        return (
            infer_media_type(material).startswith("image/")
            or local_path(material).suffix.lower() in self._suffixes
        )

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        path = local_path(material)
        media_type = infer_media_type(material)
        try:
            from PIL import Image

            with Image.open(path) as image:
                metadata = {
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                }
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()

            vision_text = ""
            vision_status = "vision_assist_disabled"
            if self.settings.vision_assist_enabled and self.vision_describer is not None:
                vision_text, vision_status = self.vision_describer(path, media_type, fingerprint)

            ocr_text = ""
            ocr_status = "ocr_unavailable"
            if self.settings.ocr_enabled:
                try:
                    ocr_text, ocr_status = _extract_clean_ocr(path, self.settings.ocr_language)
                except Exception as exc:
                    ocr_status = f"ocr_failed:{type(exc).__name__}"

            units: list[MaterialUnit] = []
            if vision_text:
                units.append(
                    MaterialUnit(
                        unit_id=unit_id(key, "vision", "视觉解析"),
                        material_id=key,
                        kind="image",
                        locator=MaterialLocator(
                            kind="image",
                            value="视觉解析",
                            label="视觉内容解析（AI 看图）",
                        ),
                        text=vision_text,
                        metadata={
                            **metadata,
                            "vision_assist_status": vision_status,
                            "ocr_status": ocr_status,
                        },
                    )
                )
            if ocr_text or not units:
                units.append(
                    MaterialUnit(
                        unit_id=unit_id(key, "image", "原图"),
                        material_id=key,
                        kind="image",
                        locator=MaterialLocator(
                            kind="image",
                            value="原图",
                            label="OCR 识别参考" if vision_text else "原始图片",
                        ),
                        text=ocr_text,
                        metadata={
                            **metadata,
                            "ocr_status": ocr_status,
                            "vision_assist_status": vision_status,
                        },
                    )
                )

            primary_text = vision_text or ocr_text
            # OCR/assist text is not a claim that every pixel detail has been inspected.
            status = "partial"
            errors = [] if primary_text else ["image_text_requires_visual_model_or_ocr"]
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=media_type,
                    status=status,
                    parser=self.name,
                    summary=primary_text[: self.settings.preview_chars],
                    unit_count=len(units),
                    available_units=[u.unit_id for u in units],
                    errors=errors,
                    missing_ranges=[
                        "原图已附带视觉/文字解析，支持视觉的模型仍可按需直看原图细节"
                        if vision_text
                        else "原图的非文字视觉信息尚需按需读取"
                    ],
                    metadata={
                        **metadata,
                        "visual_readable": True,
                        "visual_read_mode": "on_demand",
                        "vision_assist_status": vision_status,
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
                    media_type=media_type,
                    status="failed",
                    parser=self.name,
                    errors=["parse_failed:" + type(exc).__name__],
                )
            )
