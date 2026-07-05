---
name: xlsx-backtest
description: 公式驱动的回测底稿 Excel——原始数据 sheet、参数化指标（收益/回撤/滚动波动率/相关性，改参数联动重算）、归一化净值图与溯源 sheet
kind: xlsx
blocks: heading, narrative, table, data_sheet, metrics_sheet
---

# 回测底稿方法论

## 产物定位

给"要复核计算"的读者：所有指标是 Excel 公式而非写死数值，评审者改"参数"sheet
的滚动窗口即可联动重算；原始数据独立成 sheet 可逐行核对；来源在"溯源"sheet。

## 结构组织建议

1. heading + narrative：研究问题、口径声明（对齐方式、年化系数、数据频率）——
   放最前，渲染进"说明"sheet；
2. 每个资产一个 data_sheet：sheet_name 用资产简称（如"黄金GC=F"、"比特币"）；
3. 一个 metrics_sheet：data_refs 列出参与对比的 dataset（最多 2 个），
   labels 给出显示名；渲染器会自动生成 参数/对齐/指标/汇总/图表 五个 sheet；
4. table：补充性明细（如关键事件清单、区间统计对照），每表一个 sheet；
5. 收尾 narrative：局限性（日期对齐损失了加密资产的周末数据、未计交易成本等）。

## 口径纪律

- 多资产相关性基于"日期内连接"后的共同交易日（加密资产 7 天交易、
  黄金期货 5 天），说明里必须声明这一点；
- 波动率年化用 √252（默认），如按周/月频另行声明；
- 含数字结论的 narrative 挂 evidence_refs；数据 sheet 的 evidence_refs
  指向行情抓取记录。
