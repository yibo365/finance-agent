"""事件流协议（FR-18）：把 SDK 运行流翻译成面向前端/CLI 的结构化事件。

协议（prd-web-ui-v2.md §二.1）：
    session / agent_start / tool_call / tool_result / agent_end / delta / done / error

Web（SSE）与 CLI 消费同一 stream_turn 产出的同一串事件——协议只增不改，
消费端按顺序渲染即可。detail 一律定长截断：进度事件是给人看的摘要，
不承载数据（全量参数/输出走各自的持久化通道）。
"""

from __future__ import annotations

from typing import Any

DETAIL_LIMIT = 160
# 错误详情放宽：截在 160 会把 pydantic 校验错误切在字段名处，
# 用户对着日志无法定位失败原因（真实事故：连续 5 次 "…validation error for
# render_artifact_args spe…"，看不到到底哪个字段错）
ERROR_DETAIL_LIMIT = 600

# SDK 工具层报错的固定前缀（agents 库 failure_error_function 的措辞）。
# ok 判定、历史重建、错误截断三处都依赖它——集中在此一处，SDK 升级换措辞
# 只需改这里（truncated_tool_error 生成的消息也以它开头保持一致）。
TOOL_ERROR_PREFIX = "An error occurred while running the tool"
_TOOL_ERROR_PREFIX = TOOL_ERROR_PREFIX


def clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    text = " ".join(str(text).split())  # 压掉换行/连续空白，进度行只占一行
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _field(raw: Any, key: str) -> Any:
    """raw_item 可能是 pydantic 对象也可能是 dict（SDK 两种形态都出现过）。"""
    if isinstance(raw, dict):
        return raw.get(key)
    return getattr(raw, key, None)


class RunItemTranslator:
    """把一个 Runner 流的 run_item 事件翻成外发事件；按 call_id 回填工具名。"""

    def __init__(self, agent: str) -> None:
        self.agent = agent
        self._tool_names: dict[str, str] = {}

    def translate(self, item: Any) -> dict | None:
        if item.type == "tool_call_item":
            raw = item.raw_item
            name = str(_field(raw, "name") or "工具")
            call_id = str(_field(raw, "call_id") or "")
            if call_id:
                self._tool_names[call_id] = name
            arguments = str(_field(raw, "arguments") or "")
            return {
                "type": "tool_call",
                "agent": self.agent,
                "tool": name,
                "detail": clip(arguments),
                # 参数全长进审计日志：截断类失败（Invalid JSON input）一眼可辨
                # （真实事故：3 次 render_artifact 各烧 112s 生成 12K token 参数
                # 被上限掐断，日志里只有 160 字符前缀，只能靠间隔时间反推）
                "detail_len": len(arguments),
            }
        if item.type == "tool_call_output_item":
            call_id = str(_field(item.raw_item, "call_id") or "")
            output = str(getattr(item, "output", None) or _field(item.raw_item, "output") or "")
            ok = not output.startswith(_TOOL_ERROR_PREFIX)
            return {
                "type": "tool_result",
                "agent": self.agent,
                "tool": self._tool_names.get(call_id, ""),
                "ok": ok,
                "detail": clip(output, DETAIL_LIMIT if ok else ERROR_DETAIL_LIMIT),
            }
        return None
