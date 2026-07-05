# finance-agent

投研 agent：输入自然语言研究任务，自主完成「行情数据 × 行业事件」对齐分析，产出可交互、可溯源的 HTML / Excel / PPT / Word 研究产物；支持多轮会话对产物做定点修改。

> 完整的运行说明与 AI 辅助开发过程记录将在交付打磨阶段（M8）补全。
>
> 需求与设计文档：[docs/prd.md](docs/prd.md) ｜ [docs/tech-design.md](docs/tech-design.md) ｜ [docs/architecture-and-flow.md](docs/architecture-and-flow.md)（时序图 + 架构图 + agent 档案卡）

## 快速开始（当前为骨架阶段）

```bash
# 依赖 uv（https://docs.astral.sh/uv/）
uv sync

# 配置密钥（.env 已 gitignore，密钥不入库）
cp .env.example .env  # 填入 OPENAI_API_KEY

# 运行：默认进入交互会话；-p 一次性执行；--resume 恢复历史会话
uv run finance-agent
uv run finance-agent -p "回顾英伟达（NVDA）近五年行情数据……"
uv run finance-agent --resume s-20260703-a1b2

# 测试
uv run pytest                  # Python 侧
node --test tests/*.test.cjs   # 前端资产侧
```

## 目录结构

```
├── docs/                        # PRD、技术设计、AI 开发过程记录（交付文档）
├── outputs/                     # 运行产物区：每个会话一个工作区目录
│   └── <session-id>/            #   manifest.json / artifacts/ / specs/ / data/ / evidence.json
├── src/finance_agent/           # 全部 Python 源码（唯一的代码根）
│   ├── cli.py                   # 入口：REPL（默认）/ -p 一次性 / --resume 恢复
│   ├── config.py                # 环境变量、模型名等配置（密钥只从环境读取）
│   ├── tools/                   # 确定性工具：行情/资讯抓取、拐点检测、产物渲染（M1 起）
│   ├── subagents/               # 子 agent 定义：数据采集/事件研究/对齐分析/报告构建（M4）
│   ├── skills/                  # skill 机制代码：扫描、索引、按需加载（M2）
│   │   └── builtin/             # 内置 skill 资产：<name>/SKILL.md + templates/ + assets/
│   ├── workspace.py             # 会话工作区 + WorkspaceFS 文件守卫（M3，待建）
│   ├── orchestrator.py          # 主 agent：任务解析与 subagent 调度（M4，待建）
│   └── provenance.py            # evidence 溯源记录（M1，待建）
├── tests/                       # pytest（Python 侧）+ node --test（前端资产侧）
├── nvda_*.{html,js,json}        # 早期原型资产：M2 收编进 skills/builtin/kline-html-report
└── plotly-2.35.2.min.js         # 本地 vendor 的 Plotly：随原型一并收编（不引 CDN）
```

几条布局约定（详细论证见 [docs/tech-design.md](docs/tech-design.md)）：

- **skill 资产放在包内**（`src/finance_agent/skills/builtin/`）而非仓库根：资产是产品内置能力，与机制代码同生命周期，经 `importlib.resources` 定位，任何安装方式/工作目录下都可靠；外部扩展另行支持 `FINANCE_AGENT_SKILLS_DIR` 追加目录，与内置资产分离。
- **`outputs/` 是唯一的运行时写入区**：agent 的所有文件写入被 WorkspaceFS 禁闭在会话工作区内，源码树在运行期只读。
- **根目录的 nvda_* 原型文件是过渡状态**：M2 完成收编后从根目录移除。
