"""Continuous, version-bound reading across blocks and within long blocks."""

import base64
import json

from backend.contracts.modules.materials import MaterialReadOutput


def cursor_for(parsed, index, offset, stop_index=None):
    manifest = parsed.manifest
    fields = [manifest.material_id, manifest.parse_version, index, offset]
    if stop_index is not None:
        fields.append(stop_index)
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                fields,
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def cursor_position(parsed, cursor):
    try:
        fields = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        )
        key, version, index, offset = fields[:4]
        stop = fields[4] if len(fields) == 5 else len(parsed.units)
        if (len(fields) not in {4, 5} or type(stop) is not int
                or not 0 <= index <= stop <= len(parsed.units)
                or (index == stop and offset != 0)):
            raise ValueError("invalid_cursor")
        if (
            key != parsed.manifest.material_id
            or version != parsed.manifest.parse_version
        ):
            raise ValueError("expired_cursor")
        if type(index) is not int or type(offset) is not int or index < 0 or offset < 0:
            raise ValueError("invalid_cursor")
        if index > len(parsed.units) or (index == len(parsed.units) and offset):
            raise ValueError("invalid_cursor")
        if index < len(parsed.units) and offset > len(parsed.units[index].text):
            raise ValueError("invalid_cursor")
        return index, offset, stop
    except Exception:
        raise ValueError("invalid_or_expired_cursor") from None


def read_view(parsed, value, *, limit, overview_limit):
    output = MaterialReadOutput(manifest=parsed.manifest.model_copy(deep=True))
    output.manifest.summary = ""
    output.manifest.available_units = []
    output.manifest.parsed_ranges = []
    if parsed.manifest.status in {"failed", "unavailable"}:
        output.status = parsed.manifest.status
        output.notices = list(parsed.manifest.errors)
        return output
    units = parsed.units
    index, offset, stop_index = 0, value.char_offset, len(units)
    if value.position == "end" and not (value.cursor or value.unit_id or value.locator):
        remaining = limit
        index = len(units)
        while index:
            index -= 1
            length = len(units[index].text)
            if length > remaining:
                offset = length - remaining
                break
            remaining -= length
            if not remaining:
                break
        if index or offset:
            output.notices.append("本次仅提供末尾连续正文，前文仍可另行读取。")
    if value.cursor:
        try:
            index, offset, stop_index = cursor_position(parsed, value.cursor)
        except ValueError:
            output.status = "invalid_cursor"
            output.notices = ["游标无效或解析版本已更新，请重新定位。"]
            return output
    elif value.unit_id or value.locator:
        index = next(
            (
                i
                for i, unit in enumerate(units)
                if (
                    unit.unit_id == value.unit_id
                    if value.unit_id
                    else value.locator in {unit.locator.value, unit.locator.label}
                )
            ),
            -1,
        )
        if index < 0:
            output.status = "not_found"
            return output
        if offset > len(units[index].text):
            output.status = "invalid_cursor"
            return output
    if value.end_unit_id:
        selected_stop = next((i + 1 for i, unit in enumerate(units)
                             if unit.unit_id == value.end_unit_id), 0)
        if value.cursor and selected_stop != stop_index:
            output.status = "invalid_cursor"
            return output
        stop_index = selected_stop
        if stop_index <= index:
            output.status = "not_found"
            return output
    if value.mode == "overview":
        stop = min(index + overview_limit, stop_index)
        output.units = [
            u.model_copy(update={"text": "", "structured": None, "metadata": {}})
            for u in units[index:stop]
        ]
        output.next_cursor = cursor_for(parsed, stop, 0, stop_index) if stop < stop_index else None
        output.truncated = stop < stop_index
        output.status = "ok" if output.units else "end"
        return output
    used = 0
    while index < stop_index and used < limit:
        unit = units[index]
        text = unit.text
        if not text and not unit.resources and unit.kind != "image":
            index, offset = index + 1, 0
            continue
        headers = unit.metadata.get("confirmed_header_text", "") if offset else ""
        if len(headers) >= max(0, limit - used - 1):
            headers = ""
        prefix = headers + "\n" if headers else ""
        end = min(len(text), offset + limit - used - len(prefix))
        part = text[offset:end]
        output.units.append(
            unit.model_copy(
                update={
                    "text": prefix + part,
                    "structured": None,
                    "metadata": {
                        "source_label": f"{parsed.manifest.name} · {unit.locator.label or unit.locator.value}"
                    },
                }
            )
        )
        output.coverage.append(
            {
                "unit_id": unit.unit_id,
                "char_start": offset,
                "char_end": end,
                "source": unit.locator.model_dump(exclude_none=True),
            }
        )
        used += len(prefix) + len(part)
        if end < len(text):
            offset = end
            break
        index, offset = index + 1, 0
    if index < stop_index:
        output.next_cursor = cursor_for(parsed, index, offset, stop_index)
        output.truncated = True
    if not output.units:
        output.status = "end" if parsed.manifest.complete else "not_parsed"
    return output
