# 产物样例

本目录收录两组真实端到端运行产出，用于演示本地投研 Agent 工作台从自然语言任务到可溯源交付物的完整结果。样例从 `outputs/` 会话工作区显式拷贝而来，保留交付产物、渲染规格、材料数据和溯源信息；本地会话数据库、运行事件日志等执行状态文件未纳入样例。

## 样例目录

### `nvda-5y-ai-events/`

来源会话：`outputs/s-20260705-0376`

任务主题：NVDA 五年行情与 AI 事件对齐复盘。

主要产物：

- `artifacts/nvda_5y_kline_review_v2.html`：自包含 HTML 复盘报告，可直接用浏览器离线打开。
- `artifacts/nvda_5y_decision_framework_v1.pptx`：决策框架演示文稿。
- `artifacts/nvda_5y_strategy_report_v1.docx`：策略研究报告。
- `artifacts/nvda_5y_strategy_ppt_v1.pptx`：策略复盘演示文稿。

### `gold-btc-comparison/`

来源会话：`outputs/s-20260705-745a`

任务主题：黄金与比特币避险/抗通胀属性比较分析。

主要产物：

- `artifacts/gold_btc_comparison_pptx_v1.pptx`：决策框架演示文稿。
- `artifacts/gold_btc_comparison_xlsx_v1.xlsx`：回测底稿。
- `artifacts/gold_btc_comparison_docx_v1.docx`：结构化策略研究报告。

## 目录结构

每个样例目录都尽量保留真实会话的交付结构：

- `manifest.json`：产物清单、版本、标题和生成摘要。
- `evidence.json`：渲染产物使用的溯源证据索引。
- `artifacts/`：最终交付文件。
- `specs/`：渲染规格快照，和 `manifest.json` 中的 spec 路径保持一致。
- `data/`：行情数据集与索引。
- `materials/`：事件、变化点、对齐矩阵等中间材料。

未收录的文件：

- `session.db*`：本地会话数据库。
- `run_events.jsonl`：本地运行事件流。

## 查看方式

- HTML 文件可直接双击或用浏览器打开。
- PPTX、DOCX、XLSX 文件可用 Microsoft Office、WPS、LibreOffice 或兼容软件查看。
- 若需要检查产物与规格的对应关系，先看各样例目录下的 `manifest.json`。
