// CSSCI/CLSCI 法学期刊标准排版模板 (Typst 0.15+)
// 1:1 像素级对标《中国海商法研究》等中国法学核心期刊纸质版
// 全面兼容 Windows 本地与 Linux 容器（如舟序 /workspace）中文字体回退链

#let FONT_SONG = ("SimSun", "Songti SC", "STSong", "Noto Serif CJK SC", "Source Han Serif SC", "Times New Roman")
#let FONT_HEI = ("SimHei", "Heiti SC", "STHeiti", "Noto Sans CJK SC", "Source Han Sans SC", "Times New Roman")
#let FONT_KAI = ("KaiTi", "KaiTi_GB2312", "STKaiti", "Kaiti SC", "SimSun", "Times New Roman")
#let FONT_FANG = ("FangSong", "FangSong_GB2312", "STFangsong", "KaiTi", "KaiTi_GB2312", "STKaiti", "SimSun", "Times New Roman")

#let csl_paper(
  title: "",
  subtitle: "",
  author: "",
  affiliation: "",
  abstract: "",
  keywords: "",
  clc_code: "",
  doc_code: "",
  article_id: "",
  journal_cn: "",
  journal_en: "",
  vol: "",
  no: "",
  year: "",
  month: "",
  month_en: "",
  fund: "",
  author_bio: "",
  title_en: "",
  author_en: "",
  affiliation_en: "",
  abstract_en: "",
  keywords_en: "",
  doc_mode: "submission",
  length_tier: "standard",
  body,
) = {
  // 0. 防御性元数据清洗：未提供即留空；送审模式隐藏假刊头，刊样模式自动对齐季刊月份(3/6/9/12)
  let clean_author = if author in ("", "待补", "作者待补", "作者：待补") { "" } else { author }
  let clean_affil = if affiliation in ("", "待补", "单位待补", "（单位：待补）") { "" } else { affiliation }
  let clean_fund = if fund in ("", "待补") { "" } else { fund }
  let clean_bio = if author_bio in ("", "待补") { "" } else { author_bio }
  let is_proof = doc_mode == "journal_proof"

  let eff_journal_cn = if journal_cn != "" { journal_cn } else { "中 国 海 商 法 研 究" }
  let eff_journal_en = if journal_en != "" { journal_en } else { "Chinese Journal of Maritime Law" }

  let now = datetime.today()
  let eff_year = if year != "" { str(year) } else { str(now.year()) }
  let raw_month_num = if month != "" { int(month) } else { now.month() }
  // 季刊版若未显式传入合法季刊月份，自动对齐最近季刊出刊月 (3/6/9/12)
  let eff_month_num = if month != "" {
    raw_month_num
  } else if is_proof {
    if raw_month_num >= 12 { 12 } else if raw_month_num >= 9 { 9 } else if raw_month_num >= 6 { 6 } else { 3 }
  } else {
    raw_month_num
  }
  let eff_month = str(eff_month_num)
  let month_map = ("Jan.", "Feb.", "Mar.", "Apr.", "May", "Jun.", "Jul.", "Aug.", "Sep.", "Oct.", "Nov.", "Dec.")
  let eff_month_en = if month_en != "" { month_en } else { month_map.at(eff_month_num - 1) }

  let header_title = title

  // 1. 页面与版心几何设定 (A4版心，法学期刊标准边距)
  set page(
    paper: "a4",
    margin: (top: 2.8cm, bottom: 2.5cm, left: 2.5cm, right: 2.5cm),
    header: context {
      // 阻断正文首行缩进与两端对齐污染页眉（防止首页左上缩进及奇数页长标题被挤出截字）
      set par(first-line-indent: 0em, justify: false, leading: 0.45em, spacing: 0.45em)

      // 核心机制：每页重置脚注计数器，严格遵循法学期刊每页从 ① 重新计数
      counter(footnote).update(0)

      let page_num = here().page()
      if not is_proof {
        // 送审规范稿：首页留白不印假刊头与非刊期月份；正文页保留克制双栏/居中页眉与页码
        if page_num > 1 [
          #show linebreak: [ ]
          #let mid_len = if type(header_title) == str { header_title.clusters().len() } else { 20 }
          #let hdr_size = if mid_len > 30 { 8pt } else if mid_len > 25 { 8.5pt } else { 9pt }
          #if calc.even(page_num) [
            #grid(
              columns: (2.6em, 1fr, 2.6em),
              align: (left + bottom, center + bottom, right + bottom),
              text(size: 9pt, font: "Times New Roman")[#page_num],
              text(size: hdr_size, font: FONT_SONG)[#header_title],
              [],
            )
          ] else [
            #grid(
              columns: (2.6em, 1fr, 2.6em),
              align: (left + bottom, center + bottom, right + bottom),
              [],
              text(size: hdr_size, font: FONT_SONG)[#header_title],
              text(size: 9pt, font: "Times New Roman")[#page_num],
            )
          ]
          #v(-4pt)
          #line(length: 100%, stroke: 0.4pt)
        ]
      } else if page_num == 1 [
        // 刊印样稿模式首页双语刊头（卷/期留空时不虚构，季刊月份严格对齐）
        #let left_meta = {
          if vol != "" and no != "" [第 #vol 卷 第 #no 期 \ ]
          else if vol != "" [第 #vol 卷 \ ]
          else if no != "" [第 #no 期 \ ]
          [#eff_year 年 #eff_month 月]
        }
        #let right_meta = {
          if vol != "" and no != "" [Vol.#vol No.#no \ ]
          else if vol != "" [Vol.#vol \ ]
          else if no != "" [No.#no \ ]
          [#eff_month_en #eff_year]
        }
        #let center_meta = if eff_journal_en != "" [
          #text(size: 11pt, font: FONT_SONG)[#eff_journal_cn \ #text(size: 9.5pt, font: "Times New Roman")[#eff_journal_en]]
        ] else [
          #text(size: 11pt, font: FONT_SONG)[#eff_journal_cn]
        ]
        #grid(
          columns: (1fr, auto, 1fr),
          align: (left + bottom, center + bottom, right + bottom),
          text(size: 8.5pt, font: FONT_SONG)[#left_meta],
          center_meta,
          text(size: 8.5pt, font: "Times New Roman")[#right_meta],
        )
        #v(-3pt)
        #line(length: 100%, stroke: 0.9pt)
      ] else if calc.even(page_num) [
        // 偶数页眉: [页码] [期刊名] [第 X 卷(若指定)]
        #grid(
          columns: (4.2em, 1fr, 4.2em),
          align: (left + bottom, center + bottom, right + bottom),
          text(size: 9pt, font: "Times New Roman")[#page_num],
          text(size: 9pt, font: FONT_SONG)[#eff_journal_cn],
          if vol != "" { text(size: 9pt, font: FONT_SONG)[第 #vol 卷] } else { [] },
        )
        #v(-4pt)
        #line(length: 100%, stroke: 0.4pt)
      ] else [
        // 奇数页眉: [第 X 期(若指定)] [作者：主标题] [页码] (自适应字号，绝对居中不截字)
        #show linebreak: [ ]
        #let mid_text = if clean_author != "" {
          clean_author + "：" + header_title
        } else {
          header_title
        }
        #let mid_len = if type(mid_text) == str { mid_text.clusters().len() } else { 20 }
        #let hdr_size = if mid_len > 30 { 8pt } else if mid_len > 25 { 8.5pt } else { 9pt }
        #let side_w = if no != "" { 4.2em } else { 2.6em }
        #grid(
          columns: (side_w, 1fr, side_w),
          align: (left + bottom, center + bottom, right + bottom),
          if no != "" { text(size: 9pt, font: FONT_SONG)[第 #no 期] } else { [] },
          text(size: hdr_size, font: FONT_SONG)[#mid_text],
          text(size: 9pt, font: "Times New Roman")[#page_num],
        )
        #v(-4pt)
        #line(length: 100%, stroke: 0.4pt)
      ]
    },
    footer: none,
  )

  // 2. 正文字体与段落基准设定 (锁定中文方框基线高度，彻底消除段间距大于行间距的失真)
  set text(
    font: FONT_SONG,
    size: 10.5pt,
    lang: "zh",
    top-edge: 0.88em,
    bottom-edge: -0.12em,
  )
  set par(
    justify: true,
    first-line-indent: (amount: 2em, all: true),
    leading: 0.65em,
    spacing: 0.65em,
  )

  // 3. 页下注设定 (①②③ 带圈数字，单线隔离，首页面底无缝注入基金与作者注)
  set footnote(numbering: "①")
  set footnote.entry(
    indent: 0em,
    gap: 0.42em,
    separator: line(length: 30%, stroke: 0.5pt),
  )

  show footnote.entry: it => {
    let loc = it.note.location()
    let p = loc.page()
    let n = counter(footnote).at(loc).first()
    let num = numbering(it.note.numbering, n)
    set text(size: 8.5pt, font: FONT_SONG)
    set par(first-line-indent: 0em, leading: 0.58em, justify: true)

    // 在第 1 页第 1 条脚注正上方插入基金项目与作者简介，共享同一条分割线
    if p == 1 and n == 1 and (clean_fund != "" or clean_bio != "") [
      #block(below: 0.42em)[
        #if clean_fund != "" [#h(2em)#text(font: FONT_HEI, size: 8.5pt)[基金项目：]#clean_fund]
        #if clean_fund != "" and clean_bio != "" [\ ]
        #if clean_bio != "" [#h(2em)#text(font: FONT_HEI, size: 8.5pt)[作者简介：]#clean_bio]
      ]
    ]
    [#h(2em)#num#h(0.35em)#it.note.body]
  }

  // 4. 三级标题体系 (严格对齐《中国海商法研究》原刊视觉：大章保留克制呼吸感，二级/三级子标题首行缩进2字符并与正文等距紧密衔接)
  show heading.where(level: 1): it => {
    block(width: 100%, above: 1.15em, below: 0.9em, sticky: true, breakable: false)[
      #align(center)[
        #set par(first-line-indent: 0em)
        #text(font: FONT_HEI, size: 12pt)[#it.body]
      ]
    ]
  }

  show heading.where(level: 2): it => {
    block(width: 100%, above: 0.65em, below: 0.65em, sticky: true, breakable: false)[
      #set par(first-line-indent: 0em)
      #h(2em)#text(font: FONT_HEI, size: 10.5pt)[#it.body]
    ]
  }

  show heading.where(level: 3): it => {
    block(width: 100%, above: 0.65em, below: 0.65em, sticky: true, breakable: false)[
      #set par(first-line-indent: 0em)
      #h(2em)#text(font: FONT_SONG, size: 10.5pt, weight: "bold")[#it.body]
    ]
  }

  // 5. 论文标题与作者区 (超长主标题自动微调字阶，防止生硬断字孤字)
  let title_len = if type(title) == str { title.clusters().len() } else { 18 }
  let main_title_size = if title_len > 25 { 15pt } else if title_len > 22 { 16pt } else { 17pt }

  v(0.6em)
  align(center)[
    #set par(first-line-indent: 0em, leading: 0.55em)
    #text(size: main_title_size, font: FONT_HEI, weight: "bold")[#title]
    #if subtitle != "" [
      \ #v(0.2em) #text(size: 14pt, font: FONT_HEI, weight: "bold")[#subtitle]
    ]
    #if clean_author != "" [
      #v(0.85em)
      #text(size: 12pt, font: FONT_KAI)[#clean_author]
    ]
    #if clean_affil != "" [
      #v(0.25em)
      #text(size: 9.5pt, font: FONT_KAI)[#clean_affil]
    ]
  ]
  v(1.1em)

  // 6. 中文摘要与关键词栏目框 (左右缩进2em，首行再缩进2em，黑体引导词 + 仿宋[无仿宋环境自动回退楷体]正文，段距与行距严格等高)
  block(
    inset: (x: 2em),
    width: 100%,
    [
      #set text(size: 9pt, font: FONT_FANG, top-edge: 0.88em, bottom-edge: -0.12em)
      #set par(first-line-indent: (amount: 2em, all: true), leading: 0.55em, spacing: 0.55em, justify: true)

      #text(font: FONT_HEI, weight: "bold")[摘要：]#abstract

      #text(font: FONT_HEI, weight: "bold")[关键词：]#keywords

      #if clc_code != "" or doc_code != "" or article_id != "" [
        #if clc_code != "" [#text(font: FONT_HEI, weight: "bold")[中图分类号：]#text(font: "Times New Roman")[#clc_code]]
        #if clc_code != "" and doc_code != "" [#h(1.5em)]
        #if doc_code != "" [#text(font: FONT_HEI, weight: "bold")[文献标志码：]#text(font: "Times New Roman")[#doc_code]]
        #if article_id != "" [#h(1.5em)#text(font: FONT_HEI, weight: "bold")[文章编号：]#text(font: "Times New Roman")[#article_id]]
      ]
    ]
  )

  v(1.0em)

  // 兜底机制：仅当第 1 页完全没有任何脚注时，才独立渲染基金/作者栏
  context {
    let p1_fns = query(footnote).filter(f => f.location().page() == 1)
    if p1_fns.len() == 0 and (clean_fund != "" or clean_bio != "") {
      place(
        bottom,
        float: true,
        clearance: 1.2em,
        [
          #line(length: 30%, stroke: 0.5pt)
          #v(3pt)
          #set text(size: 8.5pt, font: FONT_SONG)
          #set par(first-line-indent: 0em, leading: 0.58em)
          #if clean_fund != "" [#h(2em)#text(font: FONT_HEI, size: 8.5pt)[基金项目：]#clean_fund \ ]
          #if clean_bio != "" [#h(2em)#text(font: FONT_HEI, size: 8.5pt)[作者简介：]#clean_bio]
        ]
      )
    }
  }

  // 7. 正文内容注入
  body

  // 8. 尾部英文摘要区 (严格对齐标杆末页：全宽两端对齐，防孤行跨页撕裂)
  if title_en != "" [
    #block(width: 100%, above: 2.0em, below: 0.9em, breakable: false, sticky: true)[
      #align(center)[
        #set par(first-line-indent: 0em, leading: 0.55em, spacing: 0.45em)
        #text(size: 13.5pt, font: "Times New Roman", weight: "bold")[#title_en]
        #if author_en != "" and not author_en.contains("To be") [
          \ #v(0.3em) #text(size: 10.5pt, font: "Times New Roman")[#author_en]
        ]
        #if affiliation_en != "" and not affiliation_en.contains("To be") [
          \ #text(size: 9.5pt, font: "Times New Roman")[#affiliation_en]
        ]
      ]
    ]

    #block(
      width: 100%,
      [
        #set text(size: 9.5pt, font: "Times New Roman", top-edge: 0.88em, bottom-edge: -0.12em)
        #set par(first-line-indent: 0em, leading: 0.48em, spacing: 0.48em, justify: true)
        #text(weight: "bold")[Abstract:] #abstract_en

        #text(weight: "bold")[Key words:] #keywords_en
      ]
    )
  ]
}
