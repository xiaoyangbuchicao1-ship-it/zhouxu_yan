"""Read-only corpus search with an explicit OpenSearch/local-text provenance boundary."""

import asyncio
import hashlib
import json
import re
import sqlite3
import ssl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INDEX = ROOT / "backend/.runtime/maritime-corpus.sqlite3"
CORPORA = {
    "laws": ROOT / "opensearch/corpus/manifest/chunks.jsonl",
    "cases": ROOT / "opensearch_1/海事案例库/maritime_case_rag/corpus/chunk.jsonl",
}


def corpus_root(kind):
    """Return the checked-in corpus root for a kind without trusting request data."""
    path = Path(CORPORA[kind]).resolve()
    return path.parents[2] if kind == "laws" else path.parents[1]


def safe_corpus_path(kind, relative):
    if not relative or not isinstance(relative, str):
        return None
    root = corpus_root(kind)
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def complete_law_text(item):
    path = safe_corpus_path("laws", item.get("source_path"))
    if path and path.is_file() and path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8-sig")
    document_id = item.get("document_id")
    if document_id:
        path = corpus_root("laws") / "corpus" / "sources" / f"{document_id}.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8-sig")
    return None


def complete_case_text(item):
    root = corpus_root("cases")
    case_id = item.get("case_id")
    cases_path = root / "corpus" / "cases.jsonl"
    if case_id and cases_path.is_file():
        with cases_path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("案例编号") != case_id:
                    continue
                body = record.get("案例全文与来源") or {}
                text = body.get("原文全文")
                if isinstance(text, str) and text.strip():
                    return text
                break
    path = safe_corpus_path("cases", item.get("source_path"))
    if path and path.is_file() and path.suffix.lower() == ".docx":
        try:
            from docx import Document

            return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
        except (ImportError, OSError, ValueError):
            return None
    return None


LAW_METADATA_RE = re.compile(
    r"^(?:条文ID|文献来源ID|文献名称|版本日期|效力状态|文献类型|原章节|原小节|条号|研究主题)[：:]"
)
INTERNAL_SOURCE_LINE_RE = re.compile(
    r"^(?:[-*•·]\s*)?(?:"
    r"库内标记(?:为)?现行(?:性)?|"
    r"库内历史版本|"
    r"库内未标记现行性|"
    r"正文来自舟序已整理法规语料；版本日期和库内标签不等于联网核验效力"
    r")(?:。)?$"
)
INTERNAL_LOCATOR_SUFFIX_RE = re.compile(
    r"\s*[·•]\s*库内(?:标记(?:为)?现行(?:性)?|历史版本|未标记现行性)(?:。)?"
)
INTERNAL_CHUNK_LOCATOR_RE = re.compile(
    r"^(?:原文\s*)?(?:第\s*\d+\s*个\s*)?(?:检索)?片段(?:\s*[:：]\s*\d+)?$"
)


def user_locator(locator):
    """Keep human-readable article/section location only."""
    if isinstance(locator, list):
        values = [user_locator(value) for value in locator]
        return [value for value in values if value]
    if not isinstance(locator, str):
        return locator
    cleaned = INTERNAL_LOCATOR_SUFFIX_RE.sub("", locator).strip(" ·•")
    # Case search locators describe the storage chunk, not a useful legal
    # position. The complete source is opened from the card instead.
    if INTERNAL_CHUNK_LOCATOR_RE.fullmatch(cleaned):
        return None
    return cleaned or None


def user_source_text(kind, text):
    """Remove corpus bookkeeping headers before text reaches a user or model."""
    if not isinstance(text, str):
        return text
    lines = text.splitlines()
    start = 0
    if kind == "laws":
        metadata_count = sum(
            1 for line in lines[:20] if LAW_METADATA_RE.match(line.strip())
        )
        if metadata_count >= 2:
            while start < len(lines) and (
                not lines[start].strip() or LAW_METADATA_RE.match(lines[start].strip())
            ):
                start += 1
    visible = [
        line
        for line in lines[start:]
        if not INTERNAL_SOURCE_LINE_RE.match(line.strip())
    ]
    return "\n".join(visible).strip()


def complete_source(item):
    kind = item.get("kind")
    original = item.get("text", "")
    excerpt = user_source_text(kind, item.get("excerpt") or original)
    text = complete_law_text(item) if kind == "laws" else complete_case_text(item)
    if not text:
        cleaned_original = user_source_text(kind, original)
        return {
            **item,
            "text": cleaned_original,
            "excerpt": excerpt,
            "locator": user_locator(item.get("locator")),
        }
    cleaned = user_source_text(kind, text)
    result = {
        **item,
        "text": cleaned,
        "excerpt": excerpt,
        "locator": user_locator(item.get("locator")),
    }
    if len(text) > len(original):
        result["document_complete"] = True
    return result


_ARTICLE_NUMBER_RE = re.compile(
    r"第\s*(?:(\d{1,3})|([零〇一二两三四五六七八九十百千]+))\s*条"
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_article_number(value):
    """Convert a small Chinese integer used in an article locator to digits."""

    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if "百" in value:
        left, _, right = value.partition("百")
        hundreds = _CHINESE_DIGITS.get(left, 1) if left else 1
        return hundreds * 100 + (_chinese_article_number(right) or 0)
    if "千" in value:
        left, _, right = value.partition("千")
        thousands = _CHINESE_DIGITS.get(left, 1) if left else 1
        return thousands * 1000 + (_chinese_article_number(right) or 0)
    if len(value) == 1:
        return _CHINESE_DIGITS.get(value)
    return None


def _article_locator_hints(query):
    """Return both Arabic and Chinese forms for an explicit ``第 N 条`` query."""

    hints = []
    for match in _ARTICLE_NUMBER_RE.finditer(query or ""):
        number = _chinese_article_number(match.group(1) or match.group(2))
        if number is None or number > 9999:
            continue
        hints.append(f"第{number}条")
        if number < 1000:
            if number < 10:
                chinese = "零一二三四五六七八九"[number]
            elif number < 20:
                chinese = "十" + ("一二三四五六七八九"[number - 10] if number > 10 else "")
            else:
                digits = "零一二三四五六七八九"
                tens, ones = divmod(number, 10)
                chinese = "一二三四五六七八九"[tens - 1] + "十"
                if ones:
                    chinese += digits[ones]
            hints.append(f"第{chinese}条")
    return list(dict.fromkeys(hints))


@contextmanager
def connection(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def normalize(kind, row):
    if kind == "laws":
        return dict(
            id=row["chunk_id"],
            kind=kind,
            title=row["document_name"],
            text=row["chunk_text"],
            locator=row.get("article_no") or row.get("section_path"),
            current=row.get("is_current"),
            version=row.get("version_date"),
            legal_status=row.get("legal_status"),
            document_id=row.get("document_id"),
            source_path=row.get("canonical_text_path"),
            version_relation=row.get("version_relation"),
            version_target_document_id=row.get("version_target_document_id"),
        )
    if "chunk_id" in row:
        return dict(
            id=row["chunk_id"],
            kind=kind,
            title=row["title"],
            text=row["text"],
            locator=f"原文位置 {row['start']}–{row['end']}"
            if row.get("start") is not None and row.get("end") is not None
            else None,
            case_id=row["case_id"],
            snapshot_id=row.get("snapshot_id"),
            source_path=row.get("source_path"),
            document_id=row.get("document_id"),
            current=None,
            version=None,
        )
    return dict(
        id=row["片段编号"],
        kind=kind,
        title=row["案例标题"],
        text=row["文本"],
        locator=row.get("原文位置"),
        case_id=row["案例编号"],
        source_path=row.get("原文相对路径"),
        document_id=None,
        category=row.get("案件类型"),
        section=row.get("片段类型"),
        current=None,
        version=None,
    )


def build_index(path=DEFAULT_INDEX, corpora=None):
    """Build only derived local data, atomically; never modify either source corpus."""
    corpora = corpora or CORPORA
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    with connection(path, timeout=60) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS metadata(kind TEXT PRIMARY KEY,signature TEXT,count INTEGER)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT,title TEXT,content TEXT,is_current INTEGER,metadata TEXT)"
        )
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(id UNINDEXED,title,content,tokenize='trigram')"
        )
        for kind, source in corpora.items():
            source = Path(source)
            stat = source.stat()
            signature = f"v1:{stat.st_size}:{stat.st_mtime_ns}"
            previous = db.execute(
                "SELECT signature,count FROM metadata WHERE kind=?", (kind,)
            ).fetchone()
            if previous and previous[0] == signature:
                counts[kind] = previous[1]
                continue
            db.execute(
                "DELETE FROM source_fts WHERE id IN (SELECT id FROM sources WHERE kind=?)",
                (kind,),
            )
            db.execute("DELETE FROM sources WHERE kind=?", (kind,))
            count = 0
            with source.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    value = normalize(kind, json.loads(line))
                    db.execute(
                        "INSERT INTO sources VALUES (?,?,?,?,?,?)",
                        (
                            value["id"],
                            kind,
                            value["title"],
                            value["text"],
                            value["current"],
                            json.dumps(value, ensure_ascii=False),
                        ),
                    )
                    db.execute(
                        "INSERT INTO source_fts VALUES (?,?,?)",
                        (value["id"], value["title"], value["text"]),
                    )
                    count += 1
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?,?)",
                (kind, signature, count),
            )
            counts[kind] = count
    return counts


class MaritimeCorpus:
    def __init__(self, path=DEFAULT_INDEX):
        self.path = Path(path).resolve()
        self.citation_path = self.path.with_name(self.path.stem + "-citations.sqlite3")

    def remember_source(self, item):
        payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        self.citation_path.parent.mkdir(parents=True, exist_ok=True)
        with connection(self.citation_path, timeout=15) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS citations(snapshot TEXT PRIMARY KEY, kind TEXT, source_id TEXT, payload TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "INSERT OR IGNORE INTO citations(snapshot,kind,source_id,payload) VALUES (?,?,?,?)",
                (key, item["kind"], item["id"], payload),
            )
        return key

    def citation(self, token):
        if (
            not re.fullmatch(r"ZX:[0-9a-f]{16}", token)
            or not self.citation_path.is_file()
        ):
            raise LookupError("citation_not_found")
        with connection(f"{self.citation_path.as_uri()}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT snapshot,payload FROM citations WHERE snapshot LIKE ? LIMIT 2",
                (token[3:] + "%",),
            ).fetchall()
        if len(rows) != 1:
            raise LookupError("citation_not_unique")
        snapshot, payload = rows[0]
        item = json.loads(payload)
        return f"/api/maritime/sources/{item['kind']}/{quote(item['id'], safe='')}?snapshot={snapshot}"

    def status(self):
        if not self.path.is_file():
            return {"indexed": False, "counts": {}, "notice": "本地语料索引尚未构建"}
        with connection(f"{self.path.as_uri()}?mode=ro", uri=True) as db:
            counts = dict(db.execute("SELECT kind,count FROM metadata"))
        return {
            "indexed": True,
            "counts": counts,
        }

    def source(self, kind, source_id, snapshot=None):
        if self.citation_path.is_file():
            with connection(f"{self.citation_path.as_uri()}?mode=ro", uri=True) as db:
                if snapshot:
                    cached = db.execute(
                        "SELECT payload FROM citations WHERE snapshot=? AND kind=? AND source_id=?",
                        (snapshot, kind, source_id),
                    ).fetchone()
                else:
                    cached = db.execute(
                        "SELECT payload FROM citations WHERE kind=? AND source_id=? ORDER BY rowid DESC LIMIT 1",
                        (kind, source_id),
                    ).fetchone()
            if cached:
                return complete_source(json.loads(cached[0]))
        if snapshot:
            raise LookupError("citation_snapshot_not_found")
        if not self.path.is_file():
            raise LookupError("corpus_index_unavailable")
        with connection(f"{self.path.as_uri()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT metadata FROM sources WHERE kind=? AND id=?", (kind, source_id)
            ).fetchone()
        if not row:
            raise LookupError("source_not_found")
        return complete_source(json.loads(row[0]))

    def local_search(self, query, kind, current_only=True, limit=8):
        if not self.path.is_file():
            raise LookupError("corpus_index_unavailable")
        clauses, params = ["s.kind=?"], [kind]
        if kind == "laws" and current_only:
            clauses.append("s.is_current=1")
        article_hints = _article_locator_hints(query) if kind == "laws" else []
        document_hint = "海商法" if kind == "laws" and "海商法" in (query or "") else None
        # Query grammar is built from plain terms, never passed through from a caller.
        terms = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)[:12]
        tokens = [t[:40] for t in terms if len(t) >= 3]
        with connection(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=10) as db:
            # A precise article request must win over broad FTS matches such as
            # another document's "第22条".  The index stores the human article
            # locator in metadata, so this remains a real corpus lookup and
            # does not infer or manufacture a citation.
            if article_hints:
                target_clauses = []
                target_params = list(params)
                if document_hint:
                    target_clauses.append("s.title LIKE ? ESCAPE '\\'")
                    target_params.append(f"%{document_hint}%")
                article_matches = []
                for hint in article_hints:
                    article_matches.append("(s.metadata LIKE ? ESCAPE '\\' OR s.content LIKE ? ESCAPE '\\')")
                    target_params.extend([f"%{hint}%", f"%{hint}%"])
                if article_matches:
                    target_clauses.append("(" + " OR ".join(article_matches) + ")")
                if target_clauses:
                    exact_rows = db.execute(
                        "SELECT s.metadata FROM sources s WHERE "
                        + " AND ".join(clauses + target_clauses)
                        + " ORDER BY CASE WHEN s.metadata LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END LIMIT ?",
                        [
                            *target_params,
                            f'%"locator": "{article_hints[-1]}"%',
                            limit,
                        ],
                    ).fetchall()
                    if exact_rows:
                        return [json.loads(row[0]) for row in exact_rows]
            if tokens:
                match = " OR ".join(
                    '"' + token.replace('"', '""') + '"' for token in tokens
                )
                sql = (
                    "SELECT s.metadata FROM source_fts JOIN sources s ON s.id=source_fts.id WHERE source_fts MATCH ? AND "
                    + " AND ".join(clauses)
                    + " ORDER BY bm25(source_fts,0,4,1) LIMIT ?"
                )
                rows = db.execute(sql, [match, *params, limit]).fetchall()
            else:
                sql = (
                    "SELECT s.metadata FROM sources s WHERE "
                    + " AND ".join(clauses)
                    + ' AND (s.title LIKE ? ESCAPE "\\" OR s.content LIKE ? ESCAPE "\\") LIMIT ?'
                )
                escaped = (
                    query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                rows = db.execute(
                    sql, [*params, f"%{escaped}%", f"%{escaped}%", limit]
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def search(self, query, kind="laws", current_only=True, limit=8):
        if (
            kind not in CORPORA
            or not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 500
            or not 1 <= limit <= 10
        ):
            raise ValueError("请输入 2 至 500 字查询，并选择法规或案例库")
        query = query.strip()
        precise_items = []
        if kind == "laws" and _article_locator_hints(query):
            try:
                precise_items = await asyncio.to_thread(
                    self.local_search, query, kind, current_only, limit
                )
            except (LookupError, OSError, sqlite3.Error):
                precise_items = []
        try:
            # Fixed loopback addresses only; caller content cannot choose a host or index.
            values = dotenv_values(
                ROOT / "opensearch/opensearch_delivery/dev/.env", interpolate=False
            )
            password = values.get("OPENSEARCH_INITIAL_ADMIN_PASSWORD")
            if not password:
                raise LookupError("search_credentials_unconfigured")
            context = ssl.create_default_context()
            context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
            endpoint = "https://127.0.0.1:19200"
            options = {"auth": ("admin", password), "verify": context}
            if kind == "laws":
                alias = "maritime_law_chunks_read"
                fields = ["document_name^4", "chunk_text"]
                filters = [{"term": {"is_current": True}}] if current_only else []
            else:
                alias = "maritime_cases_read"
                fields, filters = ["title^4", "text"], []
            async with httpx.AsyncClient(
                timeout=4, trust_env=False, follow_redirects=False, **options
            ) as client:
                result = await client.post(
                    f"{endpoint}/{alias}/_search",
                    json={
                        "size": limit,
                        "_source": {"excludes": ["embedding", "vector", "embed_text"]},
                        "query": {
                            "bool": {
                                "must": [
                                    {"multi_match": {"query": query, "fields": fields}}
                                ],
                                "filter": filters,
                            }
                        },
                    },
                )
                result.raise_for_status()
                data = result.json()
                if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
                    raise ValueError("incomplete_search")
                items = [
                    normalize(kind, hit["_source"]) for hit in data["hits"]["hits"]
                ]
                engine = "opensearch-bm25"
        except (httpx.HTTPError, KeyError, ValueError, LookupError, OSError):
            items = await asyncio.to_thread(
                self.local_search, query, kind, current_only, limit
            )
            engine = "local-corpus-fulltext"
        if precise_items:
            seen = {item.get("id") for item in precise_items}
            items = precise_items + [item for item in items if item.get("id") not in seen]
            items = items[:limit]
        for index, item in enumerate(items):
            item = complete_source(item)
            snapshot = await asyncio.to_thread(self.remember_source, item)
            item["citation"] = f"[[ZX:{snapshot[:16]}]]"
            item["source_url"] = (
                f"/api/maritime/sources/{kind}/{quote(item['id'], safe='')}?snapshot={snapshot}"
            )
            items[index] = item
        return {
            "query": query,
            "kind": kind,
            "engine": engine,
            "items": items,
        }


if __name__ == "__main__":
    print(json.dumps(build_index(), ensure_ascii=False))
