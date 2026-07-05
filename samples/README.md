# 产物样例

本目录收录两组真实端到端运行产出，用于演示本地投研 Agent 工作台从自然语言任务到可溯源交付物的完整结果。样例从 `outputs/` 会话工作区显式拷贝而来，保留交付产物、渲染规格、材料数据和溯源信息；本地会话数据库、运行事件日志等执行状态文件未纳入样例。

## 样例目录

### `nvda-5y-ai-events/`

来源会话：`outputs/s-20260705-05ef`

任务主题：NVDA 五年行情与 AI 事件对齐复盘。

主要产物：

- `artifacts/nvda_ai_kline_review_v1.html`：自包含 HTML 复盘报告（K 线 + 40 变化点 + 事件对齐），可直接用浏览器离线打开。
- `artifacts/nvda_ai_framework_ppt_v1.pptx`：AI 叙事驱动的决策框架演示文稿。
- `artifacts/nvda_decision_framework_v1.pptx`：事件驱动复盘决策框架演示文稿。
- `artifacts/nvda_strategy_report_v2.docx`：策略研究报告（v2 增量修改版：新增研究参数总览与 40 条变化点明细表；v1 一并保留，演示版本管理）。

### `gold-btc-comparison/`

来源会话：`outputs/s-20260705-9270`

任务主题：黄金与比特币避险/抗通胀属性比较分析（2021–2026）。

主要产物：

- `artifacts/artifact_gold_btc_xlsx_v1.xlsx`：回测底稿（双资产原始数据、参数化指标联动重算、事件对齐分析）。
- `artifacts/artifact_gold_btc_pptx_v1.pptx`：决策框架演示文稿（三类宏观场景、五维对比矩阵、SVB 共振案例）。
- `artifacts/artifact_gold_btc_docx_v1.docx`：六章结构化策略研究报告。

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
