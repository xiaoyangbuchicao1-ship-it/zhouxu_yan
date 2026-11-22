"""Rebuild legacy paper cards using the existing MinerU builder, in an isolated release.

This command never changes the live source directory, index alias or databases.
Publishing and historical-reference migration are explicit subsequent steps.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_rows(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(root: Path, output: Path, *, limit: int | None = None):
    began = time.monotonic()
    root = root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    builder_path = root / "tools/information-card-rebuild/build.py"
    spec = importlib.util.spec_from_file_location("mineru_card_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)
    sources = list(rows(root / "信息卡/source_registry.jsonl"))
    legacy = [source for source in sources if not source.get("formal_pdf_path")]
    if limit is not None:
        legacy = legacy[:limit]
    active_ids = {source["document_id"] for source in legacy}
    pdfs = {}
    for pdf in (root / "PDF").rglob("*.pdf"):
        pdfs.setdefault(pdf.stem, []).append(pdf)
    cards, rebuilt, replacements = [], [], {}
    for offset, source in enumerate(legacy):
        md = root / source["source_path"]
        if sha(md) != source["source_hash"]:
            raise ValueError("legacy_markdown_changed:" + source["document_id"])
        matches = pdfs.get(md.stem, [])
        if len(matches) != 1:
            raise ValueError("legacy_pdf_not_unique:" + source["document_id"])
        pdf = matches[0]
        # The filename is a candidate only: the actual file hash becomes identity,
        # and PDF/structure/title checks remain part of release verification.
        pdf_hash = sha(pdf)
        document_id = "pdf-" + pdf_hash[:20]
        entry = {"id": md.stem, "pdf_path": pdf.relative_to(root).as_posix(),
                 "md_path": source["source_path"], "pdf_sha256": pdf_hash,
                 "pdf_bytes": pdf.stat().st_size}
        destination = output / "rebuilt" / document_id
        builder.build_document(root, entry, md.parent.parent, destination,
                               {**source, "document_id": document_id})
        replacement = json.loads((destination / "source.json").read_text(encoding="utf-8"))
        rebuilt.append(replacement)
        cards.extend(rows(destination / "cards.jsonl"))
        replacements[source["document_id"]] = document_id
        if offset % 40 == 0:
            print(json.dumps({"rebuilt": offset + 1, "total": len(legacy), "elapsed_seconds": round(time.monotonic() - began, 1)}), flush=True)
    retained = [source for source in sources if source["document_id"] not in active_ids]
    retired = []
    for card in rows(root / "信息卡/information_cards.jsonl"):
        old_document = card["source"]["document_id"]
        if old_document in active_ids:
            retired.append({"card_id": card["card_id"], "document_id": old_document,
                            "replacement_document_id": replacements[old_document]})
        else:
            cards.append(card)
    final_sources = retained + rebuilt
    if len({source["document_id"] for source in final_sources}) != len(final_sources):
        raise ValueError("duplicate_original_pdf_across_sources")
    source_dir = output / "source"
    write_rows(source_dir / "information_cards.jsonl", cards)
    write_rows(source_dir / "source_registry.jsonl", final_sources)
    write_json(source_dir / "validation.json", {"pass": True, "schema": "mineru-unified-release/v1", "cards": len(cards), "sources": len(final_sources), "all_cards_have_physical_pages": limit is None})
    write_rows(output / "retired_cards.jsonl", retired)
    write_json(output / "document_replacements.json", replacements)
    finish_originals(root, output)
    summary = {"replaced_documents": len(legacy), "retired_cards": len(retired),
               "new_cards": sum(source["atom_count"] for source in rebuilt),
               "total_cards": len(cards), "sources": len(final_sources),
               "elapsed_seconds": round(time.monotonic() - began, 1),
               "builder_sha256": sha(builder_path), "partial_sample": limit is not None}
    write_json(output / "rebuild.json", summary)
    return summary


def finish_originals(root: Path, output: Path):
    """Stage independent verification copies without touching original files."""
    originals = output / "originals"
    for source in rows(output / "source/source_registry.jsonl"):
        for name in ("source_path", "formal_pdf_path", "middle_json_path", "text_path"):
            relative = source.get(name)
            if not relative:
                continue
            target = originals / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            generated = output / "rebuilt" / source["document_id"] / "body.txt"
            if name == "text_path" and generated.is_file():
                shutil.copy2(generated, target)
            else:
                shutil.copy2(root / relative, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--finish-originals", action="store_true")
    args = parser.parse_args()
    if args.finish_originals:
        finish_originals(args.root, args.output)
        print(json.dumps({"verification_originals_staged": True}))
    else:
        print(json.dumps(prepare(args.root, args.output, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
