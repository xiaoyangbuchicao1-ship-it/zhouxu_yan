"""Immutable derived artifacts; authorization always precedes this internal cache."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.contracts.modules.materials import ParsedMaterial
from backend.infrastructure.parsing.document_view import write_document_view


class ParseCache:
    def __init__(self, root):
        root = Path(root)
        self.root = (
            root if root.is_absolute() else Path(__file__).resolve().parents[3] / root
        )

    @staticmethod
    def key(fingerprint, engine_config):
        return hashlib.sha256(
            (fingerprint + json.dumps(engine_config, sort_keys=True)).encode()
        ).hexdigest()

    def artifact_root(self, key):
        return self.root / key

    def load(self, key):
        path = self.artifact_root(key) / "material.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            value["manifest"]["uri"] = ""
            parsed = ParsedMaterial.model_validate(value)
            write_document_view(path.parent, parsed)
            return parsed
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key, parsed):
        # Failures/capability gaps must be retryable after environment repair.
        if parsed.manifest.status in {"failed", "unavailable"}:
            return
        root = self.artifact_root(key)
        root.mkdir(parents=True, exist_ok=True)
        write_document_view(root, parsed)
        path = root / "material.json"
        temporary = root / (uuid4().hex + ".tmp")
        try:
            temporary.write_text(parsed.model_dump_json(), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
