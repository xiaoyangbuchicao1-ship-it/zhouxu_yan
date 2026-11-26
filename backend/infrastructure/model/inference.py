"""Automatic capability defaults for newly added or discovered models.

Admins shouldn't have to memorize context windows or thinking-effort modes
when connecting a relay or provider.  We check the curated upstream fact tables
first, then match well-known model naming patterns, and fall back to a safe
conservative default (128K context, 16K output, thinking disabled) for unknown
models.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache

from backend.config.settings import BACKEND_ROOT
from backend.infrastructure.model.registry import model_family


@dataclass(frozen=True)
class InferredModelDefaults:
    context_tokens: int
    max_output_tokens: int
    thinking_modes: tuple[str, ...]
    default_thinking: str
    matched: bool
    source_hint: str

    def to_dict(self) -> dict:
        return {
            "context_tokens": self.context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "thinking_modes": list(self.thinking_modes),
            "default_thinking": self.default_thinking,
            "matched": self.matched,
            "source_hint": self.source_hint,
        }


@lru_cache(maxsize=1)
def _curated_tables() -> dict[str, dict]:
    combined: dict[str, dict] = {}
    for filename in ("relay_capabilities.toml", "model_capabilities.toml"):
        path = BACKEND_ROOT / "config" / filename
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        models = data.get("models")
        if isinstance(models, dict):
            for key, entry in models.items():
                if isinstance(entry, dict):
                    combined[key.strip().lower()] = entry
    return combined


_RULES: tuple[tuple[re.Pattern[str], int, int, tuple[str, ...], str, str], ...] = (
    # DeepSeek
    (
        re.compile(r"deepseek.*(?:v4|flash)", re.I),
        1_000_000,
        393_216,
        ("none", "low", "high", "max"),
        "none",
        "DeepSeek V4",
    ),
    (
        re.compile(r"deepseek.*(?:r1|reasoner|thinking)", re.I),
        131_072,
        32_768,
        ("none", "low", "medium", "high"),
        "high",
        "DeepSeek 推理",
    ),
    (
        re.compile(r"deepseek.*v3", re.I),
        131_072,
        16_384,
        ("none", "low", "high"),
        "none",
        "DeepSeek V3",
    ),
    (
        re.compile(r"deepseek", re.I),
        131_072,
        16_384,
        ("none",),
        "none",
        "DeepSeek",
    ),
    # OpenAI GPT & o-series
    (
        re.compile(r"(?:^|/)gpt-(?:5|6)", re.I),
        400_000,
        128_000,
        ("none", "low", "medium", "high"),
        "medium",
        "OpenAI GPT 推理",
    ),
    (
        re.compile(r"(?:^|/)o(?:1|3|4)(?:[-.]|$)", re.I),
        200_000,
        100_000,
        ("low", "medium", "high"),
        "medium",
        "OpenAI o 系列推理",
    ),
    (
        re.compile(r"(?:^|/)gpt-4\.1", re.I),
        1_048_576,
        32_768,
        ("none",),
        "none",
        "OpenAI GPT-4.1",
    ),
    (
        re.compile(r"(?:^|/)gpt-4o", re.I),
        128_000,
        16_384,
        ("none",),
        "none",
        "OpenAI GPT-4o",
    ),
    # Anthropic Claude
    (
        re.compile(r"claude.*(?:4(?:[-.]|$)|3[.-]7|sonnet-4|opus-4|haiku-4)", re.I),
        200_000,
        64_000,
        ("none", "low", "medium", "high"),
        "none",
        "Claude 思考系列",
    ),
    (
        re.compile(r"claude", re.I),
        200_000,
        8_192,
        ("none",),
        "none",
        "Claude",
    ),
    # Google Gemini
    (
        re.compile(r"gemini-(?:2\.5|3|4)", re.I),
        1_048_576,
        65_536,
        ("none", "low", "medium", "high"),
        "none",
        "Gemini 思考系列",
    ),
    (
        re.compile(r"gemini-", re.I),
        1_048_576,
        8_192,
        ("none",),
        "none",
        "Gemini",
    ),
    # Qwen / QwQ
    (
        re.compile(r"(?:qwq|qwen3|qwen.*thinking)", re.I),
        131_072,
        32_768,
        ("none", "low", "medium", "high"),
        "none",
        "Qwen 思考系列",
    ),
    (
        re.compile(r"qwen", re.I),
        131_072,
        16_384,
        ("none",),
        "none",
        "Qwen",
    ),
    # GLM
    (
        re.compile(r"glm-(?:4\.[5-9]|5|z1)", re.I),
        131_072,
        32_768,
        ("none", "low", "high"),
        "none",
        "GLM 思考系列",
    ),
    (
        re.compile(r"glm-", re.I),
        131_072,
        16_384,
        ("none",),
        "none",
        "GLM",
    ),
    # Kimi / Moonshot
    (
        re.compile(r"(?:kimi.*thinking|k2-thinking)", re.I),
        131_072,
        32_768,
        ("none", "low", "high"),
        "high",
        "Kimi 思考系列",
    ),
    (
        re.compile(r"(?:kimi|moonshot)", re.I),
        131_072,
        16_384,
        ("none",),
        "none",
        "Kimi",
    ),
    # Generic thinking/reasoning keywords
    (
        re.compile(r"(?:thinking|reasoner|-r1(?:[-.]|$))", re.I),
        131_072,
        32_768,
        ("none", "low", "high"),
        "none",
        "通用思考模型",
    ),
)

_SAFE_DEFAULT = InferredModelDefaults(
    context_tokens=131_072,
    max_output_tokens=16_384,
    thinking_modes=("none",),
    default_thinking="none",
    matched=False,
    source_hint="通用默认值",
)


def infer_model_defaults(model_id: str) -> InferredModelDefaults:
    """Infer safe, realistic context/output/thinking defaults from a model ID."""

    raw = (model_id or "").strip()
    if not raw:
        return _SAFE_DEFAULT
    lowered = raw.lower()
    tail = lowered.rsplit("/", 1)[-1]

    curated = _curated_tables()
    for candidate in (lowered, tail):
        entry = curated.get(candidate)
        if entry:
            ctx = int(
                entry.get("context_tokens")
                or entry.get("raw_context_tokens")
                or entry.get("conservative_context_tokens")
                or 131_072
            )
            out = int(
                entry.get("max_generation_tokens")
                or entry.get("conservative_generation_tokens")
                or 16_384
            )
            modes_raw = entry.get("thinking_modes")
            if isinstance(modes_raw, list) and modes_raw:
                modes = tuple(str(m) for m in modes_raw)
            elif entry.get("generation_includes_thinking"):
                modes = ("none", "low", "high", "max")
            else:
                modes = ("none",)
            default_t = str(entry.get("default_thinking_mode") or modes[0])
            if default_t not in modes:
                default_t = modes[0]
            return InferredModelDefaults(
                context_tokens=ctx,
                max_output_tokens=out,
                thinking_modes=modes,
                default_thinking=default_t,
                matched=True,
                source_hint="内置模型目录",
            )

    for pattern, ctx, out, modes, default_t, hint in _RULES:
        if pattern.search(tail) or pattern.search(lowered):
            return InferredModelDefaults(
                context_tokens=ctx,
                max_output_tokens=out,
                thinking_modes=modes,
                default_thinking=default_t,
                matched=True,
                source_hint=hint,
            )

    family = model_family(raw)
    if family != "other":
        return InferredModelDefaults(
            context_tokens=131_072,
            max_output_tokens=16_384,
            thinking_modes=("none",),
            default_thinking="none",
            matched=True,
            source_hint=f"{family.upper()} 通用默认",
        )
    return _SAFE_DEFAULT
