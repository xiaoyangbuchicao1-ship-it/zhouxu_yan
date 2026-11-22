"""Versioned prompt artifacts; importing this module never changes active settings.

This is the file-based version boundary, not a published catalog or Run snapshot.
The application reads defaults.toml. Its prompt table now contains the approved
2026-11-22.1 content; the original candidate remains the immutable test reference.
"""

import json
import tomllib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Formatter
from types import MappingProxyType
from typing import Mapping

from backend.config.settings import Settings

PROMPT_SECTIONS = frozenset({
    "system", "task", "evidence", "delivery", "task_lifecycle",
    "context_inventory", "continue_stage", "continue_visible",
    "delivery_status_description", "delivery_remaining_description",
    "decision_protocol", "tool_repetition", "runtime_date", "compaction",
    "finalization",
})
TEMPLATE_FIELDS = {
    "runtime_date": {"current_date"},
    "decision_protocol": {"delivery_tool", "clarification_tool"},
}


@dataclass(frozen=True)
class PromptBundle:
    version: str
    status: str
    prompts: Mapping[str, str]
    content_sha256: str

    def apply(self, settings: Settings) -> Settings:
        """Build an isolated candidate configuration without mutating the caller."""
        candidate = settings.model_copy(deep=True)
        candidate.prompts = dict(self.prompts)
        return candidate


def load_prompt_bundle(path: Path) -> PromptBundle:
    """Validate a complete candidate at the external configuration boundary."""
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if set(data) != {"bundle", "prompts"}:
        raise ValueError("Prompt bundle must contain only bundle and prompts tables")
    metadata, prompts = data["bundle"], data["prompts"]
    if set(metadata) != {"version", "status"}:
        raise ValueError("Prompt bundle metadata requires version and status")
    if not isinstance(metadata["version"], str) or not metadata["version"].strip():
        raise ValueError("Prompt bundle version must be a nonempty string")
    if metadata["status"] != "candidate":
        raise ValueError("Only unpublished candidates are supported at this stage")
    if set(prompts) != PROMPT_SECTIONS:
        raise ValueError(
            f"Prompt sections mismatch: missing={sorted(PROMPT_SECTIONS - prompts.keys())}, "
            f"unknown={sorted(prompts.keys() - PROMPT_SECTIONS)}"
        )
    for section, content in prompts.items():
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Prompt section must be a nonempty string: {section}")
    for section, expected in TEMPLATE_FIELDS.items():
        fields = {
            name for _, name, _, _ in Formatter().parse(prompts[section])
            if name is not None
        }
        if fields != expected:
            raise ValueError(f"Prompt template variables mismatch: {section}")
    canonical = json.dumps(prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return PromptBundle(
        version=metadata["version"], status=metadata["status"],
        prompts=MappingProxyType(prompts),
        content_sha256=sha256(canonical.encode("utf-8")).hexdigest(),
    )
