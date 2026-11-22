"""Agent Skills package boundary: ZIP transport, YAML metadata and byte identity.

Format follows https://agentskills.io/specification and the upstream skills-ref
validator. The reference validator is a demonstration; archive limits, safe
paths and persistence remain the application's responsibility. No package code
or installation hooks run while importing.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 500


class PackageError(ValueError):
    pass


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = None
    compatibility: str | None = Field(default=None, max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if (
            value.startswith("-")
            or value.endswith("-")
            or "--" in value
            or not all(c == "-" or c.isnumeric() or (c.isalpha() and c.lower() == c) for c in value)
        ):
            raise ValueError("技能名称应使用小写字母、数字和连字符")
        return value

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写技能用途")
        return value


@dataclass(frozen=True)
class SkillPackage:
    metadata: SkillMetadata
    instructions: str
    files: dict[str, bytes]
    manifest: dict


def safe_resource_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 240
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or str(path) != value
    ):
        raise PackageError("技能包路径不安全，请检查压缩包目录")
    return value


def read_package(filename: str, data: bytes) -> SkillPackage:
    if not filename.lower().endswith(".zip"):
        raise PackageError("请上传 ZIP 格式的技能包")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PackageError("技能包不能超过 20 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageError("无法打开技能包，请检查 ZIP 文件") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_FILES:
            raise PackageError("技能包文件过多，最多支持 500 项")
        total = 0
        raw_files = {}
        for member in members:
            path = safe_resource_path(member.filename.rstrip("/") if member.is_dir() else member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise PackageError("技能包不能包含链接或特殊文件")
            if member.is_dir():
                continue
            total += member.file_size
            if member.file_size > MAX_FILE_BYTES or total > MAX_PACKAGE_BYTES:
                raise PackageError("技能包解压后过大：单文件最多 10 MB，总计最多 50 MB")
            if path in raw_files:
                raise PackageError("技能包包含重名文件")
            try:
                raw_files[path] = archive.read(member)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise PackageError("技能包文件损坏、已加密或压缩格式不支持") from exc

    roots = [path for path in raw_files if path == "SKILL.md" or (path.endswith("/SKILL.md") and path.count("/") == 1)]
    if len(roots) != 1:
        raise PackageError("技能包必须包含一个 SKILL.md，可放在根目录或单个技能目录中")
    prefix = roots[0].removesuffix("SKILL.md")
    if any(not path.startswith(prefix) for path in raw_files):
        raise PackageError("ZIP 中只能包含一个完整技能目录")
    files = {path[len(prefix):]: content for path, content in raw_files.items()}
    # Limit the YAML/Markdown input before parsing; scripts and binary templates
    # keep their original bytes and are not interpreted here.
    if len(files["SKILL.md"]) > 128 * 1024:
        raise PackageError("SKILL.md 过长，请将资料移到 references 目录")
    try:
        instructions = files["SKILL.md"].decode("utf-8-sig")
        matched = re.match(r"\A---\s*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", instructions, re.DOTALL)
        if matched is None:
            raise PackageError("SKILL.md 缺少开头的名称和用途信息")
        metadata = SkillMetadata.model_validate(yaml.safe_load(matched[1]))
    except (UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise PackageError("SKILL.md 格式有误，请检查名称、用途及 YAML 字段") from exc
    if prefix and prefix.rstrip("/") != metadata.name:
        raise PackageError("技能目录名应与 SKILL.md 中的 name 相同")
    manifest = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "name": PurePosixPath(filename.replace("\\", "/")).name[:200],
        "compatibility": metadata.compatibility,
        "files": [
            {"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ],
    }
    return SkillPackage(metadata, instructions, files, manifest)


def create_package_schema(db) -> None:
    db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE skill_packages (
            skill_id TEXT NOT NULL, version INTEGER NOT NULL,
            manifest TEXT NOT NULL,
            PRIMARY KEY(skill_id, version),
            FOREIGN KEY(skill_id, version) REFERENCES skill_versions(skill_id, version)
        );
        CREATE TABLE skill_package_files (
            skill_id TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL,
            content BLOB NOT NULL, sha256 TEXT NOT NULL,
            PRIMARY KEY(skill_id, version, path),
            FOREIGN KEY(skill_id, version) REFERENCES skill_packages(skill_id, version)
        );
        CREATE TABLE skill_installations (
            skill_id TEXT PRIMARY KEY REFERENCES skills(id), active INTEGER NOT NULL CHECK(active IN (0,1))
        );
        COMMIT;
    """)
