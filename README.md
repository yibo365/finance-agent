# finance-agent

投研 agent：输入自然语言研究任务，自主完成「行情数据 × 行业事件」对齐分析，产出可交互、可溯源的 HTML / Excel / PPT / Word 研究产物。

> 完整的运行说明与 AI 辅助开发过程记录将在交付打磨阶段（M6）补全。
>
> 需求与设计文档：[docs/prd.md](docs/prd.md) ｜ [docs/tech-design.md](docs/tech-design.md)

## 快速开始（当前为骨架阶段）

```bash
# 依赖 uv（https://docs.astral.sh/uv/）
uv sync

# 配置密钥（.env 已 gitignore，密钥不入库）
cp .env.example .env  # 填入 OPENAI_API_KEY

# 运行
uv run finance-agent "回顾英伟达（NVDA）近五年行情数据……"

# 测试
uv run pytest                  # Python 侧
node --test tests/*.test.cjs   # 前端资产侧
```
