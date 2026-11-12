from __future__ import annotations

import csv

from backend.config.settings import MaterialSettings
from backend.contracts.modules.materials import (
    MaterialInput,
    MaterialLocator,
    MaterialManifest,
    MaterialUnit,
    ParsedMaterial,
)
from backend.infrastructure.parsing.adapters.common import (
    infer_media_type,
    local_path,
    material_id,
    material_name,
    unit_id,
)


class SpreadsheetParser:
    name = "spreadsheet"

    def __init__(self, settings: MaterialSettings):
        self.settings = settings

    def can_handle(self, material: MaterialInput) -> bool:
        return infer_media_type(material) in {
            "text/csv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        } or local_path(material).suffix.lower() in {".csv", ".xlsx"}

    def parse(self, material: MaterialInput) -> ParsedMaterial:
        key = material_id(material)
        try:
            path = local_path(material)
            if path.stat().st_size > self.settings.max_file_bytes:
                raise ValueError("file_too_large")
            units: list[MaterialUnit] = []
            missing_formulas = []
            if path.suffix.lower() == ".csv":
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.reader(stream))
                units.extend(
                    _row_blocks(key, "CSV", rows, self.settings.table_rows_per_block)
                )
            else:
                from openpyxl import load_workbook

                workbook = load_workbook(path, read_only=False, data_only=False)
                cached = load_workbook(path, read_only=True, data_only=True)
                try:
                    for sheet in workbook.worksheets:
                        values, formulas = [], {}
                        for row in sheet.iter_rows():
                            values.append([_cell_text(cell) for cell in row])
                            for cell in row:
                                if cell.data_type == "f":
                                    formula = (
                                        cell.value
                                        if isinstance(cell.value, str)
                                        else getattr(cell.value, "text", None)
                                    )
                                    if formula is None:
                                        missing_formulas.append(
                                            f"{sheet.title}!{cell.coordinate}"
                                        )
                                    formulas[cell.coordinate] = {
                                        "formula": formula,
                                        "formula_type": type(cell.value).__name__,
                                        "cached_value": cached[sheet.title][
                                            cell.coordinate
                                        ].value,
                                        "calculated_value": None,
                                        "notice": "No formula evaluator executed; cached value may be absent or stale",
                                    }
                        units.extend(
                            _row_blocks(
                                key,
                                sheet.title,
                                values,
                                self.settings.table_rows_per_block,
                                formulas=formulas,
                                merges=[str(r) for r in sheet.merged_cells.ranges],
                            )
                        )
                finally:
                    workbook.close()
                    cached.close()
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="available" if units and not missing_formulas else "partial",
                    parser=self.name,
                    summary="\n".join(unit.text for unit in units)[
                        : self.settings.preview_chars
                    ],
                    unit_count=len(units),
                    available_units=[unit.unit_id for unit in units],
                    errors=[] if units else ["spreadsheet_has_no_sheets"],
                    missing_ranges=[
                        f"复合公式正文未恢复：{cell}" for cell in missing_formulas
                    ],
                ),
                units=units,
            )
        except Exception as exc:
            return ParsedMaterial(
                manifest=MaterialManifest(
                    material_id=key,
                    name=material_name(material),
                    uri=material.uri,
                    media_type=infer_media_type(material),
                    status="failed",
                    parser=self.name,
                    errors=["parse_failed:" + type(exc).__name__],
                )
            )


def _cell_text(cell):
    if cell.value is None:
        return ""
    if cell.data_type == "f" and not isinstance(cell.value, str):
        return getattr(cell.value, "text", None) or "[原件中的复合公式，正文尚未恢复]"
    return str(cell.value)


def _row_blocks(key, name, rows, step, *, formulas=None, merges=None):
    from openpyxl.utils.cell import get_column_letter

    columns = max((len(row) for row in rows), default=0)
    for start in range(0, len(rows), step):
        selected = rows[start : start + step]
        end = start + len(selected)
        cell_range = f"A{start + 1}:{get_column_letter(max(1, columns))}{end}"
        selected_formulas = {
            coordinate: value
            for coordinate, value in (formulas or {}).items()
            if start < int("".join(c for c in coordinate if c.isdigit())) <= end
        }
        yield MaterialUnit(
            unit_id=unit_id(key, "sheet", name + cell_range),
            material_id=key,
            kind="table",
            locator=MaterialLocator(
                kind="sheet",
                value=f"{name}!{cell_range}",
                label=f"{name}!{cell_range}",
                sheet=name,
                row_start=start + 1,
                row_end=end,
                column_start=1,
                column_end=max(1, columns),
            ),
            text="\n".join("\t".join(row) for row in selected),
            structured={
                "rows": selected,
                "formulas": selected_formulas,
                "merged_ranges": merges or [],
            },
            metadata={
                "sheet": name,
                "range": cell_range,
                "header_status": "unconfirmed",
            },
        )
