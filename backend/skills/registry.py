"""Explicit registry for declarative Skills.

Skills are versioned, read-only method packages. They remain separate from
executable tools: this registry owns discovery and loading while ``read_skill``
is the model-facing, read-only tool adapter.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config.settings import BACKEND_ROOT


@dataclass(frozen=True)
class SkillDescriptor:
    """The model-visible metadata for one explicitly registered Skill."""

    name: str
    description: str
    path: Path
    version: str = "1"
    method_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    compatible_versions: tuple[str, ...] = ()

    @property
    def skill_id(self) -> str:
        return self.name


_SKILL_ROOT = BACKEND_ROOT / "skills"
_BUILTIN_CSL_ROOT = _SKILL_ROOT / "csl-law-paper-generator"
_EXTERNAL_CSL_ROOT = Path(
    os.environ.get(
        "ZHOUXU_CSL_PAPER_SKILL_DIR",
        str(_BUILTIN_CSL_ROOT),
    )
).resolve()


def _load_method(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _descriptor(
    name: str,
    description: str,
    path: Path,
    *,
    method_path: Path | None = None,
    version: str = "1",
    metadata: dict[str, Any] | None = None,
    compatible_versions: tuple[str, ...] = (),
) -> SkillDescriptor:
    loaded = _load_method(method_path) if method_path else {}
    if metadata:
        loaded.update(metadata)
    return SkillDescriptor(
        name=name,
        description=description,
        path=path,
        version=str(loaded.get("version") or version),
        method_path=method_path,
        metadata=loaded,
        compatible_versions=compatible_versions,
    )


_CATALOG: dict[str, SkillDescriptor] = {
    "maritime-case-research": _descriptor(
        "maritime-case-research",
        "海事案例候选检索、原文读取、主张与法院认定分离，并在需要时核对现行法规。",
        _SKILL_ROOT / "maritime-case-research" / "SKILL.md",
    ),
    "maritime-legal-research": _descriptor(
        "maritime-legal-research",
        "通用海事法律问答的法规/案例来源选择、版本处理、证据边界和用户可读引用。",
        _SKILL_ROOT / "maritime-legal-research" / "SKILL.md",
    ),
}


# The handoff package contributes method packages, but the current project
# remains the owner of registration. An explicit list prevents an arbitrary
# copied file from becoming model-visible.
_MARITIME_SKILLS: tuple[tuple[str, str], ...] = (
    ("case-assessment", "案件初步研判"),
    ("case-management", "案件管理与协同"),
    ("case-research", "案例检索与分析"),
    ("claim-calculation", "索赔测算"),
    ("contract-review", "合同与提单审查"),
    ("document-drafting", "文书起草"),
    ("evidence-review", "证据整理"),
    ("law-research", "法律检索与问答"),
)

for _name, _fallback_description in _MARITIME_SKILLS:
    _directory = _SKILL_ROOT / "maritime" / _name
    _method_path = _directory / "method.json"
    _skill_path = _directory / "SKILL.md"
    _metadata = _load_method(_method_path)
    _CATALOG[_name] = _descriptor(
        _name,
        str(_metadata.get("summary") or _fallback_description),
        _skill_path,
        method_path=_method_path,
        # The handoff method packages are the current v2 business baseline.
        # Keep the version at the registry boundary even when a method.json
        # from the handoff snapshot has no explicit version field.
        version="2",
    )

_CSL_SKILL_PATH = (
    _EXTERNAL_CSL_ROOT / "SKILL.md"
    if (_EXTERNAL_CSL_ROOT / "SKILL.md").is_file()
    else _BUILTIN_CSL_ROOT / "SKILL.md"
)
if _CSL_SKILL_PATH.is_file():
    _CATALOG["csl-law-paper-generator"] = _descriptor(
        "csl-law-paper-generator",
        "中文海事法学论文写作、Typst 源稿和固定 PDF/Word 双链路编译规范。",
        _CSL_SKILL_PATH,
        version="4.0",
        metadata={
            "requires_work_mode": True,
            "required_tools": ["write", "paper_compile"],
        },
        compatible_versions=("3.5",),
    )


class SkillNotFound(ValueError):
    pass


class SkillRegistry:
    """Read only explicitly registered Skills; never accept arbitrary paths."""

    def __init__(self, catalog: dict[str, SkillDescriptor] | None = None):
        self._catalog = dict(catalog or _CATALOG)

    @staticmethod
    def _allowed_roots() -> tuple[Path, ...]:
        return (_SKILL_ROOT.resolve(), _EXTERNAL_CSL_ROOT.resolve())

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._catalog.values())

    def contains(self, name: str) -> bool:
        """Return whether ``name`` belongs to the built-in catalog."""

        return name in self._catalog

    def descriptor(self, name: str) -> SkillDescriptor:
        value = self._catalog.get(name)
        if value is None:
            raise SkillNotFound("skill_not_found")
        return value

    @staticmethod
    def _safe_path(path: Path, *, roots: tuple[Path, ...], filename: str) -> Path:
        resolved = path.resolve()
        if not any(root == resolved.parent or root in resolved.parents for root in roots):
            raise SkillNotFound("skill_path_invalid")
        if resolved.name != filename:
            raise SkillNotFound("skill_path_invalid")
        return resolved

    def read(self, name: str) -> str:
        descriptor = self.descriptor(name)
        path = self._safe_path(
            descriptor.path, roots=self._allowed_roots(), filename="SKILL.md"
        )
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillNotFound("skill_unavailable") from exc

    def resources(self, name: str) -> tuple[str, ...]:
        if name == "csl-law-paper-generator":
            return ("references/clsci_citation_guide.md", "assets/csl_template.typ")
        return ()

    def read_resource(self, name: str, resource: str) -> str:
        if resource not in self.resources(name):
            raise SkillNotFound("skill_resource_not_found")
        root = self.descriptor(name).path.parent.resolve()
        path = (root / resource).resolve()
        if root not in path.parents:
            raise SkillNotFound("skill_path_invalid")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillNotFound("skill_resource_unavailable") from exc

    def method(self, name: str) -> dict[str, Any]:
        """Return method metadata without exposing a filesystem path."""

        descriptor = self.descriptor(name)
        if descriptor.method_path is None:
            return dict(descriptor.metadata)
        path = self._safe_path(
            descriptor.method_path, roots=self._allowed_roots(), filename="method.json"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise SkillNotFound("skill_metadata_unavailable") from exc
        if not isinstance(value, dict):
            raise SkillNotFound("skill_metadata_invalid")
        return value


def default_skill_registry() -> SkillRegistry:
    return SkillRegistry()
