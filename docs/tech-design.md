# 技术设计：投研 Agent

> 版本 v1.0 ｜ 2026-07-03 ｜ 配套 [prd.md](prd.md)，记录架构划分与关键取舍

## 1. 选型与理由

| 决策 | 选择 | 理由与放弃的备选 |
|---|---|---|
| Agent SDK | **OpenAI Agents SDK（Python）** | 刻意不用自带 tools/subagents/skills 三概念的框架（如 Claude Agent SDK）——skill 机制与 subagent 边界**自研**，架构划分是自己的判断而非框架赠品。放弃 LangGraph：偏 workflow 编排，"agent 自主完成任务"的味道弱。 |
| 模型 | gpt-5.5（`FINANCE_AGENT_MODEL` 可覆盖） | 影响评级与拐点对齐是判断密集环节；开发迭代可切轻量档。 |
| 语言/工程 | Python 3.12 + uv；`src/` 布局 | docx/pptx/xlsx 生态最成熟；uv 保证评审者一键复现。 |
| 图表 | Plotly 2.35（本地 vendor） | 沿用已验证的原型资产；本地 vendor 解决 CDN 依赖安全/跨域/断网问题。 |
| 行情源 | Yahoo Chart API → Stooq → 本地缓存 | 全部免 key。放弃原型中的 Nasdaq API：需伪装 UA，反爬不稳定，评审复跑易挂。 |
| 资讯源 | HN Algolia + Yahoo 资讯 + hosted WebSearchTool | HN Algolia 支持按时间范围查历史（适合"ChatGPT 发布"这类回溯）；三路互补，且同时覆盖"自定义 function tool"与"SDK 托管工具"两种形态。 |

## 2. 总体架构

```
用户: finance-agent "回顾英伟达近五年行情…"
        │
        ▼
┌─ orchestrator（主 agent，gpt-5.5）─────────────────────────┐
│  职责：解析任务 → 规划 → 依次调用 subagent → 汇总把关       │
│  可见工具：4 个 subagent（as_tool）+ list_skills            │
│                                                            │
│  ┌────────────────┐  ┌────────────────┐                    │
│  │ data-collector │  │event-researcher│   subagents        │
│  │ 选源/重试/校验 │  │ 三路检索→去重  │   （独立上下文）   │
│  │                │  │ →筛选→影响评级 │                    │
│  ├────────────────┤  ├────────────────┤                    │
│  │alignment-      │  │ report-builder │                    │
│  │analyst         │  │ load_skill→    │                    │
│  │ 拐点↔事件对齐  │  │ 填模板→渲染    │                    │
│  └───────┬────────┘  └───────┬────────┘                    │
└──────────┼───────────────────┼─────────────────────────────┘
           ▼                   ▼
  tools（确定性，可单测）              skills（方法论+模板资产）
  fetch_ohlcv / search_hn_news        kline-html-report
  fetch_yahoo_news / web_search       xlsx-backtest
  detect_changepoints                 pptx-framework
  load_skill / render_*               docx-strategy-report
           │
           ▼
  provenance（evidence 存储，贯穿全链路）→ outputs/ 产物
```

## 3. 三层划分的边界原则（核心取舍）

**Tool、Subagent、Skill 不按"功能模块"划分，按"工作性质"划分：**

- **Tool = 确定性、可单测的原子能力。** 输入输出可精确断言（HTTP 抓取、解析、拐点规则计算、模板渲染）。工具**不做判断**，只产出带 evidence 标记的数据。判断放进工具会让它既测不了也溯不了源。
- **Subagent = 需要独立推理上下文的判断环节。** 划分动因是**上下文隔离**而非组织架构美观：event-researcher 要吞几十条搜索结果的脏上下文，不能让它污染主线程；alignment-analyst 需要干净的上下文做严谨论证。每个 subagent 有自己的 system prompt（角色方法论）与受限工具集。
- **Skill = 领域方法论 + 产物模板的打包资产。** 回答"这类产物应该长什么样、怎么组织论证"。skill 是数据不是代码——增加一种产物形态只需加一个 skill 目录，不改流水线。

**边界上的具体判断（设计说明面试点）：**

1. 拐点检测是 tool 不是 subagent：它是纯算法，LLM 掺和进来只会让结果不可复现、不可溯源。LLM 的判断力用在"拐点与事件是否吻合"（alignment-analyst），不用在"哪里是拐点"。
2. 数据采集设为 subagent（data-collector）：降级链本身是 tool 里的确定性逻辑，但**选源策略、时间范围裁剪、质量校验后的重试决策**（如某源数据行数异常少）需要判断，由 data-collector 承担。
3. report-builder 是 subagent 而 render 是 tool：写叙事、选重点事件是判断；模板填充与文件落盘是确定性操作。

## 4. 编排：agents-as-tools

orchestrator 通过 `agent.as_tool()` 调用 4 个 subagent，**全程握有控制权**，放弃 handoffs（控制权转移适合客服分流，研究流水线需要中央汇总组装）。

典型执行序（由 orchestrator 自主决定，非硬编码）：

```
parse 任务 → data-collector（行情+拐点）
          → event-researcher（拿拐点时间窗做定向检索）
          → alignment-analyst（拐点×事件 吻合性矩阵）
          → report-builder（选 skill、组装、渲染）
          → orchestrator 终检（产物存在性、溯源完整性）
```

关键数据流细节：**event-researcher 拿着拐点日期窗口做定向检索**（而非漫无目的搜"AI 大事件"），这是"事件与拐点吻合"的机制保证——先有拐点，再找解释，找不到就标注"无对应事件"。

## 5. 自研 skill 机制

```
skills/<name>/
├── SKILL.md          # frontmatter(name/description/outputs) + 方法论正文
├── templates/        # 产物模板（HTML / 文档结构定义）
└── assets/           # 静态资产（如 vendored plotly.min.js）
```

**渐进式披露**（控制上下文成本）：

1. 启动时扫描 `skills/`，仅把 frontmatter 索引（每个 skill 一行）注入 orchestrator/report-builder 的 prompt；
2. report-builder 判断需要某 skill 后调 `load_skill(name)` 读入完整方法论；
3. 模板填充经 `render_skill_template(name, data)` 执行——渲染是确定性 tool，skill 里不放可执行代码，杜绝"从仓库读文本当代码跑"的注入面。

已有原型的收编：`nvda_ai_events_candlestick.html` 参数化为 `kline-html-report/templates/report.html.j2`，`nvda_data_loader.js` 的解析逻辑移植进 Python 工具层，Plotly 进 `assets/`，JS 单测保留看护模板产物。

## 6. 工具层规格

| 工具 | 输入 → 输出 | 要点 |
|---|---|---|
| `fetch_ohlcv` | ticker, start, end → OHLCV 行 + evidence_id | 降级链 Yahoo→Stooq→本地缓存；三源解析统一到同一 schema；记录实际命中源 |
| `search_hn_news` | query, date_range → 条目[{title,url,points,date}] + evidence_id | Algolia `search_by_date` + `numericFilters` 查历史 |
| `fetch_yahoo_news` | ticker/keyword → 条目 + evidence_id | 财经视角补充 |
| `web_search` | query → 摘要+来源 | SDK hosted WebSearchTool，供 event-researcher 补漏与交叉验证 |
| `detect_changepoints` | OHLCV → 变化点[{date,type,rule,window,severity}] + evidence_id | 见 §7 |
| `load_skill` / `render_skill_template` | — | 见 §5 |

失败语义统一：工具对外抛结构化错误（含已尝试的源与原因），重试/换源的决策权在 subagent。

## 7. 拐点检测算法（确定性）

对日线收盘价与成交量：

1. **趋势段划分**：20 日滚动线性回归斜率变号 → 拐点候选（类型：拐头向上/向下）；
2. **加速/异动**：21 日滚动收益 z-score，|z| > 2.0 → 加速上涨/下跌；
3. **回撤/反弹确认**：自局部极值回撤（或反弹）幅度 > 15% → 确认级变化点；
4. **量能异常**：成交量 > 60 日均量 × 3 → 辅助信号（提升邻近变化点 severity）；
5. 邻近合并：同类型变化点在 N 日窗口内合并，保留触发强度最高者。

所有阈值集中为 `ChangepointParams` 数据类（可配置、入单测）。每个变化点携带：触发规则名、触发窗口的原始数据行引用、计算中间值——这是"拐点可解释、可溯源"的实现。

## 8. 溯源（provenance）数据模型

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

- 每个 run 一个 `outputs/<run>/evidence.json`，产物内引用 evidence id；
- **HTML**：事件卡片/拐点标注点击展开来源明细，链接可点；
- **Excel**：独立"溯源" sheet，数据 sheet 内标注来源；
- **Word/PPT**：尾注/页脚注明 evidence id 与 URL；
- computation 类 evidence 形成链：对齐结论 → 拐点 evidence + 事件 evidence → 原始行情/资讯。

## 9. 安全与跨域

- 密钥仅 `OPENAI_API_KEY`，环境变量注入，`.env` gitignore，产物中不出现；
- HTML 产物零外部请求：Plotly 内联/同目录分发，数据内嵌 JSON，`file://` 断网可开；
- skill 模板经受控渲染器执行，不 eval 仓库内文本；
- 外部数据（资讯标题等）进入 HTML 前做转义，防注入。

## 10. 测试策略

| 层 | 方式 |
|---|---|
| tools | pytest 单测：三源解析 fixture、降级链（mock HTTP）、拐点规则合成序列断言 |
| skill 机制 | 索引扫描、frontmatter 解析、渲染器输出断言 |
| 编排 | mock 模式（录制工具返回）跑通全流水线的集成测试，不烧 API |
| 前端资产 | 保留 node 测试：数据 loader 行为 + 产物 HTML 无 CDN 依赖断言 |
| 产物 | 冒烟：xlsx/pptx/docx 能被对应库重新打开解析；HTML 含 evidence 回链锚点 |

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Yahoo API 变更/限流 | 三级降级链 + 本地缓存种子入库，评审离线也能跑 mock |
| 事件检索质量不稳 | 拐点定向检索 + 三路交叉；允许"无对应事件"诚实输出 |
| LLM 输出结构漂移 | subagent 间传递用 pydantic 结构化输出，不传自由文本 |
| 时间盒超支 | M4 跑通场景 A 即具备最小可交付；M5 三个文档 skill 相互独立可裁剪 |
