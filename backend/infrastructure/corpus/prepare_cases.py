"""Admit the delivered Chinese-field corpus without changing its embeddings.

Usage: python -m backend.infrastructure.corpus.prepare_cases SOURCE DESTINATION
DESTINATION must not exist. Missing source coverage fails admission explicitly.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np

from backend.config.settings import load_settings
from backend.infrastructure.corpus.cases import CaseSnapshot, text_hash


def supported_identity(text, original):
    fields = {}
    offset = 0
    for line in text.splitlines(keepends=True):
        value = line.strip()
        start = offset + line.find(value)
        offset += len(line)
        if offset > 2500:
            break
        key = None
        if (
            "court" not in fields
            and re.fullmatch(
                r"(?:中华人民共和国)?[\u4e00-\u9fff]{2,22}(?:人民法院|海事法院)", value
            )
            and not any(s in value for s in ("向", "起诉", "关于", "诉至", "申请"))
        ):
            key = "court"
        if "case_number" not in fields and re.fullmatch(
            r"[（(]\d{4}[）)][\u4e00-\u9fff\d]{2,25}号", value
        ):
            key = "case_number"
        if key:
            fields[key] = {
                "value": value,
                "start": start,
                "end": start + len(value),
                "basis": "literal_header",
            }
    return {
        "supported_fields": fields,
        "unverified_original": {k: v for k, v in original.items() if v is not None},
        "verification_scope": "local_source_text_only",
        "judgment_date": None,
    }


def prepare(source: Path, destination: Path, settings):
    if destination.exists():
        raise ValueError("destination_must_be_new")

    def rows(name):
        return [
            json.loads(s)
            for s in (source / name).read_text(encoding="utf-8").splitlines()
            if s.strip()
        ]

    documents = rows("corpus/cases.jsonl")
    chunks = rows("corpus/chunk.jsonl")
    meta = rows("import/case_chunk_embeddings_meta.jsonl")
    vector_path = source / "import/case_chunk_embeddings.npy"
    vectors = np.load(vector_path, mmap_mode="r", allow_pickle=False)
    if (
        vectors.shape != (len(chunks), 1024)
        or vectors.dtype != np.float32
        or not np.isfinite(vectors).all()
    ):
        raise ValueError("vector_contract")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
        raise ValueError("vector_normalization")
    if len(meta) != len(chunks):
        raise ValueError("embedding_identity")
    version = (
        "case_"
        + text_hash(
            (source / "corpus/cases.jsonl").read_text(encoding="utf-8")
            + (source / "corpus/chunk.jsonl").read_text(encoding="utf-8")
            + settings.rag.embedding_revision
            + "source-v2"
        )[:16]
    )
    by_case = {doc["案例编号"]: doc for doc in documents}
    if len(by_case) != len(documents):
        raise ValueError("duplicate_case")
    converted = []
    repeated = []
    coverage = {
        cid: bytearray(len(doc["案例全文与来源"]["原文全文"]))
        for cid, doc in by_case.items()
    }
    for index, (chunk, info) in enumerate(zip(chunks, meta)):
        cid, body = chunk["案例编号"], chunk["文本"]
        if (
            info["embedding_row_index"] != index
            or info["片段编号"] != chunk["片段编号"]
            or info["案例编号"] != cid
        ):
            raise ValueError("embedding_identity")
        text = by_case[cid]["案例全文与来源"]["原文全文"]
        start = text.find(body)
        if not body or start < 0:
            raise ValueError("chunk_not_in_source")
        # Repeated paragraphs can cover multiple positions. Do not report false gaps.
        for match in re.finditer(re.escape(body), text):
            coverage[cid][match.start() : match.end()] = b"\1" * len(body)
        positions = [m.start() for m in re.finditer(re.escape(body), text)]
        if len(positions) > 1:
            repeated.append(
                {
                    "chunk_id": chunk["片段编号"],
                    "case_id": cid,
                    "positions": positions,
                    "text_chars": len(body),
                }
            )
        converted.append(
            {
                "chunk_id": chunk["片段编号"],
                "case_id": cid,
                "title": chunk["案例标题"],
                "text": body,
                "start": start,
                "end": start + len(body),
                "snapshot_id": version,
                "source_sha256": text_hash(text),
                "text_sha256": text_hash(body),
            }
        )
    normalized = []
    for cid, doc in by_case.items():
        text = doc["案例全文与来源"]["原文全文"]
        if any(
            not covered and not ch.isspace() for ch, covered in zip(text, coverage[cid])
        ):
            raise ValueError("unindexed_source_text_requires_explicit_rebuild")
        normalized.append(
            {
                "case_id": cid,
                "title": doc["案例身份"]["案例标题"],
                "text": text,
                "source_sha256": text_hash(text),
                "snapshot_id": version,
                "identity": supported_identity(text, doc["案例身份"]),
            }
        )
    if len({r["chunk_id"] for r in converted}) != len(converted):
        raise ValueError("duplicate_chunk")
    contract = {
        "model": settings.rag.embedding_model,
        "revision": settings.rag.embedding_revision,
        "pooling": "cls",
        "normalize": True,
        "dimensions": settings.rag.embedding_dimensions,
    }
    destination.mkdir(parents=True)
    for name, data in [("cases.jsonl", normalized), ("chunks.jsonl", converted)]:
        (destination / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data),
            encoding="utf-8",
        )
    shutil.copyfile(vector_path, destination / "vectors.npy")
    manifest = {
        "snapshot_id": version,
        "encoder": contract,
        "case_count": len(normalized),
        "chunk_count": len(converted),
        "original_vectors_preserved": len(converted),
        "new_vectors": 0,
        "repairs": [],
        "repeated_occurrences": repeated,
        "files": {
            name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
            for name in ("cases.jsonl", "chunks.jsonl", "vectors.npy")
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    CaseSnapshot(destination, contract)
    return {"snapshot_id": version, "cases": len(normalized), "chunks": len(converted)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.source.resolve(), args.destination.resolve(), load_settings())
        )
    )
