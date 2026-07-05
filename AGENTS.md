# AGENTS.md — AI 编码代理开发指南

给在本仓库工作的 AI 编码助手（Claude Code / Codex / Cursor 等）的项目约定。
人类贡献者同样适用。

## 项目速览

本地投研 Agent 工作台：自然语言任务 → 行情×事件对齐分析 → 可溯源产物
（HTML/Excel/PPT/Word）。Python 后端（`src/finance_agent/`，OpenAI Agents SDK +
FastAPI）+ Vite 前端（`webapp/`）。文档：[产品](docs/product.md) /
[技术](docs/technical.md)（含代码结构图）/ [架构](docs/architecture.md)。

## 常用命令

```bash
uv run pytest -q                     # 全量单测（必须全绿才能提交）
uv run ruff check .                  # 静态检查（CI 会跑，提交前先过）
node --test tests/*.test.cjs         # 前端资产单测
npm --prefix webapp run build        # 改了 webapp/ 后必须重新构建
./scripts/dev.sh                     # 本地起前后端（端口占用会自动重启）
FINANCE_AGENT_MOCK=1 uv run pytest tests/test_agents.py   # 离线冒烟
```

## 硬性设计原则（违反这些的 PR 不该合）

1. **判断交给 LLM，事实与纪律交给代码。** 任何"硬要求"（溯源、URL 真实性、
   结构约束）必须落在确定性校验层（渲染/落盘前拒绝 + 报错引导自我修正），
   不靠 prompt 自律。新校验的报错文案要能当修正指引用。
2. **大数据按引用传递。** 变化点/事件/对齐矩阵等大 JSON 不进对话历史、不进
   brief、不让 LLM 抄写——落盘为 material，上下文只传 `mat-*` id，下游用
   `load_material` / 渲染器注入。任何让 LLM 输出整段大 JSON 的设计都要先想
   "会不会撞输出上限/解析失败"。
3. **供应方兼容只收口在 `llm.py`。** 换 LLM 供应方暴露的差异（response_format、
   消息邻接、max_tokens 上限……）一律加为该文件里的确定性纯函数变换 + 单测；
   禁止在业务代码里写 if-某供应方。
4. **LLM 永远拿不到文件路径。** 工具参数只有逻辑标识（artifact_id / dataset_id /
   material_id / skill name），路径由 `workspace.py` 派生并禁闭在会话工作区。
   新工具遵守同样纪律。
5. **离线路径不碰网络栈。** mock 模式（`FINANCE_AGENT_MOCK=1`）必须完全离线；
   测试不调真实网络与 LLM（httpx MockTransport / 打桩 stream_turn）。
6. **文档不写死计数。**（测试数、行数这类会漂移的数字不进文档正文。）

## 测试要求

- 改动后 `uv run pytest -q` + `uv run ruff check .` 全绿；改前端还要 build 成功；
- 真实事故修复必须附"还原事故样本"的回归测试（仓库惯例：docstring 里写
  「真实事故：……」说明拦截的是什么）；
- 安全设计（路径守卫、权限矩阵、密钥打码）直接写成可执行断言。

## 提交规范

- 中文 commit message，`feat:` / `fix:` / `docs:` / `refactor:` 前缀；
- 正文写清「事故/动机 → 防线/方案 → 验证」；
- `outputs/`（会话工作区）与 `.env` 永不入库；样例产物经 `samples/` 显式收录。

## 容易踩的坑

- `webapp/dist` 是构建产物：改了 `webapp/src` 不 build，后端 8765 看到的还是旧页面；
- `Settings` 是 frozen dataclass：运行时改配置走 `SettingsStore.update`（写回 .env，
  只对新会话生效）；
- 会话执行锁的生命周期绑定运行任务而非 SSE 连接（`web/app.py`），别"顺手简化"回去；
- SQLiteSession 落库是全量，喂给模型前经 `TrimmedSession` 修剪——改历史相关逻辑时
  分清这两个视图；
- 嵌套 subagent 运行不进 session.db，事故复盘看 `outputs/<sid>/run_events.jsonl`。
