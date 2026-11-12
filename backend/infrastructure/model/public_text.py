"""Exact known identity projection, identical for streamed and final text."""


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

    def render(self, text):
        for key in sorted(self.replacements, key=len, reverse=True):
            text = text.replace(key, self.replacements[key])
        return text

    def push(self, text, *, final=False):
        self.pending += text
        pieces = []
        keys = sorted(self.replacements, key=len, reverse=True)
        while self.pending:
            full = next((key for key in keys if self.pending.startswith(key)), None)
            if full:
                pieces.append(self.replacements[full])
                self.pending = self.pending[len(full) :]
            elif not final and any(key.startswith(self.pending) for key in keys):
                break
            else:
                pieces.append(self.pending[0])
                self.pending = self.pending[1:]
        return "".join(pieces)
