# PRD + 技术方案：Web UI v2（事件流 × 多会话聊天）

> 迭代对象：FR-16（本地 Web 聊天界面）的第二版。本文档含 PRD 与技术方案两部分；
> 实现落地后主 PRD（prd.md）的 FR 表以 FR-18/19/20 引用本文档。
>
> **后续演进（v3，已落地）**：前端从单文件原生 JS 拆分为独立 Vite 应用
> （webapp/，浅色主题，含运行时设置弹窗），src/ 成为纯后端；本文档的
> 事件协议与 API 契约不变，§4 的"单文件"实现描述由 webapp/ 取代。

## 一、PRD

### 1. 背景与问题

当前 Web 界面是"一进程绑一个会话"的最小可用版：

- **执行过程是黑盒**。一轮研究任务动辄十几分钟（数据采集 → 事件研究 →
  对齐分析 → 报告构建），前端只有一行"正在调用 XXX…"，子代理内部的几十次
  检索/渲染动作完全不可见。真实事故：用户看到界面停在 "run event" 十分钟，
  无法判断是卡死还是在正常工作，只能到文件系统里看 evidence.json 的 mtime。
  CLI 同样只在结束时一次性输出。
- **换会话要重启进程**。`--web` 启动时即绑定唯一会话，新开研究 / 回看历史
  会话都做不到；浏览器刷新后聊天记录清空（历史在 session.db 里，但没有
  读取接口）。

### 2. 目标

1. **过程可见**：每个 agent（orchestrator 与全部 subagent）的每个动作
   （启动、工具调用、工具结果、结束）都以结构化事件实时发出，Web 与 CLI
   两端同源消费、各自渲染。
2. **多会话聊天界面**：类 ChatGPT 布局——左侧会话列表、右侧当前对话、
   底部固定输入框（消息区滚动，输入框不滚动）。
3. **会话归属前端**：单用户场景，会话列表存浏览器 localStorage，
   **不提供服务端会话列表接口**；新会话在首条消息发出后由后端返回
   session_id，前端记录；历史对话内容按 session_id 从后端拉取。

### 非目标（Non-goals）

- 多用户/鉴权/远程访问（维持仅绑定 127.0.0.1）；
- 前端构建链（维持单文件原生 JS，零外部资源）；
- 服务端会话列表/搜索/重命名接口（列表归 localStorage 管）；
- 会话删除接口（前端"删除"仅移出本地列表，工作区文件不动）。

### 3. 功能需求

| 编号 | 需求 | 说明 | 优先级 |
| --- | --- | --- | --- |
| FR-18 | agent 事件流 | 一轮执行中，orchestrator 与每个 subagent 的启动/工具调用/工具结果/结束都发出结构化事件（含 agent 名、工具名、参数/结果摘要）；Web 经 SSE 推送，CLI 逐行打印；事件协议两端同源（同一 stream_turn） | P0 |
| FR-19 | 多会话 Web API | 聊天接口支持可选 session_id：缺省即新建会话并把 session_id 作为首个 SSE 事件返回；提供按会话读取历史消息、产物状态、产物文件的接口；同一会话并发消息拒绝（一次一轮） | P0 |
| FR-20 | 类 ChatGPT 前端 | 左栏会话列表（localStorage 持久化：新建/切换/移除），中栏消息流（用户/助手气泡 + 可折叠的执行过程时间线），右栏产物面板，底部输入框固定；切换会话时从后端拉历史并还原（含历史轮次的动作时间线） | P0 |

### 4. 验收标准

1. 发起"新建研究"任务，Web 端能实时看到：orchestrator 调用
   `run_data_collector` → data-collector 内部 `fetch_market_data`、
   `run_changepoint_detection` → event-researcher 内部逐次 `web_search` /
   `search_hn_news`……直至 report-builder 的 `render_artifact`；CLI 跑同一任务
   能看到同一串动作的逐行打印。
2. 首页新建会话 → 发送消息 → 立即收到 session_id 并出现在左栏；刷新页面
   后会话仍在左栏，点击可还原完整历史（含执行过程时间线）；关闭服务重启后
   同样可还原（历史来自 session.db，不依赖进程内存）。
3. localStorage 清空后，左栏为空（设计如此——服务端不提供会话列表）；
   `--web --resume <id>` 启动时该会话自动并入左栏。
4. 消息区长对话滚动时，输入框与左右栏保持固定。
5. 同一会话在一轮执行未结束时再发消息，收到明确的"正在处理中"错误事件，
   不产生第二轮并发执行。

## 二、技术方案

### 1. 事件协议（SSE `data:` 帧内 JSON，CLI 直接消费同一流）

```jsonc
{"type": "session",     "session_id": "s-..."}                    // 每轮首个事件；新建会话前端据此入 localStorage
{"type": "agent_start", "agent": "data-collector"}                // subagent 进入
{"type": "tool_call",   "agent": "orchestrator", "tool": "run_data_collector", "detail": "<参数摘要，截断>"}
{"type": "tool_result", "agent": "data-collector", "tool": "", "ok": true, "detail": "<输出摘要，截断>"}
{"type": "agent_end",   "agent": "data-collector"}
{"type": "delta",       "text": "…"}                              // orchestrator 面向用户的回复增量
{"type": "done",        "reply": "…", "artifacts": [ … ]}          // artifacts 为本轮产物增量
{"type": "error",       "text": "…"}
```

约定：`detail` 为定长截断摘要（不回传全量参数/输出，防大 payload 拖垮流）；
事件只增不改，前端按顺序渲染即可，无需状态机。

### 2. 引擎层（session.py / context.py / orchestrator.py）

- `AppContext` 增加 `emit: Callable[[dict], None] | None`——工具代码可拿到、
  LLM 不可见，与"资源句柄在系统侧"的既有原则一致。
- `SessionCore.stream_turn` 是**唯一执行引擎**：内部 `Runner.run_streamed`
  跑 orchestrator，同时把 `ctx.emit` 指向内部队列；orchestrator 自身的
  raw/item 事件与嵌套 subagent 转发来的事件在队列中合流，按序 yield。
  `run_turn` 退化为 stream_turn 的收集器（可选 on_event 回调）——
  CLI 与 Web 因此天然同源，不存在两套口径。
- orchestrator 的四个 subagent 包装工具从 `Runner.run` 改为
  `Runner.run_streamed`，把内部 `tool_call_item` / `tool_call_output_item`
  连同 agent 名经 `ctx.emit` 转发，并在前后发 `agent_start` / `agent_end`。
- 新增 `read_history(db_path, session_id)`：直接读 SQLiteSession 的
  `agent_messages` 表，把持久化消息重建为展示消息：
  `user` / `assistant`（文本）与 `action`（function_call + 匹配的
  function_call_output，含 ok 标记与摘要）；`reasoning` 类目不外发。
  历史轮次的执行时间线由此天然可还原，无需另行落盘事件。

### 3. Web 服务层（web/app.py）

`create_app(settings, outputs_dir=None, initial_session_id=None)`——
不再持有单一 SessionCore，改为**会话注册表**（`dict[str, SessionCore]`
懒加载：内存没有则 `SessionCore.resume`，目录不存在返回 404）。

| 路由 | 说明 |
| --- | --- |
| `GET /` | 单文件前端 |
| `GET /api/state` | `{provider, model, initial_session_id}`（启动信息；不含会话列表） |
| `POST /api/chat` `{message, session_id?}` | SSE。缺 session_id 即 `SessionCore.start` 新建；首个事件必为 `session`。按会话 `asyncio.Lock` 串行，占用中直接发 `error` 事件 |
| `GET /api/sessions/{sid}/messages` | 历史消息（read_history 重建） |
| `GET /api/sessions/{sid}/state` | 该会话的 artifacts / datasets（右栏产物面板） |
| `GET /api/sessions/{sid}/artifacts/{aid}/file` | 产物文件下载（沿用 manifest 白名单，路由加会话维度） |

安全边界不变：仅 127.0.0.1；session_id / artifact_id 走既有正则与
manifest 校验；无任意路径参数。

### 4. 前端（web/static/index.html，单文件原生 JS）

- 三栏 Grid：`会话列表(240px) | 聊天(1fr) | 产物(320px)`；聊天列为
  `flex-column`：header 固定、消息区 `overflow-y:auto`、表单固定底部。
- localStorage：`fa.sessions = [{id, title, ts}]`（title 取首条消息截断），
  `fa.active = id`。新建会话按钮只清空当前视图（`active=null`），
  待首条消息的 `session` 事件返回后写入列表；移除按钮仅删本地记录。
- 执行过程渲染：一轮内的 `agent_start/tool_call/tool_result/agent_end`
  聚合为助手回复上方的"执行过程"折叠块，进行中显示旋转指示；
  历史还原时 `action` 消息以同样样式渲染。
- 动态内容一律 `textContent` 挂载（产物标题等来自 LLM 输出，不进 innerHTML）。

### 5. CLI（cli.py）

- REPL 与 `-p` 一次性模式改为消费 `run_turn(on_event=…)`：
  `▸ agent 启动`、`  ⚙ 工具(参数摘要)`、`  ✔/✘ 结果摘要` 逐行打印
  （进度走 stderr，回复走 stdout，管道友好）。
- `--web` 不再预建会话（避免空工作区垃圾）；`--web --resume <id>` 把
  该会话作为 initial_session_id 交给前端并入左栏。

### 6. 测试策略

- 事件协议：打桩 stream_turn 验证 SSE 帧；`read_history` 用真实持久化
  格式样本（user/assistant/function_call/function_call_output/reasoning）
  断言重建与过滤。
- 多会话 API：无 session_id 自动建会话且首帧为 `session`；未知 session 404；
  产物文件按会话路由 200/404。
- 前端：mock 事件流服务冒烟（页面加载、发消息、时间线渲染、刷新还原）。
