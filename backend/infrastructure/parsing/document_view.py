"""Readable derivative of canonical parser units, with stable source positions."""

import hashlib
import json
import os
from uuid import uuid4

VIEW_SCHEMA = "reading-view-1"


def write_document_view(root, parsed):
    pieces, ranges, offset, line = [], [], 0, 1
    for unit in parsed.units:
        if not unit.text and unit.kind != "image" and not unit.resources:
            continue
        text = (unit.text or f"[{unit.kind}]").replace("\r\n", "\n").replace("\r", "\n")
        ranges.append({
            "order": unit.order,
            "char_start": offset, "char_end": offset + len(text),
            "line_start": line, "line_end": line + text.count("\n"),
            "kind": unit.kind, "locator": unit.locator.model_dump(exclude_none=True),
            "source_char_end": len(unit.text),
        })
        pieces.extend([text, "\n\n"])
        offset += len(text) + 2
        line += text.count("\n") + 2
    text = "".join(pieces)
    mapping = {
        "schema": VIEW_SCHEMA, "parse_version": parsed.manifest.parse_version,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "ranges": ranges,
    }
    root.mkdir(parents=True, exist_ok=True)
    for name, body in {
        "reading.md": text,
        "reading-map.json": json.dumps(mapping, ensure_ascii=False, separators=(",", ":")),
    }.items():
        path = root / name
        if path.is_file() and path.read_text(encoding="utf-8") == body:
            continue
        temporary = root / (uuid4().hex + ".tmp")
        try:
            temporary.write_text(body, encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
