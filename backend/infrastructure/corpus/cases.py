"""Immutable local case source bundle. Never resolve model-supplied file paths."""

import hashlib
import json
import re
from pathlib import Path


class CaseSourceError(ValueError):
    pass


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CaseSnapshot:
    def __init__(self, directory: Path, encoder_contract: dict):
        self.directory = directory.resolve()
        self.manifest = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        self.version = self.manifest["snapshot_id"]
        if not re.fullmatch(r"case_[a-f0-9]{16}", self.version):
            raise CaseSourceError("snapshot_identity")
        if self.manifest["encoder"] != encoder_contract:
            raise CaseSourceError("encoder_contract")
        for name in ("cases.jsonl", "chunks.jsonl", "vectors.npy"):
            path = self.directory / name
            if (
                hashlib.sha256(path.read_bytes()).hexdigest()
                != self.manifest["files"][name]
            ):
                raise CaseSourceError("source_checksum")

        def rows(name):
            return [
                json.loads(s)
                for s in (self.directory / name)
                .read_text(encoding="utf-8")
                .splitlines()
                if s.strip()
            ]

        documents = rows("cases.jsonl")
        self.chunks = rows("chunks.jsonl")
        self.docs = {r["case_id"]: r for r in documents}
        self.by_id = {r["chunk_id"]: r for r in self.chunks}
        if len(self.docs) != len(documents) or len(self.by_id) != len(self.chunks):
            raise CaseSourceError("duplicate_source")
        if (
            len(self.docs) != self.manifest["case_count"]
            or len(self.chunks) != self.manifest["chunk_count"]
        ):
            raise CaseSourceError("source_count")
        for doc in documents:
            if (
                doc["snapshot_id"] != self.version
                or text_hash(doc["text"]) != doc["source_sha256"]
            ):
                raise CaseSourceError("document_checksum")
            for field in doc["identity"]["supported_fields"].values():
                start, end = field["start"], field["end"]
                if (
                    not 0 <= start < end <= len(doc["text"])
                    or doc["text"][start:end] != field["value"]
                ):
                    raise CaseSourceError("identity_range")
        for row in self.chunks:
            self.validate_chunk(row)

    def validate_chunk(self, row):
        doc = self.docs.get(row.get("case_id"))
        if (
            doc is None
            or row.get("snapshot_id") != self.version
            or doc["source_sha256"] != row.get("source_sha256")
        ):
            raise CaseSourceError("source_version")
        start, end = row["start"], row["end"]
        if (
            not 0 <= start <= end <= len(doc["text"])
            or doc["text"][start:end] != row["text"]
            or text_hash(row["text"]) != row["text_sha256"]
        ):
            raise CaseSourceError("source_range")

    def read(self, case_id, start=0, chars=5000, snapshot_id=None):
        if snapshot_id is not None and snapshot_id != self.version:
            raise CaseSourceError("source_version")
        if type(start) is not int or type(chars) is not int or start < 0 or chars < 1:
            raise CaseSourceError("source_range")
        doc = self.docs.get(case_id)
        if doc is None:
            return {
                "status": "not_found",
                "case_id": case_id,
                "snapshot_id": self.version,
            }
        if start > len(doc["text"]):
            raise CaseSourceError("source_range")
        end = min(start + chars, len(doc["text"]))
        return {
            "status": "ok",
            "source_type": "local_case_material",
            "case_id": case_id,
            "title": doc["title"],
            "snapshot_id": self.version,
            "text": doc["text"][start:end],
            "start": start,
            "end": end,
            "next_start": end if end < len(doc["text"]) else None,
            "total_chars": len(doc["text"]),
            "full_document_returned": start == 0 and end == len(doc["text"]),
            "reference_id": f"{case_id}@{self.version}:{start}-{end}",
            "source_sha256": doc["source_sha256"],
            "supported_identity": doc["identity"]["supported_fields"],
            "law_effect": "not_verified",
        }
