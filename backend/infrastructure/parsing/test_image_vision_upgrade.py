"""Golden tests for Scheme B image recognition & multimodal vision upgrades."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.materials import MaterialInput, MaterialReadInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem, ContextLink
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.database.migrate_vision_v1 import migrate as migrate_vision_v1
from backend.infrastructure.model.inference import infer_model_defaults
from backend.infrastructure.model.protocols import OpenAIResponsesModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.adapters.image import ImageParser
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.parsing.vision_assist import encode_image_for_transport


def _make_sample_png(path: Path, *, size=(600, 300), mode="RGBA") -> None:
    color = (255, 255, 255, 0) if mode == "RGBA" else (255, 255, 255)
    img = Image.new(mode, size, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, size[0] - 20, size[1] - 20], outline=(30, 30, 30), width=3)
    draw.text((40, 60), "ESP32-S3 MAIN MCU U9", fill=(0, 0, 0))
    img.save(path, format="PNG")


class ImageVisionUpgradeTests(unittest.TestCase):
    def test_openai_responses_encodes_image_url_as_input_image(self):
        settings = load_settings()
        settings.model.provider = "relay"
        settings.model.name = "gpt-5.6"
        settings.model.protocol = "openai_responses"
        settings.model.endpoint = "https://relay.example/v1/responses"
        settings.model.allowed_hosts = ["relay.example"]
        settings.model.thinking_mode = "none"
        settings.model.relay_effort_supported = False
        settings.model_capabilities["gpt-5.6"] = ModelCapabilities(
            provider="relay",
            model_id="gpt-5.6",
            version_or_alias="gpt-5.6",
            raw_context_tokens=400_000,
            max_generation_tokens=16_384,
            generation_includes_thinking=True,
            image_transport="chat_user_data_url",
            image_token_upper_bound=2048,
            counting_method="utf8_upper_bound_estimate",
            source=["test"],
            checked_on="2026-11-27",
            notes="test",
        )
        model = OpenAIResponsesModel(settings, SecretStr("test-key"), MemoryEventSink())
        items = [
            ContextItem(
                id="current-user",
                type="message",
                source="user",
                role="user",
                body={"text": "这张图是什么？"},
            ),
            ContextItem(
                id="tool-1",
                type="tool_call",
                source="reasoning",
                role="assistant",
                origin_run_id="r1",
                body={
                    "calls": [
                        {
                            "action_id": "call_img_1",
                            "tool_name": "read_material",
                            "arguments": {"material_id": "m1", "include_image": True},
                        }
                    ]
                },
            ),
            ContextItem(
                id="res-1",
                type="tool_result",
                source="action",
                role="tool",
                origin_run_id="r1",
                links=[ContextLink(relation="responds_to", target_id="tool-1")],
                body={
                    "action_id": "call_img_1",
                    "tool_name": "read_material",
                    "outcome": "ok",
                    "result": {
                        "material_id": "m1",
                        "name": "schematic.png",
                        "image_data_url": "data:image/png;base64,iVBORw0KGgo=",
                    },
                },
            ),
        ]
        req = ModuleRequest(
            run_id="r1",
            call_id="c1",
            input=ReasoningInput(
                perceived=PerceivedContext(run_id="r1", items=items, assembly=ContextAssembly()),
                tools=[],
            ),
        )
        packet = model._payload(req)
        image_parts = [
            part
            for entry in packet["input"]
            if isinstance(entry, dict) and isinstance(entry.get("content"), list)
            for part in entry["content"]
            if isinstance(part, dict) and part.get("type") == "input_image"
        ]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["image_url"], "data:image/png;base64,iVBORw0KGgo=")
        _, estimate = model.budget.check(packet, purpose="main")
        self.assertEqual(estimate.image_count, 1)

    def test_image_parser_integrates_vision_assist_and_falls_back_cleanly(self):
        settings = load_settings()
        with tempfile.TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "schematic.png"
            _make_sample_png(img_path, mode="RGBA")
            settings.materials.ocr_enabled = False
            settings.materials.vision_assist_enabled = True

            def fake_vision(path: Path, media_type: str, fingerprint: str):
                self.assertEqual(media_type, "image/png")
                self.assertTrue(len(fingerprint) == 64)
                return (
                    "ESP32-S3 主控电路原理图：分区1为主控U9，分区6为2.4G天线ANT-2.4G-PCB。",
                    "vision_assist_ok:deepseek-flash",
                )

            parser = ImageParser(settings.materials, vision_describer=fake_vision)
            parsed = parser.parse(
                MaterialInput(uri=str(img_path), material_id="m-img", name="schematic.png", media_type="image/png")
            )
            self.assertIn("ESP32-S3 主控电路原理图", parsed.manifest.summary)
            self.assertEqual(parsed.units[0].locator.label, "视觉内容解析（AI 看图）")
            self.assertEqual(parsed.manifest.metadata["vision_assist_status"], "vision_assist_ok:deepseek-flash")

            # Honest fallback when vision assist fails
            failing_parser = ImageParser(
                settings.materials,
                vision_describer=lambda *_: ("", "vision_assist_failed:TimeoutException"),
            )
            fallback = failing_parser.parse(
                MaterialInput(uri=str(img_path), material_id="m-img2", name="schematic.png", media_type="image/png")
            )
            self.assertEqual(fallback.manifest.metadata["vision_assist_status"], "vision_assist_failed:TimeoutException")
            self.assertEqual(fallback.units[0].locator.label, "原始图片")

    def test_catalog_auto_resizes_oversized_image_and_emits_image_read_hint(self):
        settings = load_settings()
        with tempfile.TemporaryDirectory() as tmp:
            settings.materials.cache_root = str(Path(tmp) / "cache")
            settings.materials.ocr_enabled = False
            settings.materials.vision_assist_enabled = False
            settings.materials.max_inline_image_bytes = 90_000

            # Large high-res PNG larger than 90 KB that compresses cleanly
            big_path = Path(tmp) / "large_diagram.png"
            img = Image.effect_noise((1400, 1000), 25).convert("RGB")
            img.save(big_path, format="PNG")
            self.assertGreater(big_path.stat().st_size, 90_000)

            encoded = encode_image_for_transport(
                big_path, "image/png", max_bytes=90_000, max_edge=2048
            )
            self.assertIsNotNone(encoded)

            catalog = MaterialCatalog(settings)
            parsed = catalog.registry.parse(
                MaterialInput(uri=str(big_path), material_id="m-big", name="large_diagram.png", media_type="image/png")
            )
            catalog.bind(parsed, run_id="run-1")

            candidates = catalog.candidates("run-1")
            self.assertEqual(len(candidates), 1)
            self.assertIn("include_image=true", candidates[0].body["read_hint"])

            out = catalog.read(
                MaterialReadInput(material_id="m-big", include_image=True),
                run_id="run-1",
                limit=4000,
                image_allowed=True,
            )
            self.assertIsNotNone(out.image_data_url)
            self.assertTrue(out.image_data_url.startswith("data:image/"))

    def test_vision_inference_and_idempotent_db_migration(self):
        self.assertTrue(infer_model_defaults("deepseek-flash").supports_vision)
        self.assertFalse(infer_model_defaults("deepseek-v4-pro").supports_vision)
        self.assertTrue(infer_model_defaults("gpt-5.6").supports_vision)
        self.assertTrue(infer_model_defaults("claude-sonnet-4-6").supports_vision)
        self.assertTrue(infer_model_defaults("gemini-4-argon").supports_vision)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE model_provider_revisions (id TEXT PRIMARY KEY, provider_id TEXT, revision INTEGER);
                CREATE TABLE model_profiles (
                    profile_id TEXT PRIMARY KEY, provider_id TEXT, upstream_model_id TEXT,
                    label TEXT, family TEXT, protocol_override TEXT, context_tokens INTEGER,
                    max_output_tokens INTEGER, thinking_modes_json TEXT, default_thinking TEXT,
                    capabilities_json TEXT, access_scope TEXT, verification_json TEXT,
                    current_revision INTEGER, source TEXT, checked_on TEXT, updated_at TEXT, archived_at TEXT
                );
                CREATE TABLE model_profile_revisions (
                    id TEXT PRIMARY KEY, profile_id TEXT, provider_revision_id TEXT,
                    revision INTEGER, facts_json TEXT, created_at TEXT
                );
                INSERT INTO model_provider_revisions VALUES ('relay:r1', 'relay', 1), ('deepseek:r1', 'deepseek', 1);
                INSERT INTO model_profiles VALUES
                    ('relay:gpt-5.6', 'relay', 'gpt-5.6', 'gpt-5.6', 'gpt', 'openai_responses', 400000, 128000, '["none"]', 'none', '{"tools":"confirmed","vision":"unconfirmed"}', 'allowlist', '{"connectivity":{"status":"verified","profile_revision":1}}', 1, 'seed', '2026-11-21', '2026-11-21', NULL),
                    ('deepseek-v4-pro', 'deepseek', 'deepseek-v4-pro', 'DeepSeek Pro', 'deepseek', 'deepseek_chat_completions', 1000000, 393216, '["none"]', 'none', '{"tools":"confirmed","vision":"unsupported"}', 'all', '{"connectivity":{"status":"verified","profile_revision":1}}', 1, 'seed', '2026-11-21', '2026-11-21', NULL);
                """
            )
            conn.commit()
            conn.close()

            first = migrate_vision_v1(db, skip_backup=True)
            self.assertEqual(first["upgraded_count"], 1)
            self.assertEqual(first["upgraded_profiles"], ["relay:gpt-5.6"])

            # Idempotent second run changes nothing
            second = migrate_vision_v1(db, skip_backup=True)
            self.assertEqual(second["upgraded_count"], 0)

            conn = sqlite3.connect(db)
            gpt_caps = json.loads(
                conn.execute("SELECT capabilities_json FROM model_profiles WHERE profile_id='relay:gpt-5.6'").fetchone()[0]
            )
            pro_caps = json.loads(
                conn.execute("SELECT capabilities_json FROM model_profiles WHERE profile_id='deepseek-v4-pro'").fetchone()[0]
            )
            rev = conn.execute("SELECT id, revision FROM model_profile_revisions WHERE profile_id='relay:gpt-5.6'").fetchone()
            conn.close()
            self.assertEqual(gpt_caps["vision"], "confirmed")
            self.assertEqual(pro_caps["vision"], "unsupported")
            self.assertEqual(rev, ("relay:gpt-5.6:r2", 2))


if __name__ == "__main__":
    unittest.main()
