# 可复用技能

技能是可复用的方法、流程和策略组合，可以通过统一模块调用协议组合八个 Agent 模块完成一个业务目标。

技能不复制模块实现，也不直接访问基础设施。新增能力是否做成 Skill，要根据它是否主要描述业务流程和编排来判断；不能因为目录存在就预先锁定。当前不建立独立技能运行时，不锁定加载和发布机制。

当前已沉淀的业务 Skill：

- `maritime-case-research/`：海事案例候选检索、原文读取、主张与裁判认定分离、案例依据与现行法规依据分离。
- `maritime-legal-research/`：通用海事法律问答的法规与案例来源选择、版本处理和引用边界。
- `maritime/case-assessment/`：案件初步研判。
- `maritime/case-management/`：案件管理与协同。
- `maritime/case-research/`：案例检索与分析。
- `maritime/claim-calculation/`：索赔测算。
- `maritime/contract-review/`：合同与提单审查。
- `maritime/document-drafting/`：文书起草。
- `maritime/evidence-review/`：证据整理。
- `maritime/law-research/`：法律检索与问答。

Skill 由 Harness 按当前任务需要提供方法约束；实时数据仍由 `search_cases`、`read_case` 和法规 RAG 工具返回。目录中的 Skill 文档不是一个绕过权限的执行入口。

当前运行时已经提供最小的 Skill 加载闭环：启动时只暴露固定注册表的名称、描述和版本，模型在需要专业流程时调用只读 `read_skill`，读取后仍必须调用实际法规/案例工具。`read_skill` 是模型可调用的 Tool，Skill 本体仍是由 `SkillRegistry` 管理的只读方法资源；两者不混为同一个执行器。Skill 不能传入任意路径、URL、数据库查询或账户凭证；来源原文由受控语料服务按引用句柄读取，前端只显示可读来源卡和“查看原文”。

新增业务 Skill 时，先在目录中写清触发边界、证据来源、失败处理和用户输出，再在 `datasets/skills/` 增加正常、边界和故障验收定义。Skill 只编排能力，不复制检索实现，也不把内部索引字段暴露给用户。
