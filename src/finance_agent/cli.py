"""CLI 入口：接收自然语言研究任务，交给 orchestrator 自主完成。

用法：
    finance-agent "回顾英伟达（NVDA）近五年行情数据，梳理同期 AI 行业大事件……"
"""

from __future__ import annotations

import argparse

from finance_agent.config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finance-agent",
        description="投研 agent：输入自然语言研究任务，产出可交互、可溯源的研究产物。",
    )
    parser.add_argument("task", help="自然语言描述的研究任务")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="产物输出目录（默认 outputs/）",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    settings.require_api_key()
    # orchestrator 在 M3 接入；此处为 M0 骨架
    raise SystemExit(f"orchestrator 尚未接入（M3），收到任务：{args.task!r}")


if __name__ == "__main__":
    main()
