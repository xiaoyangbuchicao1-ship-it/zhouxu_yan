---
name: expense-summary
description: 按包内规则把费用记录汇总成可下载的 CSV。
compatibility: Python 3.11 标准库；工作模式
metadata:
  input: JSON 文件
  output: CSV 文件
---

先读取 `references/rules.md`，再调用 `scripts/summarize.py`。
脚本需要一个 JSON 输入文件，格式为 `[{"category": "港口费", "amount": "12.50"}]`，
并把结果写到当前工作区的 `expense-summary.csv`。不要在回答中手算合计，也不要重写脚本。

命令格式：`python <package_path>/scripts/summarize.py expenses.json expense-summary.csv`。
`package_path` 使用 read_skill 返回的实际路径。脚本会读取包内的规则与表头模板。
