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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config.settings import BACKEND_ROOT, Settings, resolve_case_snapshot_dir
from backend.contracts.modules.sources import (
    PaperOriginal,
    SourceDocument,
    SourceReference,
)

_URL = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
_SOURCE_ID = re.compile(r"(?:law|case)-[a-f0-9]{24}|paper-[A-Za-z0-9._-]+")
_PAPER_MARKUP = re.compile(r"<[^>]*>")


@dataclass(frozen=True)
class PaperFile:
    path: Path
    filename: str
    sha256: str


def _paper_public_text(value: Any) -> str:
    """Remove source-export markup before paper metadata reaches the UI."""

    return _PAPER_MARKUP.sub("", str(value)).replace(r"\*", "*").strip()


def _source_id(kind: str, *parts: Any) -> str:
    value = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True)
    return f"{kind}-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def law_source_id(document_name: str, article_no: str, version_date: str | None) -> str:
    return _source_id("law", document_name, article_no, version_date or "")


def case_source_id(case_id: str, title: str, snapshot_id: str | None) -> str:
    return _source_id("case", case_id, title, snapshot_id or "")


def paper_source_id(card_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", card_id):
        raise ValueError("invalid_information_card_id")
    return f"paper-{card_id}"


def _version_label(is_current: Any) -> str | None:
    if is_current is True:
        return "现行版本"
    if is_current is False:
        return "历史版本"
    return None


def _paper_reference(item: dict[str, Any]) -> dict[str, Any] | None:
    card_id = str(item.get("card_id") or "").strip()
    title = _paper_public_text(item.get("source_title") or item.get("title") or "")
    if not card_id or not title:
        return None
    locator = item.get("source_locator")
    if not isinstance(locator, dict):
        locator = {}
    page = locator.get("page")
    page_label = f"第 {page} 页" if type(page) is int and page >= 1 else None
    heading = _paper_public_text(locator.get("heading") or "") or None
    authors = _paper_public_text(item.get("source_authors") or "") or None
    collection = _paper_public_text(item.get("source_collection") or "") or None
    time_scope = _paper_public_text(item.get("time_scope") or item.get("source_time_scope") or "") or None
    return SourceReference(
        source_id=paper_source_id(card_id),
        kind="paper",
        title=title,
        locator=page_label,
        section=heading,
        version_date=time_scope,
        version_label=authors or collection,
        authors=authors,
        collection=collection,
    ).model_dump(mode="json", exclude_none=True)


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
        elif tool_name == "information_card_search":
            reference = _paper_reference(item)
            if reference:
                refs.append(reference)
        elif tool_name == "information_card_read":
            card = item.get("card") if isinstance(item.get("card"), dict) else item
            reference = _paper_reference(card)
            if reference:
                refs.append(reference)
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
        self._paper_offsets: dict[str, int] | None = None
        self._paper_catalog: tuple[int, dict[str, dict[str, Any]]] | None = None
        self._paper_hashes: dict[tuple[str, int, int], str] = {}

    def get(self, source_id: str) -> SourceDocument | None:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            return None
        if source_id.startswith("law-"):
            return self._law(source_id)
        if source_id.startswith("case-"):
            return self._case(source_id)
        if source_id.startswith("paper-"):
            return self._paper(source_id)
        return None

    def _information_release_dir(self) -> Path:
        default_release = self.root.parent / "import" / "information_cards"
        if (default_release / "cards.jsonl").is_file():
            return default_release
        configured = (self.settings.research_cards.information_cards_dir or "").strip()
        if configured:
            candidate = Path(configured)
            resolved = candidate.resolve() if candidate.is_absolute() else (BACKEND_ROOT.parent / candidate).resolve()
            if (resolved / "cards.jsonl").is_file():
                return resolved
        return default_release

    def _load_paper_offsets(self) -> dict[str, int]:
        if self._paper_offsets is not None:
            return self._paper_offsets
        release = self._information_release_dir()
        cards = release / "cards.jsonl"
        index = release / "cards.offsets.json"
        if not cards.is_file() or not index.is_file():
            raise ValueError("paper_card_offset_index_missing")
        payload = json.loads(index.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("cards_size") != cards.stat().st_size
        ):
            raise ValueError("paper_card_offset_index_stale")
        offsets = payload.get("offsets")
        if not isinstance(offsets, dict) or any(
            not isinstance(card_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", card_id)
            or type(offset) is not int
            or offset < 0
            for card_id, offset in offsets.items()
        ):
            raise ValueError("paper_card_offset_index_invalid")
        self._paper_offsets = offsets
        return offsets

    def _load_paper(self, card_id: str) -> dict[str, Any] | None:
        offset = self._load_paper_offsets().get(card_id)
        if offset is None:
            return None
        path = self._information_release_dir() / "cards.jsonl"
        with path.open("rb") as stream:
            stream.seek(offset)
            row = json.loads(stream.readline())
        return row if isinstance(row, dict) else None

    def _papers(self) -> dict[str, dict[str, Any]]:
        path = self._information_release_dir() / "papers.jsonl"
        stamp = path.stat().st_mtime_ns
        if self._paper_catalog is None or self._paper_catalog[0] != stamp:
            papers = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["document_id"] in papers:
                    raise ValueError("paper_original_duplicate_identity")
                papers[row["document_id"]] = row
            self._paper_catalog = stamp, papers
        return self._paper_catalog[1]

    def _original_path(self, relative: str, expected_hash: str) -> Path:
        configured = self.settings.research_cards.originals_root
        if not configured:
            raise ValueError("paper_originals_not_configured")
        root = Path(configured).resolve()
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or ":" in relative or not path.is_relative_to(root):
            raise ValueError("paper_original_path_invalid")
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._paper_hashes:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
            self._paper_hashes[key] = digest.hexdigest()
        if self._paper_hashes[key] != expected_hash:
            raise ValueError("paper_original_hash_mismatch")
        return path

    def _paper(self, source_id: str) -> SourceDocument | None:
        card_id = source_id.removeprefix("paper-")
        if card_id.startswith("document-"):
            paper = self._papers().get(card_id.removeprefix("document-"))
            if paper is None:
                return None
            row = None
            reference = SourceReference(source_id=source_id, kind="paper",
                title=_paper_public_text(paper["title"]),
                authors=_paper_public_text(paper.get("authors_hint", "")) or None,
                collection=str(paper.get("collection", "")) or None).model_dump(mode="json", exclude_none=True)
        else:
            row = self._load_paper(card_id)
            if row is None or row.get("card_id") != card_id:
                return None
            reference = _paper_reference(row)
            if reference is None:
                raise ValueError("paper_reference_invalid")
            paper = self._papers()[row["source_document_id"]]
        self._original_path(paper["source_path"], paper["source_hash"])
        body = self._original_path(paper["text_path"], paper["text_sha256"]).read_text(encoding="utf-8")
        pdf = self._original_path(paper["formal_pdf_path"], paper["pdf_sha256"])
        page = page_end = None
        text = body
        if row is not None:
            locator = row["source_locator"]
            page, page_end = locator["page"], locator["page_end"]
            if not 1 <= page <= page_end <= paper["page_count"]:
                raise ValueError("paper_page_locator_invalid")
            text = body[locator["char_start"]:locator["char_end"]]
            if (text != row["claim_or_fact"] or locator["char_offset_basis"] != paper["text_path"]
                    or row["source_sha256"] != paper["source_hash"]):
                raise ValueError("paper_text_locator_mismatch")
        original = PaperOriginal(document_id=paper["document_id"], sha256=paper["pdf_sha256"],
            size_bytes=pdf.stat().st_size, page=page, page_end=page_end, page_count=paper["page_count"],
            content_url=f"/workspace/sources/{source_id}/original?sha256={paper['pdf_sha256']}")
        return SourceDocument(**reference, text=_paper_public_text(text), original=original)

    def paper_file(self, source_id: str, expected_hash: str) -> PaperFile | None:
        document = self.get(source_id)
        if document is None or document.original is None:
            return None
        if document.original.sha256 != expected_hash:
            raise ValueError("paper_version_mismatch")
        paper = self._papers()[document.original.document_id]
        path = self._original_path(paper["formal_pdf_path"], expected_hash)
        return PaperFile(path=path, filename=Path(paper["formal_pdf_path"]).name, sha256=expected_hash)

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
        path = resolve_case_snapshot_dir(self.settings)
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
