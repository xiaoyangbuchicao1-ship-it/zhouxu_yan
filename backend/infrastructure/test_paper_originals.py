"""A paper card must identify and serve its verified original, never a guessed file."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from backend.config.settings import load_settings
from backend.infrastructure.sources import CorpusSourceRepository


class PaperOriginalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "opensearch/import/information_cards"
        self.release.mkdir(parents=True)
        self.originals = self.root / "originals"
        (self.originals / "PDF").mkdir(parents=True)
        (self.originals / "md").mkdir()
        self.pdf = self.originals / "PDF/paper.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=400)
        writer.add_blank_page(width=300, height=400)
        writer.write(self.pdf)
        self.body = "第一段原文。\n\n这段在论文第二页，不能跳到另一篇论文。"
        (self.originals / "md/body.txt").write_text(self.body, encoding="utf-8")
        (self.originals / "md/paper.md").write_text(self.body, encoding="utf-8")
        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        self.paper = {
            "document_id": "pdf-example", "title": "同名论文", "authors_hint": "甲",
            "source_path": "md/paper.md", "source_hash": digest(self.originals / "md/paper.md"),
            "formal_pdf_path": "PDF/paper.pdf", "pdf_sha256": digest(self.pdf),
            "pdf_bytes": self.pdf.stat().st_size, "page_count": 2,
            "text_path": "md/body.txt", "text_sha256": digest(self.originals / "md/body.txt"),
        }
        self.card = {
            "card_id": "info-pdf-example-span", "source_document_id": "pdf-example",
            "source_title": "同名论文", "source_authors": "甲",
            "source_sha256": self.paper["source_hash"],
            "source_locator": {"page": 2, "page_end": 2, "page_kind": "physical", "char_start": 8, "char_end": len(self.body), "char_offset_basis": "md/body.txt"},
            "claim_or_fact": self.body[8:], "retrieval_text": "题目\n" + self.body[8:],
        }
        self.write_release()
        settings = load_settings().model_copy(deep=True)
        settings.research_cards.originals_root = str(self.originals)
        self.repository = CorpusSourceRepository(settings)
        self.repository.root = self.root / "opensearch/corpus"

    def write_release(self):
        for name, value in (("cards.jsonl", self.card), ("papers.jsonl", self.paper)):
            (self.release / name).write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        cards = self.release / "cards.jsonl"
        (self.release / "cards.offsets.json").write_text(
            json.dumps({"schema_version": 1, "cards_size": cards.stat().st_size,
                        "offsets": {self.card["card_id"]: 0}}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_prebuilt_card_offset_index_is_used(self):
        cards = self.release / "cards.jsonl"
        cards.write_text(json.dumps(self.card, ensure_ascii=False) + "\n{broken\n", encoding="utf-8")
        offset = 0
        (self.release / "cards.offsets.json").write_text(
            json.dumps({"schema_version": 1, "cards_size": cards.stat().st_size,
                        "offsets": {self.card["card_id"]: offset}}, ensure_ascii=False),
            encoding="utf-8",
        )
        source = self.repository.get("paper-" + self.card["card_id"])
        self.assertEqual(source.text, self.body[8:])

    def test_stale_card_offset_index_fails_loudly(self):
        index = self.release / "cards.offsets.json"
        index.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "paper_card_offset_index_stale"):
            self.repository.get("paper-" + self.card["card_id"])

    def test_card_and_document_open_the_same_pdf_and_real_page(self):
        source = self.repository.get("paper-" + self.card["card_id"])
        self.assertEqual(source.text, self.body[8:])
        self.assertEqual(source.original.page, 2)
        self.assertEqual(source.original.sha256, self.paper["pdf_sha256"])
        self.assertNotIn(str(self.originals), source.model_dump_json())
        original = self.repository.paper_file(source.source_id, source.original.sha256)
        # Windows may expose the same temporary directory through two mapped
        # roots (for example C:\\Users and D:\\WpSystem). Compare resolved
        # paths so the assertion checks the actual file rather than the alias.
        self.assertEqual(original.path.resolve(), self.pdf.resolve())
        document = self.repository.get("paper-document-pdf-example")
        self.assertEqual(document.original.sha256, source.original.sha256)
        self.assertIsNone(document.original.page)

    def test_changed_pdf_and_wrong_requested_version_fail(self):
        with self.assertRaisesRegex(ValueError, "paper_version_mismatch"):
            self.repository.paper_file("paper-" + self.card["card_id"], "0" * 64)
        self.pdf.write_bytes(self.pdf.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "paper_original_hash_mismatch"):
            self.repository.get("paper-" + self.card["card_id"])

    def test_bad_span_is_not_served_as_success(self):
        self.card["source_locator"]["char_start"] = 1
        self.write_release()
        with self.assertRaisesRegex(ValueError, "paper_text_locator_mismatch"):
            self.repository.get("paper-" + self.card["card_id"])

    def test_path_escape_is_not_served_as_success(self):
        self.paper["formal_pdf_path"] = "../outside.pdf"
        self.write_release()
        with self.assertRaisesRegex(ValueError, "paper_original_path_invalid"):
            self.repository.get("paper-" + self.card["card_id"])


if __name__ == "__main__":
    unittest.main()
