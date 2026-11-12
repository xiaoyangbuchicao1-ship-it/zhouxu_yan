"""Local subprocess port for the isolated, pinned MinerU SDK environment."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from backend.contracts.modules.materials import MaterialManifest, ParsedMaterial
from backend.infrastructure.parsing.adapters.common import (
    infer_media_type,
    material_id,
    material_name,
)


class MinerUAdapter:
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
        executable = Path(self.settings.mineru_python)
        if not executable.is_absolute():
            executable = Path(__file__).resolve().parents[4] / executable
        if not executable.is_file():
            manifest.errors = ["mineru_isolated_environment_unavailable"]
            manifest.missing_ranges = ["MinerU 独立运行环境尚未安装"]
            return ParsedMaterial(manifest=manifest)
        if not self.settings.mineru_runtime_ready:
            manifest.errors = ["mineru_local_runtime_not_prepared_no_inference_started"]
            manifest.missing_ranges = ["本地权重与运行环境尚未确认就绪"]
            return ParsedMaterial(manifest=manifest)
        if not self.settings.mineru_runtime_revision:
            manifest.errors = ["mineru_weights_and_config_revision_unconfirmed"]
            manifest.missing_ranges = ["本地权重与影响结果的配置指纹尚未记录"]
            return ParsedMaterial(manifest=manifest)
        root = Path(material.metadata["_artifact_root"])
        root.mkdir(parents=True, exist_ok=True)
        output = root / ("worker-" + uuid4().hex + ".json")
        packet = {
            "material": material.model_dump(mode="json"),
            "output": str(output),
            "settings": {
                "mineru_version": self.settings.mineru_version,
                "mineru_tier": self.settings.mineru_tier,
                "mineru_runtime_ready": True,
            },
        }
        env = dict(os.environ)
        env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "MODELSCOPE_OFFLINE": "1",
                "MINERU_TELEMETRY_DISABLED": "1",
            }
        )
        try:
            completed = subprocess.run(
                [str(executable), str(Path(__file__).with_name("mineru_worker.py"))],
                input=json.dumps(packet),
                text=True,
                encoding="utf-8",
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.settings.mineru_timeout_seconds,
                check=False,
            )
            if completed.returncode or not output.is_file():
                raise ValueError("mineru_worker_failed")
            data = json.loads(output.read_text(encoding="utf-8"))
            data["manifest"]["uri"] = material.uri
            return ParsedMaterial.model_validate(data)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            manifest.status = "failed"
            manifest.errors = ["mineru_local_worker_failed_or_timed_out"]
            return ParsedMaterial(manifest=manifest)
        finally:
            output.unlink(missing_ok=True)
