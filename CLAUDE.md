# CLAUDE.md

本仓库的完整开发约定见 **[AGENTS.md](AGENTS.md)**（项目速览、常用命令、硬性设计
原则、测试与提交规范、常见坑）——先读它。

Claude Code 补充提示：

- 改动收尾三件套：`uv run pytest -q`、`uv run ruff check .`、
  （动了 `webapp/` 时）`npm --prefix webapp run build`；
- 修 bug 先在 `outputs/<session_id>/run_events.jsonl` 与 `session.db` 里找事故现场，
  再动代码；修复必须附还原事故样本的回归测试；
- 所有供应方兼容问题只修 `src/finance_agent/llm.py`；所有"硬要求"进确定性校验层
  （`workspace.py` / 渲染器），不写进 prompt 就完事；
- 文档三篇（docs/product|technical|architecture.md）与实现保持同步是交付标准的
  一部分——改了行为记得同步文档，但不要在文档里写死会漂移的计数。
