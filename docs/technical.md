# 技术文档：finance-agent

> 本篇讲**怎么用、怎么改**（技术栈 / 代码结构 / API / 配置 / 测试 / 开发流程）。
> 产品定位见 [product.md](product.md)，设计机制与取舍见 [architecture.md](architecture.md)。

## 1. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| Agent 编排 | OpenAI Agents SDK（`openai-agents`） | agents-as-tools 模式，自定义 function_tool 包装 subagent |
| LLM 供应 | 任何 OpenAI 兼容 API | 三元组 base_url + api_key + model；供应方差异由 `llm.py` 兼容层抹平 |
| 后端 | FastAPI + uvicorn（仅 127.0.0.1） | SSE 流式聊天 + 会话/产物/设置 API |
| 前端 | Vite + 原生 JS（`webapp/`） | 无框架；marked + DOMPurify 渲染 Markdown |
| 数据 | pandas；Yahoo Chart API（多源降级）；Tavily / HN Algolia / Yahoo 资讯 | 检索均为确定性 HTTP API，不经 LLM 转述 |
| 产物渲染 | 自研 ArtifactSpec + openpyxl / python-pptx / python-docx / Plotly 模板 | LLM 产 spec，确定性代码渲染文件 |
| 持久化 | 文件工作区 + SQLite（对话历史） | 每会话一个目录，见 architecture.md §工作区 |

## 2. 代码结构

```
src/finance_agent/            # 后端（Python 包）
├── cli.py                    # 入口：--web 起服务；REPL / -p 一次性为辅助入口
├── config.py                 # Settings（env/.env）+ SettingsStore（运行时更新写回 .env）
├── llm.py                    # 供应方兼容层（唯一收口）：response_format 降级/消息序列规整/max_tokens 去参重试
├── session.py                # SessionCore（stream_turn 唯一执行引擎）+ TrimmedSession 历史修剪 + read_history
├── orchestrator.py           # 主 agent：意图路由、subagent 调度、材料落盘与摘要、输出打捞
├── subagents/                # data_collector / event_researcher / alignment_analyst / report_builder
├── contracts.py              # 环节间结构化契约（TaskBrief 下行、各 output_type 上行）
├── context.py                # AppContext：事件回调、检索预算计数、事件累积器
├── events.py                 # 事件流协议 + SDK run_item 翻译（Web/CLI 同源）
├── json_repair.py            # 模型 JSON 输出的确定性修复（围栏/坏引号/截断打捞）
├── provenance.py             # Evidence 溯源模型 + EvidenceLog
├── workspace.py              # WorkspaceFS：产物/数据/材料/审计日志的落盘层 + 确定性校验
├── tools/                    # market（行情多源）/ changepoints（变化点算法）/ news（HN+Yahoo）/ websearch（Tavily）/ agent_tools（function_tool 层）
├── artifacts/                # spec.py（ArtifactSpec 中间表示）+ renderers/（html/xlsx/pptx/docx）
├── skills/                   # loader + builtin/ 四个产物方法论（SKILL.md + 模板资产）
└── web/app.py                # FastAPI 多会话服务（注册表/锁/停止/设置）

webapp/                       # 前端（Vite）：src/main.js + startup.js + style.css
scripts/dev.sh                # 一键启动前后端（端口占用自动重启；后端就绪探活）
tests/                        # Python 单测 + node --test 前端资产测试
docs/                         # 本文档集
outputs/<session_id>/         # 运行时唯一写入区（会话工作区）
```

## 3. 运行方式

```bash
# 开发模式（前后端热更新；前端 5173 代理 /api → 后端 8765）
./scripts/dev.sh
# 端口可换：BACKEND_PORT=8899 FRONTEND_PORT=5200 ./scripts/dev.sh

# 生产模式（构建后仅起后端，8765 直接服务 webapp/dist）
npm --prefix webapp install && npm --prefix webapp run build
uv run finance-agent --web            # 打开 http://127.0.0.1:8765

# CLI（辅助入口）
uv run finance-agent                  # REPL 交互
uv run finance-agent -p "研究任务……"   # 一次性执行
uv run finance-agent --list-sessions  # 列出可恢复会话
uv run finance-agent --resume <id>    # 恢复会话（REPL）；--web --resume 则并入前端左栏
```

## 4. 配置

优先级：环境变量 > `.env`（自动加载）。Web 设置弹窗保存的值写回 `.env`，对新会话生效。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | 必填（或 mock 模式）。任何 OpenAI 兼容供应方的 key |
| `OPENAI_BASE_URL` | 空 = OpenAI 官方 | 兼容网关地址（OpenRouter / DeepSeek 官方 / Kimi / 自建） |
| `FINANCE_AGENT_MODEL` | `gpt-5.5` | 模型名原样透传（OpenRouter 需带厂商前缀，如 `deepseek/deepseek-v4-pro`） |
| `TAVILY_API_KEY` | — | 联网检索（未配置时事件研究退回 HN/Yahoo 并如实声明） |
| `FINANCE_AGENT_WEB_MAX_RESULTS` | 5 | 每次联网检索返回条数 |
| `FINANCE_AGENT_SEARCH_BUDGET` | 36 | 单次 subagent 运行的检索次数预算（确定性收敛闸） |
| `FINANCE_AGENT_MAX_TOKENS` | 200000 | 单次调用输出上限；`0`=不发送（用供应方默认）；超供应方上限时兼容层自动去参重试 |
| `FINANCE_AGENT_JSON_MODE` | `object` | 结构化输出策略：`object`（json_schema 降级，各家通吃）/ `schema` / `off` |
| `FINANCE_AGENT_MOCK` | — | `=1` 离线 mock（行情用内置 NVDA 种子、资讯用离线夹具） |
| `FINANCE_AGENT_SKILLS_DIR` | — | 追加外部 skill 目录 |
| `OPENROUTER_API_KEY` | — | 旧配置兼容：单独设置时自动采用其 key 与 base_url |

## 5. 后端 API

| 路由 | 说明 |
| --- | --- |
| `GET /` | 前端页面（`webapp/dist`；未构建时返回指引页） |
| `GET /api/state` | `{model, base_url, initial_session_id}` 启动信息 |
| `GET /api/settings` / `PUT /api/settings` | 运行时配置读写（密钥打码外发；明文只落本机 .env） |
| `POST /api/chat` `{message, session_id?}` | SSE 事件流。缺 session_id 即新建会话，首帧必为 `session`；同会话一次一轮（运行中再发返回 error 事件） |
| `GET /api/sessions/{sid}/messages` | 历史消息回放（user/assistant 文本 + action 时间线） |
| `GET /api/sessions/{sid}/state` | `{running, artifacts, datasets, workspace_dir}` |
| `POST /api/sessions/{sid}/stop` | 停止运行中的轮次（真取消，锁随任务释放） |
| `GET /api/sessions/{sid}/artifacts/{aid}/file[?version=N][&download=1]` | 产物预览/下载（manifest 白名单，无路径参数） |

### SSE 事件协议（Web 与 CLI 同源，见 `events.py`）

```jsonc
{"type": "session",     "session_id": "s-..."}      // 每轮首帧
{"type": "agent_start", "agent": "data-collector"}
{"type": "tool_call",   "agent": "...", "tool": "...", "detail": "<截断摘要>"}
{"type": "tool_result", "agent": "...", "tool": "...", "ok": true, "detail": "<截断；错误放宽到 600 字符>"}
{"type": "agent_end",   "agent": "..."}
{"type": "delta",       "text": "…"}                 // 回复增量（前端渲染 Markdown）
{"type": "done",        "reply": "…", "artifacts": [/* 本轮产物增量 */]}
{"type": "error",       "text": "…"}
```

## 6. 前端要点（webapp/）

- 三栏布局；会话列表存 `localStorage`（`fa.sessions` / `fa.active`），服务端无会话列表接口；
- **运行中切换会话不断流**：事件写入按会话的内存缓冲，切回整轮重放后继续实时追加；
  轮次结束后缓冲即弃（该轮已落库，回放走历史接口）；
- 运行态 UI：本地流或服务端 `running` 均驱动"⏹ 停止"双态按钮与输入禁用；
  刷新后接不回流则轮询 `/state` 收敛并自动加载结果；
- 助手回复 `marked`（GFM）+ `DOMPurify` 渲染；其余动态内容一律 `textContent`。

## 7. 测试与 CI

```bash
uv run pytest                  # Python 全量单测（工具/渲染器/工作区/agent 层/Web/兼容层）
uv run ruff check .            # 静态检查
node --test tests/*.test.cjs   # 前端资产单测（K线渲染骨架、启动逻辑）
FINANCE_AGENT_MOCK=1 uv run pytest tests/test_agents.py   # 离线冒烟
```

约定：**测试不碰真实网络与 LLM**（httpx MockTransport / 打桩 stream_turn / mock 模式）；
真实事故一律沉淀为还原样本的回归用例。CI（GitHub Actions）：uv 安装 → ruff →
pytest → 前端 install + build + node 测试——每次提交即是在干净环境的可移植性验证。

## 8. 开发流程速查

改动 → `uv run pytest -q && uv run ruff check .` 全绿 → 涉及前端则 `npm --prefix webapp run build`
→ 提交（中文 commit message，`feat:`/`fix:` 前缀，正文写清"真实事故→防线"）。
更多约定见根目录 [AGENTS.md](../AGENTS.md)。
