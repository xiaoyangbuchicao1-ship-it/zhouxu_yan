from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract

MaterialStatus = Literal["available", "partial", "failed", "unavailable"]
MaterialUnitKind = Literal[
    "text", "page", "paragraph", "table", "sheet", "image", "link"
]


class MaterialInput(Contract):
    """A user supplied local path or URL.

    The URI is an input reference, not model authority. Parsers keep it out of
    system/developer roles and return an attributed manifest instead.
    """

    uri: str = Field(min_length=1)
    material_id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    media_type: str | None = Field(default=None, min_length=1)
    selected: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoredMaterialRef(Contract):
    """Safe public view of an uploaded material.

    The browser receives an opaque ID and metadata only. The storage URI is
    deliberately absent so server paths cannot enter the client contract.
    """

    material_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    status: str = Field(min_length=1)
    checksum: str = Field(min_length=1)


class MaterialLocator(Contract):
    kind: MaterialUnitKind
    value: str = Field(min_length=1)
    label: str | None = None
    page: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    sheet: str | None = None
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    column_start: int | None = Field(default=None, ge=1)
    column_end: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None


class MaterialUnit(Contract):
    """A readable, attributable unit such as a page or spreadsheet range."""

    unit_id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    kind: MaterialUnitKind
    locator: MaterialLocator
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    parse_version: str = ""
    order: int = Field(default=0, ge=0)
    parent_id: str | None = None
    structured: dict[str, Any] | None = None
    resources: list[str] = Field(default_factory=list)


class MaterialManifest(Contract):
    material_id: str = Field(min_length=1)
    name: str
    # The parser keeps the source URI for server-side reads, but the field is
    # excluded from serialized model/tool context.  A model receives the
    # opaque material_id and must use read_material instead of a local path or
    # remote URL that it cannot safely access.
    uri: str = Field(exclude=True)
    media_type: str
    status: MaterialStatus
    parser: str
    summary: str = ""
    unit_count: int = Field(default=0, ge=0)
    available_units: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_fingerprint: str = ""
    parse_version: str = ""
    engine_config: dict[str, Any] = Field(default_factory=dict)
    parsed_ranges: list[str] = Field(default_factory=list)
    missing_ranges: list[str] = Field(default_factory=list)
    complete: bool = False
    resources: list[str] = Field(default_factory=list)


class ParsedMaterial(Contract):
    manifest: MaterialManifest
    units: list[MaterialUnit] = Field(default_factory=list)


class MaterialReadInput(Contract):
    material_id: str = Field(min_length=1, description="必须复制上下文提供的 material_id，不用来源标签或文件名代替。")
    mode: Literal["overview", "read"] = "read"
    position: Literal["start", "end"] = Field(default="start", description="无游标或定位时，连续读取开头或末尾；读末尾用 end。")
    unit_id: str | None = Field(default=None, min_length=1)
    end_unit_id: str | None = Field(default=None, min_length=1, description="可选的末端位置，包含该单元；必须来自已提供的位置标记。")
    locator: str | None = Field(default=None, min_length=1, description="只能使用已经返回的精确位置标记；末尾位置用 position=end。")
    cursor: str | None = Field(default=None, min_length=1, max_length=1024, description="原样复制返回的 next_cursor，不能编造或填写 start/end。")
    char_offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1)
    include_image: bool = False


class MaterialSearchInput(Contract):
    query: str = Field(min_length=1, description="简短的正文关键词或短语；查找开头、末尾或连续全部内容时直接用读取工具。")
    material_ids: list[str] = Field(default_factory=list, description="仅使用提供的材料ID；省略则搜索本次运行绑定的材料。")
    cursor: str | None = Field(default=None, max_length=1024)
    max_results: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1)


class MaterialReadOutput(Contract):
    manifest: MaterialManifest
    units: list[MaterialUnit] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    status: Literal[
        "ok",
        "end",
        "no_match",
        "not_parsed",
        "failed",
        "unavailable",
        "invalid_cursor",
        "not_found",
    ] = "ok"
    coverage: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    image_data_url: str | None = None
