"""
Typst 学术论文源码 -> 出版级 Word (.docx) 转换引擎
解析 csl_template.typ 规范的 .typ 文件，生成原生排版的高保真法学核心期刊 Word 文档。
包含：
- A4 严格法学核心期刊边距与页眉页码
- 标题/副标题/作者/单位/摘要/关键词的字阶与缩进
- 一级/二级/三级标题体系
- 原生 Word 圈码页下脚注 (①②③)，每页自动重新编号
- 纯文本段落首行缩进 2 字符、两端对齐、标准行距
"""

import sys
import os
import re
import tempfile
import zipfile
import shutil
from pathlib import Path

try:
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

W_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

FONT_SERIF = "SimSun"
FONT_SANS = "SimHei"
FONT_KAI = "KaiTi"
FONT_FANG = "FangSong"
FONT_TNR = "Times New Roman"


def set_run_font(run, east=FONT_SERIF, ascii_=FONT_TNR, size=10.5, bold=False, italic=False, color=None):
    run.font.name = ascii_
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), ascii_)
    rFonts.set(qn("w:hAnsi"), ascii_)
    rFonts.set(qn("w:eastAsia"), east)
    if size is not None:
        run.font.size = Pt(size)
    if bold:
        run.font.bold = True
    if italic:
        run.font.italic = True
    if color:
        run.font.color.rgb = color


def iter_balanced_calls(text: str, marker: str):
    """按括号深度配平扫描 `#marker[...]` 调用，避免 `[德]` 等内嵌方括号提前截断。"""
    n = len(text)
    pos = 0
    mlen = len(marker)
    while pos < n:
        idx = text.find(marker, pos)
        if idx == -1:
            break
        i = idx + mlen
        depth = 1
        in_str = False
        while i < n and depth > 0:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        yield idx, i + 1, text[idx + mlen : i]
                        pos = i + 1
                        break
            i += 1
        else:
            pos = idx + mlen


def parse_inline_segments(text: str) -> list[tuple[str, bool, bool]]:
    """解析行内 `#emph[...]` 与 `#strong[...]` 片段，返回 `(内容, bold, italic)` 列表。"""
    segments: list[tuple[str, bool, bool]] = []
    pos = 0
    n = len(text)
    while pos < n:
        idx_e = text.find("#emph[", pos)
        idx_s = text.find("#strong[", pos)
        candidates = [(i, k) for i, k in ((idx_e, "emph"), (idx_s, "strong")) if i != -1]
        if not candidates:
            tail = text[pos:].replace("\\", "")
            if tail:
                segments.append((tail, False, False))
            break
        next_idx, kind = min(candidates, key=lambda x: x[0])
        if next_idx > pos:
            plain = text[pos:next_idx].replace("\\", "")
            if plain:
                segments.append((plain, False, False))
        marker = f"#{kind}["
        matched = list(iter_balanced_calls(text[next_idx : next_idx + len(text) - next_idx], marker))
        if matched and matched[0][0] == 0:
            _, end_rel, inner = matched[0]
            clean_inner = inner.replace("\\", "")
            segments.append((clean_inner, kind == "strong", kind == "emph"))
            pos = next_idx + end_rel
        else:
            segments.append((marker, False, False))
            pos = next_idx + len(marker)
    return segments


def _extract_typst_field(arg_block: str, key: str) -> str:
    """稳健提取 `key: "..."`（含转义引号）或 `key: [...]` 参数值。"""
    pattern = re.compile(rf"\b{re.escape(key)}\s*:\s*")
    m = pattern.search(arg_block)
    if not m:
        return ""
    i = m.end()
    n = len(arg_block)
    if i >= n:
        return ""
    if arg_block[i] == '"':
        i += 1
        chars = []
        while i < n:
            ch = arg_block[i]
            if ch == "\\" and i + 1 < n:
                nxt = arg_block[i + 1]
                chars.append(nxt if nxt in ('"', "\\", "n") else nxt)
                i += 2
                continue
            if ch == '"':
                break
            chars.append(ch)
            i += 1
        val = "".join(chars)
    elif arg_block[i] == "[":
        matched = list(iter_balanced_calls(arg_block[i:], "["))
        val = matched[0][2] if matched else ""
    else:
        return ""
    val = re.sub(r"#linebreak\(\)", " ", val)
    val = re.sub(r"#v\(.*?\)", " ", val)
    return val.strip()


def _match_balanced_parens(text: str, open_idx: int) -> int:
    """给定 `(` 的索引，返回匹配的 `)` 之后索引；未闭合则返回 -1。"""
    n = len(text)
    if open_idx < 0 or open_idx >= n or text[open_idx] != "(":
        return -1
    depth = 1
    in_str = False
    i = open_idx + 1
    while i < n and depth > 0:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def _split_top_level_args(text: str) -> list[str]:
    """按顶层逗号拆分 Typst 调用参数，保留引号、方括号与圆括号内部结构。"""
    parts: list[str] = []
    buf: list[str] = []
    paren_d = 0
    brack_d = 0
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(text[i : i + 2])
            i += 2
            continue
        if not in_str and ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            buf.append("\n")
            continue
        if ch == '"':
            in_str = not in_str
            buf.append(ch)
            i += 1
            continue
        if not in_str:
            if ch == "(":
                paren_d += 1
            elif ch == ")":
                paren_d = max(0, paren_d - 1)
            elif ch == "[":
                brack_d += 1
            elif ch == "]":
                brack_d = max(0, brack_d - 1)
            elif ch == "," and paren_d == 0 and brack_d == 0:
                item = "".join(buf).strip()
                if item:
                    parts.append(item)
                buf.clear()
                i += 1
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _unwrap_typst_cell(expr: str) -> list[str]:
    """将 Typst 表格参数（普通单元格 / table.header / table.cell）展平为文本单元格列表。"""
    s = expr.strip()
    if not s:
        return []
    if s.startswith("table.hline(") or s.startswith("table.vline("):
        return []
    if s.startswith("table.header(") or s.startswith("table.footer("):
        open_i = s.find("(")
        end_i = _match_balanced_parens(s, open_i)
        inner = s[open_i + 1 : end_i - 1] if end_i != -1 else s[open_i + 1 :]
        cells: list[str] = []
        for sub in _split_top_level_args(inner):
            if re.match(r"^[A-Za-z_][\w-]*\s*:", sub):
                continue
            cells.extend(_unwrap_typst_cell(sub))
        return cells
    if s.startswith("table.cell("):
        open_i = s.find("(")
        end_i = _match_balanced_parens(s, open_i)
        if end_i != -1:
            rest = s[end_i:].strip()
            if rest.startswith("[") and rest.endswith("]"):
                return [_clean_typst_cell_text(rest[1:-1])]
            return _unwrap_typst_cell(s[open_i + 1 : end_i - 1])
    if s.startswith("[") and s.endswith("]"):
        return [_clean_typst_cell_text(s[1:-1])]
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return [_clean_typst_cell_text(s[1:-1])]
    if re.match(r"^[A-Za-z_][\w-]*\s*:", s):
        return []
    return [_clean_typst_cell_text(s)]


def _clean_typst_cell_text(text: str) -> str:
    s = re.sub(r"#linebreak\(\)", "\n", text)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_typst_table(inner: str) -> dict | None:
    cols = 0
    cells: list[str] = []
    for arg in _split_top_level_args(inner):
        m_cols = re.match(r"^columns\s*:\s*(.+)$", arg, re.S)
        if m_cols:
            val = m_cols.group(1).strip()
            if val.isdigit():
                cols = max(1, int(val))
            elif val.startswith("(") and val.endswith(")"):
                cols = max(1, len(_split_top_level_args(val[1:-1])))
            continue
        if re.match(r"^[A-Za-z_][\w-]*\s*:", arg):
            continue
        cells.extend(_unwrap_typst_cell(arg))
    if not cells:
        return None
    if cols <= 0:
        cols = min(3, len(cells)) or 1
    rows = [cells[k : k + cols] for k in range(0, len(cells), cols)]
    for r in rows:
        while len(r) < cols:
            r.append("")
    return {"cols": cols, "rows": rows}


def _append_text_blocks(chunk: str, blocks: list) -> None:
    lines = chunk.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("//"):
            continue
        if re.fullmatch(r"#(?:pagebreak|v|h)\([^)]*\)", line):
            continue

        if line.startswith("=== "):
            blocks.append(("h3", line[4:].strip()))
        elif line.startswith("== "):
            blocks.append(("h2", line[3:].strip()))
        elif line.startswith("= "):
            blocks.append(("h1", line[2:].strip()))
        else:
            para_lines = [line]
            while i < len(lines):
                next_line = lines[i].strip()
                if (
                    not next_line
                    or next_line.startswith("=")
                    or next_line.startswith("//")
                    or re.fullmatch(r"#(?:pagebreak|v|h)\([^)]*\)", next_line)
                ):
                    break
                para_lines.append(next_line)
                i += 1
            blocks.append(("p", " ".join(para_lines)))


def parse_typ_paper(typ_content: str):
    """从 .typ 源码中精准解析元数据和结构化正文块"""
    meta = {
        "title": "",
        "subtitle": "",
        "author": "",
        "affiliation": "",
        "abstract": "",
        "keywords": "",
        "clc_code": "",
        "doc_code": "",
        "article_id": "",
        "journal_cn": "",
        "journal_en": "",
        "vol": "",
        "no": "",
        "year": "",
        "month": "",
        "month_en": "",
        "fund": "",
        "author_bio": "",
        "title_en": "",
        "author_en": "",
        "affiliation_en": "",
        "abstract_en": "",
        "keywords_en": "",
        "doc_mode": "submission",
        "length_tier": "standard",
    }

    # 提取 csl_paper.with(...) 块（支持内部含小括号的标题/摘要）
    header_m = re.search(r"#show:\s*csl_paper\.with\s*\(", typ_content)
    if header_m:
        i = header_m.end()
        n = len(typ_content)
        depth = 1
        in_str = False
        while i < n and depth > 0:
            ch = typ_content[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        arg_block = typ_content[header_m.end() : i]
        for key in meta.keys():
            val = _extract_typst_field(arg_block, key)
            if val:
                meta[key] = val
        body_text = typ_content[i + 1 :]
    else:
        body_text = typ_content

    # 解析正文行、标题与 `#table(...)` 结构
    blocks = []
    pos = 0
    n = len(body_text)
    while pos < n:
        idx = body_text.find("#table(", pos)
        if idx == -1:
            _append_text_blocks(body_text[pos:], blocks)
            break
        end_i = _match_balanced_parens(body_text, idx + len("#table(") - 1)
        if end_i == -1:
            _append_text_blocks(body_text[pos:], blocks)
            break
        _append_text_blocks(body_text[pos:idx], blocks)
        parsed_tbl = _parse_typst_table(body_text[idx + len("#table(") : end_i - 1])
        if parsed_tbl:
            blocks.append(("table", parsed_tbl))
        pos = end_i

    return meta, blocks


def build_docx_from_typ(input_typ_path: str, output_docx_path: str):
    if not HAS_DOCX:
        raise RuntimeError("python-docx is not installed in the active Python environment.")
    typ_path = Path(input_typ_path).resolve()
    content = typ_path.read_text(encoding="utf-8")
    meta, blocks = parse_typ_paper(content)

    doc = Document()

    # 1. 页面版心几何：A4，法学核心期刊标准
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.8)
    sec.bottom_margin = Cm(2.5)
    sec.left_margin = Cm(2.5)
    sec.right_margin = Cm(2.5)
    sec.header_distance = Cm(1.5)
    sec.footer_distance = Cm(1.5)

    # 2. 设置原生 Word 脚注圈码 (①②③) 及每页重新编号
    sectPr = sec._sectPr
    fnPr = OxmlElement("w:footnotePr")
    numFmt = OxmlElement("w:numFmt")
    numFmt.set(qn("w:val"), "decimalEnclosedCircle")
    fnPr.append(numFmt)
    numRestart = OxmlElement("w:numRestart")
    numRestart.set(qn("w:val"), "eachPage")
    fnPr.append(numRestart)
    sectPr.insert(0, fnPr)

    # 3. 页眉设置：按 doc_mode 区分送审稿与刊印样稿
    doc_mode = (meta.get("doc_mode") or "submission").strip()
    is_proof = doc_mode == "journal_proof"
    j_cn = meta.get("journal_cn") or "中 国 海 商 法 研 究"
    j_en = meta.get("journal_en") or "Chinese Journal of Maritime Law"

    def _add_page_field(paragraph):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fr = paragraph.add_run()
        set_run_font(fr, east=FONT_TNR, size=9)
        fld1 = OxmlElement("w:fldChar"); fld1.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve"); instr.text = "PAGE"
        fld2 = OxmlElement("w:fldChar"); fld2.set(qn("w:fldCharType"), "end")
        fr._r.append(fld1); fr._r.append(instr); fr._r.append(fld2)

    if is_proof:
        header = sec.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hdr_text = f"{j_cn}  ·  {j_en}" if j_cn and j_en else (j_cn or j_en)
        hr = header.add_run(hdr_text)
        set_run_font(hr, east=FONT_SERIF, size=9)
    else:
        sec.different_first_page_header_footer = True
        header = sec.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if meta.get("title"):
            hr = header.add_run(meta["title"])
            set_run_font(hr, east=FONT_SERIF, size=9)
        _add_page_field(sec.first_page_footer.paragraphs[0])

    # 4. 页脚设置：页码居中
    _add_page_field(sec.footer.paragraphs[0])

    # 5. 论文主标题与副标题
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(8)
    p_title.paragraph_format.space_after = Pt(4 if meta["subtitle"] else 12)
    p_title.paragraph_format.line_spacing = 1.15
    r_title = p_title.add_run(meta["title"])
    set_run_font(r_title, east=FONT_SANS, size=17, bold=True)

    if meta["subtitle"]:
        p_sub = doc.add_paragraph()
        p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_sub.paragraph_format.space_before = Pt(0)
        p_sub.paragraph_format.space_after = Pt(12)
        p_sub.paragraph_format.line_spacing = 1.15
        r_sub = p_sub.add_run(meta["subtitle"])
        set_run_font(r_sub, east=FONT_SANS, size=14, bold=True)

    # 6. 作者与单位 (仅当指定时渲染，未提供自动优雅跳过)
    if meta["author"] and meta["author"] not in ("待补", "作者待补"):
        p_author = doc.add_paragraph()
        p_author.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_author.paragraph_format.space_before = Pt(0)
        p_author.paragraph_format.space_after = Pt(2)
        r_auth = p_author.add_run(meta["author"])
        set_run_font(r_auth, east=FONT_KAI, size=12)

    if meta["affiliation"] and meta["affiliation"] not in ("待补", "单位待补"):
        p_aff = doc.add_paragraph()
        p_aff.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_aff.paragraph_format.space_before = Pt(0)
        p_aff.paragraph_format.space_after = Pt(12)
        r_aff = p_aff.add_run(meta["affiliation"])
        set_run_font(r_aff, east=FONT_KAI, size=9.5)

    # 7. 中文摘要与关键词
    if meta["abstract"]:
        p_abs = doc.add_paragraph()
        pf_abs = p_abs.paragraph_format
        pf_abs.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf_abs.left_indent = Pt(21)
        pf_abs.right_indent = Pt(21)
        pf_abs.first_line_indent = Pt(21)
        pf_abs.line_spacing = Pt(13)
        pf_abs.space_before = Pt(0)
        pf_abs.space_after = Pt(0)
        r_lbl = p_abs.add_run("摘要：")
        set_run_font(r_lbl, east=FONT_SANS, size=9, bold=True)
        r_cnt = p_abs.add_run(meta["abstract"])
        set_run_font(r_cnt, east=FONT_FANG, size=9)

    if meta["keywords"]:
        p_kw = doc.add_paragraph()
        pf_kw = p_kw.paragraph_format
        pf_kw.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf_kw.left_indent = Pt(21)
        pf_kw.right_indent = Pt(21)
        pf_kw.first_line_indent = Pt(21)
        pf_kw.line_spacing = Pt(13)
        pf_kw.space_before = Pt(0)
        pf_kw.space_after = Pt(14)
        r_klbl = p_kw.add_run("关键词：")
        set_run_font(r_klbl, east=FONT_SANS, size=9, bold=True)
        r_kcnt = p_kw.add_run(meta["keywords"])
        set_run_font(r_kcnt, east=FONT_FANG, size=9)

    # 8. 正文解析与原生脚注收集
    footnotes = []

    def add_footnote_reference(paragraph, fn_text):
        footnotes.append(fn_text)
        fid = len(footnotes)
        run = paragraph.add_run()
        run.font.superscript = True
        set_run_font(run, east=FONT_SERIF, ascii_=FONT_TNR, size=8.5)
        ref = OxmlElement("w:footnoteReference")
        ref.set(qn("w:id"), str(fid))
        run._r.append(ref)

    def _append_inline_runs(
        paragraph,
        raw_segment: str,
        *,
        east=FONT_SERIF,
        ascii_=FONT_TNR,
        size=10.5,
        force_bold=False,
    ):
        for seg_text, is_bold, is_italic in parse_inline_segments(raw_segment):
            r = paragraph.add_run(seg_text)
            set_run_font(r, east=east, ascii_=ascii_, size=size, bold=bool(is_bold or force_bold), italic=is_italic)

    def render_body_text_with_footnotes(paragraph, raw_text, *, font_size=10.5, force_bold=False):
        pos = 0
        for start, end, fn_inner in iter_balanced_calls(raw_text, "#footnote["):
            before = raw_text[pos:start]
            if before:
                _append_inline_runs(paragraph, before, east=FONT_SERIF, ascii_=FONT_TNR, size=font_size, force_bold=force_bold)
            add_footnote_reference(paragraph, fn_inner.strip())
            pos = end
        tail = raw_text[pos:]
        if tail:
            _append_inline_runs(paragraph, tail, east=FONT_SERIF, ascii_=FONT_TNR, size=font_size, force_bold=force_bold)

    for kind, text in blocks:
        if kind == "table":
            rows_data = text["rows"]
            cols_count = text["cols"]
            if rows_data and cols_count > 0:
                tbl = doc.add_table(rows=len(rows_data), cols=cols_count)
                try:
                    tbl.style = "Table Grid"
                except KeyError:
                    pass
                tblPr = tbl._tbl.tblPr
                borders = OxmlElement("w:tblBorders")
                for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    edge_el = OxmlElement(f"w:{edge}")
                    edge_el.set(qn("w:val"), "single")
                    edge_el.set(qn("w:sz"), "4")
                    edge_el.set(qn("w:space"), "0")
                    edge_el.set(qn("w:color"), "BFBFBF")
                    borders.append(edge_el)
                tblPr.append(borders)
                for r_idx, row_cells in enumerate(rows_data):
                    for c_idx, cell_val in enumerate(row_cells):
                        cell_p = tbl.cell(r_idx, c_idx).paragraphs[0]
                        cell_p.paragraph_format.space_before = Pt(2)
                        cell_p.paragraph_format.space_after = Pt(2)
                        cell_p.paragraph_format.line_spacing = 1.15
                        render_body_text_with_footnotes(
                            cell_p,
                            cell_val,
                            font_size=9.5,
                            force_bold=(r_idx == 0),
                        )
        elif kind == "h1":
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pf.space_before = Pt(14)
            pf.space_after = Pt(8)
            pf.line_spacing = Pt(16)
            pf.keep_with_next = True
            r = p.add_run(text)
            set_run_font(r, east=FONT_SANS, size=12, bold=True)
        elif kind == "h2":
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf.first_line_indent = Pt(21)
            pf.space_before = Pt(8)
            pf.space_after = Pt(6)
            pf.line_spacing = Pt(16)
            pf.keep_with_next = True
            r = p.add_run(text)
            set_run_font(r, east=FONT_SANS, size=10.5, bold=True)
        elif kind == "h3":
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
            pf.first_line_indent = Pt(21)
            pf.space_before = Pt(6)
            pf.space_after = Pt(4)
            pf.line_spacing = Pt(16)
            pf.keep_with_next = True
            r = p.add_run(text)
            set_run_font(r, east=FONT_SERIF, size=10.5, bold=True)
        else:
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            pf.first_line_indent = Pt(21)
            pf.line_spacing = Pt(16)
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            render_body_text_with_footnotes(p, text)

    # 9. 英文摘要区 (仅当指定时渲染)
    if meta["title_en"]:
        p_te = doc.add_paragraph()
        p_te.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_te.paragraph_format.space_before = Pt(24)
        p_te.paragraph_format.space_after = Pt(4)
        p_te.paragraph_format.keep_with_next = True
        r_te = p_te.add_run(meta["title_en"])
        set_run_font(r_te, east=FONT_TNR, ascii_=FONT_TNR, size=13.5, bold=True)

    if meta["abstract_en"]:
        p_ae = doc.add_paragraph()
        pf_ae = p_ae.paragraph_format
        pf_ae.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf_ae.line_spacing = Pt(13)
        pf_ae.space_before = Pt(6)
        pf_ae.space_after = Pt(0)
        r_albl = p_ae.add_run("Abstract: ")
        set_run_font(r_albl, east=FONT_TNR, ascii_=FONT_TNR, size=9.5, bold=True)
        _append_inline_runs(p_ae, meta["abstract_en"], east=FONT_TNR, ascii_=FONT_TNR, size=9.5)

    if meta["keywords_en"]:
        p_ke = doc.add_paragraph()
        pf_ke = p_ke.paragraph_format
        pf_ke.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf_ke.line_spacing = Pt(13)
        pf_ke.space_before = Pt(0)
        pf_ke.space_after = Pt(0)
        r_klbl = p_ke.add_run("Key words: ")
        set_run_font(r_klbl, east=FONT_TNR, ascii_=FONT_TNR, size=9.5, bold=True)
        r_kcnt = p_ke.add_run(meta["keywords_en"])
        set_run_font(r_kcnt, east=FONT_TNR, ascii_=FONT_TNR, size=9.5)

    # 10. 临时保存并注入原生 Word 脚注 XML 部件
    out_path = Path(output_docx_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_base = out_path.with_name(f"__temp_{out_path.stem}_{os.getpid()}.docx")
    doc.save(str(temp_base))

    inject_native_footnotes(temp_base, out_path, footnotes)
    try:
        temp_base.unlink(missing_ok=True)
    except OSError:
        pass

    return len(footnotes)


def inject_native_footnotes(base_docx_path: Path, final_docx_path: Path, footnotes: list):
    """向 docx 压缩包内注入符合 OpenXML 规范的 /word/footnotes.xml"""
    final_docx_path.parent.mkdir(parents=True, exist_ok=True)
    tempdir = Path(tempfile.mkdtemp(prefix=".docx-fn-inject-", dir=str(final_docx_path.parent)))
    try:
        with zipfile.ZipFile(base_docx_path, 'r') as zin:
            zin.extractall(tempdir)

        # 构建 footnotes.xml
        root = OxmlElement('w:footnotes')

        # 默认的 separator 与 continuationSeparator
        for fid in (-1, 0):
            fn = OxmlElement('w:footnote')
            fn.set(qn('w:id'), str(fid))
            fn.set(qn('w:type'), 'separator' if fid == -1 else 'continuationSeparator')
            p = OxmlElement('w:p')
            r = OxmlElement('w:r')
            r.append(OxmlElement('w:separator' if fid == -1 else 'w:continuationSeparator'))
            p.append(r)
            fn.append(p)
            root.append(fn)

        for fid, text in enumerate(footnotes, start=1):
            fn = OxmlElement('w:footnote')
            fn.set(qn('w:id'), str(fid))
            p = OxmlElement('w:p')
            pp = OxmlElement('w:pPr')
            pstyle = OxmlElement('w:pStyle')
            pstyle.set(qn('w:val'), 'FootnoteText')
            pp.append(pstyle)
            p.append(pp)

            # 编号引用上标
            r1 = OxmlElement('w:r')
            rpr1 = OxmlElement('w:rPr')
            rstyle = OxmlElement('w:rStyle')
            rstyle.set(qn('w:val'), 'FootnoteReference')
            rpr1.append(rstyle)
            r1.append(rpr1)
            r1.append(OxmlElement('w:footnoteRef'))
            p.append(r1)

            # 脚注内容文本（支持内联斜体/加粗与空格保留）
            segments = parse_inline_segments(text) or [(text.replace("\\", ""), False, False)]
            for idx_seg, (seg_text, is_bold, is_italic) in enumerate(segments):
                r2 = OxmlElement('w:r')
                rpr2 = OxmlElement('w:rPr')
                rf2 = OxmlElement('w:rFonts')
                rf2.set(qn('w:ascii'), FONT_TNR)
                rf2.set(qn('w:hAnsi'), FONT_TNR)
                rf2.set(qn('w:eastAsia'), FONT_SERIF)
                rpr2.append(rf2)
                sz2 = OxmlElement('w:sz')
                sz2.set(qn('w:val'), '17')  # 8.5 pt = 17 half-points
                rpr2.append(sz2)
                if is_bold:
                    rpr2.append(OxmlElement('w:b'))
                if is_italic:
                    rpr2.append(OxmlElement('w:i'))
                r2.append(rpr2)
                t = OxmlElement('w:t')
                t.set(qn('xml:space'), 'preserve')
                t.text = f" {seg_text}" if idx_seg == 0 else seg_text
                r2.append(t)
                p.append(r2)

            fn.append(p)
            root.append(fn)

        from lxml import etree
        fn_xml_path = tempdir / 'word' / 'footnotes.xml'
        fn_xml_path.write_bytes(etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone='yes'))

        # 关联 relationships
        rels_path = tempdir / 'word' / '_rels' / 'document.xml.rels'
        rels_tree = etree.parse(str(rels_path))
        rr = rels_tree.getroot()
        max_id = 0
        for rel in rr:
            rid = rel.get('Id', '')
            if rid.startswith('rId'):
                try:
                    max_id = max(max_id, int(rid[3:]))
                except ValueError:
                    pass
        new_rel = etree.SubElement(rr, '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship')
        new_rel.set('Id', f'rId{max_id + 1}')
        new_rel.set('Type', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes')
        new_rel.set('Target', 'footnotes.xml')
        rels_tree.write(str(rels_path), xml_declaration=True, encoding='UTF-8', standalone=True)

        # 注册 Content_Types
        ct_path = tempdir / '[Content_Types].xml'
        ct_tree = etree.parse(str(ct_path))
        cr = ct_tree.getroot()
        if not any(el.get('PartName') == '/word/footnotes.xml' for el in cr):
            ov = etree.SubElement(cr, '{http://schemas.openxmlformats.org/package/2006/content-types}Override')
            ov.set('PartName', '/word/footnotes.xml')
            ov.set('ContentType', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml')
            ct_tree.write(str(ct_path), xml_declaration=True, encoding='UTF-8', standalone=True)

        # 重新打包
        final_docx_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(final_docx_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for f in tempdir.rglob('*'):
                if f.is_file():
                    zout.write(f, f.relative_to(tempdir).as_posix())
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
