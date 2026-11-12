"""Small, fixed skill catalog used by the Harness.

Skill files describe reusable domain workflows.  They are not executable
plugins and never receive direct access to a database, account, or filesystem
outside their own read-only package.
"""

from dataclasses import dataclass
from pathlib import Path

from backend.config.settings import BACKEND_ROOT


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    description: str
    path: Path


_SKILL_ROOT = BACKEND_ROOT / "skills"
_CATALOG: dict[str, SkillDescriptor] = {
    "maritime-case-research": SkillDescriptor(
        name="maritime-case-research",
        description="海事案例候选检索、原文读取、主张与法院认定分离，并在需要时核对现行法规。",
        path=_SKILL_ROOT / "maritime-case-research" / "SKILL.md",
    ),
    "maritime-legal-research": SkillDescriptor(
        name="maritime-legal-research",
        description="通用海事法律问答的法规/案例来源选择、版本处理、证据边界和用户可读引用。",
        path=_SKILL_ROOT / "maritime-legal-research" / "SKILL.md",
    ),
}


class SkillNotFound(ValueError):
    pass


class SkillRegistry:
    """Discover only explicitly registered skills; never accept arbitrary paths."""

    def __init__(self, catalog: dict[str, SkillDescriptor] | None = None):
        self._catalog = dict(catalog or _CATALOG)

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._catalog.values())

    def descriptor(self, name: str) -> SkillDescriptor:
        value = self._catalog.get(name)
        if value is None:
            raise SkillNotFound("skill_not_found")
        return value

    def read(self, name: str) -> str:
        descriptor = self.descriptor(name)
        path = descriptor.path.resolve()
        root = _SKILL_ROOT.resolve()
        if root not in path.parents or path.name != "SKILL.md":
            raise SkillNotFound("skill_path_invalid")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillNotFound("skill_unavailable") from exc


def default_skill_registry() -> SkillRegistry:
    return SkillRegistry()
