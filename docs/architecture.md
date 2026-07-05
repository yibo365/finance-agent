# 架构文档：finance-agent

> 本篇讲**为什么这样设计**：分层原则、编排模式、数据流与各道确定性防线。
> 大部分机制背后都有一次真实事故——文中以「事故」标注，它们同时被固化成回归测试。

## 1. 三层划分（核心原则）

```
LLM 判断层      orchestrator + 4 个 subagent：意图路由、检索策略、内容组织
──────────────────────────────────────────────────────────────
确定性代码层    行情采集/变化点算法/检索 API/渲染器/工作区：可测、可复现
──────────────────────────────────────────────────────────────
确定性校验层    落盘前的硬校验：溯源回链、URL 成员、占位符、预算、修复
```

边界铁律：**判断交给 LLM，事实与纪律交给代码**。所有"硬要求"必须落在校验层
（渲染前确定性拒绝 + 报错引导模型自我修正），不靠 prompt 自律——prompt 只能降低
概率，校验才能保证下限。

## 2. Agent 编排（agents-as-tools）

```
用户 ── Web(SSE)/CLI ── SessionCore.stream_turn（唯一执行引擎）
                              │
                        orchestrator（持有对话记忆，TrimmedSession 修剪）
                              │  agents-as-tools：自定义 function_tool 包装
        ┌──────────────┬──────┴────────┬────────────────┐
   data-collector  event-researcher  alignment-analyst  report-builder
   行情+变化点      三路检索+增量提交    纯推理对齐（零检索）   spec 组装+渲染
        └──────────────┴───────┬───────┴────────────────┘
                         Workspace（材料/数据/产物/溯源/审计日志）
```

- 不用 SDK 裸 `as_tool`：包装工具的参数 schema 即 **TaskBrief**，强制携带用户原话
  （`original_request` 逐字引用），治理编排层传话失真；
- subagent 每次调用都是干净上下文（无对话记忆）；对话记忆只在 orchestrator 层；
- 权限矩阵：每个 subagent 只挂它职责内的工具（可执行断言见 test_agents），
  report-builder 是唯一能写产物的环节，且只能经 render/update_artifact 间接写盘。

## 3. 材料按引用传递（上下文治理之本）

「事故」变化点/事件/对齐矩阵按值在 brief 与输出里来回复制：51KB brief 永久驻留
主对话历史；子代理单次请求滚到 7.8M tokens（超 8MB 上限）。

现行数据流：subagent 全量输出 → 包装层自动落盘为 `materials/mat-<kind>-<n>.json`
→ orchestrator 历史里只留 **material_id + 确定性摘要**（事件一行一条）→ 下游
subagent 用 `load_material` 按需读全量 → 产物 spec 也按引用挂载
（`events_material` / `changepoints_material`，渲染器从工作区注入全量，
LLM 不抄写几十条事件——「事故」内联事件超输出上限，参数被截断必然失败）。

配套四道防线：

1. **主 agent 历史修剪**（TrimmedSession）：喂给模型时保留最近 2 个用户轮完整，
   更早的轮只留 user/assistant 文本；落库仍全量（审计与前端回放不受影响）；
2. **检索预算**（默认 36 次/运行）：耗尽时检索工具返回收敛指令而非结果
   ——「事故」researcher 无预算连搜 98 次、轮次打满整体作废；
3. **失败消息截断**：工具错误进上下文前截断（pydantic 会回显完整参数，
   失败重试按 2×参数体积滚雪球）；
4. **事件增量提交**（submit_events）：研究成果小批量落入运行内累积器，
   最终输出只需 coverage_notes——收尾损坏甚至 Max turns 打满时，
   已提交事件仍由包装层合并返回，成果不作废。

## 4. 结构化输出的容错链

「事故」弱工具调用模型（deepseek 等）的结构化输出带 ```json 围栏/中文前言/
未转义引号/截断，SDK 严格解析失败 → 整体重跑。

按序兜底（`json_repair.py`，全部确定性字符串操作）：
SDK 解析 → 失败则从错误取回原文 → 围栏剥离 → 花括号窗口 → 坏引号迭代转义 →
列表逐项打捞（坏一两条丢一两条）→ 仍失败且累积器有事件则合并兜底 → 才报错。

## 5. 供应方兼容层（`llm.py`，唯一收口）

底层模型可随时换（OpenAI/OpenRouter/DeepSeek 官方/Kimi/自建网关），应用代码
不感知差异。三条确定性变换（对宽容供应方均为无害）：

1. `response_format: json_schema` → `json_object` 降级（schema 改由提示词携带）
   ——「事故」"This response_format type is unavailable now"；
2. 消息序列规整：同轮"边说话边调工具"被 SDK 拆成插队 assistant 消息，
   严格供应方校验 tool 回执邻接直接 400——发送前并入文本、回执按声明顺序紧随、
   缺失补占位、无主丢弃；
3. `max_tokens` 超供应方上限被 400 拒绝 → 自动去参重试（预算默认放宽 200K）。

联网检索独立于 LLM（Tavily 确定性 API）：换模型不改变检索数据，研究可复现。

## 6. 确定性校验层清单（落盘前拒绝）

| 校验 | 拦截的事故 |
| --- | --- |
| evidence 引用存在性 | 自造语义化 evidence id（`ev-match-xxx`），页面锚点悬空 |
| 事件 URL 成员校验（对照溯源记录的 URL 全集） | 材料传递丢字段后，模型凭记忆编造 reuters/bloomberg 链接（404） |
| 事件回链类型校验（须含 news/search 类 evidence） | 25 个事件的回链整体挂到行情数据 evidence |
| 事件必须带可点击 URL | PRD 硬要求确定性化 |
| xlsx 占位符拒绝（"公式"/"待填"/TBD…） | 指标表全是字面量"公式"的空壳产物 |
| skill 结构约束（如恰好 1 个 kline_chart） | 结构性缺失 |
| 路径守卫（见 §8） | —— |

校验失败返回具体原因，模型在重试循环内自我修正——报错文案就是修正指引。

## 7. 会话、工作区与溯源

```
outputs/<session_id>/
├── manifest.json        产物注册表（artifact_id → 版本历史，原子写）
├── session.db           SQLiteSession 对话历史（落库全量；读时修剪）
├── artifacts/           渲染产物，全版本保留（append-only）
├── specs/               每版 ArtifactSpec 快照
├── data/ + index.json   行情缓存（dataset_id 注册表；修改产物不重抓）
├── materials/           subagent 全量输出（引用传递载体）
├── evidence.json        溯源记录（原子写；urls 记录检索的全部候选链接）
└── run_events.jsonl     嵌套 subagent 运行审计日志（事故复盘用）
```

溯源模型：工具每次抓取/计算登记一条 Evidence（id/kind/source_url/urls/query/
fetched_at/excerpt）；产物 block 以 evidence_id 引用生成回链锚点；computation 类
evidence 指向输入 evidence 形成链，"对齐结论 → 拐点/事件 → 原始行情/资讯"逐级可回溯。

## 8. 文件安全（WorkspaceFS，无 OS 沙箱的替代约束）

1. agent 不持有通用文件工具——文件 I/O 只发生在确定性领域工具内部，经 WorkspaceFS 单点中介；
2. LLM 永远不提供路径——参数只有逻辑标识（artifact_id/dataset_id/material_id/skill name），
   实际路径由系统派生，**路径注入在参数层就不存在**；
3. 所有解析后路径必须落在会话工作区内（resolve 后前缀校验，防符号链接逃逸；
   拒绝绝对路径与 `..`；写入限白名单子目录）。

写入语义：版本文件 append-only；注册表原子写（tmp + os.replace）；单文件大小上限。
Web 仅绑定 127.0.0.1；密钥只存本机 .env，接口外发一律打码。

## 9. Web 并发模型

- 每会话一把 `asyncio.Lock`，**锁的生命周期绑定运行任务而非 SSE 连接**
  ——「事故」浏览器刷新释放了锁但 SDK 运行未停，新旧两轮并发写同一 session.db；
- 停止是真取消：`/stop` → 任务 cancel → 一路传导到 SDK `result.cancel()`；
- 断连只停推送不停任务；前端凭 `/state` 的 `running` 恢复运行态并轮询收敛；
- 会话注册表 LRU 淘汰（持锁会话不淘汰）。

## 10. 已知边界与风险

- 无真实 LLM 的自动化端到端回归：确定性层全测 + 真实事故回归样本，
  但流水线协议改动仍需人工跑一次真实任务验收；
- 事件 URL 成员校验用溯源 URL 全集（非逐事件对照）——防伪度与误拒率的权衡；
- 中文财经资讯源实质依赖 Tavily 一路；行情依赖 Yahoo 非官方接口（多源降级，无 SLA）；
- 供应方 5xx 无自动重试退避；子代理无墙钟超时（有轮次上限与检索预算兜底）；
- 若未来产物需执行用户自定义代码（如自定义指标脚本），必须补真沙箱——明确 out of scope。
