# ruff: noqa: E402
# Executed only by the isolated MinerU interpreter; never imported at backend startup.
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
"""MinerU 4.1.4 local SDK adapter; no Doclib, HTTP fallback or guessed CLI JSON."""

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from backend.contracts.modules.materials import (
    MaterialLocator,
    MaterialManifest,
    MaterialUnit,
    ParsedMaterial,
)
from backend.infrastructure.parsing.adapters.common import (
    infer_media_type,
    local_path,
    material_id,
    material_name,
    unit_id,
)


class LocalSDKParser:
    name = "mineru-sdk-4.1.4"

    def __init__(self, settings):
        self.settings = settings

    def parse(self, material):
        manifest = MaterialManifest(
            material_id=material_id(material),
            name=material_name(material),
            uri=material.uri,
            media_type=infer_media_type(material),
            parser=self.name,
            status="unavailable",
        )
        try:
            installed = version("mineru")
        except PackageNotFoundError:
            manifest.errors = ["mineru_dependency_unavailable"]
            manifest.missing_ranges = ["视觉解析能力未安装"]
            return ParsedMaterial(manifest=manifest)
        if (
            installed != self.settings.mineru_version
            or installed != "4.1.4"
            or version("docvortex") != "0.6.11"
        ):
            manifest.errors = ["mineru_sdk_version_not_verified"]
            return ParsedMaterial(manifest=manifest)
        if not self.settings.mineru_runtime_ready:
            manifest.errors = ["mineru_local_runtime_not_prepared_no_inference_started"]
            manifest.missing_ranges = ["视觉解析运行环境或本地权重尚未确认就绪"]
            return ParsedMaterial(manifest=manifest)
        try:
            from mineru.config import VlmConfig
            from mineru.parser import ParseResult, parse

            # Explicit local engine, blank remote connection. No remote client.
            result = parse(
                local_path(material),
                tier=self.settings.mineru_tier,
                ocr_mode="auto",
                page_range="",
                vlm_config=VlmConfig(engine="llama-cpp", server_url="", api_key=""),
            )
            root = Path(material.metadata["_artifact_root"]).resolve()

            class SafeWriter:
                def write(self, path, data):
                    target = (root / path).resolve()
                    if root not in target.parents:
                        raise ValueError("mineru_asset_path_invalid")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)

                def write_string(self, path, text):
                    self.write(path, text.encode("utf-8"))

            result.save(SafeWriter())
            # SDK save materializes resource bytes and updates references. Use
            # that exported intermediate, not the CLI command-status envelope.
            exported = ParseResult.from_json(
                (root / "middle_json.json").read_text(encoding="utf-8")
            )
            structured = exported.structured_content()
            middle = exported.to_dict()
            units = []
            for page, raw_page in zip(
                structured["pages"], middle["pages"], strict=True
            ):
                page_no = page["page_idx"] + 1  # confirmed original zero-based page_idx
                for index, (block, raw) in enumerate(
                    zip(page["blocks"], raw_page.get("blocks", []), strict=True)
                ):
                    kind = (
                        "table"
                        if block["type"] == "table"
                        else "image"
                        if block["type"] in {"image", "chart"}
                        else "text"
                    )
                    pieces = [block.get("content", "")]
                    for group in ("captions", "footnotes"):
                        pieces.extend(
                            note.get("content", "") for note in block.get(group, [])
                        )
                    units.append(
                        MaterialUnit(
                            unit_id=unit_id(
                                manifest.material_id, "page", f"{page_no}:{index}"
                            ),
                            material_id=manifest.material_id,
                            kind=kind,
                            locator=MaterialLocator(
                                kind="page",
                                value=f"{page_no}:{index}",
                                page=page_no,
                                label=f"第{page_no}页，第{index + 1}块",
                                bbox=block.get("bbox"),
                            ),
                            text="\n".join(p for p in pieces if p),
                            structured=raw,
                            resources=[block["image_source"]]
                            if block.get("image_source")
                            else [],
                        )
                    )
            manifest.status = "available" if middle["is_full_document"] else "partial"
            manifest.complete = bool(middle["is_full_document"])
            manifest.metadata = {
                "producer": middle["metadata"]["producer"],
                "schema": middle.get("schema"),
                "schema_version": middle["schema_version"],
                "tier": self.settings.mineru_tier,
                "bbox_units": "normalized_0_to_1",
            }
            manifest.resources = [
                "middle_json.json",
                "structured_content.json",
                "markdown.md",
            ]
            if not manifest.complete:
                manifest.missing_ranges = [
                    "SDK reports partial document; no invented coverage"
                ]
            return ParsedMaterial(manifest=manifest, units=units)
        except Exception as exc:
            manifest.status = "failed"
            manifest.errors = ["mineru_local_parse_failed:" + type(exc).__name__]
            return ParsedMaterial(manifest=manifest)


if __name__ == "__main__":
    from types import SimpleNamespace

    from backend.contracts.modules.materials import MaterialInput

    packet = json.loads(sys.stdin.read())
    value = MaterialInput.model_validate(packet["material"])
    parsed = LocalSDKParser(SimpleNamespace(**packet["settings"])).parse(value)
    # SDK logs go to stderr; the final protocol is an explicit output artifact.
    output = Path(packet["output"])
    output.write_text(parsed.model_dump_json(), encoding="utf-8")
