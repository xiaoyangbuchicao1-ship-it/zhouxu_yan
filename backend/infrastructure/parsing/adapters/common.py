from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from backend.contracts.modules.materials import MaterialInput


def infer_media_type(material: MaterialInput) -> str:
    if material.media_type:
        return material.media_type.lower().split(";", 1)[0]
    uri = material.uri.lower().split("?", 1)[0]
    if uri.startswith(("http://", "https://")):
        return "text/html"
    guessed, _ = mimetypes.guess_type(uri)
    return guessed or "application/octet-stream"


def material_name(material: MaterialInput) -> str:
    if material.name:
        return material.name
    return Path(material.uri.split("?", 1)[0]).name or material.uri


def material_id(material: MaterialInput) -> str:
    if material.material_id:
        return material.material_id
    digest = hashlib.sha256(material.uri.encode("utf-8")).hexdigest()[:16]
    return f"material-{digest}"


def unit_id(material_key: str, kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{material_key}:{kind}:{value}".encode()).hexdigest()[:16]
    return f"{material_key}:{kind}:{digest}"


def local_path(material: MaterialInput) -> Path:
    uri = material.uri
    if uri.startswith("file://"):
        uri = uri[7:]
    return Path(uri)
