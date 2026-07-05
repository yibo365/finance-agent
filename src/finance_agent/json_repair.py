"""模型 JSON 输出的确定性修复（弱工具调用模型的容错层）。

真实事故（多次）：deepseek 等模型的结构化最终输出带 ```json 围栏/中文前言、
字符串内含未转义引号、或超长被截断——SDK 严格解析失败，orchestrator 只能
整体重跑 subagent，几分钟的检索成果全部作废。

本模块在 subagent 包装层兜底：从 SDK 错误消息取回原始文本，按序尝试
围栏剥离 → 花括号窗口截取 → 坏引号迭代转义 → （列表型）逐项打捞
（坏一两条丢一两条，不拖垮整体）。全部确定性字符串操作，不引入 LLM。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

_RAW_RE = re.compile(r"Invalid JSON when parsing (.*?)(?: for TypeAdapter| for \w+$|$)", re.DOTALL)
_MAX_QUOTE_FIXES = 200


def extract_raw_from_error(error_text: str) -> str | None:
    """从 SDK 的解析错误消息里取回模型的原始输出文本。"""
    match = _RAW_RE.search(error_text)
    if not match:
        return None
    raw = match.group(1).strip()
    return raw or None


def repair_json_text(raw: str) -> str:
    """围栏剥离 + 花括号窗口 + 未转义引号迭代修复。返回尽力修复后的文本。"""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    first, last = s.find("{"), s.rfind("}")
    if first != -1 and last > first:
        s = s[first:last + 1]
    for _ in range(_MAX_QUOTE_FIXES):
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError as exc:
            # 字符串提前被野引号终止 → 解析器在其后位置抱怨分隔符
            if exc.msg.startswith(("Expecting ',' delimiter", "Expecting ':' delimiter")):
                if exc.pos > 0 and s[exc.pos - 1] == '"':
                    s = s[: exc.pos - 1] + '\\"' + s[exc.pos:]
                    continue
                if exc.pos < len(s) and s[exc.pos] == '"':
                    s = s[: exc.pos] + '\\"' + s[exc.pos + 1:]
                    continue
            return s  # 其他错误（截断等）交给逐项打捞
    return s


def parse_items_loose(raw: str, key: str) -> list[dict]:
    """从（可能截断/局部损坏的）JSON 文本中逐项打捞 key 对应数组的对象。

    用 raw_decode 顺序解码数组元素：坏的/不完整的对象跳过，好的保留——
    "坏一两条丢一两条"，不让局部损坏拖垮整体。
    """
    s = repair_json_text(raw)
    anchor = s.find(f'"{key}"')
    if anchor == -1:
        return []
    start = s.find("[", anchor)
    if start == -1:
        return []
    decoder = json.JSONDecoder()
    items: list[dict] = []
    pos = start + 1
    while pos < len(s):
        while pos < len(s) and s[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(s) or s[pos] == "]":
            break
        if s[pos] != "{":
            break
        try:
            obj, end = decoder.raw_decode(s, pos)
        except json.JSONDecodeError:
            # 当前对象损坏/被截断：跳到下一个可能的对象起点再试。
            # 可能误入损坏对象的嵌套子对象——由上层的逐项 pydantic 校验剔除。
            nxt = s.find('{"', pos + 1)
            if nxt == -1:
                break
            pos = nxt
            continue
        if isinstance(obj, dict):
            items.append(obj)
        pos = end
    return items


def salvage_output(error_text: str, model_type: type[BaseModel]) -> BaseModel | None:
    """SDK 最终输出解析失败后的兜底：修复文本再验证；失败返回 None。

    对含单一列表字段的模型（如 EventList.events）额外做逐项打捞：
    逐条 pydantic 校验，坏条目丢弃，好的保留。
    """
    raw = extract_raw_from_error(error_text)
    if raw is None:
        return None
    repaired = repair_json_text(raw)
    try:
        return TypeAdapter(model_type).validate_json(repaired)
    except ValidationError:
        pass
    # 逐项打捞：找出模型里唯一的 list[BaseModel] 字段
    list_fields = [
        (name, field.annotation.__args__[0])
        for name, field in model_type.model_fields.items()
        if getattr(field.annotation, "__origin__", None) is list
        and isinstance(getattr(field.annotation, "__args__", [None])[0], type)
        and issubclass(field.annotation.__args__[0], BaseModel)
    ]
    if len(list_fields) != 1:
        return None
    field_name, item_type = list_fields[0]
    good: list[Any] = []
    for candidate in parse_items_loose(raw, field_name):
        try:
            good.append(item_type.model_validate(candidate))
        except ValidationError:
            continue  # 坏条目丢弃
    if not good:
        return None
    other = {
        name: "" if field.annotation is str else field.default
        for name, field in model_type.model_fields.items()
        if name != field_name and field.is_required()
    }
    try:
        return model_type.model_validate({field_name: good, **other})
    except ValidationError:
        return None
