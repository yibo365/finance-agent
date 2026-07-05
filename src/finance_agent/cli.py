"""CLI 入口。

    finance-agent                       # 交互会话（REPL，默认）
    finance-agent -p "研究任务……"        # 一次性执行后退出
    finance-agent --resume s-20260703-a1b2   # 恢复历史会话继续修改
    finance-agent --list-sessions       # 列出可恢复的会话
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from finance_agent.config import Settings
from finance_agent.session import SessionCore, list_sessions

_EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finance-agent",
        description="投研 agent：输入自然语言研究任务，产出可交互、可溯源的研究产物。",
    )
    parser.add_argument("-p", "--prompt", default=None, help="一次性执行该任务后退出")
    parser.add_argument("--resume", metavar="SESSION_ID", default=None, help="恢复历史会话")
    parser.add_argument("--list-sessions", action="store_true", help="列出可恢复的会话")
    parser.add_argument("--web", action="store_true", help="启动本地 Web 聊天界面（仅 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="Web 界面端口（默认 8765）")
    return parser


def _print_artifacts(delta: list[dict]) -> None:
    for item in delta:
        print(f"  ✔ [{item['artifact_id']} v{item['current_version']}] "
              f"{item['change_summary']} → {item['file']}")


async def _run_once(core: SessionCore, text: str) -> None:
    before = core.artifact_snapshot()
    reply = await core.run_turn(text)
    print(reply)
    _print_artifacts(core.artifact_delta(before))


async def _repl(core: SessionCore) -> None:
    print(f"会话 {core.workspace.session_id}（产物目录：{core.workspace.dir}）")
    print("输入研究任务开始；exit 退出；之后可用 --resume 恢复本会话。\n")
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in _EXIT_COMMANDS:
            break
        try:
            await _run_once(core, text)
        except Exception as exc:  # noqa: BLE001 —— REPL 内报错不退出
            print(f"[错误] {exc}", file=sys.stderr)
        print()
    print(f"会话已保存，可用 finance-agent --resume {core.workspace.session_id} 继续。")


def main() -> None:
    args = build_parser().parse_args()
    if args.list_sessions:
        sessions = list_sessions()
        print("\n".join(sessions) if sessions else "（暂无会话）")
        return

    settings = Settings.from_env()
    settings.require_api_key()
    core = (
        SessionCore.resume(settings, args.resume)
        if args.resume
        else SessionCore.start(settings)
    )
    if args.web:
        from finance_agent.web.app import serve

        serve(core, port=args.port)
    elif args.prompt:
        asyncio.run(_run_once(core, args.prompt))
        print(f"\n会话 {core.workspace.session_id} 已保存，"
              f"可用 finance-agent --resume {core.workspace.session_id} 继续修改。")
    else:
        asyncio.run(_repl(core))


if __name__ == "__main__":
    main()
