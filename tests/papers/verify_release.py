"""Full-release acceptance: no retired cards, no invented PDF/character locations."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(release: Path, originals: Path):
    papers = {row["document_id"]: row for row in map(json.loads, (release / "papers.jsonl").read_text(encoding="utf-8").splitlines())}
    body_cache, files, count, legacy = {}, set(), 0, 0
    for raw in (release / "cards.jsonl").open(encoding="utf-8"):
        card = json.loads(raw)
        count += 1
        legacy += card["card_id"].startswith("info-atom-")
        paper = papers[card["source_document_id"]]
        locator = card["source_locator"]
        assert locator["page_kind"] == "physical"
        assert 1 <= locator["page"] <= locator["page_end"] <= paper["page_count"]
        assert locator["char_offset_basis"] == paper["text_path"]
        if paper["document_id"] not in body_cache:
            body_cache[paper["document_id"]] = (originals / paper["text_path"]).read_text(encoding="utf-8")
        body = body_cache[paper["document_id"]]
        assert body[locator["char_start"]:locator["char_end"]] == card["claim_or_fact"], card["card_id"]
        assert card["source_sha256"] == paper["source_hash"]
        files.add(paper["document_id"])
    assert count and legacy == 0, f"retired_cards_still_present:{legacy}"
    for document_id in papers:
        paper = papers[document_id]
        for name, expected in ((paper["formal_pdf_path"], paper["pdf_sha256"]), (paper["source_path"], paper["source_hash"]), (paper["text_path"], paper["text_sha256"])):
            assert hashlib.sha256((originals / name).read_bytes()).hexdigest() == expected, name
    return {"cards": count, "represented_papers": len(files), "catalog_papers": len(papers), "retired_cards": legacy, "positions_and_files_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--originals", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.release, args.originals)))
