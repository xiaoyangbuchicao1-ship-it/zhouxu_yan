#!/usr/bin/env python3
"""Publish the external information-card JSONL into its own OpenSearch index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

DELIVERY = Path(__file__).resolve().parents[1]
OPENSEARCH_ROOT = DELIVERY.parent
PROJECT_ROOT = OPENSEARCH_ROOT.parent
DEFAULT_SOURCE = PROJECT_ROOT.parent / "信息卡"
DEFAULT_INDEX = "maritime_information_cards_v4"
SCHEMA_DIRECTORY = "maritime_information_cards_v2"
DEFAULT_ALIAS = "maritime_information_cards_read"
DEFAULT_EMBEDDINGS = OPENSEARCH_ROOT / "import" / "information_cards"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Direct CLI execution needs the project root above before this import.
from opensearch.opensearch_delivery.scripts.maritime_index import Client  # noqa: E402


class CardImportError(ValueError):
    pass


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise CardImportError(f"expected_object:{path.name}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _required_text(value, field: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CardImportError(f"invalid_{field}:{line_number}")
    return value.strip()


def normalize_card(value: dict, line_number: int) -> dict:
    card_id = _required_text(value.get("card_id"), "card_id", line_number)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", card_id):
        raise CardImportError(f"invalid_card_id:{line_number}")
    retrieval_text = _required_text(
        value.get("retrieval_text"), "retrieval_text", line_number
    )
    status = _required_text(value.get("status"), "status", line_number)
    card_type = _required_text(value.get("card_type"), "card_type", line_number)
    source = value.get("source")
    if not isinstance(source, dict):
        raise CardImportError(f"invalid_source:{line_number}")
    source_document_id = _required_text(
        source.get("document_id"), "source_document_id", line_number
    )
    source_title = _required_text(source.get("title"), "source_title", line_number)
    source_path = _required_text(source.get("path"), "source_path", line_number)
    source_sha256 = _required_text(source.get("sha256"), "source_sha256", line_number)
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise CardImportError(f"invalid_source_sha256:{line_number}")
    locator = source.get("locator")
    if not isinstance(locator, dict):
        raise CardImportError(f"invalid_locator:{line_number}")
    start, end, page = (
        locator.get("char_start"),
        locator.get("char_end"),
        locator.get("page"),
    )
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise CardImportError(f"invalid_character_locator:{line_number}")
    if page is not None and (type(page) is not int or page < 1):
        raise CardImportError(f"invalid_page_locator:{line_number}")
    if page is not None and source.get("page_count") and page > source["page_count"]:
        raise CardImportError(f"page_locator_out_of_range:{line_number}")
    if status != "validated_for_retrieval" or card_type != "information":
        raise CardImportError(f"information_card_not_validated:{line_number}")
    topics = value.get("topics") or []
    if not isinstance(topics, list) or not all(
        isinstance(item, str) for item in topics
    ):
        raise CardImportError(f"invalid_topics:{line_number}")
    return {
        "card_id": card_id,
        "card_type": card_type,
        "status": status,
        "evidence_type": str(value.get("evidence_type") or ""),
        "time_scope": str(value.get("time_scope") or ""),
        "topics": topics,
        "retrieval_text": retrieval_text,
        "claim_or_fact": str(value.get("claim_or_fact") or retrieval_text),
        "caveat": str(value.get("caveat") or ""),
        "source_document_id": source_document_id,
        "source_title": source_title,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "source_locator": locator,
        "source_uri": str(source.get("source_uri") or source.get("uri") or ""),
        "source_provenance": source,
        "source_time_scope": str(value.get("time_scope") or ""),
    }


def load_cards(source_dir: Path) -> tuple[list[dict], dict]:
    if not source_dir.is_dir():
        raise CardImportError(f"information_card_source_missing:{source_dir}")
    cards_path = source_dir / "information_cards.jsonl"
    registry_path = source_dir / "source_registry.jsonl"
    validation_path = source_dir / "validation.json"
    if (
        not cards_path.is_file()
        or not registry_path.is_file()
        or not validation_path.is_file()
    ):
        raise CardImportError("information_card_source_contract_incomplete")
    validation = _read_json(validation_path)
    if validation.get("pass") is not True:
        raise CardImportError("information_card_source_validation_failed")
    registry = {}
    with registry_path.open(encoding="utf-8-sig") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            record = json.loads(raw)
            document_id = _required_text(
                record.get("document_id"), "document_id", line_number
            )
            if document_id in registry:
                raise CardImportError(f"duplicate_source_document:{document_id}")
            registry[document_id] = record
    cards = []
    seen = set()
    with cards_path.open(encoding="utf-8-sig") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CardImportError(
                    f"invalid_information_card_json:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise CardImportError(f"invalid_information_card_record:{line_number}")
            card = normalize_card(value, line_number)
            source = registry.get(card["source_document_id"])
            if source is None or source.get("source_hash") != card["source_sha256"]:
                raise CardImportError(f"source_registry_hash_mismatch:{line_number}")
            if card["source_path"] not in (
                source.get("source_path"),
                source.get("formal_md_path"),
            ):
                raise CardImportError(f"source_registry_path_mismatch:{line_number}")
            card["source_authors"] = str(source.get("authors_hint") or "")
            card["source_collection"] = str(source.get("collection") or "")
            if card["card_id"] in seen:
                raise CardImportError(f"duplicate_information_card:{card['card_id']}")
            seen.add(card["card_id"])
            cards.append(card)
    if not cards:
        raise CardImportError("information_card_source_empty")
    manifest = {
        "schema_version": "1.0",
        "source_type": "validated_information_card",
        "card_count": len(cards),
        "source_count": len(registry),
        "cards_with_page": sum(
            type(card["source_locator"].get("page")) is int for card in cards
        ),
        "information_cards_sha256": _file_sha256(cards_path),
        "source_registry_sha256": _file_sha256(registry_path),
        "validation_sha256": _file_sha256(validation_path),
        "source_dir": str(source_dir),
    }
    return cards, manifest


def definition() -> dict:
    folder = DELIVERY / "index_definitions" / SCHEMA_DIRECTORY
    return {
        "settings": json.loads((folder / "settings.json").read_text(encoding="utf-8")),
        "mappings": json.loads((folder / "mappings.json").read_text(encoding="utf-8")),
    }


class CardEncoder:
    """Shared offline BGE-M3 encoder for full releases and resumed batch builds."""

    max_tokens = 2048

    def __init__(self, *, device="cpu", threads=4):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.contract = definition()["mappings"]["_meta"]["embedding"]
        options = {
            "revision": self.contract["revision"],
            "local_files_only": True,
            "trust_remote_code": False,
        }
        self.tokenizer = AutoTokenizer.from_pretrained(self.contract["model"], **options)
        self.model = AutoModel.from_pretrained(self.contract["model"], **options).eval().to(device)
        self.device = device
        torch.set_num_threads(threads)
        if device.startswith("cuda"):
            torch.set_float32_matmul_precision("high")

    def token_lengths(self, texts):
        tokens = self.tokenizer(texts, truncation=False, padding=False)
        return [len(ids) for ids in tokens["input_ids"]]

    def encode(self, texts):
        import torch

        batch = self.tokenizer(
            texts, padding=True, truncation=True,
            max_length=self.max_tokens, return_tensors="pt",
        )
        with torch.inference_mode():
            encoded = self.model(
                **{k: v.to(self.device) for k, v in batch.items()}
            ).last_hidden_state[:, 0]
            encoded = torch.nn.functional.normalize(encoded.float(), p=2, dim=1)
        return encoded.cpu().numpy()


def build_embeddings(cards, manifest, folder, *, device="cpu", batch_size=8, reuse_dir=None, stop_after=None):
    """Same pinned CLS/L2 contract as the resident query encoder."""
    import numpy as np

    if reuse_dir is not None:
        return _incremental_embeddings(cards, manifest, folder, reuse_dir, device=device,
                                       batch_size=batch_size, stop_after=stop_after)

    encoder = CardEncoder(device=device)
    contract = encoder.contract
    folder.mkdir(parents=True, exist_ok=True)
    vector_path = folder / "vectors.npy"
    temporary = folder / "vectors.building.npy"
    vectors = np.lib.format.open_memmap(
        temporary,
        mode="w+",
        dtype=np.float32,
        shape=(len(cards), contract["dimensions"]),
    )
    max_tokens = encoder.max_tokens
    lengths = encoder.token_lengths([c["retrieval_text"] for c in cards])
    # Sort only the inference batches; vector rows stay in original card order.
    order = sorted(range(len(cards)), key=lambda i: min(lengths[i], max_tokens))
    began = time.monotonic()
    for offset in range(0, len(order), batch_size):
        indices = order[offset : offset + batch_size]
        vectors[indices] = encoder.encode([cards[i]["retrieval_text"] for i in indices])
        if offset % (batch_size * 16) == 0:
            vectors.flush()
            print(
                f"BGE-M3 cards: {offset + len(indices)}/{len(cards)}; {time.monotonic() - began:.1f}s",
                flush=True,
            )
    vectors.flush()
    del vectors
    temporary.replace(vector_path)
    sidecar = folder / "vectors_meta.jsonl"
    with sidecar.open("w", encoding="utf-8") as out:
        for i, card in enumerate(cards):
            out.write(
                json.dumps(
                    {
                        "row": i,
                        "card_id": card["card_id"],
                        "input_sha256": hashlib.sha256(
                            card["retrieval_text"].encode()
                        ).hexdigest(),
                        "original_tokens": lengths[i],
                        "truncated": lengths[i] > max_tokens,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    result = {
        **manifest,
        "embedding": contract,
        "max_tokens": max_tokens,
        "inference_device": device,
        "matmul_precision": "high" if device.startswith("cuda") else "highest",
        "input_field": "retrieval_text",
        "vectors_sha256": _file_sha256(vector_path),
        "sidecar_sha256": _file_sha256(sidecar),
        "truncated_count": sum(n > max_tokens for n in lengths),
    }
    (folder / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    normalize_release(cards, manifest, folder)
    return {
        "embedded": len(cards),
        "embedding": contract,
        "truncated_count": result["truncated_count"],
    }


def _incremental_embeddings(cards, manifest, folder, reuse_dir, *, device, batch_size, stop_after):
    """Reuse only identical inputs under the identical pinned encoder contract."""
    import numpy as np

    contract = definition()["mappings"]["_meta"]["embedding"]
    cached = _read_json(reuse_dir / "manifest.json")
    if cached["embedding"] != contract:
        raise CardImportError("cached_embedding_contract_changed")
    for filename, key in (("vectors.npy", "vectors_sha256"), ("vectors_meta.jsonl", "sidecar_sha256")):
        if _file_sha256(reuse_dir / filename) != cached[key]:
            raise CardImportError("cached_embedding_file_changed:" + filename)
    cache_rows = [json.loads(line) for line in (reuse_dir / "vectors_meta.jsonl").read_text(encoding="utf-8").splitlines()]
    cache_vectors = np.load(reuse_dir / "vectors.npy", mmap_mode="r")
    if cache_vectors.shape != (len(cache_rows), contract["dimensions"]):
        raise CardImportError("cached_embedding_shape_invalid")
    cache = {row["input_sha256"]: row for row in cache_rows}
    hashes = [hashlib.sha256(card["retrieval_text"].encode()).hexdigest() for card in cards]
    identity = hashlib.sha256(json.dumps([manifest, hashes], sort_keys=True).encode()).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    progress_path = folder / "vectors.progress.json"
    temporary = folder / "vectors.building.npy"
    if progress_path.is_file():
        progress = _read_json(progress_path)
        if progress["identity"] != identity:
            raise CardImportError("unfinished_embedding_release_changed")
        lengths, done = progress["lengths"], progress["done"]
        vectors = np.lib.format.open_memmap(temporary, mode="r+")
        reused = progress["reused"]
    else:
        vectors = np.lib.format.open_memmap(temporary, mode="w+", dtype=np.float32,
                                           shape=(len(cards), contract["dimensions"]))
        lengths, done, reused = [None] * len(cards), [False] * len(cards), 0
        for index, digest in enumerate(hashes):
            if digest in cache:
                record = cache[digest]
                vectors[index] = cache_vectors[record["row"]]
                lengths[index], done[index] = record["original_tokens"], True
                reused += 1
    pending = [index for index, complete in enumerate(done) if not complete]
    encoder = CardEncoder(device=device) if pending else None
    unknown = [index for index in pending if lengths[index] is None]
    if unknown:
        for index, length in zip(unknown, encoder.token_lengths([cards[i]["retrieval_text"] for i in unknown]), strict=True):
            lengths[index] = length
    pending.sort(key=lambda index: min(lengths[index], CardEncoder.max_tokens))
    began = time.monotonic()
    encoded = 0

    def save():
        vectors.flush()
        progress_path.write_text(json.dumps({"identity": identity, "lengths": lengths, "done": done, "reused": reused}), encoding="utf-8")

    save()
    for offset in range(0, len(pending), batch_size):
        indices = pending[offset:offset + batch_size]
        vectors[indices] = encoder.encode([cards[index]["retrieval_text"] for index in indices])
        for index in indices:
            done[index] = True
        encoded += len(indices)
        save()
        elapsed = round(time.monotonic() - began, 1)
        print(json.dumps({"reused": reused, "newly_encoded": encoded, "remaining": sum(not x for x in done), "seconds": elapsed}), flush=True)
        if stop_after is not None and encoded >= stop_after and not all(done):
            return {"complete": False, "reused": reused, "newly_encoded": encoded, "seconds": elapsed}
    del vectors
    temporary.replace(folder / "vectors.npy")
    with (folder / "vectors_meta.jsonl").open("w", encoding="utf-8") as stream:
        for index, card in enumerate(cards):
            stream.write(json.dumps({"row": index, "card_id": card["card_id"], "input_sha256": hashes[index],
                                     "original_tokens": lengths[index], "truncated": lengths[index] > CardEncoder.max_tokens}) + "\n")
    result = {**manifest, "embedding": contract, "max_tokens": CardEncoder.max_tokens,
              "inference_device": device, "input_field": "retrieval_text",
              "vectors_sha256": _file_sha256(folder / "vectors.npy"),
              "sidecar_sha256": _file_sha256(folder / "vectors_meta.jsonl"),
              "truncated_count": sum(length > CardEncoder.max_tokens for length in lengths), "reused_vectors": reused}
    (folder / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    normalize_release(cards, manifest, folder)
    progress_path.unlink()
    return {"complete": True, "embedded": len(cards), "reused": reused, "newly_encoded": encoded}


def normalize_release(cards, manifest, folder):
    """Persist reviewable normalized records alongside the derived vectors."""
    folder.mkdir(parents=True, exist_ok=True)
    release = _read_json(folder / "manifest.json")
    for key in (
        "information_cards_sha256",
        "source_registry_sha256",
        "validation_sha256",
    ):
        if release[key] != manifest[key]:
            raise CardImportError(f"normalized_source_changed:{key}")
    sources = {}
    with (folder / "cards.jsonl").open("w", encoding="utf-8") as out:
        for card in cards:
            out.write(json.dumps(card, ensure_ascii=False) + "\n")
            sources.setdefault(
                card["source_document_id"],
                {
                    key: card[key]
                    for key in (
                        "source_document_id",
                        "source_title",
                        "source_authors",
                        "source_collection",
                        "source_path",
                        "source_sha256",
                        "source_uri",
                    )
                },
            )
    with (folder / "sources.jsonl").open("w", encoding="utf-8") as out:
        for value in sources.values():
            out.write(json.dumps(value, ensure_ascii=False) + "\n")
    registry_path = Path(manifest["source_dir"]) / "source_registry.jsonl"
    with registry_path.open(encoding="utf-8-sig") as records, (folder / "papers.jsonl").open("w", encoding="utf-8") as out:
        for line in records:
            if line.strip():
                record = json.loads(line)
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
    release.update(
        {
            "normalized_cards_sha256": _file_sha256(folder / "cards.jsonl"),
            "sources_sha256": _file_sha256(folder / "sources.jsonl"),
            "papers_sha256": _file_sha256(folder / "papers.jsonl"),
            "represented_source_count": len(sources),
        }
    )
    (folder / "manifest.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"normalized_cards": len(cards), "represented_sources": len(sources)}


def load_embeddings(folder, cards, manifest):
    import numpy as np

    meta = _read_json(folder / "manifest.json")
    for key in (
        "information_cards_sha256",
        "source_registry_sha256",
        "validation_sha256",
    ):
        if meta[key] != manifest[key]:
            raise CardImportError(f"embedding_source_changed:{key}")
    if (
        meta["embedding"] != definition()["mappings"]["_meta"]["embedding"]
        or meta["input_field"] != "retrieval_text"
        or meta["max_tokens"] != 2048
    ):
        raise CardImportError("embedding_contract_mismatch")
    for name, key in (
        ("vectors.npy", "vectors_sha256"),
        ("vectors_meta.jsonl", "sidecar_sha256"),
        ("cards.jsonl", "normalized_cards_sha256"),
        ("sources.jsonl", "sources_sha256"),
        ("papers.jsonl", "papers_sha256"),
    ):
        if _file_sha256(folder / name) != meta[key]:
            raise CardImportError(f"embedding_file_changed:{name}")
    with (folder / "cards.jsonl").open(encoding="utf-8") as stream:
        if any(
            json.loads(raw) != card for raw, card in zip(stream, cards, strict=True)
        ):
            raise CardImportError("normalized_cards_changed")
    vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
    if (
        vectors.shape != (len(cards), 1024)
        or vectors.dtype != np.float32
        or not np.isfinite(vectors).all()
        or not np.all(np.abs(np.linalg.norm(vectors, axis=1) - 1) < 0.001)
    ):
        raise CardImportError("embedding_values_invalid")
    with (folder / "vectors_meta.jsonl").open(encoding="utf-8") as stream:
        rows = [json.loads(raw) for raw in stream]
    if len(rows) != len(cards) or any(
        row["row"] != i
        or row["card_id"] != card["card_id"]
        or row["input_sha256"]
        != hashlib.sha256(card["retrieval_text"].encode()).hexdigest()
        for i, (row, card) in enumerate(zip(rows, cards, strict=True))
    ):
        raise CardImportError("embedding_card_identity_mismatch")
    return vectors, meta


def validate(source_dir: Path) -> dict:
    cards, manifest = load_cards(source_dir)
    return {
        "passed": True,
        "count": len(cards),
        "manifest": manifest,
        "sample_ids": [card["card_id"] for card in cards[:3]],
    }


def create(client: Client, index: str) -> dict:
    existing = client.call("GET", f"/{index}", missing_ok=True)
    if existing is not None:
        mapping = client.call("GET", f"/{index}/_mapping")[index]["mappings"]
        expected = definition()["mappings"]
        if (
            mapping.get("dynamic") != expected["dynamic"]
            or mapping.get("properties") != expected["properties"]
        ):
            raise CardImportError("information_card_mapping_differs_use_new_index")
        return {"created": False, "index": index}
    payload = definition()
    payload["mappings"]["_meta"]["created_by"] = "information_cards_index.py"
    client.call("PUT", f"/{index}", payload)
    return {"created": True, "index": index}


def import_cards(
    client: Client,
    index: str,
    cards: list[dict],
    manifest: dict,
    batch_size: int = 200,
    *,
    embeddings_dir=DEFAULT_EMBEDDINGS,
) -> dict:
    vectors, embedding_manifest = load_embeddings(embeddings_dir, cards, manifest)
    refresh = client.call(
        "PUT", f"/{index}/_settings", {"index": {"refresh_interval": "-1"}}
    )
    del refresh
    try:
        for offset in range(0, len(cards), batch_size):
            batch = cards[offset : offset + batch_size]
            lines = []
            for i, card in enumerate(batch, offset):
                lines.append(
                    json.dumps(
                        {"index": {"_index": index, "_id": card["card_id"]}},
                        ensure_ascii=False,
                    )
                )
                lines.append(
                    json.dumps(
                        {**card, "vector": vectors[i].tolist()}, ensure_ascii=False
                    )
                )
            body = ("\n".join(lines) + "\n").encode("utf-8")
            response = client.call("POST", "/_bulk?refresh=false", body)
            if response.get("errors"):
                raise CardImportError(f"information_card_bulk_failed:{offset}")
        client.call(
            "PUT",
            f"/{index}/_mapping",
            {
                "_meta": {
                    **embedding_manifest,
                    "imported_at": datetime.now(UTC).isoformat(),
                }
            },
        )
    finally:
        client.call("PUT", f"/{index}/_settings", {"index": {"refresh_interval": "1s"}})
    return {"imported": len(cards), "index": index, "manifest": manifest}


def verify(
    client: Client,
    index: str,
    expected_count: int,
    *,
    cards=None,
    manifest=None,
    embeddings_dir=DEFAULT_EMBEDDINGS,
) -> dict:
    client.call("POST", f"/{index}/_refresh")
    count = client.call("GET", f"/{index}/_count")
    actual = int(count.get("count", -1))
    if actual != expected_count:
        raise CardImportError(
            f"information_card_count_mismatch:{actual}:{expected_count}"
        )
    mapping = client.call("GET", f"/{index}/_mapping")[index]["mappings"]
    if (
        mapping.get("dynamic") != "strict"
        or mapping.get("properties") != definition()["mappings"]["properties"]
    ):
        raise CardImportError("information_card_mapping_mismatch")
    contract = definition()["mappings"]["_meta"]["embedding"]
    if mapping.get("_meta", {}).get("embedding") != contract:
        raise CardImportError("information_card_embedding_mismatch")
    vector_count = client.call(
        "POST", f"/{index}/_count", {"query": {"exists": {"field": "vector"}}}
    )["count"]
    if vector_count != expected_count:
        raise CardImportError("information_card_vector_coverage_incomplete")
    if cards is not None:
        import numpy as np

        vectors, meta = load_embeddings(embeddings_dir, cards, manifest)
        for key in (
            "information_cards_sha256",
            "source_registry_sha256",
            "validation_sha256",
            "vectors_sha256",
            "sidecar_sha256",
            "normalized_cards_sha256",
            "sources_sha256",
        ):
            if mapping["_meta"].get(key) != meta[key]:
                raise CardImportError(f"information_card_index_manifest_changed:{key}")
        for i in range(0, len(cards), max(1, len(cards) // 12)):
            row = client.call("GET", f"/{index}/_doc/{cards[i]['card_id']}")["_source"]
            vector = row.pop("vector")
            if row != cards[i] or not np.array_equal(
                np.asarray(vector, dtype=np.float32), vectors[i]
            ):
                raise CardImportError(f"information_card_index_body_changed:{i}")
    return {"passed": True, "count": actual, "vectors": vector_count, "index": index}


def activate(
    client: Client, index: str, alias: str, *, replace_index=None,
    previous_source_sha256=None,
) -> dict:
    new_mapping = client.call("GET", f"/{index}/_mapping")[index]["mappings"]
    verify(client, index, new_mapping["_meta"]["card_count"])
    current = client.call("GET", f"/_alias/{alias}", missing_ok=True)
    indexes = sorted(current or {})
    if indexes == [index]:
        return {"activated": False, "alias": alias, "index": index}
    actions = []
    if indexes:
        if indexes != [replace_index] or alias != DEFAULT_ALIAS:
            raise CardImportError(f"information_card_alias_points_elsewhere:{indexes}")
        old_mapping = client.call("GET", f"/{replace_index}/_mapping")[replace_index][
            "mappings"
        ]
        source_changed = any(old_mapping.get("_meta", {}).get(key) != new_mapping["_meta"][key] for key in (
            "information_cards_sha256",
            "source_registry_sha256",
            "validation_sha256",
        ))
        if source_changed and (
            not previous_source_sha256
            or old_mapping.get("_meta", {}).get("information_cards_sha256") != previous_source_sha256
        ):
            raise CardImportError("information_card_source_update_requires_previous_source_sha256")
        actions.append({"remove": {"index": replace_index, "alias": alias}})
    actions.append({"add": {"index": index, "alias": alias}})
    client.call("POST", "/_aliases", {"actions": actions})
    return {"activated": True, "alias": alias, "index": index}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "validate",
            "embed",
            "normalize",
            "create",
            "import",
            "verify",
            "activate",
        ),
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--root", type=Path, default=OPENSEARCH_ROOT)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    parser.add_argument("--url", default="https://127.0.0.1:19200")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--embeddings-dir", type=Path, default=DEFAULT_EMBEDDINGS)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--embedding-batch-size", type=int, default=8)
    parser.add_argument("--reuse-embeddings-dir", type=Path)
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--replace-index")
    parser.add_argument("--previous-source-sha256")
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    cards, manifest = load_cards(source_dir)
    if args.command == "validate":
        result = validate(source_dir)
    elif args.command == "embed":
        result = build_embeddings(
            cards,
            manifest,
            args.embeddings_dir,
            device=args.device,
            batch_size=args.embedding_batch_size,
            reuse_dir=args.reuse_embeddings_dir,
            stop_after=args.stop_after,
        )
    elif args.command == "normalize":
        result = normalize_release(cards, manifest, args.embeddings_dir)
    else:
        client = Client(args.root.resolve(), args.url)
        if args.command == "create":
            result = create(client, args.index)
        elif args.command == "import":
            result = import_cards(
                client,
                args.index,
                cards,
                manifest,
                args.batch_size,
                embeddings_dir=args.embeddings_dir,
            )
        elif args.command == "verify":
            result = verify(
                client,
                args.index,
                len(cards),
                cards=cards,
                manifest=manifest,
                embeddings_dir=args.embeddings_dir,
            )
        else:
            verify(
                client,
                args.index,
                len(cards),
                cards=cards,
                manifest=manifest,
                embeddings_dir=args.embeddings_dir,
            )
            result = activate(
                client, args.index, args.alias, replace_index=args.replace_index,
                previous_source_sha256=args.previous_source_sha256,
            )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
