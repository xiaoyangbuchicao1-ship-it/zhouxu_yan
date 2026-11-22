"""Verify browser-downloaded paper files; run with the Linux API image."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def verify(directory):
    pdf, docx = directory / "paper.pdf", directory / "paper.docx"
    assert pdf.read_bytes().startswith(b"%PDF-"), "invalid PDF signature"
    info = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True).stdout
    pages = int(re.search(r"^Pages:\s+(\d+)", info, re.M)[1])
    assert pages > 1, "paper PDF has no substantive pages"
    pdf_text = subprocess.run(["pdftotext", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(docx) as package:
        assert package.testzip() is None, "corrupt DOCX archive"
        document = ET.fromstring(package.read("word/document.xml"))
        footnotes = ET.fromstring(package.read("word/footnotes.xml"))
        relationships = ET.fromstring(package.read("word/_rels/document.xml.rels"))
        assert any(r.attrib["Type"].endswith("/footnotes") for r in relationships)
        attr = "{" + ns["w"] + "}id"
        refs = [ref.attrib[attr] for ref in document.findall(".//w:footnoteReference", ns)]
        note_ids = {note.attrib[attr] for note in footnotes.findall("w:footnote", ns)}
        assert len(refs) >= 12 and len(refs) == len(set(refs)), "missing or duplicate native footnote references"
        assert set(refs) <= note_ids, "unresolved native footnotes"
        text = "".join(node.text or "" for node in document.findall(".//w:t", ns))
        for phrase in ("迟延交付", "约定", "合理", "交易可预见性"):
            assert phrase in text and phrase in pdf_text, f"missing required topic: {phrase}"
        # An append replay must not duplicate a whole chapter/paragraph.
        paragraphs = ["".join(t.text or "" for t in p.findall(".//w:t", ns))
                      for p in document.findall(".//w:p", ns)]
        substantial = [p for p in paragraphs if len(p) > 100]
        assert len(substantial) == len(set(substantial)), "duplicate substantive paragraphs"
    return {"pages": pages, "native_footnotes": len(refs), "duplicate_paragraphs": 0,
            "files": [{"name": p.name, "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (pdf, docx)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify(parser.parse_args().directory), ensure_ascii=False))
