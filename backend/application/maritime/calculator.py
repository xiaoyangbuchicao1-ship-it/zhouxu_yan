"""Exact decimal arithmetic only. The calculator does not establish legal entitlement."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext


def number(value):
    if isinstance(value, bool):
        raise ValueError("数值不能是布尔值")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal("1e15"):
            raise ValueError("请输入非负且不超过 10^15 的有限数值")
        if result.as_tuple().exponent < -8:
            raise ValueError("输入最多保留八位小数")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("缺少或无效的数值") from None


def calculate_claim(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("需要 1 至 100 个索赔项目")
    lines, totals = [], {}
    with localcontext() as ctx:
        ctx.prec = 80
        for item in items:
            currency = str(item.get("currency", "")).upper()
            if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                raise ValueError("每项需要三位币种代码，不能混加币种")
            quantity, rate = number(item.get("quantity")), number(item.get("rate"))
            name = str(item.get("name", "")).strip()
            if not name or len(name) > 160:
                raise ValueError("每项需要不超过 160 字的名称")
            amount = (quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            totals[currency] = totals.get(currency, Decimal(0)) + amount
            lines.append(
                {
                    "name": name,
                    "quantity": str(quantity),
                    "rate": str(rate),
                    "currency": currency,
                    "amount": str(amount),
                    "formula": f"{quantity} × {rate} = {amount} {currency}",
                }
            )
    return {
        "items": lines,
        "totals": {key: str(value) for key, value in totals.items()},
        "notice": "按输入逐项四舍五入至小数点后两位；不同币种不合并，不包含汇率、责任比例、免赔或法律认可判断。",
    }
