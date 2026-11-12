from __future__ import annotations

import hashlib
import threading
from importlib.metadata import PackageNotFoundError, version

from backend.config.settings import MaterialSettings
from backend.contracts.modules.materials import (
    MaterialInput,
    MaterialManifest,
    ParsedMaterial,
)
from backend.contracts.schemas.core import Error, PortError
from backend.infrastructure.parsing.adapters.common import local_path
from backend.infrastructure.parsing.adapters.docx import DocxParser
from backend.infrastructure.parsing.adapters.image import ImageParser
from backend.infrastructure.parsing.adapters.pdf import PdfParser
from backend.infrastructure.parsing.adapters.spreadsheet import SpreadsheetParser
from backend.infrastructure.parsing.adapters.text import TextParser
from backend.infrastructure.parsing.adapters.web import WebParser
from backend.infrastructure.parsing.normalization import PARSER_SCHEMA, normalize
from backend.infrastructure.parsing.parse_cache import ParseCache

_artifact_lock = threading.RLock()


class MaterialParserRegistry:
    def __init__(self, settings: MaterialSettings):
        self.settings = settings
        self.cache = ParseCache(settings.cache_root)
        self.parsers = [
            WebParser(settings),
            PdfParser(settings),
            DocxParser(settings),
            SpreadsheetParser(settings),
            ImageParser(settings),
            TextParser(settings),
        ]

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        for parser in self.parsers:
            if parser.can_handle(material):
                return self._parse(parser, material)
        raise PortError(
            Error(
                code="material_type_unsupported",
                message="暂不支持此材料类型。",
                retryable=False,
            )
        )

    def _parse(self, parser, material):
        # One process shares this lock across preview and Run registries. It
        # prevents duplicate OCR on simultaneous misses without storing Run state.
        with _artifact_lock:
            return self._parse_locked(parser, material)

    def _parse_locked(self, parser, material):
        remote = material.uri.lower().startswith(("http://", "https://"))
        if remote:
            fingerprint = ""
        else:
            try:
                path = local_path(material)
                if path.stat().st_size > self.settings.max_file_bytes:
                    raise PortError(
                        Error(
                            code="material_resource_limit",
                            message="材料超过配置的解析文件资源上限。",
                        )
                    )
                fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                raise PortError(
                    Error(
                        code="material_source_unavailable",
                        message="材料原件当前不可用。",
                    )
                ) from None
        packages = {}
        for name in (
            "python-docx",
            "openpyxl",
            "pypdf",
            "Pillow",
            "pytesseract",
            "beautifulsoup4",
            "mineru",
            "docvortex",
        ):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = "unavailable"
        config = {
            "schema": PARSER_SCHEMA,
            "parser": parser.name,
            "packages": packages,
            "ocr": self.settings.ocr_enabled,
            "language": self.settings.ocr_language,
            "table_rows": self.settings.table_rows_per_block,
            "mineru": self.settings.mineru_enabled,
            "mineru_version": self.settings.mineru_version,
            "tier": self.settings.mineru_tier,
            "runtime_ready": self.settings.mineru_runtime_ready,
            "mineru_python": self.settings.mineru_python,
            "mineru_runtime_revision": self.settings.mineru_runtime_revision,
            "ocr_runtime_revision": self.settings.ocr_runtime_revision,
            "preview_chars": self.settings.preview_chars,
            "adapter_code": hashlib.sha256(
                b"".join(
                    path.read_bytes()
                    for path in sorted(
                        __import__("pathlib")
                        .Path(__file__)
                        .parent.glob("adapters/*.py")
                    )
                )
            ).hexdigest(),
        }
        key = self.cache.key(fingerprint, config)
        reusable = not remote and not (
            parser.name.startswith("pillow")
            and self.settings.ocr_enabled
            and not self.settings.ocr_runtime_revision
        )
        cached = self.cache.load(key) if reusable else None
        if cached is not None:
            return normalize(
                cached,
                material,
                fingerprint=fingerprint,
                version=key,
                engine_config=config,
            )
        selected = material.model_copy(deep=True)
        selected.metadata["_artifact_root"] = str(self.cache.artifact_root(key))
        required_package = {
            "python-docx": "python-docx",
            "spreadsheet": "openpyxl",
            "pdf-pypdf-baseline": "pypdf",
            "pillow-optional-ocr": "Pillow",
            "httpx-beautifulsoup": "beautifulsoup4",
        }.get(parser.name)
        if required_package and packages[required_package] == "unavailable":
            from backend.infrastructure.parsing.adapters.common import (
                infer_media_type,
                material_id,
                material_name,
            )

            parsed = ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=material_id(material),
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="unavailable",
                    parser=parser.name,
                    errors=["parser_dependency_unavailable:" + required_package],
                    missing_ranges=["格式解析依赖未安装，正文尚未解析"],
                )
            )
        else:
            parsed = parser.parse(selected)
        if (
            self.settings.mineru_enabled
            and (parser.name.startswith("pdf") or parser.name.startswith("pillow"))
            and parsed.manifest.status != "available"
        ):
            from backend.infrastructure.parsing.adapters.mineru import MinerUAdapter

            fallback = MinerUAdapter(self.settings).parse(selected)
            if fallback.units:
                parsed = fallback
            else:
                parsed.manifest.errors.extend(fallback.manifest.errors)
                parsed.manifest.missing_ranges.extend(fallback.manifest.missing_ranges)
        if remote:
            # URL snapshots are fetched once per ingest. A URL alone is NOT a
            # content fingerprint or a freshness guarantee.
            fingerprint = (
                parsed.manifest.content_fingerprint
                or hashlib.sha256(
                    "".join(u.text for u in parsed.units).encode()
                ).hexdigest()
            )
            key = self.cache.key(fingerprint, config)
        parsed = normalize(
            parsed, material, fingerprint=fingerprint, version=key, engine_config=config
        )
        self.cache.save(key, parsed)
        return parsed
