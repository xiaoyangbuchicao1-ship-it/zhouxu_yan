"""Small model-facing reading view; provenance stays in the attributed items."""


def reference_view(value):
    """Project navigation bookkeeping, never mutate private records.

    Endpoints are a preview, not a claim that every intervening unit was read.
    Ordinary evidence/user content must not be passed through this projection.
    """
    if isinstance(value, list):
        return [reference_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if "material_id" in value and key in {"coverage", "unit_ids"} and isinstance(item, list):
            result[key + "_preview"] = {
                "count": len(item),
                "first": item[0] if item else None,
                "last": item[-1] if item else None,
            }
        else:
            result[key] = reference_view(item)
    return result


def reading_view(value):
    if not isinstance(value, dict) or "units" not in value:
        return value
    result = {
        "name": value.get("name"),
        "material_id": value.get("material_id"),
        "status": value.get("status"),
        "parse_status": value.get("parse_status"),
        "complete": value.get("complete"),
        "text": "\n".join(u.get("text", "") for u in value["units"] if u.get("text")),
        "truncated": value.get("truncated", False),
    }
    if value["units"]:
        first, last = value["units"][0], value["units"][-1]
        result["read_range"] = {
            "start_unit_id": first.get("unit_id"), "end_unit_id": last.get("unit_id"),
            "start": first.get("locator"), "end": last.get("locator"),
        }
    result["read_hint"] = value.get("read_hint")
    result["body_in_tool_history"] = value.get("body_in_tool_history", False)
    if value.get("missing_ranges"):
        result["missing_content"] = value["missing_ranges"]
    if value.get("notices"):
        result["notices"] = value["notices"]
    has_visuals = any(unit.get("kind") == "image" for unit in value["units"])
    if has_visuals:
        result["visual_content"] = (
            "含图像资源，需要时通过 read_material 读取原图；文字不代表全部视觉内容。"
        )
    if value.get("truncated") or not result["text"] or has_visuals:
        result["material_id"] = value.get("material_id")
        result["next_cursor"] = value.get("next_cursor")
        result["read_hint"] = value.get("read_hint")
    return result
