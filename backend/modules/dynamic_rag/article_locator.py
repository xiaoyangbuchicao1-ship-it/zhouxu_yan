"""Generic document-and-location anchors for precise evidence retrieval."""

import re
from dataclasses import dataclass

_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_ARTICLE_RE = re.compile(
    r"第\s*([0-9]{1,4}|[零〇一二三四五六七八九十百千万两]+)\s*条"
)


@dataclass(frozen=True)
class ArticleLocator:
    """A document identity and a structural location, independent of domain."""

    document_title: str | None = None
    article_no: str | None = None

    @property
    def precise(self) -> bool:
        return bool(self.document_title and self.article_no)


def _parse_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _DIGITS:
            number = _DIGITS[char]
        elif char in _UNITS:
            unit = _UNITS[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            return None
    return total + section + number


def _to_chinese(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    if number < 20:
        return "十" + (digits[number - 10] if number > 10 else "")
    if number < 100:
        return digits[number // 10] + "十" + (digits[number % 10] if number % 10 else "")
    if number < 1000:
        rest = number % 100
        tail = _to_chinese(rest) if rest else ""
        if rest and rest < 10:
            tail = "零" + tail
        return digits[number // 100] + "百" + tail
    if number < 10000:
        rest = number % 1000
        tail = _to_chinese(rest) if rest else ""
        if rest and rest < 100:
            tail = "零" + tail
        return digits[number // 1000] + "千" + tail
    raise ValueError("article_number_out_of_range")


def normalize_article_no(value: str) -> str | None:
    """Normalize Arabic or Chinese article notation to the corpus form."""
    number = _parse_number(value)
    if number is None or number <= 0 or number >= 10000:
        return None
    return f"第{_to_chinese(number)}条"


def _document_title(query: str) -> str | None:
    quoted = list(dict.fromkeys(re.findall(r"《([^》]{2,100})》", query)))
    if quoted:
        return quoted[0].strip() if len(quoted) == 1 else None
    # Generic document suffixes; this is not a maritime or business classifier.
    candidates = re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,80}(?:法|条例|规定|规则|办法|细则|公约|通知|决定))",
        query,
    )
    candidates = list(dict.fromkeys(candidates))
    return candidates[0].strip() if len(candidates) == 1 else None


def locate_article(query: str) -> ArticleLocator:
    match = _ARTICLE_RE.search(query)
    article_no = normalize_article_no(match.group(1)) if match else None
    return ArticleLocator(document_title=_document_title(query), article_no=article_no)
