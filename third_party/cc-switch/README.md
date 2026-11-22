# CC Switch 复用说明

舟序的中转模型接入参考并移植了 [CC Switch](https://github.com/farion1231/cc-switch)
在固定版本 `fc665e818e842bf6542e5432ca0fe37d33ffabe1` 中的协议桥接思路。

本目录只保留许可证和复用说明；协议适配仍挂在舟序现有的模型端口和 Harness 上。

我们实际借用的部分：

- 按供应商和模型分别记录协议、上下文容量与自动整理阈值；
- OpenAI Responses、OpenAI Chat、Anthropic Messages、Gemini 原生请求分别处理；
- Responses 工具调用保留原生 `function_call` 帧，并把工具结果作为对应的
  `function_call_output` 继续传回；
- 只有真实探测确认工具调用和流式输出后，模型才会进入工作模式菜单。

我们没有搬入的部分：

- CC Switch 的桌面应用、账号管理和本地代理服务；
- 另一套任务调度器。舟序继续使用自己的 Run、事件、检查点和产物索引；
- CC Switch 的加密摘要封装。舟序的上下文整理结果存入已有的运行记录，避免伪装成
  上游密文。

CC Switch 的压缩桥接由上游 Codex 发起触发；它负责跨协议转发和结果封装，并不替代
舟序的运行调度。舟序因此只复用协议和压缩完成判定，自动整理仍由现有 Harness 负责。

许可证见同目录 `LICENSE`。来源仓库为 MIT 许可。
