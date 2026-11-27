"""Cached multimodal vision transcription for uploaded image materials.

Gives pure-text models (and the attachment preview modal) an accurate, structured
visual transcription of diagrams, schematics, screenshots, and tables by routing
once per unique image SHA-256 through an active vision-capable model (preferring
``deepseek-flash`` when configured).  Falls back honestly when no vision model
is configured or when an upstream call fails.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Callable

import httpx

from backend.config.settings import Settings
from backend.infrastructure.database.connection import WORKSPACE_ROOT

_PROMPT_VERSION = "v2"
_VISION_PROMPT = (
    "请忠实提取这张图片的全部关键信息，供后续问答与检索使用：\n"
    "1. 先用 1-2 句话概括图片主题与类型（如电路原理图、软件界面截图、表格、文档扫描件等）。\n"
    "2. 按分区或从上到下、从左到右逐项清晰转写图中可读的中文、英文、编号、数值、图例与关键标注（同一组密集引脚/列表按区段概括，不要机械重复）。\n"
    "3. 如含图表、电路连线、分区框或结构关系，简要说明各区域/模块之间的对应关系。\n"
    "要求：严格忠实于原图，严禁编造看不清的细节；确实模糊无法辨认处标注“[模糊]”。直接输出清晰的结构化纯文本，不要输出开场白或套话。"
)

VisionDescriber = Callable[[Path, str, str], tuple[str, str]]


def encode_image_for_transport(
    source_path: Path,
    media_type: str,
    *,
    max_bytes: int,
    max_edge: int = 2048,
    prefer_png_limit: int = 1_200_000,
) -> tuple[str, str] | None:
    """Return ``(media_type, base64_data)`` fitting within ``max_bytes``.

    When the original image already fits under ``max_bytes`` and is a supported
    wire media type, it is returned verbatim.  Otherwise Pillow composites any
    alpha channel onto white and progressively downscales/re-encodes so large
    screenshots or technical schematics stay crisp and deliverable.
    """
    supported = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    raw = source_path.read_bytes()
    if media_type in supported and len(raw) <= max_bytes:
        return media_type, base64.b64encode(raw).decode("ascii")

    from PIL import Image, ImageOps

    with Image.open(source_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.split()[-1])
            rgb = canvas
        elif img.mode != "RGB":
            rgb = img.convert("RGB")
        else:
            rgb = img.copy()

    w, h = rgb.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        rgb = rgb.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.Resampling.LANCZOS,
        )

    # Prefer lossless PNG for schematics/screenshots when reasonably compact.
    png_buf = io.BytesIO()
    rgb.save(png_buf, format="PNG", optimize=True)
    png_bytes = png_buf.getvalue()
    if len(png_bytes) <= min(max_bytes, prefer_png_limit):
        return "image/png", base64.b64encode(png_bytes).decode("ascii")

    # Progressive JPEG encoding & downscale fallback.
    current = rgb
    for quality in (88, 78, 68):
        buf = io.BytesIO()
        current.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return "image/jpeg", base64.b64encode(data).decode("ascii")
    for edge in (1400, 1024, 768):
        cw, ch = current.size
        if max(cw, ch) > edge:
            scale = edge / float(max(cw, ch))
            current = current.resize(
                (max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        current.save(buf, format="JPEG", quality=72, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return "image/jpeg", base64.b64encode(data).decode("ascii")
    return None


def _pick_vision_revision(model_registry):
    directory = getattr(model_registry, "directory", None)
    candidates = []
    if directory is not None:
        try:
            candidates = list(directory.list_current())
        except Exception:
            candidates = []
    else:
        try:
            for profile in model_registry.profiles():
                candidates.append(model_registry.resolve(profile.profile_id))
        except Exception:
            candidates = []

    vision_ready = [
        rev
        for rev in candidates
        if rev.profile.configured
        and rev.profile.available
        and rev.profile.capabilities.vision == "confirmed"
        and rev.endpoint
    ]
    if not vision_ready:
        return None

    def priority(rev):
        pid = rev.profile.profile_id.lower()
        mid = rev.profile.model_id.lower()
        if pid == "deepseek-flash" or "deepseek-flash" in mid:
            return (0, pid)
        if "flash" in mid or "mini" in mid or "haiku" in mid:
            return (1, pid)
        if "gpt-4o" in mid or "gemini" in mid:
            return (2, pid)
        return (3, pid)

    vision_ready.sort(key=priority)
    return vision_ready[0]


def _extract_response_text(protocol: str, data: dict) -> str:
    if protocol in {"deepseek_chat_completions", "openai_chat_completions"}:
        choices = data.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        return ""
    if protocol == "openai_responses":
        parts = []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                for block in item.get("content") or []:
                    if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
        return "".join(parts).strip()
    if protocol == "anthropic_messages":
        return "".join(
            block.get("text", "")
            for block in data.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    if protocol == "google_generative_language":
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
        ).strip()
    return ""


def _build_request(protocol: str, model_id: str, media_type: str, b64_data: str) -> dict:
    data_url = f"data:{media_type};base64,{b64_data}"
    if protocol in {"deepseek_chat_completions", "openai_chat_completions"}:
        body = {
            "model": model_id,
            "temperature": 0,
            "max_tokens": 3072,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        if protocol == "deepseek_chat_completions":
            body["thinking"] = {"type": "disabled"}
        return body
    if protocol == "openai_responses":
        return {
            "model": model_id,
            "store": False,
            "max_output_tokens": 2048,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _VISION_PROMPT},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
        }
    if protocol == "anthropic_messages":
        return {
            "model": model_id,
            "max_tokens": 2048,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        },
                    ],
                }
            ],
        }
    if protocol == "google_generative_language":
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": _VISION_PROMPT},
                        {"inlineData": {"mimeType": media_type, "data": b64_data}},
                    ],
                }
            ],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0},
        }
    raise ValueError(f"unsupported_vision_protocol:{protocol}")


def _cache_dir(settings: Settings) -> Path:
    root = Path(settings.materials.cache_root)
    if not root.is_absolute():
        root = (WORKSPACE_ROOT / root).resolve()
    target = root / "vision_cache"
    target.mkdir(parents=True, exist_ok=True)
    return target


def create_vision_describer(settings: Settings, model_registry) -> VisionDescriber:
    """Create a cached, fail-soft multimodal describer backed by the active model directory."""

    def describe(image_path: Path, media_type: str, fingerprint: str) -> tuple[str, str]:
        if not settings.materials.vision_assist_enabled:
            return "", "vision_assist_disabled"
        revision = _pick_vision_revision(model_registry)
        if revision is None:
            return "", "vision_assist_unavailable:no_vision_model"
        profile = revision.profile
        cache_key = hashlib.sha256(
            f"{fingerprint}:{profile.profile_id}:{profile.model_id}:{_PROMPT_VERSION}".encode("utf-8")
        ).hexdigest()[:32]
        cache_file = _cache_dir(settings) / f"{cache_key}.json"
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and isinstance(cached.get("text"), str) and cached["text"].strip():
                    return cached["text"].strip(), f"vision_assist_cached:{profile.profile_id}"
            except (OSError, ValueError):
                pass

        try:
            key = model_registry.credential(revision)
        except Exception:
            return "", f"vision_assist_unavailable:credential:{profile.profile_id}"

        encoded = encode_image_for_transport(
            image_path,
            media_type,
            max_bytes=min(settings.materials.max_inline_image_bytes, 2_500_000),
            max_edge=1600,
        )
        if encoded is None:
            return "", "vision_assist_failed:image_encode"
        wire_media, b64_data = encoded

        headers = {"Content-Type": "application/json"}
        secret = key.get_secret_value()
        if revision.auth_style == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif revision.auth_style == "x-api-key":
            headers["x-api-key"] = secret
            if profile.protocol == "anthropic_messages":
                headers["anthropic-version"] = "2023-06-01"
        elif revision.auth_style == "x-goog-api-key":
            headers["x-goog-api-key"] = secret

        try:
            payload = _build_request(profile.protocol, profile.model_id, wire_media, b64_data)
            with httpx.Client(timeout=settings.materials.vision_assist_timeout_seconds) as client:
                response = client.post(revision.endpoint, headers=headers, json=payload)
            if not response.is_success:
                return "", f"vision_assist_failed:http_{response.status_code}"
            text = _extract_response_text(profile.protocol, response.json())
            if not text:
                return "", "vision_assist_failed:empty_response"
            try:
                cache_file.write_text(
                    json.dumps(
                        {
                            "profile_id": profile.profile_id,
                            "model_id": profile.model_id,
                            "prompt_version": _PROMPT_VERSION,
                            "text": text,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return text, f"vision_assist_ok:{profile.profile_id}"
        except Exception as exc:
            return "", f"vision_assist_failed:{type(exc).__name__}"

    return describe
