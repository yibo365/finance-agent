"""溯源记录：每条数据、每个计算结果都有 Evidence 可回链。

Evidence 是全链路溯源的最小单元：工具每次抓取/计算都登记一条，
产物 spec 的 block 以 evidence_id 引用，最终随工作区 evidence.json 落盘。
computation 类 evidence 的 source_url 指向输入 evidence（形成链），
使"对齐结论 → 拐点/事件 → 原始行情/资讯"可逐级回溯。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceKind = Literal["market_data", "news", "search", "computation"]


class Evidence(BaseModel):
    id: str
    kind: EvidenceKind
    source_url: str
    # 本次抓取返回的全部候选原文 URL（citations / 资讯条目）。
    # 产物中事件的 sources.url 必须能在溯源记录的 URL 集合里找到——
    # 只记 source_url（首条）不够，会漏掉同一次检索的其余结果。
    urls: list[str] = Field(default_factory=list)
    query: dict[str, Any] = Field(default_factory=dict)
    fetched_at: str
    excerpt: str = ""


class EvidenceLog:
    """一次会话内的 evidence 集合；id 单调递增，可整体落盘/回读。"""

    def __init__(self, run_id: str = "run") -> None:
        self.run_id = run_id
        self._items: list[Evidence] = []

    def record(
        self,
        kind: EvidenceKind,
        *,
        source_url: str,
        urls: list[str] | None = None,
        query: dict[str, Any] | None = None,
        excerpt: str = "",
    ) -> Evidence:
        evidence = Evidence(
            id=f"ev-{self.run_id}-{len(self._items) + 1}",
            kind=kind,
            source_url=source_url,
            urls=urls or [],
            query=query or {},
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            excerpt=excerpt,
        )
        self._items.append(evidence)
        return evidence

    def items(self) -> list[Evidence]:
        return list(self._items)

    def known_urls(self) -> set[str]:
        """溯源记录里真实出现过的全部原文 URL（产物事件来源校验的对照集）。"""
        urls: set[str] = set()
        for item in self._items:
            if item.source_url.startswith(("http://", "https://")):
                urls.add(item.source_url)
            urls.update(item.urls)
        return urls

    def get(self, evidence_id: str) -> Evidence:
        for item in self._items:
            if item.id == evidence_id:
                return item
        raise KeyError(f"evidence 不存在：{evidence_id}")

    def save(self, path: Path) -> None:
        payload = {
            "run_id": self.run_id,
            "items": [item.model_dump() for item in self._items],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> EvidenceLog:
        payload = json.loads(path.read_text(encoding="utf-8"))
        log = cls(run_id=payload["run_id"])
        log._items = [Evidence.model_validate(item) for item in payload["items"]]
        return log
