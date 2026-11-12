from __future__ import annotations

import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from backend.config.settings import MaterialSettings
from backend.contracts.modules.materials import (
    MaterialInput,
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


class DocxParser:
    name = "python-docx"

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        return (
            infer_media_type(material)
            in {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            or local_path(material).suffix.lower() == ".docx"
        )

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        try:
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            path = local_path(material)
            document = Document(str(path))
            from backend.infrastructure.parsing.adapters.word_numbering import (
                WordNumbering,
            )
            numbering = WordNumbering(document)
            revision_markers = _revision_markers(path)
            units: list[MaterialUnit] = []
            complex_containers = []
            paragraph_index = table_index = 0
            for child in document.element.body.iterchildren():
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    paragraph_index += 1
                    paragraph = Paragraph(child, document)
                    units.append(
                        MaterialUnit(
                            unit_id=unit_id(key, "paragraph", str(paragraph_index)),
                            material_id=key,
                            kind="paragraph",
                            locator=MaterialLocator(
                                kind="paragraph",
                                value=str(paragraph_index),
                                label=f"第{paragraph_index}段",
                            ),
                            text=(numbering.prefix(paragraph) + paragraph.text) if paragraph.text.strip() else "",
                            metadata={
                                "style": paragraph.style.name
                                if paragraph.style
                                else None,
                                "note_refs": [
                                    dict(node.attrib)
                                    for node in child.iter()
                                    if node.tag.rsplit("}", 1)[-1]
                                    in {"footnoteReference", "endnoteReference"}
                                ],
                            },
                        )
                    )
                elif tag == "tbl":
                    table_index += 1
                    table = Table(child, document)
                    matrix = [[cell.text for cell in row.cells] for row in table.rows]
                    confirmed = [
                        matrix[i]
                        for i, row in enumerate(table.rows)
                        if row._tr.xpath("./w:trPr/w:tblHeader")
                    ]
                    header = "\n".join("\t".join(row) for row in confirmed)
                    step = self.settings.table_rows_per_block
                    for start in range(0, len(matrix), step):
                        rows = matrix[start : start + step]
                        label = (
                            f"表格{table_index}，第{start + 1}–{start + len(rows)}行"
                        )
                        units.append(
                            MaterialUnit(
                                unit_id=unit_id(key, "table", label),
                                material_id=key,
                                kind="table",
                                locator=MaterialLocator(
                                    kind="table",
                                    value=f"{table_index}:{start + 1}",
                                    label=label,
                                    row_start=start + 1,
                                    row_end=start + len(rows),
                                ),
                                text=(header + "\n" if start and header else "")
                                + "\n".join("\t".join(row) for row in rows),
                                structured={
                                    "rows": rows,
                                    "confirmed_headers": confirmed,
                                },
                                metadata={
                                    "table": table_index,
                                    "confirmed_header_text": header,
                                },
                            )
                        )
                elif tag != "sectPr" and any(
                    node.tag.rsplit("}", 1)[-1] in {"t", "drawing", "object", "altChunk"}
                    for node in child.iter()
                ):
                    complex_containers.append(tag)
            # Notes have reliable part/ID locations, not invented rendered pages.
            with ZipFile(path) as archive:
                for member in archive.namelist():
                    if not re.fullmatch(
                        r"word/(footnotes|endnotes|comments|header\d+|footer\d+)\.xml",
                        member,
                    ):
                        continue
                    part = Path(member).stem
                    xml = ElementTree.fromstring(archive.read(member))
                    for index, node in enumerate(xml, 1):
                        paragraphs = (
                            [node]
                            if node.tag.endswith("}p")
                            else [p for p in node.iter() if p.tag.endswith("}p")]
                        )
                        text = "\n".join("".join(p.itertext()) for p in paragraphs)
                        if text:
                            units.append(
                                MaterialUnit(
                                    unit_id=unit_id(
                                        key, "paragraph", f"{part}:{index}"
                                    ),
                                    material_id=key,
                                    kind="paragraph",
                                    locator=MaterialLocator(
                                        kind="paragraph",
                                        value=f"{part}:{index}",
                                        label=f"{part} 附注{index}",
                                    ),
                                    text=text,
                                    metadata={
                                        "part": part,
                                        "attributes": dict(node.attrib),
                                    },
                                )
                            )
                for member in archive.namelist():
                    if not member.startswith("word/media/") or member.endswith("/"):
                        continue
                    payload = archive.read(member)
                    asset = (
                        "images/"
                        + hashlib.sha256(payload).hexdigest()
                        + Path(member).suffix.lower()
                    )
                    if material.metadata.get("_artifact_root"):
                        target = Path(material.metadata["_artifact_root"]) / asset
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                        units.append(
                            MaterialUnit(
                                unit_id=unit_id(key, "image", member),
                                material_id=key,
                                kind="image",
                                locator=MaterialLocator(
                                    kind="image",
                                    value=member,
                                    label=f"内嵌资源 {Path(member).name}",
                                ),
                                resources=[asset],
                                metadata={
                                    "source_part": member,
                                    "anchor_status": "unresolved",
                                },
                            )
                        )
            summary = "\n".join(unit.text for unit in units)[
                : self.settings.preview_chars
            ]
            errors = ["docx_revision_state_not_resolved"] if revision_markers else []
            if document.inline_shapes or any(unit.resources for unit in units):
                errors.append("embedded_visual_content_requires_on_demand_read")
            if complex_containers:
                errors.append("docx_complex_body_containers_not_fully_extracted")
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="available" if units and not errors else "partial",
                    parser=self.name,
                    summary=summary,
                    unit_count=len(units),
                    available_units=[unit.unit_id for unit in units],
                    errors=errors if units else ["docx_has_no_extractable_content"],
                    metadata={
                        "embedded_images": len(document.inline_shapes),
                        "revision_markers": revision_markers,
                        "unresolved_body_containers": complex_containers,
                    },
                ),
                units=units,
            )
        except Exception as exc:
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="failed",
                    parser=self.name,
                    errors=["parse_failed:" + type(exc).__name__],
                )
            )


def _revision_markers(path) -> list[str]:
    markers: list[str] = []
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            if "<w:ins" in xml or "<w:del" in xml:
                markers.append("tracked_changes")
            if "word/comments.xml" in names:
                markers.append("comments")
    except Exception:
        markers.append("revision_metadata_unreadable")
    return markers
