"""Render OOXML list numbering, including overrides and nested levels."""

import re

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn


def value(node, path, default=None):
    child = node.find(path) if node is not None else None
    return child.get(qn("w:val"), default) if child is not None else default


def format_number(number, kind):
    if kind in {"lowerLetter", "upperLetter"}:
        text = ""
        while number > 0:
            number, digit = divmod(number - 1, 26)
            text = chr(97 + digit) + text
        return text.upper() if kind == "upperLetter" else text
    if kind in {"lowerRoman", "upperRoman"}:
        text = ""
        for amount, symbol in (
            (1000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        ):
            count, number = divmod(number, amount)
            text += symbol * count
        return text.lower() if kind == "lowerRoman" else text
    return str(number)


class WordNumbering:
    def __init__(self, document):
        # Reading numbering_part may try to CREATE an absent optional part;
        # python-docx does not implement that constructor. Read the relationship.
        self.nums, self.abstract, self.counters = {}, {}, {}
        try:
            root = document.part.part_related_by(RT.NUMBERING).element
        except KeyError:
            return
        self.nums = {n.get(qn("w:numId")): n for n in root.findall(qn("w:num"))}
        self.abstract = {
            n.get(qn("w:abstractNumId")): n for n in root.findall(qn("w:abstractNum"))
        }

    def prefix(self, paragraph):
        props = paragraph._p.find(qn("w:pPr"))
        num = props.find(qn("w:numPr")) if props is not None else None
        style = paragraph.style
        visited = set()
        while num is None and style is not None and style.style_id not in visited:
            visited.add(style.style_id)
            ppr = style.element.find(qn("w:pPr"))
            num = ppr.find(qn("w:numPr")) if ppr is not None else None
            style = style.base_style
        num_id = value(num, qn("w:numId"))
        if num_id in {None, "0"} or num_id not in self.nums:
            return ""
        level = int(value(num, qn("w:ilvl"), "0"))
        instance = self.nums[num_id]
        abstract = self.abstract.get(value(instance, qn("w:abstractNumId")))
        levels = (
            {int(n.get(qn("w:ilvl"))): n for n in abstract.findall(qn("w:lvl"))}
            if abstract is not None
            else {}
        )
        overrides = {
            int(n.get(qn("w:ilvl"))): n for n in instance.findall(qn("w:lvlOverride"))
        }
        for depth in range(level + 1):
            override = overrides.get(depth)
            replacement = override.find(qn("w:lvl")) if override is not None else None
            definition = replacement if replacement is not None else levels.get(depth)
            levels[depth] = definition
            key = (num_id, depth)
            start = int(
                value(
                    override,
                    qn("w:startOverride"),
                    value(definition, qn("w:start"), "1"),
                )
            )
            if key not in self.counters:
                self.counters[key] = start
            elif depth == level:
                self.counters[key] += 1
        for key in list(self.counters):
            if key[0] == num_id and key[1] > level:
                del self.counters[key]
        template = value(levels.get(level), qn("w:lvlText"), "%1.")
        return (
            re.sub(
                r"%([1-9])",
                lambda m: format_number(
                    self.counters.get((num_id, int(m[1]) - 1), 1),
                    value(levels.get(int(m[1]) - 1), qn("w:numFmt"), "decimal"),
                ),
                template,
            )
            + " "
        )
