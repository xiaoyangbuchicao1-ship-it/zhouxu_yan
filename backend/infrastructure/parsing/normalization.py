"""Version and rebind parser output; no semantic labels or model budgets."""

from backend.infrastructure.parsing.adapters.common import (
    material_id,
    material_name,
    unit_id,
)

PARSER_SCHEMA = "material-structure-2"


def normalize(parsed, material, *, fingerprint, version, engine_config):
    key = material_id(material)
    for order, unit in enumerate(parsed.units):
        unit.material_id = key
        unit.order = order
        unit.parse_version = version
        unit.unit_id = unit_id(key, version, str(order))
        if unit.kind == "page" and unit.locator.value.isdigit():
            unit.locator.page = int(unit.locator.value)
    manifest = parsed.manifest
    manifest.material_id, manifest.uri = key, material.uri
    manifest.name = material_name(material)
    manifest.content_fingerprint, manifest.parse_version = fingerprint, version
    manifest.engine_config = engine_config
    manifest.unit_count = len(parsed.units)
    manifest.available_units = [unit.unit_id for unit in parsed.units]
    if not manifest.parsed_ranges:
        manifest.parsed_ranges = [
            unit.locator.label or unit.locator.value
            for unit in parsed.units
            if unit.text.strip()
        ]
    if not manifest.missing_ranges and manifest.status != "available":
        manifest.missing_ranges = manifest.errors or ["content_not_fully_extracted"]
    manifest.complete = manifest.status == "available" and not manifest.missing_ranges
    return parsed
