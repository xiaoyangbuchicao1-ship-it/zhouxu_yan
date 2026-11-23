"""
CSSCI 法学期刊双链路编译执行器 (4.0 出版级 · 并发沙箱与反 AI 语体门禁版)
一键将 .typ 源码同时编译为：
1. 1:1 像素复刻法学核心期刊的高保真 PDF (基于 Typst)
2. 原生排版、原生带圈页下脚注、对齐体例的 Word (.docx) (基于 python-docx + OpenXML 注入)

多用户并发与临时构建隔离策略：
- 源码与研究台账由调用方放在各自会话隔离工作区（如 ./work/<topic_slug>/ 或 $CSL_WORK_DIR/<slug>/）；
- 编译过程在本工具生成的瞬态隔离沙箱内完成，绝不向源码目录临时写入或删除 csl_template.typ，彻底避免并发竞争与只读权限冲突。
"""

import sys
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_DIR / "assets" / "csl_template.typ"
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import typ_to_docx  # noqa: E402


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def get_default_output_dir() -> Path:
    env_dir = os.environ.get("CSL_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("./output")


DEFAULT_OUTPUT_DIR = get_default_output_dir()


def get_python_with_typst() -> str:
    return sys.executable


def _strip_footnotes(text: str) -> tuple[str, list[str]]:
    """移除正文中的 `#footnote[...]` 以便精准统计正文汉字与行文结构。"""
    footnotes: list[str] = []
    parts: list[str] = []
    pos = 0
    for start, end, inner in typ_to_docx.iter_balanced_calls(text, "#footnote["):
        parts.append(text[pos:start])
        footnotes.append(inner.strip())
        pos = end
    parts.append(text[pos:])
    return "".join(parts), footnotes


def lint_typst_source(source_text: str) -> tuple[list[str], list[str]]:
    """编译前静态质量与反 AI 语体审计：返回 `(errors, warnings)`。"""
    errors: list[str] = []
    warnings: list[str] = []

    meta, blocks = typ_to_docx.parse_typ_paper(source_text)

    # 1. 结构与排版语法红线
    if re.search(r"^\s*=+\s*(引言|导言|绪论|Introduction)\s*$", source_text, re.MULTILINE):
        errors.append("违规包含 `= 引言` 标题：法学核心期刊导语段落紧接摘要之后直接正文起步，严禁添加“引言”标题。")

    if re.search(r"^\s*=+\s*(参考文献|参考规范|参考资料|Bibliography|References)\s*$", source_text, re.MULTILINE):
        errors.append("违规包含文末【参考文献/参考规范】章节：法学核心期刊必须采用纯页下脚注，严禁文末列参考文献。")

    for idx, line in enumerate(source_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("="):
            continue
        if len(stripped) <= 38 and not stripped.endswith(("。", "，", "；", "：")):
            if re.match(r"^[一二三四五六七八九十]+、\s*\S+", stripped):
                errors.append(f"第 {idx} 行疑似漏写一级标题标记 `= `：`{stripped}`（请改为 `= {stripped}`）。")
            elif re.match(r"^（[一二三四五六七八九十]+）\s*\S+", stripped):
                errors.append(f"第 {idx} 行疑似漏写二级标题标记 `== `：`{stripped}`（请改为 `== {stripped}`）。")

    for table_or_fig in ("#table(", "table(", "#image(", "image(", "#figure(", "figure("):
        if table_or_fig in source_text:
            warnings.append(f"检测到图表调用 `{table_or_fig}`：表格将同步转为 Word 表格（图片或复杂嵌套排版请核对导出表现）。")
            break

    for dirty in ("待补", "To be completed", "To be supplied", "拟投稿研究稿"):
        if dirty in source_text:
            errors.append(f"发现粗糙占位符 `{dirty}`：请使用规范学术元数据或留空，严禁输出占位字样。")

    # 2. 季刊刊期校验（仅在 journal_proof 刊印模式下对默认《中国海商法研究》校验）
    doc_mode = (meta.get("doc_mode") or "submission").strip()
    journal_cn = (meta.get("journal_cn") or "").replace(" ", "")
    month_val = (meta.get("month") or "").strip()
    if doc_mode == "journal_proof" and (not journal_cn or "中国海商法研究" in journal_cn) and month_val:
        try:
            m_int = int(month_val)
            if m_int not in (3, 6, 9, 12):
                errors.append(f"刊印样稿模式月份违规：`《中国海商法研究》`为季刊（3/6/9/12 月出版），不可传入 `month: \"{month_val}\"`。")
        except ValueError:
            errors.append(f"非法月份格式：`month: \"{month_val}\"`。")

    # 3. 禁止套用旧版固化五章标题
    stale_headings = (
        "问题的缘起与基本范畴",
        "既有规范框架的适用张力与学说检视",
        "核心制度的法理基础与运行机理",
        "现行法框架下的解释论展开与司法适用",
    )
    hit_stale = [h for h in stale_headings if h in source_text]
    if len(hit_stale) >= 2:
        errors.append(f"检测到机械套用旧版固化章节模板 {hit_stale}：请依据具体法学争议内生命名各章标题，禁止套壳。")

    # 4. 禁止 AI 检索自白、内部工具泄漏与虚构占位案名
    confession_terms = (
        "公开转载的开篇", "主办单位公布", "仅采用摘要", "仅取得公开摘要",
        "本次未取得原文", "card_id", "information_card", "dynamic_rag", "search_cases", "capability_card",
    )
    hit_confessions = [t for t in confession_terms if t in source_text]
    if hit_confessions:
        errors.append(f"检测到 AI 检索自白或内部工具字样 {hit_confessions}：学术正文与脚注严禁汇报检索受限或工具名，仅保留可核验的规范出处。")

    fake_case_patterns = (
        r"XXXX+",
        r"某某[\u4e00-\u9fff]{0,8}公司",
        r"(?:原告|被告|第三人)[A-Z甲乙丙]",
        r"甲公司诉乙公司",
    )
    fake_hits = []
    for pat in fake_case_patterns:
        fake_hits.extend(re.findall(pat, source_text))
    if fake_hits:
        errors.append(f"检测到虚构占位案名/主体 {fake_hits[:3]}：必须援引真实核验过的海事判例，纯理论推演请使用“设若……”而不使用占位主体。")
    anon_cases = re.findall(r"某[\u4e00-\u9fff]{1,10}公司[^。；\n]{0,18}案", source_text)
    if anon_cases and not re.search(r"[（(]\d{4}[)）][^。\n\]]{1,14}\d+号", source_text):
        warnings.append(f"检测到匿名化案名 {anon_cases[:2]} 且全文未见配套真实案号：请确认其源自真实裁判库（如人民法院案例库）而非自行虚构。")

    # 5. 正文抽取与定量语言风格审计
    raw_paras = [txt for kind, txt in blocks if kind == "p"]
    clean_paras: list[str] = []
    all_footnotes: list[str] = []
    for p in raw_paras:
        p_clean, fns = _strip_footnotes(p)
        all_footnotes.extend(fns)
        p_clean = re.sub(r"#(?:emph|strong)\[([^\]]*)\]", r"\1", p_clean).strip()
        if p_clean:
            clean_paras.append(p_clean)

    body_text = "\n".join(clean_paras)
    body_hanzi = len(re.findall(r"[\u4e00-\u9fff]", body_text))
    fn_count = len(all_footnotes)

    length_tier = (meta.get("length_tier") or "standard").strip()
    hard_min_chars = 3800 if length_tier == "compact" else 4800
    target_min_chars = 7500 if length_tier == "compact" else 9500
    if body_hanzi < hard_min_chars:
        warnings.append(f"正文实质汉字 {body_hanzi} 字（低于 {length_tier} 档建议底线 {hard_min_chars} 字），若需投稿级长文可进一步充实法条细读、案例分析与学理对话。")
    elif body_hanzi < target_min_chars:
        warnings.append(f"正文实质汉字 {body_hanzi} 字，尚低于核心期刊建议体量（{target_min_chars}+ 字），建议进一步充实实证案例与文献展开。")

    # 6. 引注丰度与结构检验
    if fn_count < 12:
        warnings.append(f"全文当前共 {fn_count} 条脚注（低于核心期刊常见 12 条基准），如非短篇研究可补充期刊、专著与裁判对话。")
    if fn_count > 0:
        journal_hits = sum(1 for fn in all_footnotes if re.search(r"载《|年第\s*\d+\s*期|Vol\.|Review|Quarterly|Law", fn))
        book_hits = sum(1 for fn in all_footnotes if re.search(r"出版社\s*\d{4}\s*年版|Press|Sweet\s*&\s*Maxwell|Routledge|Carswell", fn))
        case_hits = sum(1 for fn in all_footnotes if re.search(r"判决书|裁定书|指导案例|\(\d{4}\)|（\d{4}）|\sv\.\s", fn))
        if journal_hits == 0 and case_hits == 0:
            warnings.append(f"引注结构偏法条化（共 {fn_count} 条脚注，但期刊论文 0 条、司法裁判 0 条），建议补入文献与裁判对话。")
        elif journal_hits < 3 or (case_hits == 0 and book_hits < 2):
            warnings.append(f"引注结构偏单薄（总 {fn_count} 条：期刊 {journal_hits}、专著 {book_hits}、裁判 {case_hits}），建议补充核心期刊论文与真实海事判例。")

        all_statutes = all(
            any(k in fn for k in ("条", "款", "项", "公约", "法》"))
            and not any(k in fn for k in ("载《", "出版社", "年版", "判决书", "裁定书", "Review", "Law", "pp.", "页"))
            for fn in all_footnotes
        )
        if all_statutes:
            warnings.append("当前脚注均为法规或公约条款，可按需补充学术文献与司法案例引注。")

    # 7. 反 AI “压缩饼干”复合语体门禁（基于 39 篇 2025 真实刊物统计特征，仅复合越界才阻断）
    if body_hanzi >= 1500 and len(clean_paras) >= 8:
        scale = max(len(body_text) / 10000.0, 0.2)
        para_lens = [len(p) for p in clean_paras]
        para_mean = statistics.mean(para_lens)
        para_std = statistics.stdev(para_lens) if len(para_lens) > 1 else 0.0
        semicolons_10k = body_text.count("；") / scale
        colons_10k = (body_text.count("：") + body_text.count(":")) / scale
        long_quotes_10k = len(re.findall(r"“[^”]{12,}”", body_text)) / scale
        not_but_10k = len(re.findall(r"(?:不是|并非|而不|而非)[^。；]{2,40}(?:而是|而在于|而是指)", body_text)) / scale
        buneng_10k = body_text.count("不能") / scale

        cliche_patterns = (
            r"文义射程", r"评价裂痕", r"价值裂痕", r"分层构造", r"三维体系", r"三维架构",
            r"底层逻辑", r"问题并不?在于[^。]{2,25}而在于", r"先行[^。]{0,8}居中[^。]{0,8}收束",
        )
        cliche_hits = []
        for pat in cliche_patterns:
            cliche_hits.extend(re.findall(pat, body_text))

        print(
            f"[Style Telemetry] 正文汉字={body_hanzi}, 脚注={fn_count}, 段数={len(clean_paras)}, "
            f"段均长={para_mean:.0f}(标准差={para_std:.0f}), 分号/万字={semicolons_10k:.1f}, "
            f"冒号/万字={colons_10k:.1f}, 长引语/万字={long_quotes_10k:.1f}, 不能/万字={buneng_10k:.1f}",
            file=sys.stderr,
        )

        if len(cliche_hits) >= 3:
            errors.append(f"检测到多处高唤醒 AI 学术套话 {cliche_hits[:4]}：请改写为平实克制的法言法语。")
        elif cliche_hits:
            warnings.append(f"发现疑似 AI 套话用语 {cliche_hits[:3]}，建议审视是否可替换为更平实的法学表述。")
        if semicolons_10k > 30 and colons_10k > 30:
            errors.append(
                f"检测到严重 AI 排比/总领冒号腔（分号 {semicolons_10k:.1f}/万字、冒号 {colons_10k:.1f}/万字，远超真刊基准 ~10.6 / ~13.0）：请拆改回逗号和句号主导的连续叙事。"
            )
        if para_mean < 200 and long_quotes_10k < 4.5:
            errors.append(
                f"段落过碎且缺乏原典直接引述（段均长 {para_mean:.0f} 字、长引语 {long_quotes_10k:.1f}/万字，真刊基准 ~345 字 / ~14.1）：请合并单薄段落并直接引述条文、判词与学者原话。"
            )
        if not_but_10k > 2.8 and buneng_10k > 11.0:
            errors.append(
                f"检测到高频二元翻转与否定说教腔（`并非/不是…而是…` {not_but_10k:.1f}/万字、`不能` {buneng_10k:.1f}/万字）：请改为平实正面的法理推演。"
            )

    return errors, warnings


def compile_dual_paper(
    input_path: str,
    output_dir: str = None,
    make_pdf: bool = True,
    make_docx: bool = True,
    strict: bool = True,
) -> bool:
    _configure_utf8_streams()
    input_file = Path(input_path).resolve()
    if not input_file.exists():
        print(f"[Error] 输入文件不存在: {input_file}", file=sys.stderr)
        return False

    source_text = input_file.read_text(encoding="utf-8")
    lint_errors, lint_warnings = lint_typst_source(source_text)
    for warn in lint_warnings:
        print(f"[Quality Warn] {warn}", file=sys.stderr)
    if lint_errors:
        for issue in lint_errors:
            print(f"[Lint {'Error' if strict else 'Warn'}] {issue}", file=sys.stderr)
        if strict:
            return False

    out_dir = Path(output_dir).resolve() if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_output = out_dir / f"{input_file.stem}.pdf"
    docx_output = out_dir / f"{input_file.stem}.docx"

    success_pdf = True
    success_docx = True

    # 1. 编译 PDF (Typst) —— 在 out_dir 内创建并发安全的瞬态构建沙箱，绝不修改源码目录
    if make_pdf:
        try:
            with tempfile.TemporaryDirectory(prefix=".csl_build_", dir=str(out_dir)) as tmp_dir:
                build_dir = Path(tmp_dir)
                staged_typ = build_dir / input_file.name
                shutil.copy2(input_file, staged_typ)
                if TEMPLATE_PATH.exists():
                    shutil.copy2(TEMPLATE_PATH, build_dir / "csl_template.typ")

                try:
                    import typst
                    typst.compile(str(staged_typ), output=str(pdf_output))
                    print(f"[Success] PDF 交付完成: {pdf_output}")
                except ImportError:
                    py_exe = get_python_with_typst()
                    child_env = os.environ.copy()
                    child_env["PYTHONIOENCODING"] = "utf-8"
                    cmd = [
                        py_exe,
                        "-c",
                        "import sys, typst; typst.compile(sys.argv[1], output=sys.argv[2])",
                        str(staged_typ),
                        str(pdf_output),
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env)
                    if res.returncode == 0:
                        print(f"[Success] PDF 交付完成: {pdf_output}")
                    else:
                        diag = (res.stderr + "\n" + res.stdout).replace(str(build_dir), str(input_file.parent))
                        print(f"[Error] Typst 编译失败:\n{diag}", file=sys.stderr)
                        success_pdf = False
        except Exception as e:
            print(f"[Error] Typst 编译异常: {e}", file=sys.stderr)
            success_pdf = False

    # 2. 编译 Word DOCX (原生排版与圈码脚注)
    if make_docx:
        try:
            if getattr(typ_to_docx, "HAS_DOCX", False):
                n = typ_to_docx.build_docx_from_typ(str(input_file), str(docx_output))
                print(f"[Success] Word (DOCX) 交付完成: {docx_output} (注入原生脚注: {n} 条)")
            else:
                py_exe = get_python_with_typst()
                child_env = os.environ.copy()
                child_env["PYTHONIOENCODING"] = "utf-8"
                cmd = [
                    py_exe,
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); import typ_to_docx; "
                    "n = typ_to_docx.build_docx_from_typ(sys.argv[2], sys.argv[3]); "
                    "print(f'[Success] Word (DOCX) 交付完成: {sys.argv[3]} (注入原生脚注: {n} 条)')",
                    str(SCRIPTS_DIR),
                    str(input_file),
                    str(docx_output),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env)
                if res.returncode == 0:
                    print(res.stdout.strip())
                else:
                    print(f"[Error] DOCX 生成失败:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
                    success_docx = False
        except Exception as e:
            print(f"[Error] DOCX 生成异常: {e}", file=sys.stderr)
            success_docx = False

    return success_pdf and success_docx


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python compile_paper.py <input.typ> [output_dir] [--pdf-only] [--docx-only] [--no-strict]")
        sys.exit(1)

    args = sys.argv[1:]
    strict_mode = "--no-strict" not in args
    make_pdf = "--docx-only" not in args
    make_docx = "--pdf-only" not in args

    cleaned_args = [a for a in args if not a.startswith("--")]
    inp = cleaned_args[0]
    out = cleaned_args[1] if len(cleaned_args) > 1 else None

    ok = compile_dual_paper(inp, out, make_pdf=make_pdf, make_docx=make_docx, strict=strict_mode)
    sys.exit(0 if ok else 1)
