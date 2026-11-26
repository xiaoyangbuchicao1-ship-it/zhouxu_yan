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
from typing import Any, Literal

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
    mode_scope: Literal["chat", "work"] = "work"
    ready: bool = True
    runtime_instructions: str = ""

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
    mode_scope: Literal["chat", "work"] = "work",
    ready: bool = True,
    runtime_instructions: str = "",
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
        mode_scope=mode_scope,
        ready=ready,
        runtime_instructions=runtime_instructions,
    )


_CATALOG: dict[str, SkillDescriptor] = {
    "maritime-case-research": _descriptor(
        "maritime-case-research",
        "海事案例候选检索、原文读取、主张与法院认定分离，并在需要时核对现行法规。",
        _SKILL_ROOT / "maritime-case-research" / "SKILL.md",
        mode_scope="chat",
        ready=True,
    ),
    "maritime-legal-research": _descriptor(
        "maritime-legal-research",
        "通用海事法律问答的法规/案例来源选择、版本处理、证据边界和用户可读引用。",
        _SKILL_ROOT / "maritime-legal-research" / "SKILL.md",
        mode_scope="chat",
        ready=True,
    ),
    "maritime-paper-research": _descriptor(
        "maritime-paper-research",
        "海事法学术论文信息卡检索与原文核验流程，梳理学者观点、发表线索与期刊页码来源。",
        _SKILL_ROOT / "maritime-paper-research" / "SKILL.md",
        mode_scope="chat",
        ready=True,
    ),
}


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
        mode_scope="work",
        ready=True,
        runtime_instructions=(
            "舟序运行适配：先按选题调用 capability_card_select，匹配时用 capability_card_read "
            "读取方法；用 information_card_search/read 获取论文观点，法规与案例分别用现有工具核对。"
            "没有匹配方法时继续研究，不套用无关能力卡。包内引注指南可通过 read_skill 的 resource 参数读取。"
            "长篇 .typ 源稿请先用 write 创建头部与前半章，再用 write(..., append=true) 分 2~3 次按章节稳步追加，"
            "避免单次生成超过输出长度上限；源稿写完后调用 paper_compile(source_path=相对路径)。"
            "paper_compile 执行本 Skill 原始固定脚本并提供模板；不要用 bash 猜测宿主路径或自行重写编译器。"
            "只有双端编译成功才能交付 PDF 和 Word；错误信息可用于局部 edit 修正源稿后重新编译。"
            "引注中的作者、出版信息、案号和具体页码必须有真实来源；卡片没有页码时不能据此编造印刷页码。"
            "能力卡、信息卡、工具名和检索状态只供内部组织工作，不写进论文正文。"
        ),
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

    def descriptors(
        self,
        *,
        ready_only: bool = False,
        mode: str | None = None,
    ) -> tuple[SkillDescriptor, ...]:
        items = tuple(self._catalog.values())
        if ready_only:
            items = tuple(item for item in items if item.ready)
        if mode == "chat":
            items = tuple(item for item in items if item.mode_scope == "chat")
        return items

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
        # Other built-in skills may ship reference notes their SKILL.md points to.
        references = self.descriptor(name).path.parent / "references"
        if references.is_dir():
            return tuple(f"references/{item.name}" for item in sorted(references.glob("*.md")))
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
