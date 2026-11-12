"""Keyword navigation over the same versioned units used by continuous reads."""

import base64
import hashlib
import json
import re


def search_view(bindings, value, settings, *, limit):
    selected = list(dict.fromkeys(value.material_ids or list(bindings)))
    if any(key not in bindings for key in selected):
        return {"status": "unavailable", "matches": [], "notices": ["material_not_bound"]}
    query = value.query.strip()
    words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", query)
    terms = list(dict.fromkeys(words + [word[i:i + 2] for word in words
                                       if re.fullmatch(r"[\u4e00-\u9fff]+", word)
                                       for i in range(len(word) - 1)]))
    if not query:
        return {"status": "no_match", "matches": [], "notices": ["empty_query"]}
    identity = hashlib.sha256(json.dumps([
        query, [(key, bindings[key].manifest.parse_version) for key in selected],
    ], ensure_ascii=False).encode()).hexdigest()
    start = 0
    if value.cursor:
        try:
            stamp, start = json.loads(base64.urlsafe_b64decode(
                value.cursor + "=" * (-len(value.cursor) % 4)))
            if stamp != identity or type(start) is not int or start < 0:
                raise ValueError("cursor")
        except (ValueError, TypeError, UnicodeError):
            return {"status": "invalid_cursor", "matches": [], "notices": []}
    found = []
    incomplete = []
    for material_order, key in enumerate(selected):
        parsed = bindings[key]
        if not parsed.manifest.complete:
            incomplete.append(key)
        for index, unit in enumerate(parsed.units):
            exact = re.search(re.escape(query), unit.text, re.I)
            hits = [match.start() for term in terms
                    if (match := re.search(re.escape(term), unit.text, re.I))]
            if exact or hits:
                found.append(((bool(exact), len(hits), -material_order, -index),
                              key, unit, exact.start() if exact else min(hits)))
    found.sort(key=lambda item: item[0], reverse=True)
    maximum = min(value.max_results or settings.search_max_results, settings.search_max_results)
    result = {"status": "ok" if found else "no_match", "matches": [],
              "truncated": False, "next_cursor": None,
              "incomplete_material_ids": incomplete,
              "notices": ["search_covers_parsed_text_only"] if incomplete else []}
    used = len(json.dumps(result, ensure_ascii=False))
    for _, key, unit, position in found[start:start + maximum]:
        size = min(settings.search_excerpt_chars, max(0, limit - used))
        while size > 0:
            left = max(0, position - size // 4)
            match = {
                "material_id": key, "unit_id": unit.unit_id,
                "name": bindings[key].manifest.name,
                "source_label": f"{bindings[key].manifest.name} · {unit.locator.label or unit.locator.value}",
                "locator": unit.locator.model_dump(exclude_none=True),
                "char_offset": left,
                "excerpt": unit.text[left:left + size],
            }
            cost = len(json.dumps(match, ensure_ascii=False)) + 2
            if used + cost <= limit:
                break
            size //= 2
        else:
            break
        result["matches"].append(match)
        used += cost
    stop = start + len(result["matches"])
    if stop < len(found):
        result["truncated"] = True
        result["next_cursor"] = base64.urlsafe_b64encode(json.dumps(
            [identity, stop], separators=(",", ":")).encode()).decode().rstrip("=")
    if found and not result["matches"]:
        result["status"] = "deferred"
        result["notices"].append("context_space_needed")
    return result
