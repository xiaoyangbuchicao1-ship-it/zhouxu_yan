"""Human-facing source references and controlled corpus reading.

Search adapters retain their internal identities for recovery and validation.
This module is the only projection used by the Workbench source cards.  It
keeps those identities out of the public fields and reads only the checked-in
canonical corpus, never an arbitrary path supplied by a model or browser.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.sources import SourceDocument, SourceReference

_URL = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
_SOURCE_ID = re.compile(r"(?:law|case)-[a-f0-9]{24}")


def _source_id(kind: str, *parts: Any) -> str:
    value = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True)
    return f"{kind}-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def law_source_id(document_name: str, article_no: str, version_date: str | None) -> str:
    return _source_id("law", document_name, article_no, version_date or "")


def case_source_id(case_id: str, title: str, snapshot_id: str | None) -> str:
    return _source_id("case", case_id, title, snapshot_id or "")


def _version_label(is_current: Any) -> str | None:
    if is_current is True:
        return "现行版本"
    if is_current is False:
        return "历史版本"
    return None


def source_refs_from_action(result: Any) -> list[dict[str, Any]]:
    """Project a tool receipt into deduplicated, UI-safe source references."""

    tool_name = getattr(result, "tool_name", None)
    payload = getattr(result, "result", None)
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items")
    items = raw_items if isinstance(raw_items, list) else [payload]
    refs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if tool_name == "dynamic_rag_search":
            title = str(item.get("document_name") or "").strip()
            article = str(item.get("article_no") or "").strip()
            if not title or not article:
                continue
            version = item.get("version_date") or None
            section = item.get("section_path")
            if isinstance(section, list):
                section = " / ".join(str(part) for part in section if part)
            refs.append(
                SourceReference(
                    source_id=law_source_id(title, article, version),
                    kind="law",
                    title=title,
                    locator=article,
                    section=str(section) if section else None,
                    version_date=version,
                    version_label=_version_label(item.get("corpus_current_label", item.get("is_current"))),
                ).model_dump(mode="json", exclude_none=True)
            )
        elif tool_name in {"search_cases", "read_case"}:
            case_id = str(item.get("case_id") or "").strip()
            title = str(item.get("title") or "").strip()
            snapshot = item.get("snapshot_id")
            if not case_id or not title:
                continue
            refs.append(
                SourceReference(
                    source_id=case_source_id(case_id, title, snapshot),
                    kind="case",
                    title=title,
                    locator="案例原文",
                    version_date=None,
                    version_label="本地案例库版本",
                ).model_dump(mode="json", exclude_none=True)
            )
    unique: dict[str, dict[str, Any]] = {}
    for ref in refs:
        unique.setdefault(ref["source_id"], ref)
    return list(unique.values())


class CorpusSourceRepository:
    """Controlled read service for source-card detail views."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = (BACKEND_ROOT.parent / "opensearch" / "corpus").resolve()
        self._laws: dict[str, dict[str, Any]] | None = None
        self._cases: dict[str, dict[str, Any]] | None = None

    def get(self, source_id: str) -> SourceDocument | None:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            return None
        if source_id.startswith("law-"):
            return self._law(source_id)
        if source_id.startswith("case-"):
            return self._case(source_id)
        return None

    def _load_laws(self) -> dict[str, dict[str, Any]]:
        if self._laws is not None:
            return self._laws
        result: dict[str, dict[str, Any]] = {}
        path = self.root / "records" / "articles.jsonl"
        if not path.is_file():
            self._laws = result
            return result
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("record_type") != "article":
                    continue
                key = law_source_id(
                    str(row.get("document_name") or ""),
                    str(row.get("article_no") or ""),
                    row.get("version_date") or None,
                )
                result.setdefault(key, row)
        except (OSError, UnicodeError, json.JSONDecodeError):
            result = {}
        self._laws = result
        return result

    def _law(self, source_id: str) -> SourceDocument | None:
        row = self._load_laws().get(source_id)
        if not row:
            return None
        title = str(row.get("document_name") or "")
        article = str(row.get("article_no") or "")
        version = row.get("version_date") or None
        text = str(row.get("article_text") or row.get("chunk_text") or "").strip()
        if not title or not article or not text:
            return None
        validity = row.get("validity_evidence")
        urls = validity.get("urls", []) if isinstance(validity, dict) else []
        url = next((item for item in urls if isinstance(item, str) and item.startswith(("http://", "https://"))), None)
        return SourceDocument(
            source_id=source_id,
            kind="law",
            title=title,
            locator=article,
            section=" / ".join(str(x) for x in row.get("section_path", []) if x) or None,
            version_date=version,
            version_label=_version_label(row.get("is_current")) or row.get("classification", {}).get("legal_status"),
            text=text,
            source_url=url,
        )

    def _load_cases(self) -> dict[str, dict[str, Any]]:
        if self._cases is not None:
            return self._cases
        result: dict[str, dict[str, Any]] = {}
        configured = Path(self.settings.cases.snapshot_dir)
        path = configured if configured.is_absolute() else (BACKEND_ROOT / configured).resolve()
        manifest_path = path / "manifest.json"
        cases_path = path / "cases.jsonl"
        if not cases_path.is_file():
            self._cases = result
            return result
        try:
            snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot_id = snapshot.get("snapshot_id")
            for line in cases_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                case_id = str(row.get("case_id") or "")
                title = str(row.get("title") or "")
                if case_id and title:
                    result[case_source_id(case_id, title, snapshot_id)] = {
                        **row,
                        "snapshot_id": snapshot_id,
                    }
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            result = {}
        self._cases = result
        return result

    def _case(self, source_id: str) -> SourceDocument | None:
        row = self._load_cases().get(source_id)
        if not row:
            return None
        title = str(row.get("title") or "")
        text = str(row.get("text") or "").strip()
        if not title or not text:
            return None
        # Only an explicitly curated source_url may become an external link;
        # arbitrary URLs inside case text are evidence, not navigation targets.
        candidate_url = row.get("source_url")
        url = candidate_url if isinstance(candidate_url, str) and _URL.fullmatch(candidate_url) else None
        return SourceDocument(
            source_id=source_id,
            kind="case",
            title=title,
            locator="案例原文",
            version_label="本地案例库版本",
            text=text,
            source_url=url,
        )
