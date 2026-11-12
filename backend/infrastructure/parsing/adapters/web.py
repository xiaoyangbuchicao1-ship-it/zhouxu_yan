from __future__ import annotations

import hashlib
import re

import httpx

from backend.config.settings import MaterialSettings
from backend.contracts.modules.materials import (
    MaterialInput,
    MaterialLocator,
    MaterialManifest,
    MaterialUnit,
    ParsedMaterial,
)
from backend.infrastructure.parsing.adapters.common import (
    local_path,
    material_id,
    material_name,
    unit_id,
)


class WebParser:
    name = "httpx-beautifulsoup"

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        return material.uri.lower().startswith(("http://", "https://")) or local_path(
            material
        ).suffix.lower() in {".html", ".htm"}

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        try:
            from bs4 import BeautifulSoup

            remote = material.uri.lower().startswith(("http://", "https://"))
            with httpx.Client(
                timeout=self.settings.web_timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": self.settings.web_user_agent},
                trust_env=False,
            ) as client:
                if remote:
                    with client.stream("GET", material.uri) as response:
                        response.raise_for_status()
                        parts, used = [], 0
                        clipped = False
                        for part in response.iter_bytes():
                            remaining = self.settings.web_max_bytes - used
                            parts.append(part[:remaining])
                            used += min(len(part), remaining)
                            if len(part) > remaining:
                                clipped = True
                                break
                        body = b"".join(parts)
                    status_code = response.status_code
                    media_type = response.headers.get(
                        "content-type", "text/html"
                    ).split(";", 1)[0]
                else:
                    body = local_path(material).read_bytes()
                    clipped, status_code, media_type = False, 200, "text/html"
            soup = BeautifulSoup(body, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            title = (
                soup.title.get_text(" ", strip=True)
                if soup.title
                else material_name(material)
            )
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            unit = MaterialUnit(
                unit_id=unit_id(key, "link", material.uri),
                material_id=key,
                kind="link",
                locator=MaterialLocator(kind="link", value=material.uri, label=title),
                text=text,
                metadata={"title": title, "status_code": status_code},
            )
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=title,
                    uri=material.uri,
                    media_type=media_type,
                    status="available" if text and not clipped else "partial",
                    parser=self.name,
                    summary=text[: self.settings.preview_chars],
                    unit_count=1,
                    available_units=[unit.unit_id],
                    errors=[] if text else ["web_page_has_no_extractable_text"],
                    content_fingerprint=hashlib.sha256(body).hexdigest(),
                    missing_ranges=["web_response_beyond_resource_limit"]
                    if clipped
                    else [],
                ),
                units=[unit],
            )
        except Exception as exc:
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type="text/html",
                    status="failed",
                    parser=self.name,
                    errors=["web_parse_failed:" + type(exc).__name__],
                )
            )
