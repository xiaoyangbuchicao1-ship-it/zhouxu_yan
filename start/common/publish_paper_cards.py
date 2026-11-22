"""Publish an already verified paper release while the API is stopped.

Only the three card-source files, generated MinerU body files, and normalized
paper release are replaced. Original PDFs, Markdown and MinerU JSON stay intact.
"""

import argparse
import json
import shutil
from pathlib import Path

from start.common.unify_paper_cards import rows, sha, write_json


def write_card_offsets(cards: Path, index: Path) -> None:
    """Write the byte offsets needed for direct paper-card reads."""

    offsets = {}
    with cards.open("rb") as stream:
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            if not line.strip():
                continue
            card_id = json.loads(line).get("card_id")
            if not isinstance(card_id, str) or not card_id:
                raise ValueError("paper_card_offset_identity_invalid")
            if card_id in offsets:
                raise ValueError("paper_card_offset_duplicate")
            offsets[card_id] = offset
    write_json(index, {
        "schema_version": 1,
        "cards_size": cards.stat().st_size,
        "offsets": offsets,
    })


def publish(project: Path, corpus: Path, stage: Path, backup: Path):
    project, corpus, stage, backup = (path.resolve() for path in (project, corpus, stage, backup))
    if project.parent != corpus or not backup.is_relative_to(project):
        raise ValueError("paper_publish_roots_invalid")
    manifest = json.loads((stage / "release/manifest.json").read_text(encoding="utf-8"))
    for filename, key in (("information_cards.jsonl", "information_cards_sha256"), ("source_registry.jsonl", "source_registry_sha256"), ("validation.json", "validation_sha256")):
        if sha(stage / "rebuild/source" / filename) != manifest[key]:
            raise ValueError("paper_publish_source_changed:" + filename)
    for filename, key in (("cards.jsonl", "normalized_cards_sha256"), ("sources.jsonl", "sources_sha256"), ("papers.jsonl", "papers_sha256"), ("vectors.npy", "vectors_sha256"), ("vectors_meta.jsonl", "sidecar_sha256")):
        if sha(stage / "release" / filename) != manifest[key]:
            raise ValueError("paper_publish_release_changed:" + filename)
    sources = list(rows(stage / "rebuild/source/source_registry.jsonl"))
    bodies = []
    for source in sources:
        generated = stage / "rebuild/rebuilt" / source["document_id"] / "body.txt"
        if generated.is_file():
            target = (corpus / source["text_path"]).resolve()
            if not target.is_relative_to(corpus / "md") or sha(generated) != source["text_sha256"]:
                raise ValueError("paper_body_publish_invalid")
            bodies.append((generated, target))
    backup.mkdir(parents=True, exist_ok=False)
    before = backup / "source-before"
    before.mkdir()
    for filename in ("information_cards.jsonl", "source_registry.jsonl", "validation.json"):
        shutil.copy2(corpus / "信息卡" / filename, before / filename)
    old_release = project / "opensearch/import/information_cards"
    shutil.copytree(old_release, backup / "release-before")
    for generated, target in bodies:
        if target.is_file():
            if sha(target) != sha(generated):
                raise ValueError("existing_body_would_be_overwritten:" + str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(generated, target)
    for filename in ("information_cards.jsonl", "source_registry.jsonl", "validation.json"):
        shutil.copy2(stage / "rebuild/source" / filename, corpus / "信息卡" / filename)
    for filename in ("cards.jsonl", "sources.jsonl", "papers.jsonl", "vectors.npy", "vectors_meta.jsonl"):
        shutil.copy2(stage / "release" / filename, old_release / filename)
    write_card_offsets(old_release / "cards.jsonl", old_release / "cards.offsets.json")
    manifest["source_dir"] = "/app/runtime-resources/information_cards"
    write_json(old_release / "manifest.json", manifest)
    metadata = project / "data/migrations/paper-cards-v4-20261121"
    metadata.mkdir(parents=True, exist_ok=True)
    for filename in ("retired_cards.jsonl", "document_replacements.json"):
        shutil.copy2(stage / "rebuild" / filename, metadata / filename)
    result = {"cards": manifest["card_count"], "catalog_papers": len(sources), "generated_bodies": len(bodies),
              "previous_source_sha256": sha(before / "information_cards.jsonl"),
              "new_source_sha256": manifest["information_cards_sha256"]}
    write_json(metadata / "release.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.project, args.corpus, args.stage, args.backup)))
