from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: summarize.py INPUT.json OUTPUT.csv")
    input_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    package = Path(__file__).resolve().parents[1]
    rules = json.loads((package / "references/rules.json").read_text(encoding="utf-8"))
    header = next(csv.reader((package / "assets/header.csv").read_text(encoding="utf-8").splitlines()))
    records = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("费用记录不能为空")
    totals: dict[str, Decimal] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("category"), str):
            raise ValueError("每条记录必须包含 category")
        try:
            amount = Decimal(str(record["amount"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("每条记录必须包含有效 amount") from exc
        if not amount.is_finite():
            raise ValueError("费用金额必须是有限数字")
        totals[record["category"]] = totals.get(record["category"], Decimal("0")) + amount
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for category, amount in totals.items():
            writer.writerow([category, f"{amount:.{rules['decimal_places']}f}"])
        writer.writerow([rules["total_label"], f"{sum(totals.values(), Decimal('0')):.{rules['decimal_places']}f}"])


if __name__ == "__main__":
    main()
