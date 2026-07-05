# 技术设计：投研 Agent

> 版本 v1.1 ｜ 2026-07-03 ｜ 配套 [prd.md](prd.md)，记录架构划分与关键取舍
> v1.1：从"一次性命令行任务"升级为"有状态研究工作台"——新增会话与工作区（§5）、ArtifactSpec 产物管线（§6）、Web 聊天薄层（§11）
> v1.2：无沙箱取舍下的文件访问设计——WorkspaceFS 层（§5）、data_ref 改逻辑键、路径守卫入测试与风险

## 1. 选型与理由

| 决策 | 选择 | 理由与放弃的备选 |
|---|---|---|
| Agent SDK | **OpenAI Agents SDK（Python）** | 刻意不用自带 tools/subagents/skills 三概念的框架（如 Claude Agent SDK）——skill 机制与 subagent 边界**自研**，架构划分是自己的判断而非框架赠品。放弃 LangGraph：偏 workflow 编排，"agent 自主完成任务"的味道弱。 |
| 模型 | gpt-5.5（`FINANCE_AGENT_MODEL` 可覆盖） | 影响评级与拐点对齐是判断密集环节；开发迭代可切轻量档。 |
| LLM 供应方 | OpenAI 直连 或 **OpenRouter**（自动探测，`FINANCE_AGENT_PROVIDER` 可显式指定） | OpenRouter 走 Chat Completions（其无 Responses API），SDK 侧切默认 API 并关 tracing 上传；托管 WebSearchTool 不可用 → 换其 **web 插件** function tool，返回 URL citations 并登记 evidence（溯源比托管搜索更完整）；搜索模型/条数经 `FINANCE_AGENT_SEARCH_MODEL`/`FINANCE_AGENT_WEB_MAX_RESULTS` 配置。 |
| 入口与会话 | 终端 REPL（默认）+ `-p` 一次性 + 本地 Web 聊天薄层；SDK `SQLiteSession` 持久化，`--resume` 跨进程恢复 | 投研修改是常态，一次性命令产完即忘不可用；Web 层薄到只是同一会话核心的另一张脸，排期在 e2e 之后，可被时间盒裁剪的是它而不是核心。 |
| 文件安全 | **无 OS 沙箱，应用层 WorkspaceFS 约束**（§5） | 时间盒取舍：容器/seccomp 级隔离做不完且收益有限——本项目不执行 LLM 生成的代码，威胁面是"LLM 生成的参数"，用"只传逻辑标识不传路径 + 路径守卫"在参数层拦截更对症。 |
| 语言/工程 | Python 3.12 + uv；`src/` 布局 | docx/pptx/xlsx 生态最成熟；uv 保证评审者一键复现。 |
| 图表 | Plotly 2.35（本地 vendor） | 沿用已验证的原型资产；本地 vendor 解决 CDN 依赖安全/跨域/断网问题。 |
| 行情源 | Yahoo Chart API（query1→query2 双前端主机）→ 本地缓存 | 全部免 key。放弃原型中的 Nasdaq API：需伪装 UA，反爬不稳定；原计划的 Stooq 在 M1 实测已加 JS 工作量证明反爬（返回验证页而非 CSV），移出默认链（实现保留，见 market.py 注释）。 |
| 资讯源 | HN Algolia + Yahoo 资讯 + hosted WebSearchTool | HN Algolia 支持按时间范围查历史（适合"ChatGPT 发布"这类回溯）；三路互补，且同时覆盖"自定义 function tool"与"SDK 托管工具"两种形态。 |

## 2. 总体架构

> 更细的运行时视图（用户输入后的完整时序、每个 agent 的工具/输入/输出档案卡、工具×agent 权限矩阵）见 [architecture-and-flow.md](architecture-and-flow.md)。

```
终端 REPL ──┐
finance-agent -p "…" ──┤── 会话核心（SQLiteSession 多轮记忆，--resume 恢复）
本地 Web 聊天（薄层）──┘
        │
        ▼
┌─ orchestrator（主 agent，gpt-5.5）─────────────────────────┐
│  职责：解析任务/修改意图 → 规划 → 调度 subagent → 汇总把关  │
│  可见工具：4 个 subagent（as_tool）+ list_skills            │
│           + list_artifacts / read_artifact（工作区感知）    │
│                                                            │
│  ┌────────────────┐  ┌────────────────┐                    │
│  │ data-collector │  │event-researcher│   subagents        │
│  │ 选源/重试/校验 │  │ 三路检索→去重  │   （独立上下文）   │
│  │                │  │ →筛选→影响评级 │                    │
│  ├────────────────┤  ├────────────────┤                    │
│  │alignment-      │  │ report-builder │                    │
│  │analyst         │  │ 产出/修改      │                    │
│  │ 拐点↔事件对齐  │  │ ArtifactSpec   │                    │
│  └───────┬────────┘  └───────┬────────┘                    │
└──────────┼───────────────────┼─────────────────────────────┘
           ▼                   ▼
  tools（确定性，可单测）              skills（方法论+骨架+组件库）
  fetch_ohlcv / search_hn_news        kline-html-report
  fetch_yahoo_news / web_search       xlsx-backtest
  detect_changepoints                 pptx-framework
  load_skill / render_artifact        docx-strategy-report
  update_artifact
           │
           ▼
  工作区 outputs/<session-id>/
  manifest.json（产物注册表）· artifacts/（全版本渲染文件）
  specs/（全版本 spec 快照）· data/（行情/资讯缓存）· evidence.json
```

## 3. 三层划分的边界原则（核心取舍）

**Tool、Subagent、Skill 不按"功能模块"划分，按"工作性质"划分：**

- **Tool = 确定性、可单测的原子能力。** 输入输出可精确断言（HTTP 抓取、解析、拐点规则计算、spec 渲染）。工具**不做判断**，只产出带 evidence 标记的数据。判断放进工具会让它既测不了也溯不了源。
- **Subagent = 需要独立推理上下文的判断环节。** 划分动因是**上下文隔离**而非组织架构美观：event-researcher 要吞几十条搜索结果的脏上下文，不能让它污染主线程；alignment-analyst 需要干净的上下文做严谨论证。每个 subagent 有自己的 system prompt（角色方法论）与受限工具集。
- **Skill = 领域方法论 + 渲染骨架 + 组件库的打包资产。** 回答"这类产物应该怎么组织论证、有哪些可用组件"，**不预设内容结构**（见 §6）。skill 是数据不是代码——增加一种产物形态只需加一个 skill 目录，不改流水线。

**边界上的具体判断（设计说明面试点）：**

1. 拐点检测是 tool 不是 subagent：它是纯算法，LLM 掺和进来只会让结果不可复现、不可溯源。LLM 的判断力用在"拐点与事件是否吻合"（alignment-analyst），不用在"哪里是拐点"。
2. 数据采集设为 subagent（data-collector）：降级链本身是 tool 里的确定性逻辑，但**选源策略、时间范围裁剪、质量校验后的重试决策**（如某源数据行数异常少）需要判断，由 data-collector 承担。
3. report-builder 是 subagent 而渲染是 tool：决定产物结构、写叙事、选重点事件是判断（产出 ArtifactSpec）；spec → 文件是确定性操作（render_artifact）。
4. 修改意图的解析在 orchestrator，spec 的定点变更在 report-builder，变更的落盘与版本递增在 tool（update_artifact）——判断与执行始终分离。

## 4. 编排：agents-as-tools

orchestrator 通过 `agent.as_tool()` 调用 4 个 subagent，**全程握有控制权**，放弃 handoffs（控制权转移适合客服分流，研究流水线需要中央汇总组装）。

**新建产物**的典型执行序（由 orchestrator 自主决定，非硬编码）：

```
parse 任务 → data-collector（行情+拐点）
          → event-researcher（拿拐点时间窗做定向检索）
          → alignment-analyst（拐点×事件 吻合性矩阵）
          → report-builder（选 skill、产出 spec、渲染）
          → orchestrator 终检（产物存在性、溯源完整性）
```

**修改产物**的执行序：

```
识别修改意图 → list_artifacts / read_artifact 定位目标产物与当前 spec
            → 判断是否需要新数据（仅补数据时才回到 data-collector/event-researcher）
            → report-builder 对 spec 做定点变更 → update_artifact 重渲染升版
```

关键数据流细节：**event-researcher 拿着拐点日期窗口做定向检索**（而非漫无目的搜"AI 大事件"），这是"事件与拐点吻合"的机制保证——先有拐点，再找解释，找不到就标注"无对应事件"。

subagent 间不传自由文本，传 pydantic 结构化对象（MarketDataset、EventList、AlignmentMatrix、ArtifactSpec），防止 LLM 输出漂移在环节间放大。

## 5. 会话与工作区

**会话**：每次启动分配 `session_id`（如 `s-20260703-a1b2`），对话历史由 SDK `SQLiteSession` 落盘至工作区；`finance-agent --resume s-20260703-a1b2` 恢复上下文继续对话。REPL、`-p`、Web 三个入口共用同一会话核心（`SessionCore`），同一 session 可以今天在终端开工、明天在 Web 里接着改。

**工作区**（每会话一个目录，`outputs/<session_id>/`）：

```
manifest.json    # 产物注册表（见下）
session.db       # SQLiteSession 对话历史
artifacts/       # 渲染产物，全版本保留：nvda_kline_report_v1.html, _v2.html…
specs/           # 每版 spec 快照（JSON，很小，全留）
data/            # 行情/资讯抓取缓存（parquet/json）——改产物不重抓
evidence.json    # 溯源记录
```

**manifest.json**（产物的身份与版本档案）：

```json
{
  "artifacts": [{
    "artifact_id": "nvda-kline-report",       // 稳定 slug，会话内唯一
    "kind": "html",
    "title": "NVDA 五年 K 线 × AI 事件对齐报告",
    "current_version": 2,
    "versions": [
      {"v": 1, "file": "artifacts/nvda_kline_report_v1.html",
       "spec": "specs/nvda-kline-report_v1.json",
       "created_at": "…", "change_summary": "初版"},
      {"v": 2, "file": "artifacts/nvda_kline_report_v2.html",
       "spec": "specs/nvda-kline-report_v2.json",
       "created_at": "…", "change_summary": "事件#7 评级调整为高；新增 2024 财报季拐点分析"}
    ]
  }]
}
```

对话中 agent 以 `[artifact_id vN]` 指代产物，用户始终知道改的是哪个、第几版、改了什么（change_summary 由 report-builder 撰写，进 manifest 也进回复）。

**数据缓存的意义**：修改类请求（调评级、改文案、增删章节）不重抓行情/资讯，直接改 spec 重渲染——快、省 API、且保证"只改我说的那处"。数据文件由 `data/index.json` 注册表管理：`dataset_id → {文件路径, 来源 evidence, schema, 行数}`，spec 与工具均以 `dataset_id` 引用数据，不见路径。

### WorkspaceFS：无沙箱前提下的文件访问设计

时间盒内不引入 OS 级沙箱，代之以应用层约束。三条核心原则：

1. **agent 不持有通用文件工具。** 不向 LLM 暴露 read_file/write_file 这类原始能力；所有文件 I/O 都发生在确定性领域工具（render_artifact / update_artifact / fetch_ohlcv 的缓存写入等）内部，并统一经 WorkspaceFS 单点中介——文件安全逻辑只需在一处实现、一处测试。
2. **LLM 永远不提供文件路径。** 工具参数只有逻辑标识：`artifact_id`、`dataset_id`、skill name。实际路径由系统派生：产物文件名 = `slug(artifact_id)_v{n}.{ext}`，扩展名由 `kind` 查表决定（白名单 html/xlsx/pptx/docx）；数据路径经注册表解析。**路径注入在参数层就不存在**，而非依赖运行时拦截。
3. **所有解析后的路径必须落在会话工作区内。** WorkspaceFS 对每次读写守卫：`resolve()` 后必须以 workspace root 为前缀（防符号链接逃逸）；拒绝绝对路径与含 `..` 的输入（纵深防御，即使按原则 2 它们不应出现）；写入仅限白名单子目录（artifacts/ specs/ data/）。

写入语义：

- **版本文件 append-only**：渲染从不覆盖已有版本文件，重渲染只产生新版本号——用户对照旧版的能力不依赖"没被覆盖"的运气；
- **注册表原子写**：manifest.json 与 data/index.json 用临时文件 + `os.replace` 更新，进程中断不产生半写状态；
- 单文件大小上限，防失控输出撑爆磁盘。

skill 静态资产（如 plotly.min.js）由渲染器从内置 skill 的 `assets/` 按白名单复制进产物目录，agent 无法指定来源路径。源码树（含 skill 资产）在运行期只读，`outputs/` 是唯一写入区。

**如实陈述的剩余风险**：工具与渲染器本身是可信代码（纯库调用，无 shell/exec/eval）；威胁面是 LLM 生成的参数与外部数据内容，分别被"逻辑标识 + pydantic 校验"与"转义 + 白名单"拦截。若未来产物需要执行用户自定义代码（如自定义指标脚本），必须补真沙箱——明确 out of scope。

## 6. 产物管线：ArtifactSpec 中间表示

回应两个需求：**产物结构不能是固定模板**（用户任务多样）、**产物要能被多轮定点修改**。方案是在 LLM 判断与文件渲染之间放一层结构化中间表示：

```
report-builder（判断，自由）      ArtifactSpec（结构化 IR）        渲染器（确定性 tool）
"该有哪些章节/页面/sheet、   →   JSON block 树，每块带类型/   →   spec → html/docx/pptx/xlsx
 每块说什么、引哪些数据"          内容/data_ref/evidence_refs       纯代码，可单测
```

**spec 是一棵灵活的 block 树**，产物有几章、PPT 有几页、Excel 有哪些 sheet 完全由 agent 按任务决定：

```json
{
  "artifact_id": "nvda-kline-report", "kind": "html",
  "title": "…", "skill": "kline-html-report",
  "blocks": [
    {"type": "heading", "text": "一、五年行情全景"},
    {"type": "narrative", "md": "…", "evidence_refs": ["ev-…-3"]},
    {"type": "kline_chart", "data_ref": "ds-nvda-ohlcv-5y",
     "annotations": [{"date": "2022-11-30", "event": "ChatGPT 发布",
                      "rating": "高/正面", "evidence_refs": ["ev-…-12"]}]},
    {"type": "event_card", "…": "…"},
    {"type": "changepoint_table", "…": "…"}
  ]
}
```

- **block 类型库**由各渲染器声明支持集合：通用（heading/narrative/table/footnote）+ 专用（kline_chart/event_card/slide/formula_sheet/pivot_summary…）。遇到不支持的 block 显式报错，不静默丢弃。
- **spec 里没有文件路径**：`data_ref` 是 dataset_id（经工作区 `data/index.json` 注册表解析），渲染器经 WorkspaceFS 取数——LLM 产出的 spec 无法表达"读工作区外的文件"。
- **skill 与 spec 的关系**：skill 的 SKILL.md 告诉 report-builder"这类产物惯常怎么组织论证、有哪些 block 可用、评级怎么呈现"（方法论），templates/assets 提供渲染骨架（Plotly 交互外壳、docx 样式集）——**内容结构在 spec 里，每次都是新的**。
- **修改 = spec 定点变更 + 重渲染**：`read_artifact` 读回当前 spec → report-builder 只改目标 block → `update_artifact` 校验、渲染、版本 +1、写 manifest。未涉及的 block 原样保留，"定点生效"由此保证，且两版 spec 的 diff 就是改动审计记录。
- **可测试性**：渲染器用 spec fixture 做单测（断言 HTML 含标注锚点、xlsx 公式存在、pptx 页数正确），完全不依赖 LLM。

## 7. 自研 skill 机制

```
src/finance_agent/skills/           # 机制代码：扫描、索引、加载
└── builtin/<name>/                 # 内置 skill 资产（包数据，随包分发）
    ├── SKILL.md          # frontmatter(name/description/kind/blocks) + 方法论正文
    ├── templates/        # 渲染骨架（HTML 交互外壳、文档样式定义）——非内容模板
    └── assets/           # 静态资产（如 vendored plotly.min.js）
```

**资产放包内而非仓库根**：skill 资产是产品内置能力，与机制代码、渲染器同生命周期、同测试、同交付；经 `importlib.resources` 定位，任何安装方式与 CWD 下都可靠（放仓库根则要靠向上爬目录猜仓库位置，非 editable 安装即断）。用户扩展诉求分离处理：`FINANCE_AGENT_SKILLS_DIR` 可追加外部 skill 目录，扫描时与内置资产合并索引。

**渐进式披露**（控制上下文成本）：

1. 启动时扫描内置 `builtin/` 与 `FINANCE_AGENT_SKILLS_DIR`（如设置），仅把 frontmatter 索引（每个 skill 一行）注入 orchestrator/report-builder 的 prompt；
2. report-builder 判断需要某 skill 后调 `load_skill(name)` 读入完整方法论；
3. 产物落地统一走 `render_artifact(spec)`——渲染是确定性 tool，skill 里不放可执行代码，杜绝"从仓库读文本当代码跑"的注入面。

已有原型的收编：`nvda_ai_events_candlestick.html` 拆解为 `kline-html-report` 的渲染骨架（Plotly 初始化、缩放/悬浮/标注点击交互、evidence 展开面板），`nvda_data_loader.js` 的解析逻辑移植进 Python 工具层，Plotly 进 `assets/`，JS 单测保留看护渲染骨架的行为。

## 8. 工具层规格

| 工具 | 输入 → 输出 | 要点 |
|---|---|---|
| `fetch_ohlcv` | ticker, start, end → 数据摘要 + **dataset_id** + evidence_id | 降级链 Yahoo query1→query2→本地缓存；多源解析统一 schema；记录实际命中源；数据落工作区 data/ 并登记 index.json，后续环节以 dataset_id 引用 |
| `search_hn_news` | query, date_range → 条目[{title,url,points,date}] + evidence_id | Algolia `search_by_date` + `numericFilters` 查历史 |
| `fetch_yahoo_news` | ticker/keyword → 条目 + evidence_id | 财经视角补充 |
| `web_search` | query → 摘要+来源 | SDK hosted WebSearchTool，供 event-researcher 补漏与交叉验证 |
| `detect_changepoints` | dataset_id → 变化点[{date,type,rule,window,severity}] + evidence_id | 见 §9；输入经注册表取数，不接受路径 |
| `load_skill` | name → SKILL.md 全文 | 见 §7 |
| `list_artifacts` | — → manifest 摘要 | 工作区感知：有哪些产物、各自最新版本与变更摘要 |
| `read_artifact` | artifact_id → 当前 spec | 供修改流程读回结构 |
| `render_artifact` | spec → 文件 + manifest 登记（v1） | 新建产物；文件名由系统派生（slug + 版本 + kind 查表扩展名），LLM 不传路径 |
| `update_artifact` | artifact_id, 新 spec → 文件 + 版本 +1 | spec 校验（pydantic）失败即拒绝，不产出半成品；append-only 不覆盖旧版 |

失败语义统一：工具对外抛结构化错误（含已尝试的源与原因），重试/换源的决策权在 subagent。

**文件访问原则**（呼应 §5 WorkspaceFS）：上表没有、也不会有通用 read_file/write_file 工具；所有涉盘操作都在领域工具内部经 WorkspaceFS 完成，工具参数一律为逻辑标识（artifact_id / dataset_id / skill name）。

## 9. 拐点检测算法（确定性）

对日线收盘价与成交量：

1. **趋势段划分**：20 日滚动线性回归斜率变号 → 拐点候选（类型：拐头向上/向下）；
2. **加速/异动**：21 日滚动收益 z-score，|z| > 2.0 → 加速上涨/下跌；
3. **回撤/反弹确认**：自局部极值回撤（或反弹）幅度 > 15% → 确认级变化点；
4. **量能异常**：成交量 > 60 日均量 × 3 → 辅助信号（提升邻近变化点 severity）；
5. 邻近合并：同类型变化点在 N 日窗口内合并，保留触发强度最高者。

所有阈值集中为 `ChangepointParams` 数据类（可配置、入单测）。每个变化点携带：触发规则名、触发窗口的原始数据行引用、计算中间值——这是"拐点可解释、可溯源"的实现。

## 10. 溯源（provenance）数据模型

```
Evidence {
  id:          ev-<run>-<seq>
  kind:        market_data | news | search | computation
  source_url:  原始 URL（computation 则指向输入 evidence）
  query:       实际请求参数/查询词
  fetched_at:  ISO 时间戳
  excerpt:     原始数据摘录（前 N 行 / 标题列表）
}
```

- 每个会话一份 `evidence.json`，spec 的 block 级 `evidence_refs` 引用 evidence id，跨版本累积；
- **HTML**：事件卡片/拐点标注点击展开来源明细，链接可点；
- **Excel**：独立"溯源" sheet，数据 sheet 内标注来源；
- **Word/PPT**：尾注/页脚注明 evidence id 与 URL；
- computation 类 evidence 形成链：对齐结论 → 拐点 evidence + 事件 evidence → 原始行情/资讯。

## 11. Web 聊天界面（薄层）

- FastAPI + 单文件 `index.html`（原生 JS，无构建链、无 npm 依赖，样式与脚本内联或同目录本地文件）；
- 仅监听 `127.0.0.1`，不设远程访问；SSE 流式输出对话；
- 左侧聊天、右侧产物面板（读 manifest：产物、版本、变更摘要，点击用系统默认程序打开本地文件）；
- **与 REPL 共用 `SessionCore`**：Web 层不含任何业务逻辑，只是会话核心的 HTTP 皮肤——这是它敢排在最后、必要时可裁剪的原因。

## 12. 安全与跨域

- 密钥仅 `OPENAI_API_KEY`，环境变量注入，`.env` gitignore，产物与 manifest 中不出现；
- agent 无通用文件读写工具，全部文件 I/O 经 WorkspaceFS 单点守卫（§5）：工作区禁闭、白名单子目录/扩展名、append-only 版本、注册表原子写；
- HTML 产物零外部请求：Plotly 内联/同目录分发，数据内嵌 JSON，`file://` 断网可开；
- skill 模板经受控渲染器执行，不 eval 仓库内文本；spec 经 pydantic 校验后才进渲染器；
- 外部数据（资讯标题等）进入 HTML 前做转义，防注入；
- Web 服务只绑本地回环地址，无认证需求即不引入认证复杂度。

## 13. 测试策略

| 层 | 方式 |
|---|---|
| tools | pytest 单测：三源解析 fixture、降级链（mock HTTP）、拐点规则合成序列断言 |
| skill 机制 | 索引扫描、frontmatter 解析、load_skill 行为 |
| spec/渲染器 | spec fixture → 断言产物结构（HTML 标注锚点与 evidence 面板、xlsx 公式、pptx 页数、docx 章节）；非法 spec 被拒 |
| 工作区 | manifest 读写、版本递增、update_artifact 定点变更后未涉及 block 不变；WorkspaceFS 路径守卫（`..`/绝对路径/symlink 逃逸被拒）、注册表原子写、版本文件 append-only |
| 编排 | mock 模式（录制工具返回）跑通"新建→修改"全流水线的集成测试，不烧 API |
| 前端资产 | 保留 node 测试：渲染骨架行为 + 产物 HTML 无 CDN 依赖断言 |
| 产物 | 冒烟：xlsx/pptx/docx 能被对应库重新打开解析 |

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Yahoo API 变更/限流 | 三级降级链 + 本地缓存种子入库，评审离线也能跑 mock |
| 事件检索质量不稳 | 拐点定向检索 + 三路交叉；允许"无对应事件"诚实输出 |
| LLM 输出结构漂移 | subagent 间传递与 spec 全部 pydantic 校验，不传自由文本 |
| spec 定点修改误伤其他 block | update_artifact 前后 diff 落盘；单测覆盖"改 A 不动 B" |
| 无沙箱下的文件误写/路径注入 | LLM 只传逻辑标识不传路径（参数层消除）；WorkspaceFS 单点守卫 + 白名单 + append-only（运行时纵深）；守卫行为全量单测 |
| Web 层挤占时间盒 | 薄层设计 + 排期最后（M7）；核心验收不依赖它，必要时降级交付 REPL |
| 时间盒超支 | M4 跑通场景 A 即具备最小可交付；文档 skills 相互独立可裁剪 |
