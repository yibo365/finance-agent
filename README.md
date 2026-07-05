# finance-agent

**中文** ｜ [English](README.en.md)

投研 Agent 工作台：输入自然语言研究任务，自主完成「行情数据 × 行业事件」对齐分析，产出**可交互、可溯源**的 HTML / Excel / PPT / Word 研究产物；类 ChatGPT 的本地 Web 界面，多会话并行、执行过程实时可见、支持多轮对产物做定点修改（版本全保留）。

基于 **OpenAI Agents SDK**：tools（确定性能力）、subagents（独立上下文的判断单元）、skills（方法论 + 渲染骨架资产，自研加载机制）三层协作，全链路 evidence 溯源；LLM 供应方可为**任何 OpenAI 兼容 API**（OpenAI / OpenRouter / DeepSeek 官方 / Kimi / 自建网关），差异由兼容层自动抹平。

**文档**：[产品文档](docs/product.md) ｜ [技术文档](docs/technical.md) ｜ [架构文档](docs/architecture.md) ｜ [AI 辅助开发过程](docs/ai-process.md)

## 快速开始

```bash
# 依赖：uv（https://docs.astral.sh/uv/）+ Node.js（前端构建）
uv sync

# 配置（也可跳过：启动后在 Web 界面左下角"设置"里填，自动写回 .env）
cp .env.example .env    # 填 OPENAI_API_KEY（+ OPENAI_BASE_URL 指定网关）、TAVILY_API_KEY

# 启动（开发模式，一键起前后端）
./scripts/dev.sh        # 打开 http://127.0.0.1:5173
```

生产模式（构建一次前端，之后仅起后端）：

```bash
npm --prefix webapp install && npm --prefix webapp run build
uv run finance-agent --web    # 打开 http://127.0.0.1:8765
```

## 使用

在 Web 界面输入研究任务，例如：

> 回顾英伟达（NVDA）近五年行情数据，梳理同期 AI 行业大事件（如 ChatGPT 发布、B100、DeepSeek 等），在 K 线图上标记行情变化触发时刻的主要事件与影响评级，生成可交互、可溯源的 HTML。

> 请构建黄金与比特币作为避险/抗通胀资产的可交互比较分析体系，产物包括 Excel 回测底稿、PPT 决策框架、Word 策略报告。

- **执行过程实时可见**：每个 agent 的检索、计算、渲染动作流式呈现在时间线上；
- **多会话并行**：不同会话可同时跑任务，切换不中断执行；运行中可一键"⏹ 停止"；
- **多轮修改**：对着产物继续说"把 DeepSeek 事件的评级改成高"即可定点修改，版本全保留；
- **产物面板**：右栏预览/下载产物；文件也在 `outputs/<session-id>/artifacts/`，HTML 自包含（断网双击可开），溯源记录在同目录 `evidence.json`。

CLI 辅助入口（可选）：`uv run finance-agent`（REPL）；`-p "任务……"`（一次性执行）；`--list-sessions` / `--resume <id>`（恢复会话）。

## 配置速查

| 变量 | 说明 |
| --- | --- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `FINANCE_AGENT_MODEL` | 任何 OpenAI 兼容供应方三元组；Base URL 留空 = OpenAI 官方 |
| `TAVILY_API_KEY` | 联网检索（确定性 API，不经 LLM 转述——换模型不改变检索数据）；未配置时退回 HN/Yahoo 两路 |
| `FINANCE_AGENT_MOCK=1` | 离线 mock：行情用内置 NVDA 种子、资讯用离线夹具 |

全量配置项（检索预算、输出上限、JSON 模式等）见[技术文档 §配置](docs/technical.md#4-配置)。

## 测试

```bash
uv run pytest                  # Python 全量单测（不碰真实网络与 LLM）
uv run ruff check .            # 静态检查
node --test tests/*.test.cjs   # 前端资产单测
FINANCE_AGENT_MOCK=1 uv run pytest tests/test_agents.py   # 离线冒烟
```

## 安全说明

- 密钥经环境变量/.env 注入，仓库与产物中均不出现；设置接口外发一律打码；
- Web 仅绑定 127.0.0.1（单用户本地工具，无鉴权即无远程暴露）；
- 行情/资讯数据源免 key 可复跑（Yahoo Chart、HN Algolia）；HTML 产物零外部请求（Plotly 本地内嵌）；
- 无 OS 沙箱的替代约束：agent 无通用文件读写工具，工具参数只有逻辑标识，路径由系统派生并禁闭在会话工作区内（详见[架构文档 §文件安全](docs/architecture.md#8-文件安全workspacefs无-os-沙箱的替代约束)）。
