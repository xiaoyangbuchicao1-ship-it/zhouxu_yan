"""Display metadata for the built-in skills shown to users."""

READY_BUILTIN_METADATA: dict[str, dict[str, str]] = {
    "maritime-case-research": {
        "display_name": "海事案例检索",
        "mode_scope": "chat",
        "deliverable_tag": "只读检索",
        "summary": "检索判决并精读原文，严格分离主张与法院认定。",
        "starter_prompt": "请检索与以下案情相近的海事判决并读取原文，区分当事人主张与法院认定：",
    },
    "maritime-legal-research": {
        "display_name": "海事条文检索",
        "mode_scope": "chat",
        "deliverable_tag": "只读检索",
        "summary": "检索海商法条并核对现行与历史版本及出处。",
        "starter_prompt": "请核对以下海商法律义务、期限或责任限制的现行及历史版本规则，并附原文出处：",
    },
    "maritime-paper-research": {
        "display_name": "海事论文检索",
        "mode_scope": "chat",
        "deliverable_tag": "只读检索",
        "summary": "检索已核验学术论文观点、作者与期刊页码线索。",
        "starter_prompt": "请检索以下主题的已核验海事法学论文资料，提炼核心学术观点并列明规范出处：",
    },
    "csl-law-paper-generator": {
        "display_name": "海事论文写作",
        "mode_scope": "work",
        "deliverable_tag": "PDF · Word",
        "summary": "检索文献与法条依规撰稿，并编译 PDF 与 Word。",
        "starter_prompt": "请围绕以下选题撰写规范中文海事法学论文，使用检索依据支撑论点，并生成 PDF 与 Word 文档：",
    },
}
