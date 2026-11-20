# 科研资源接入

用户可在原来的聊天/工作入口检索论文观点、选择研究方法，以及按完整的
`csl-law-paper-generator` 生成论文和 Word/PDF。启动入口不变。

## 文件放置

```text
论文库/
├─ 信息卡/                              原始数据，只读，不复制进项目
├─ 能力卡/                              原始方法，只读，不复制进项目
├─ 技能候选/csl-law-paper-generator/     完整原始 Skill，只读
└─ 真正的舟序/
   ├─ backend/
   │  ├─ contracts/modules/research_cards.py
   │  ├─ infrastructure/search/research_cards.py
   │  ├─ infrastructure/tools/research_cards.py
   │  ├─ infrastructure/tools/paper_compile.py
   │  └─ skills/registry.py              只登记原 Skill；不重写原文
   ├─ opensearch/
   │  ├─ opensearch_delivery/
   │  │  ├─ scripts/information_cards_index.py
   │  │  └─ index_definitions/maritime_information_cards_v2/
   │  ├─ import/information_cards/       cards.jsonl、sources.jsonl、向量与哈希清单
   │  └─ vector_db_data/opensearch/      法规、案例、信息卡共用数据目录
   ├─ models/
   │  ├─ huggingface/                   固定 BGE-M3 缓存，不进入镜像/Git
   │  └─ fonts/                        模板所用宋体、黑体、楷体、仿宋与 Times
   ├─ data/
   │  ├─ runtime/                      持久化任务记录，重建容器后保留
   │  └─ files/generated/run-workspaces/ 各账户/会话源码与最终文件
   └─ deploy/app/compose.yaml           原启动入口与只读挂载
```

信息卡、能力卡、原 Skill 不在项目里重复存一份。部署时可通过 `.env` 的目录变量
指定其他外部位置。模型、字体及向量是运行资源，需随部署数据准备。

## 参考与二创依据

| 模块 | 复用对象 | 原因 | 借用与简化 |
|---|---|---|---|
| 论文观点检索 | 项目已有 OpenSearch 双路检索；[OpenSearch k-NN](https://docs.opensearch.org/2.19/search-plugins/knn/) | 与原法规库共享检索基础设施 | 复用 HTTP 生命周期、过滤、HNSW、RRF；仅新增独立卡片字段与索引 |
| 向量 | [BAAI/BGE-M3](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181) | 必须与法规/案例已有 1024 维向量保持一致 | 固定版本、CLS、L2；只用 dense，不新增稀疏/ColBERT 服务 |
| 研究方法 | 外部能力卡的触发条件、步骤与停止条件 | 方法与证据必须分开 | 小规模本地选择、按需读取，不把方法混进法条索引 |
| 论文生成 | 用户原始 CSL Skill 和 `compile_paper.py` | 保留原写作与排版要求 | 只增加资源读取与固定编译工具；不另写排版器 |
| 交付 | 已有工作区文件收集和认证下载接口 | 前端必须真正下载得到文件 | PDF/DOCX 保存到当前会话目录，沿用原权限与校验 |

## 检索与执行

```text
用户输入 → 模型按任务选择工具
  ├─ 论文观点 → information_card_search → information_card_read
  │             BM25 + 同版本 BGE-M3 → 现有 RRF(k=60)
  ├─ 研究方法 → capability_card_select → capability_card_read
  ├─ 规范依据 → 原法规检索工具
  ├─ 裁判材料 → 原案例检索/读取工具
  └─ 写论文 → read_skill（完整原文，另附运行适配说明）
                → 按需读取包内引注指南/模板
                → write(.typ) → paper_compile → 原认证下载 Word/PDF
```

信息卡是论文材料候选，不是现行法。作者建议、法条、案例、模型推断保持来源区分。
无页码的卡片不补造页码；原始来源对象保留在完整读取结果中。
`source_path` 是原文的来源定位，不表示该原文存在于当前会话工作区；完整卡片
用 `information_card_read` 读取。工作区只读缺文件会给模型明确可纠正的错误，
权限或路径越界仍然报错。研究方法与学术论文任务按题选择并读取能力卡，
普通计算不强行进入科研流程。
正常检索为 `method=hybrid`；语义通道故障时明确返回 `status=partial`、
`method=bm25` 与说明，不能把降级当作完整混合检索通过。

索引别名 `maritime_information_cards_read` 是统一读取入口，本次重建的目标数据索引为 v3。第二批改用官方 MinerU 重建，
完整验证新卡片、向量和上一版来源哈希后，再原子切换 v2→v3；不改变法规、案例索引。
字段结构沿用 `index_definitions/maritime_information_cards_v2/`，数据版本与结构版本分开管理。
重启检查源文件与向量
哈希、字段、条数及向量覆盖，已发布且一致时跳过重复导入。

## 数据更新与启动

原始信息卡更新后，先在具备 `rag` 依赖与固定模型缓存的环境生成向量：

```powershell
python opensearch/opensearch_delivery/scripts/information_cards_index.py embed --device cpu
```

GPU 预处理可使用 `--device cuda --embedding-batch-size 8`；运行时仍使用镜像内
CPU 编码查询。输出在 `opensearch/import/information_cards/`。manifest 与 JSONL
逐条记录身份、输入哈希和截断情况。发布脚本拒绝不匹配的向量或未知别名替换。
后续已有 v3 数据的更新需使用新的索引版本并审阅切换，不在启动时静默覆盖。
变更来源的切换必须显式传入 `--previous-source-sha256`，且与旧索引记录的哈希相符。

已有启动命令保持：

```powershell
.\start\windows\start-compose.cmd
```

也可在项目根目录执行：

```powershell
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml up -d --build
```

API 与 init 在镜像内执行 Python。原始信息卡只挂载给 init；能力卡和完整 Skill
只读挂载给 API。向量库数据由 OpenSearch 容器独立持久化。

## 五个用户验收场景

| 测试名称 | 用户实际输入/场景 | 防什么问题 | 直观成功表现 |
|---|---|---|---|
| 查论文观点 | 查找海事污染跨区域协同治理两篇论文，给作者、来源、可查定位，区分建议与现行法 | 卡片入库却查不到、作者建议冒充法律 | 真实模型检索并读取卡片，返回可回查来源；轨迹中两路召回均运行 |
| 研究方法与三类依据 | 研究跨区域海上污染多机构协同责任，分别列方法、法规、真实案例、论文观点 | 能力卡只注册未使用，三库来源混淆 | 方法按题触发，真实法规/案例/信息卡工具均有回执，回答各有依据 |
| 普通计算 | 堆场耗电1200千瓦时，单价0.85元，只算电费 | 所有问题强套科研方法 | 直接给出1020元，不调用论文 Skill 或污染能力卡 |
| 完整论文下载 | 撰写约6000字跨区域海上污染协同责任论文，不要图表，生成 Word/PDF | 只写正文、未读 Skill、文件在磁盘却下载不到 | 真实模型自主读 Skill/查资料/写稿/双端编译，网页显示并下载两份有效文件 |
| 无效稿件编译 | 请求将含明确语法错误的 Typst 原样编译，禁止修改 | 编译失败还交付旧文件或仅一端成功 | 返回可理解的失败原因，不出现成功的 PDF/DOCX 附件 |

上述是用户链路验收，不以工具函数的局部测试替代。实际运行结果、来源使用证据和
论文主观质量在当次验收记录中填写；工具调用成功本身不等于论文达到核心期刊水平。

## 2026-11-18 实机验收

通过真实网页发送上述五类问题，调用 DeepSeek `deepseek-flash`；研究/论文采用
高度思考。四个问答及错误处理场景达到约定功能预期，论文也已自主生成并从网页
下载 Word 和 PDF。最终 PDF 7 页，Word 含 26 条原生脚注；下载哈希与最后一次
`paper_compile` 回执一致。原始 Skill 的完整读取内容与源文件逐字一致，SHA256
为 `71e0c3ee321090e06369611a2bd0bd7f852f1162557cf2244ac70a7a22665b26`。

方法问答实际调用了能力卡选择与读取；整篇论文这一轮未调用能力卡，说明提示
路由尚不能保证每次执行。论文真实使用了法规、案例与信息卡，但缺学术引注卷期、
页码和专著配比，部分学说与结论论证不足。论文主观评分 6.5/10，只能认定文件
生成链路通过，不能认定完整满足 Skill 的学术质量要求或达到投稿标准。

本轮验收成品与详细证据在工作区 `交付/科研接入验收-20261118/`，不依赖临时目录。
