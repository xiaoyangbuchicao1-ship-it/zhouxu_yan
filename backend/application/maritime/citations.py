"""Check internal source links against immutable retrieved text, never legal truth."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from backend.contracts.modules.loop import ReflectionResult
from backend.contracts.schemas.core import Error, ModuleResponse
from backend.modules.reflection.service import RuleReflection

SOURCE_LINK = re.compile(r'(?:https?://|/)[^\s<>\)\]\x60"，。；]+')
MARKER = re.compile(r"\[\[(ZX:[0-9a-f]{16})\]\]")
INTERNAL_MARKER = re.compile(r"\[\[(ZX:[^\]\r\n]{0,100})\]\]")
MARKER_PREFIX = "[[ZX:"


def expand_citations(text, corpus):
    def resolve(match):
        try:
            return f"[核对原文]({corpus.citation(match[1])})"
        except LookupError:
            return match[0]

    return MARKER.sub(resolve, text or "")


def public_citations(text, corpus):
    """Project citation protocol text into safe user-facing Markdown.

    A model can emit a citation marker before the reflection pass has checked
    it. Valid markers become the normal source link; unresolved or malformed
    internal markers are removed so provider/storage protocols never reach the
    browser. The validator still receives the original expanded text and can
    reject an unresolved citation.
    """

    def resolve(match):
        try:
            token = match.group(1)
            return f"[核对原文]({corpus.citation(token)})"
        except LookupError:
            return ""

    return INTERNAL_MARKER.sub(resolve, text or "")


def strip_internal_markers(text):
    """Remove legacy protocol markers from already-persisted public text."""

    return INTERNAL_MARKER.sub("", text or "")


def _compact_public_text(value):
    """Normalize human text for a conservative source-name comparison."""

    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _source_title_is_named(title, answer_compact):
    """Recognise a source when the answer uses its usual shortened name.

    The corpus stores formal titles (for example ``中华人民共和国海商法``),
    while a model will normally write ``海商法``.  Requiring the complete
    formal title made the evidence projection silently disappear even though
    the model had used the retrieved rule.  We keep the gate conservative:
    an exact title still wins, and shortened aliases must be at least three
    characters long and come from the title itself.
    """

    compact = _compact_public_text(title)
    if not compact or compact in answer_compact:
        return bool(compact)
    # Chinese titles have no word boundary.  Use short suffixes and the
    # meaningful runs separated by punctuation/parentheses as human aliases;
    # generic two-character fragments are deliberately excluded.
    aliases = set()
    for size in range(3, min(8, len(compact) + 1)):
        aliases.add(compact[-size:])
    for part in re.findall(r"[0-9A-Za-z\u4e00-\u9fff]{3,}", str(title or "")):
        normalized = _compact_public_text(part)
        if len(normalized) >= 3:
            aliases.add(normalized)
    return any(alias in answer_compact for alias in aliases)


def _retrieved_source_entries(answer, tool_results, corpus, *, include_existing=False):
    """Return real citations named by an answer but omitted by the model.

    Search receipts are private run state.  A provider may correctly use the
    returned material while forgetting to copy the short ``[[ZX:...]]`` token.
    We only project a receipt when its human-readable title is present in the
    answer and its excerpt is non-empty.  This prevents an unrelated search
    hit from becoming a misleading "回答依据" card.
    """

    answer_compact = _compact_public_text(answer)
    if not answer_compact:
        return []
    links = []
    seen = set()
    for receipt in tool_results or []:
        if not isinstance(receipt, dict) or receipt.get("tool_name") != "search_maritime_corpus":
            continue
        if receipt.get("outcome", "ok") not in {"ok", "partial"}:
            continue
        result = receipt.get("result")
        if not isinstance(result, dict):
            continue
        for item in result.get("items", []):
            if not isinstance(item, dict) or not item.get("text"):
                continue
            title = str(item.get("title") or "").strip()
            if not _source_title_is_named(title, answer_compact):
                continue
            citation = item.get("citation")
            if not isinstance(citation, str) or not MARKER.fullmatch(citation):
                continue
            try:
                href = corpus.citation(citation[2:-2])
            except LookupError:
                continue
            if href in seen or (not include_existing and href in (answer or "")):
                continue
            seen.add(href)
            links.append(
                {
                    "href": href,
                    "kind": item.get("kind") if item.get("kind") in {"laws", "cases"} else "laws",
                    "title": title,
                    "locator": item.get("locator"),
                    "citation": citation,
                }
            )
            if len(links) >= 4:
                return links
    return links


def _retrieved_source_links(answer, tool_results, corpus):
    return [
        (entry["title"], entry.get("locator"), entry["citation"])
        for entry in _retrieved_source_entries(answer, tool_results, corpus)
    ]


def public_source_refs(answer, tool_results, corpus):
    """Return card metadata without exposing internal corpus identities."""

    return [
        {
            "href": entry["href"],
            "kind": entry["kind"],
            "title": entry["title"],
            "locator": entry.get("locator"),
        }
        for entry in _retrieved_source_entries(
            answer, tool_results, corpus, include_existing=True
        )
    ]


def ensure_retrieved_citations(answer, tool_results, corpus):
    """Add a small public source list for named, real retrieval receipts.

    The added markers are expanded by :func:`expand_citations` immediately
    afterwards, so the browser receives the same immutable snapshot URL as a
    model-authored citation.  Nothing is fabricated when the answer does not
    name a retrieved source.
    """

    links = _retrieved_source_links(answer, tool_results, corpus)
    if not links:
        return answer
    # A model may already have created a source section.  Append the missing
    # real links to that section instead of creating a second visible heading.
    heading = re.compile(r"(?m)^\s*#{2,4}\s*回答依据\s*$")
    lines = [""] if heading.search(answer or "") else ["", "### 回答依据"]
    for title, locator, citation in links:
        location = f"{locator}" if locator else ""
        lines.append(f"- 《{title}》{location} [[{citation[2:-2]}]]")
    return (answer or "").rstrip() + "\n" + "\n".join(lines)


class CitationStream:
    """Convert citation markers without leaking split SSE chunks.

    Provider streams may split ``[[ZX:...]]`` at any character boundary. The
    small pending buffer holds only the possible marker until it is complete;
    ordinary answer text continues to stream immediately. Invalid markers are
    discarded rather than shown as implementation syntax.
    """

    def __init__(self, corpus):
        self.corpus = corpus
        self.pending = ""

    def _keep_partial_prefix(self) -> int:
        for start in range(max(0, len(self.pending) - len(MARKER_PREFIX) + 1), len(self.pending) + 1):
            if MARKER_PREFIX.startswith(self.pending[start:]):
                return start
        return len(self.pending)

    def push(self, text: str = "", *, final: bool = False) -> str:
        self.pending += text or ""
        output: list[str] = []
        while self.pending:
            start = self.pending.find(MARKER_PREFIX)
            if start < 0:
                if final:
                    output.append(self.pending)
                    self.pending = ""
                else:
                    keep = self._keep_partial_prefix()
                    output.append(self.pending[:keep])
                    self.pending = self.pending[keep:]
                break
            if start:
                output.append(self.pending[:start])
                self.pending = self.pending[start:]
            end = self.pending.find("]]", len(MARKER_PREFIX))
            if end < 0:
                if final:
                    # An unfinished protocol marker is never user content.
                    self.pending = ""
                break
            marker = self.pending[: end + 2]
            self.pending = self.pending[end + 2 :]
            match = MARKER.fullmatch(marker)
            if not match:
                continue
            try:
                output.append(f"[核对原文]({self.corpus.citation(match.group(1))})")
            except LookupError:
                # Keep the answer readable while the reflection layer reports
                # the unresolved citation and prevents a false completion.
                continue
        return "".join(output)


def invalid_citations(text, corpus):
    invalid = []
    for link in SOURCE_LINK.findall(text or ""):
        url = urlsplit(link)
        path = unquote(url.path)
        if path.startswith("/maritime/sources/"):
            path = "/api" + path
        if not path.startswith("/api/maritime/sources/"):
            if url.hostname and (
                url.hostname.endswith(".invalid") or url.hostname == "api.maritime"
            ):
                invalid.append(link)
            continue
        parts = path.split("/", 5)
        snapshot = parse_qs(url.query).get("snapshot", [""])[0]
        try:
            if (
                len(parts) != 6
                or parts[4] not in {"laws", "cases"}
                or not re.fullmatch("[0-9a-f]{64}", snapshot)
            ):
                raise LookupError("invalid_source")
            corpus.source(parts[4], parts[5], snapshot)
        except (LookupError, ValueError):
            invalid.append(link)
    invalid.extend(match[0] for match in MARKER.finditer(text or ""))
    return invalid


class MaritimeReflection(RuleReflection):
    def __init__(self, settings, corpus):
        super().__init__(settings)
        self.corpus = corpus

    def validate_final(self, text):
        if invalid_citations(text, self.corpus):
            return Error(
                code="citation_unresolved",
                message="正文包含无法核验的来源链接，本次未通过引用校验。已保留草稿，请重做并核对真实原文。",
                retryable=True,
            )
        return None

    def transform(self, text):
        return expand_citations(text, self.corpus)

    def public_transform(self, text):
        return public_citations(text, self.corpus)

    def evidence_transform(self, text, tool_results):
        return ensure_retrieved_citations(text, tool_results, self.corpus)

    def source_refs(self, text, tool_results):
        return public_source_refs(text, tool_results, self.corpus)

    def stream_transform(self):
        return CitationStream(self.corpus)

    async def handle(self, request):
        proposed = request.input.proposed
        if proposed.kind == "answer":
            proposed.answer = self.transform(proposed.answer)
            invalid = invalid_citations(proposed.answer, self.corpus)
            if invalid:
                return ModuleResponse(
                    status="ok",
                    output=ReflectionResult(
                        decision="retry",
                        issues=["internal_citation_unresolved"],
                        feedback="引用未通过真实快照校验。请逐字复制 search_maritime_corpus 回执中的 citation 标记，系统自动转换为原文链接。不要编写网址；无法核验则撤回该引用及依赖它的确定性结论，并明确资料缺口。无效引用："
                        + ", ".join(invalid[:5]),
                    ),
                )
        return await super().handle(request)
