# 后端维护约定

先读取[项目说明](../README.md)、[后端八规范](../AGENTS/backend-eight-rules.md)、[当前架构](../docs/architecture.md)、[接口合同](../docs/module-contracts.md)，再读取目标模块及邻近实现。本轮用户已授权的操作范围以会话指令为准。

- Python 3.11、FastAPI、Pydantic v2、httpx、自研 Harness；复用 pyproject.toml 与 uv.lock。
- bootstrap.py 是具体实现的组装入口。模块依赖公共端口，不互调内部实现。
- 参数、文案与提示词放 config；密钥通过配置读取器在运行时取得，不进入代码、文档、Trace 或公开事件。
- 生产固定 DeepSeek Flash 的 none / low / high；交付与补充控制留在适配层，业务工具进入 Action。
- 唯一完整请求预算在 infrastructure/model/budget.py。材料、历史、工具、索引、续做状态和私有供应商续接均需计量；不在预算后追加全文。
- account、auth_session、conversation 与 run 身份分开；material_ids 与 attached_material_ids 分开验证。
- 检查点和授权历史持久化；启动恢复 queued/running，停止与等待补充不自动继续，未知副作用不自动重放。
- 读取覆盖、当前正文可见和任务完成分别表达；历史回答与引用不作为当前已核验证据。
- SQLiteRunStore 限定单服务进程；Native 文件目录隔离不等于多租户操作系统沙箱。
- 固定题目、原件、标准答案在 ../datasets/。不得为通过验收改题或改 gold。基础版本按用户要求不附测试执行代码和历史结果。
- 修改后运行相关 lint、类型检查、构建或真实流程验证。协作、自进化、长期经验与独立语义评审尚未实现。
