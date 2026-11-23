"""
本地海事论文语料探测脚本（非舟序宿主 / 本地 CLI 环境可选加速器）

仅当当前环境未注入舟序内置检索工具（information_card_search / capability_card_select）
且本地存在语料目录（信息卡 / 能力卡 / md）时按需使用。
在舟序生产环境中，仍应严格使用舟序提供的只读工具。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _cards_file(root: Path) -> Path | None:
    for rel in (
        Path("information_cards") / "information_cards.jsonl",
        Path("信息卡") / "information_cards.jsonl",
        Path("information_cards.jsonl"),
    ):
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def _capability_file(root: Path) -> Path | None:
    for rel in (
        Path("capability_cards") / "capability_cards.jsonl",
        Path("能力卡") / "capability_cards.jsonl",
        Path("capability_cards.jsonl"),
    ):
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def _is_valid_corpus_root(resolved: Path) -> bool:
    return (
        _cards_file(resolved) is not None
        or (resolved / "papers" / "md").is_dir()
        or (resolved / "md").is_dir()
    )


def resolve_corpus_root(explicit: str | None = None) -> Path | None:
    if explicit:
        resolved = Path(explicit).resolve()
        return resolved if _is_valid_corpus_root(resolved) else None
    candidates = []
    for env_name in ("CSL_CORPUS_DIR", "ZHOUXU_PAPER_ORIGINALS_DIR", "ZHOUXU_INFORMATION_CARDS_DIR"):
        env_dir = os.environ.get(env_name, "").strip()
        if env_dir:
            p = Path(env_dir)
            candidates.extend([p, p.parent])
    # 仓库内标准目录：<repo>/data/research；容器挂载目录：/app/runtime-resources
    candidates.extend([
        Path("/app/runtime-resources"),
        Path("/app/data/research"),
        SKILL_DIR.parents[2] / "data" / "research",
        Path.cwd() / "data" / "research",
        Path.cwd(),
    ])
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if _is_valid_corpus_root(resolved):
            return resolved
    return None


def _score_terms(text: str, terms: list[str]) -> int:
    lower = text.casefold()
    score = 0
    for term in terms:
        t = term.casefold()
        if t in lower:
            score += 3 if len(t) >= 3 else 1
            score += min(lower.count(t), 3)
    return score


def search_cards(corpus_root: Path, query: str, year: str | None, limit: int) -> list[dict]:
    cards_path = _cards_file(corpus_root)
    if cards_path is None:
        return []
    terms = [t.strip() for t in re.split(r"[\s,，;；]+", query) if len(t.strip()) >= 2]
    if not terms:
        terms = [query.strip()]
    scored = []
    with cards_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if year and str(row.get("time_scope", "")) != year:
                continue
            src = row.get("source") or {}
            haystack = f"{src.get('title', '')}\n{row.get('retrieval_text', '')}\n{row.get('claim_or_fact', '')}"
            s = _score_terms(haystack, terms)
            if s <= 0:
                continue
            loc = src.get("locator") or {}
            scored.append((
                s,
                str(row.get("time_scope", "")),
                {
                    "card_id": row.get("card_id"),
                    "title": src.get("title"),
                    "authors_hint": src.get("authors_hint") or "",
                    "time_scope": row.get("time_scope"),
                    "source_uri": src.get("source_uri") or "",
                    "md_path": src.get("path") or "",
                    "body_path": src.get("body_path") or "",
                    "heading": loc.get("heading") or "",
                    "pdf_page": loc.get("page"),
                    "excerpt": (row.get("claim_or_fact") or row.get("retrieval_text") or "")[:450],
                },
            ))
    scored.sort(key=lambda x: (-x[0], x[1]), reverse=False)
    return [item[2] for item in scored[:limit]]


def read_article(corpus_root: Path, rel_path: str, start: int, chars: int) -> dict:
    clean = rel_path.lstrip("/\\")
    candidates = [
        (corpus_root / clean).resolve(),
        (corpus_root / "papers" / clean).resolve(),
    ]
    orig_dir = os.environ.get("ZHOUXU_PAPER_ORIGINALS_DIR", "").strip()
    if orig_dir:
        candidates.append((Path(orig_dir) / clean).resolve())
    allowed_roots = [corpus_root]
    if orig_dir:
        allowed_roots.append(Path(orig_dir).resolve())
    target = None
    for cand in candidates:
        if any(root == cand or root in cand.parents for root in allowed_roots) and cand.is_file():
            target = cand
            break
    if target is None:
        if not any(root == candidates[0] or root in candidates[0].parents for root in allowed_roots):
            raise ValueError("path_outside_corpus_root")
        raise FileNotFoundError(rel_path)
    text = target.read_text(encoding="utf-8", errors="replace")
    # 尝试从同级 .md 或自身提取文章编号以确认真实连续刊页起点
    probe_text = text[:3000]
    if target.name == "body.txt":
        md_siblings = [p for p in target.parent.glob("*.md")]
        if md_siblings:
            probe_text = md_siblings[0].read_text(encoding="utf-8", errors="replace")[:3000]
    article_id_match = re.search(
        r"2096-028X\s*[\(（]?\s*(\d{4})\s*[\)）]?\s*(\d{2})-(\d{4})-(\d{2})",
        probe_text,
    )
    journal_page_hint = None
    if article_id_match:
        journal_page_hint = {
            "year": article_id_match.group(1),
            "issue": int(article_id_match.group(2)),
            "start_page": int(article_id_match.group(3)),
            "page_count": int(article_id_match.group(4)),
        }
    end = min(len(text), start + chars)
    return {
        "path": rel_path,
        "total_chars": len(text),
        "start": start,
        "end": end,
        "next_start": end if end < len(text) else None,
        "journal_page_hint": journal_page_hint,
        "text": text[start:end],
    }


def search_capabilities(corpus_root: Path, query: str, limit: int) -> list[dict]:
    cap_path = _capability_file(corpus_root)
    if cap_path is None:
        return []
    terms = [t.strip() for t in re.split(r"[\s,，;；]+", query) if len(t.strip()) >= 2] or [query.strip()]
    scored = []
    with cap_path.open("r", encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            hay = f"{row.get('name', '')}\n{row.get('trigger', '')}\n{' '.join(row.get('steps') or row.get('procedure') or [])}"
            s = _score_terms(hay, terms)
            if s > 0:
                scored.append((s, {
                    "candidate_id": row.get("candidate_id") or row.get("card_id"),
                    "name": row.get("name"),
                    "trigger": row.get("trigger"),
                    "steps": row.get("steps") or row.get("procedure") or [],
                    "evidence_requirements": row.get("evidence_requirements") or [],
                    "stop_conditions": row.get("stop_conditions") or row.get("failure_and_rollback") or [],
                }))
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:limit]]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Probe local maritime law research corpus")
    parser.add_argument("--corpus-root", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sc = sub.add_parser("search-cards")
    p_sc.add_argument("--query", required=True)
    p_sc.add_argument("--year", default=None)
    p_sc.add_argument("--limit", type=int, default=8)

    p_rm = sub.add_parser("read-md")
    p_rm.add_argument("--path", required=True)
    p_rm.add_argument("--start", type=int, default=0)
    p_rm.add_argument("--chars", type=int, default=6000)

    p_cap = sub.add_parser("search-capabilities")
    p_cap.add_argument("--query", required=True)
    p_cap.add_argument("--limit", type=int, default=3)

    args = parser.parse_args()
    root = resolve_corpus_root(args.corpus_root)
    if root is None:
        print(json.dumps({"status": "unavailable", "message": "未探测到本地语料目录（data/research 或 /app/runtime-resources）。请改用当前运行时检索工具或联网核验。"}, ensure_ascii=False))
        return 1

    try:
        if args.cmd == "search-cards":
            items = search_cards(root, args.query, args.year, args.limit)
            print(json.dumps({"status": "ok" if items else "empty", "corpus_root": str(root), "items": items}, ensure_ascii=False, indent=2))
        elif args.cmd == "read-md":
            res = read_article(root, args.path, args.start, args.chars)
            print(json.dumps({"status": "ok", **res}, ensure_ascii=False, indent=2))
        elif args.cmd == "search-capabilities":
            items = search_capabilities(root, args.query, args.limit)
            print(json.dumps({"status": "ok" if items else "empty", "items": items}, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
