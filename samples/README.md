# 产物样例（真实端到端运行产出，未经人工修饰）

- `scenario-a/`：任务一「NVDA 五年 K 线 × AI 事件」——自包含交互 HTML（v2，含一轮
  多轮修改：评级调整+新增总览章节）+ 该会话的 evidence.json / manifest.json / spec 快照。
  双击 HTML 即可离线打开。
- `scenario-b/`：任务二「黄金 vs 比特币比较分析体系」——Excel 回测底稿（公式驱动，
  改"参数"sheet 的窗口值联动重算）+ PPT 决策框架（页脚溯源+末页清单）+ Word 策略
  报告（正文溯源标记+附录）。

生成模型：deepseek/deepseek-v4-pro（经 OpenRouter）。复现命令见根目录 README。
