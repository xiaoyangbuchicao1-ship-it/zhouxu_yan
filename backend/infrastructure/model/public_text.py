"""Exact known identity projection, identical for streamed and final text."""

from backend.infrastructure.sources import paper_source_id


class PublicText:
    def __init__(self, items):
        self.replacements = {}
        self.pending = ""

        def visit(value, label="材料来源"):
            if isinstance(value, dict):
                label = (
                    value.get("source_label")
                    or value.get("document_name")
                    or value.get("name")
                    or label
                )
                for key, item in value.items():
                    if key in {
                        "material_id",
                        "unit_id",
                        "start_unit_id",
                        "end_unit_id",
                        "block_id",
                        "parse_version",
                        "cursor",
                        "next_cursor",
                        "chunk_id",
                        "resource_id",
                    }:
                        if isinstance(item, str) and len(item) > 8:
                            self.replacements[item] = label
                    else:
                        visit(item, label)
            elif isinstance(value, list):
                for item in value:
                    visit(item, label)

        for item in items:
            visit(item.body)
            self.replacements.update(item.identity_labels)
        # Citation targets are opaque public addresses supplied by the existing
        # research tools. Do not project identities inside these exact targets.
        for item in items:
            action = None
            if item.source == "action" and item.type in {"tool_result", "resource"}:
                action = item.body
            elif item.source == "graph" and item.kind == "graph.tool":
                action = item.body["output"]["action"]
            if action is None or action.get("outcome") not in {"ok", "partial"}:
                continue
            tool = action.get("tool_name")
            if tool not in {"information_card_search", "information_card_read"}:
                continue
            result = action["result"]
            if result.get("source_type") != "validated_information_card":
                continue
            cards = result["items"] if tool.endswith("search") else [result["card"]]
            for card in cards:
                url = card["citation_url"]
                if url != f"/workspace/sources/{paper_source_id(card['card_id'])}":
                    continue
                for target in (f"]({url})", f"](<{url}>)"):
                    self.replacements[target] = target
        self.keys = sorted(self.replacements, key=len, reverse=True)

    def render(self, text):
        return self._project(text, final=True)[0]

    def push(self, text, *, final=False):
        visible, self.pending = self._project(self.pending + text, final=final)
        return visible

    def _project(self, text, *, final):
        pieces = []
        while text:
            if not final and any(
                len(key) > len(text) and key.startswith(text) for key in self.keys
            ):
                break
            full = next((key for key in self.keys if text.startswith(key)), None)
            if full:
                pieces.append(self.replacements[full])
                text = text[len(full) :]
            else:
                pieces.append(text[0])
                text = text[1:]
        return "".join(pieces), text
