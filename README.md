# finance-agent

投研 agent：输入自然语言研究任务，自主完成「行情数据 × 行业事件」对齐分析，产出**可交互、可溯源**的 HTML / Excel / PPT / Word 研究产物；支持多轮会话对产物做定点修改（版本全保留）。

基于 **OpenAI Agents SDK** 实现；tools（确定性能力）、subagents（独立上下文的判断单元）、skills（方法论+渲染骨架资产，自研加载机制）三层协作，全链路 evidence 溯源。

**文档**：[需求 PRD](docs/prd.md) ｜ [技术设计](docs/tech-design.md) ｜ [运行流程与架构图解](docs/architecture-and-flow.md)（时序图/架构图/agent 档案卡/权限矩阵）｜ [AI 辅助开发过程](docs/ai-process.md)

## 一键运行

```bash
# 依赖 uv（https://docs.astral.sh/uv/）
uv sync

# 配置密钥（唯一的密钥；.env 已 gitignore，不入库、不出现在产物中）
# 支持两种供应方式，二选一填入即可自动识别：
#   OpenAI 直连  → OPENAI_API_KEY=sk-...
#   OpenRouter  → OPENROUTER_API_KEY=sk-or-...（联网搜索自动切换为其 web 插件）
cp .env.example .env

# 方式一：交互会话（默认）
uv run finance-agent

# 方式二：一次性执行（适合快速验收）
uv run finance-agent -p "回顾英伟达（NVDA）近五年行情数据（开盘价、收盘价、最高价、最低价、成交量），梳理同期AI行业大事件（如ChatGPT发布、B100芯片发布、DeepSeek等），在K线图上标记行情变化触发时刻的主要事件、影响评级，产物可交互、可溯源，最终生成一个HTML。"

uv run finance-agent -p "请构建黄金与比特币作为避险/抗通胀资产的可交互比较分析体系，产物包括Excel回测底稿、PPT决策框架、Word策略报告。"

# 方式三：本地 Web 聊天界面（仅 127.0.0.1）
uv run finance-agent --web              # 打开 http://127.0.0.1:8765
uv run finance-agent --web --port 8899  # 默认端口被占用时可指定其他端口

# 多轮修改：关掉终端后仍可恢复会话，接着改上次的产物
uv run finance-agent --list-sessions
uv run finance-agent --resume s-20260703-a1b2   # REPL 里直接说"把 DeepSeek 评级改成高"
```

产物在 `outputs/<session-id>/artifacts/`（版本全保留），溯源记录在同目录 `evidence.json`，产物注册表在 `manifest.json`。HTML 产物自包含（Plotly 与数据内嵌），断网双击可开。

```bash
# 测试
uv run pytest                  # Python 全量单测（工具/渲染器/工作区/agent 层/Web）
node --test tests/*.test.cjs   # 前端渲染骨架纯函数 + 模板无外链断言

# 离线冒烟（不消耗 API 的数据管线验证；agent 对话仍需 key）
FINANCE_AGENT_MOCK=1 uv run pytest tests/test_agents.py
```

环境变量：`OPENAI_API_KEY` / `OPENROUTER_API_KEY`（二选一必需，双设时可用 `FINANCE_AGENT_PROVIDER` 指定）；`FINANCE_AGENT_MODEL`（OpenAI 默认 `gpt-5.5`，OpenRouter 默认 `openai/gpt-5.5`，可换任意其托管模型）；`TAVILY_API_KEY`（推荐：确定性联网搜索，结构化结果不经 LLM 转述，与 LLM 供应方解耦；`FINANCE_AGENT_SEARCH_BACKEND` 可显式指定 tavily / openrouter-plugin / hosted）；`FINANCE_AGENT_SEARCH_MODEL` / `FINANCE_AGENT_WEB_MAX_RESULTS`（联网搜索的模型与条数；模型仅 OpenRouter 插件回落路径使用）；`FINANCE_AGENT_MOCK=1`（行情用内置 NVDA 种子、资讯用离线夹具）；`FINANCE_AGENT_SKILLS_DIR`（追加外部 skill 目录）；`FINANCE_AGENT_BASE_URL`（自建 OpenAI 兼容网关）。

OpenRouter 模式的差异：走 Chat Completions API（自动切换）；联网搜索从 OpenAI 托管 WebSearchTool 换成 OpenRouter web 插件——后者返回 URL citations 并登记 evidence，**溯源链路反而更完整**。

## 架构一图流

```
REPL / -p / Web ── SessionCore（SQLiteSession 多轮记忆，--resume 恢复）
        │
orchestrator（意图路由：新建研究/修改产物/咨询产物/无关话题——不需要的链路一步不调）
        │ TaskBrief（强制携带用户原话，治理传参失真）
        ├─ data-collector    → fetch_market_data（Yahoo 双主机→本地缓存降级）+ 确定性拐点检测
        ├─ event-researcher  → HN Algolia（按拐点时间窗定向回溯）+ Yahoo 资讯 + WebSearch
        ├─ alignment-analyst → 零工具纯推理：拐点×事件吻合论证（match/partial/none，不强行归因）
        └─ report-builder    → load_skill 方法论 → 组织 ArtifactSpec → 确定性渲染
        │
工作区 outputs/<session>/（WorkspaceFS 守卫：LLM 只传逻辑标识不传路径；
manifest 版本 append-only；evidence 全链路回链）
```

关键取舍详见 [tech-design.md](docs/tech-design.md)；每个 agent 的工具、输入输出与"为什么这么划"见 [architecture-and-flow.md](docs/architecture-and-flow.md)。

## 目录结构

```
├── docs/                        # PRD、技术设计、流程图解、AI 开发过程记录
├── outputs/                     # 运行产物区：每个会话一个工作区目录
│   └── <session-id>/            #   manifest.json / artifacts/ / specs/ / data/ / evidence.json
├── src/finance_agent/           # 全部 Python 源码（唯一的代码根）
│   ├── cli.py                   # 入口：REPL / -p / --resume / --web
│   ├── config.py                # 环境变量配置（密钥只从环境读取）
│   ├── contracts.py             # TaskBrief 与 subagent 结构化输出契约
│   ├── provenance.py            # evidence 溯源记录
│   ├── orchestrator.py          # 主 agent：意图路由、调度、终检
│   ├── session.py               # SessionCore：三入口共用的会话核心
│   ├── workspace.py             # 工作区 + WorkspaceFS 文件守卫 + manifest 版本管理
│   ├── context.py               # AppContext：经 SDK context 注入工具的运行时句柄
│   ├── subagents/               # 四个子 agent 定义（prompt 即档案卡）
│   ├── tools/                   # 确定性工具 + function_tool 包装层
│   ├── artifacts/               # ArtifactSpec 中间表示 + 四格式渲染器
│   ├── skills/                  # skill 机制代码 + builtin/ 资产（SKILL.md+模板+本地Plotly）
│   ├── seeds/                   # 离线种子数据（NVDA 五年日线）
│   └── web/                     # FastAPI 薄层 + 单文件前端（零构建链）
└── tests/                       # pytest + node --test
```

布局约定：skill 资产随包走（`importlib.resources` 定位，外部扩展另走 `FINANCE_AGENT_SKILLS_DIR`）；`outputs/` 是唯一运行时写入区，源码树运行期只读。

## 安全说明

- 密钥仅 `OPENAI_API_KEY`，经环境变量注入，仓库与产物中均不出现；
- 行情/资讯数据源全部免 key（Yahoo Chart、HN Algolia、Yahoo 资讯），评审可复跑；
- HTML 产物零外部请求（Plotly 本地内嵌），无 CDN 依赖与跨域问题；外部文本进产物前全部转义；
- 无 OS 沙箱的替代约束：agent 无通用文件读写工具，工具参数只有逻辑标识，路径由系统派生并禁闭在会话工作区内（详见 tech-design §5，守卫行为有全量单测）。
