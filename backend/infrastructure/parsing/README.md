# 材料解析与读取

解析存储与模型上下文分离。解析器只恢复内容、顺序和结构，不判断海事业务或法律结论。整体边界见[当前架构](../../../docs/architecture.md)。

## 文件与合同

- `registry.py`：格式选择、文件资源边界、引擎依赖检查、内容指纹、配置版本、缓存入口和可选视觉回退。应用权限解析必须先完成。
- `normalization.py`：统一 `MaterialManifest/MaterialUnit` 的材料身份、解析版本、原文顺序与覆盖。`unit_id` 就是内部块身份，不再建立另一套 block 合同。
- `parse_cache.py`：在 `data/files/derived/materials/<parse_version>/` 原子保存 `material.json`。原件仍在原上传存储，解析全文不按模型预算截断。失败与不可用结果不作为成功缓存；没有已记录 OCR 运行版本时不复用跨 Run 的图片 OCR 缓存。
- `catalog.py`：每个 Run 的授权材料绑定、读取位置、实际上下文覆盖、粘贴原文和阅读候选。关闭一个 Run 只删除其绑定和状态，不清空缓存或其他 Run。
- `read_view.py`：overview、read、search 的确定性阅读视图；读操作复用解析产物，不重新 OCR。
- `adapters/`：原生格式适配器，以及隔离的 MinerU SDK 端口。

文档层保留名称、内容指纹、解析版本、实际引擎/配置、状态、已解析/缺失范围、完整性与资源。块层保留身份、版本、顺序、类型、正文、结构化数据、来源位置和资源引用。模型接收的是阅读投影，不是完整内部对象。

## 原生格式

| 格式 | 当前路径 | 已实现的边界 |
|---|---|---|
| DOCX | python-docx + 原始包结构 | 按 body 子节点顺序交错段落和表格；表格按行段保存矩阵，仅复用显式重复表头；保留附注、批注、页眉页脚与内嵌资源，不编造 Word 页码。修订、视觉内容和无法可靠定位的图像标记为部分可用。|
| XLSX / CSV | openpyxl / csv | 分行段而非整表文本块；保留工作表及行列范围、矩阵、合并区域；公式、文件缓存值与未执行的程序计算明确区分，不猜首行为表头。|
| PDF | pypdf 原生页级文本 | 保留原始页码；空白/不可提取页明确记录，原生文本阅读顺序不等于视觉版面恢复。没有声称已恢复复杂表格或图示。|
| 图片 | Pillow + 可选 Tesseract | 保存图像及 OCR 状态；OCR 正文不等于完整视觉理解。图像按需传输，必须同时满足当前模型能力和请求预算。|
| TXT / Markdown / JSON | 原文文本 | 不美化或永久截断原文；按段落保留字符范围，单个长段落可从块内续读。UTF-8 替换解码明确标记 partial。|
| HTML / 链接 | BeautifulSoup / httpx | 原生 HTML 文本或已有链接读取；网络获取有资源上限，超过上限标记缺失范围。URL 不作为永久内容缓存键，快照按已取得内容指纹保存。|

DOC、XLS、PPTX 当前未注册原生适配器，不因为扩展名常见就宣称支持。复杂 Word 容器、图片锚点、PDF 表格版面、CSV 编码/方言及 Excel 公式重算仍有质量限制，需真实样本验证。Office 不默认转为图片，也不先经过主模型语义分析。

## 阅读规则

对外 `read_material` 支持 overview/read，默认直接返回正文，显式 overview 才列有限目录。可按单元、范围、首尾或游标读取；独立的 `search_material` 定位匹配词和附近摘录，复用内部搜索视图。

游标编码材料身份、解析版本、块序号与块内字符偏移。读满当前块后继续相邻块；单块读不完时保留块内位置；游标版本变化返回 invalid_cursor；末尾返回 end。搜索的未命中只表示当前已解析范围无匹配。未解析、依赖不可用、失败、未定位和正常结束分开表达。

返回正文、位置、覆盖、截断标记、下一游标与缺失说明。一次视图最多128块，是阅读视图资源边界，全文仍保存在缓存。后台的 Run 额度限制工具参数，max_chars 只能减小它。工具还会有限缩小过大的序列化阅读视图；完整请求最终仍由模型适配器统一检查。

短且明确相关的材料提供充分正文；多材料未被明确指向时先提供可读取候选，避免自动塞入全部正文。无可靠边界的长混合用户输入完整保存为用户来源材料，未覆盖全部原始输入前不能标记任务完成。明确 `<material>…</material>` 或 material 围栏才用于确定性分离，不能声称理解了任意隐含约束。

## MinerU 4.1.4

已下载发行包并静态检查 SDK 与依赖，未运行解析推理。主环境的 RAG 使用 Transformers 4.x，MinerU 的跨平台依赖要求与其冲突，因此使用独立环境及独立锁文件：

```powershell
uv sync --project backend/infrastructure/parsing/mineru_runtime --locked
```

本轮没有执行该安装命令，没有安装该独立运行环境或下载权重。安装与配置就绪后，设置 `materials.mineru_python`、`mineru_enabled`、`mineru_runtime_ready` 和覆盖本地权重/配置的 `mineru_runtime_revision`；OCR 运行版本使用 `ocr_runtime_revision`。缺少环境、版本不匹配、权重/配置指纹未记录时明确报告不可用，不能把下载了 wheel 当成解析验证完成。

`adapters/mineru.py` 只负责本地子进程和受限超时；`mineru_worker.py` 在独立解释器使用 `mineru.parser.parse/ParseResult`。显式选择本地 llama-cpp VLM，关闭遥测并设置子进程离线环境，不配置远程解析地址。默认 Standard，可配置 Basic/Advanced；Flash 不作为扫描材料的默认质量回退。当前端口对 PDF/图片选择性回退，Office 仍走原生适配器。

SDK 默认 page_range 为空，解析全部 PDF 页面；范围参数采用1基页码，输出 page_idx 是原始0基索引，合同转换为1基页码。`ParseResult.save()` 保存 Middle JSON、Structured Content、Markdown 与图片，再从导出的结构恢复统一材料；不把 `mineru parse --json` 的命令响应当成中间协议。资源路径须留在解析产物目录，不通过 CLI 文档库、队列或数据库保存材料。

官方依据（2026-11-02）：[SDK](https://opendatalab.github.io/MinerU/usage/sdk_api/)、[输出合同](https://opendatalab.github.io/MinerU/reference/output_files/)、[档位与运行环境](https://opendatalab.github.io/MinerU/usage/tiers/)、[许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)。4.1.4 要求 Python 3.11–3.14；本项目独立环境锁为3.11。其许可是 Apache 2.0 加附加条款，包含对外在线服务标识义务与商业门槛；部署前仍须核对所用版本及相关模型许可。本轮仅完成静态接入，未验证 Windows 运行环境、OCR 准确率或视觉资源输出质量。

固定材料与验收输入见 [datasets](../../../datasets/README.md)，基础版本不附旧测试执行代码与结果。
