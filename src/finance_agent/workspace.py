"""会话工作区：产物、数据缓存与溯源的落盘层（docs/architecture.md §7-8）。

无 OS 沙箱的替代约束（WorkspaceFS 三原则）在此单点实现：
1. agent 不持有通用文件工具——所有涉盘操作都经这里的领域方法；
2. LLM 只传逻辑标识（artifact_id / dataset_id），实际路径由本层派生；
3. 所有解析后的路径必须落在会话工作区内（拒绝越界/../绝对路径/symlink 逃逸）。

写入语义：版本文件 append-only（渲染从不覆盖旧版）；manifest 与 dataset
注册表原子写（tmp + os.replace）；单文件大小上限。

目录布局：
    outputs/<session_id>/
    ├── manifest.json       产物注册表（artifact_id → 版本历史）
    ├── artifacts/          渲染产物，全版本保留
    ├── specs/              每版 ArtifactSpec 快照
    ├── data/               数据缓存 + index.json（dataset_id → 文件/来源）
    ├── materials/          subagent 全量输出（引用传递载体，mat-<kind>-<n>.json）
    ├── evidence.json       溯源记录
    ├── run_events.jsonl    嵌套 subagent 运行的审计日志（工具调用/结果摘要）
    └── session.db          对话历史（SQLiteSession，M4 接线）
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from finance_agent.artifacts.spec import ArtifactSpec, ChangepointMarker, EventAnnotation
from finance_agent.provenance import EvidenceLog

_SESSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_ARTIFACT_EXT = {"html": "html", "xlsx": "xlsx", "pptx": "pptx", "docx": "docx"}
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024


class WorkspaceError(RuntimeError):
    pass


class ArtifactVersion(BaseModel):
    v: int
    file: str            # 工作区相对路径
    spec: str            # spec 快照相对路径
    created_at: str
    change_summary: str


class ArtifactRecord(BaseModel):
    artifact_id: str
    kind: str
    title: str
    current_version: int
    versions: list[ArtifactVersion] = Field(default_factory=list)


class Manifest(BaseModel):
    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        for record in self.artifacts:
            if record.artifact_id == artifact_id:
                return record
        return None


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_session_id() -> str:
    return f"s-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(2)}"


class Workspace:
    """一个会话的文件领地。所有写操作经守卫与原子写。"""

    def __init__(self, outputs_dir: Path, session_id: str, *,
                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        if not _SESSION_ID_RE.match(session_id):
            raise WorkspaceError(f"非法 session_id：{session_id!r}")
        self.session_id = session_id
        self.dir = (Path(outputs_dir) / session_id).resolve()
        self.max_file_bytes = max_file_bytes
        self._evidence: EvidenceLog | None = None

    # ---------- 生命周期 ----------

    @classmethod
    def create(cls, outputs_dir: Path, session_id: str | None = None, **kwargs) -> Workspace:
        ws = cls(outputs_dir, session_id or new_session_id(), **kwargs)
        if ws.dir.exists():
            raise WorkspaceError(f"工作区已存在：{ws.session_id}")
        for sub in ("artifacts", "specs", "data"):
            (ws.dir / sub).mkdir(parents=True)
        return ws

    @classmethod
    def open(cls, outputs_dir: Path, session_id: str, **kwargs) -> Workspace:
        ws = cls(outputs_dir, session_id, **kwargs)
        if not ws.dir.is_dir():
            existing = sorted(
                p.name for p in Path(outputs_dir).glob("s-*") if p.is_dir()
            ) if Path(outputs_dir).is_dir() else []
            hint = f"可用会话：{'、'.join(existing)}" if existing else "（当前没有任何会话）"
            raise WorkspaceError(f"会话不存在：{session_id}。{hint}")
        return ws

    @property
    def session_db_path(self) -> Path:
        return self.dir / "session.db"

    # ---------- WorkspaceFS 守卫与写入语义 ----------

    def _guarded(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.dir):
            raise WorkspaceError(f"路径越界（禁闭于 {self.dir}）：{path}")
        return resolved

    def _join(self, subdir: str, name: str) -> Path:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceError(f"非法文件名：{name!r}")
        return self._guarded(self.dir / subdir / candidate)

    def _write_text(self, path: Path, content: str | bytes, *, overwrite: bool = False) -> None:
        path = self._guarded(path)
        data = content.encode("utf-8") if isinstance(content, str) else content
        if len(data) > self.max_file_bytes:
            raise WorkspaceError(
                f"文件超出大小上限（{len(data)} > {self.max_file_bytes} bytes）：{path.name}"
            )
        if path.exists() and not overwrite:
            raise WorkspaceError(f"版本文件 append-only，拒绝覆盖：{path.name}")
        path.write_bytes(data)

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        """注册表类文件的原子更新：临时文件 + os.replace，不产生半写状态。"""
        path = self._guarded(path)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---------- evidence ----------

    @property
    def evidence(self) -> EvidenceLog:
        if self._evidence is None:
            path = self.dir / "evidence.json"
            self._evidence = (
                EvidenceLog.load(path) if path.is_file() else EvidenceLog(run_id=self.session_id)
            )
        return self._evidence

    def save_evidence(self) -> None:
        if self._evidence is not None:
            self._evidence.save(self._guarded(self.dir / "evidence.json"))

    # ---------- dataset 注册表 ----------

    def _data_index(self) -> dict[str, Any]:
        path = self.dir / "data" / "index.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def store_dataset(
        self, dataset_id: str, df: pd.DataFrame, *,
        ticker: str = "", source: str = "", evidence_id: str = "",
    ) -> str:
        """落盘数据缓存并登记。文件格式兼容 LocalCacheSource（可直接复用为降级种子）。"""
        if not _SESSION_ID_RE.match(dataset_id):
            raise WorkspaceError(f"非法 dataset_id：{dataset_id!r}")
        path = self._join("data", f"{dataset_id}.json")
        payload = {"symbol": ticker, "rows": df.to_dict(orient="records")}
        self._write_json_atomic(path, payload)
        index = self._data_index()
        index[dataset_id] = {
            "file": f"data/{dataset_id}.json",
            "ticker": ticker,
            "source": source,
            "evidence_id": evidence_id,
            "rows": len(df),
            "start": str(df["date"].iloc[0]) if len(df) else "",
            "end": str(df["date"].iloc[-1]) if len(df) else "",
        }
        self._write_json_atomic(self.dir / "data" / "index.json", index)
        return dataset_id

    def dataset_index(self) -> dict[str, Any]:
        return self._data_index()

    # ---------- 材料（subagent 全量输出的引用传递载体） ----------
    #
    # 真实事故：变化点/事件/对齐矩阵按值在 orchestrator ↔ subagent 间来回复制，
    # 单个 brief 到 51KB 且永久驻留对话历史；子代理运行内上下文一次滚到
    # 7.8M tokens（超 8MB 请求上限）。材料落盘、上下文只传 material_id，
    # 下游用 load_material 按需取。

    def store_material(self, kind: str, payload: dict[str, Any]) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", kind):
            raise WorkspaceError(f"非法 material kind：{kind!r}")
        (self.dir / "materials").mkdir(exist_ok=True)
        seq = len(list((self.dir / "materials").glob(f"mat-{kind}-*.json"))) + 1
        material_id = f"mat-{kind}-{seq}"
        self._write_json_atomic(self.dir / "materials" / f"{material_id}.json", payload)
        return material_id

    def material_index(self) -> list[str]:
        """已落盘材料的 id 清单（注入 orchestrator 工作区状态——历史修剪后
        旧轮的 material_id 会从对话里消失，清单是跨轮/恢复会话的找回通道）。"""
        mat_dir = self.dir / "materials"
        if not mat_dir.is_dir():
            return []
        return sorted(p.stem for p in mat_dir.glob("mat-*.json"))

    def append_run_log(self, record: dict[str, Any]) -> None:
        """嵌套 subagent 运行的审计日志（jsonl 追加，非关键路径）。

        真实事故：子代理运行内上下文滚到 7.8M tokens，但嵌套运行不落库，
        爆炸现场只能靠推理还原。此日志记录每次工具调用/结果摘要供事后复盘。
        """
        path = self._guarded(self.dir / "run_events.jsonl")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_material(self, material_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"mat-[a-z][a-z0-9-]{0,30}-\d+", material_id):
            raise WorkspaceError(f"非法 material_id：{material_id!r}")
        path = self._join("materials", f"{material_id}.json")
        if not path.is_file():
            existing = sorted(p.stem for p in (self.dir / "materials").glob("mat-*.json")) \
                if (self.dir / "materials").is_dir() else []
            hint = f"可用材料：{'、'.join(existing)}" if existing else "（本会话暂无材料）"
            raise WorkspaceError(f"材料不存在：{material_id}。{hint}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_dataset(self, dataset_id: str) -> pd.DataFrame:
        entry = self._data_index().get(dataset_id)
        if entry is None:
            raise WorkspaceError(f"dataset 未登记：{dataset_id}")
        path = self._join("data", Path(entry["file"]).name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return pd.DataFrame(payload["rows"])

    # ---------- manifest ----------

    def manifest(self) -> Manifest:
        path = self.dir / "manifest.json"
        if not path.is_file():
            return Manifest()
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_manifest(self, manifest: Manifest) -> None:
        self._write_json_atomic(self.dir / "manifest.json", manifest.model_dump())

    # ---------- 产物操作（render / update / read / list） ----------

    def _render(self, spec: ArtifactSpec) -> str | bytes:
        """spec → 产物内容。按 kind 分派渲染器；数据与 evidence 由本层解析注入。"""
        from finance_agent.artifacts.renderers.docx import render_docx
        from finance_agent.artifacts.renderers.html import render_html
        from finance_agent.artifacts.renderers.pptx import render_pptx
        from finance_agent.artifacts.renderers.xlsx import render_xlsx
        from finance_agent.skills.loader import scan_skills

        renderers = {"html": render_html, "xlsx": render_xlsx,
                     "pptx": render_pptx, "docx": render_docx}
        default_skills = {"html": "kline-html-report", "xlsx": "xlsx-backtest",
                          "pptx": "pptx-framework", "docx": "docx-strategy-report"}
        skills = scan_skills()
        skill_name = spec.skill or default_skills[spec.kind]
        if skill_name not in skills:
            raise WorkspaceError(f"skill 不存在：{skill_name}")
        data_refs: set[str] = set()
        for block in spec.blocks:
            if getattr(block, "data_ref", None):
                data_refs.add(block.data_ref)
            data_refs.update(getattr(block, "data_refs", []) or [])
        datasets = {ref: self.load_dataset(ref) for ref in data_refs}
        evidence = {ev.id: ev for ev in self.evidence.items()}
        return renderers[spec.kind](
            spec, datasets=datasets, evidence=evidence, skill=skills[skill_name]
        )

    def render_artifact(self, spec: ArtifactSpec, *, change_summary: str = "初版") -> ArtifactVersion:
        """新建产物（v1）。同 id 已存在时应走 update_artifact。"""
        manifest = self.manifest()
        if manifest.get(spec.artifact_id) is not None:
            raise WorkspaceError(
                f"产物已存在：{spec.artifact_id}（修改请用 update_artifact，会递增版本）"
            )
        version = self._write_version(spec, 1, change_summary)
        manifest.artifacts.append(
            ArtifactRecord(
                artifact_id=spec.artifact_id, kind=spec.kind, title=spec.title,
                current_version=1, versions=[version],
            )
        )
        self._save_manifest(manifest)
        self.save_evidence()
        return version

    def update_artifact(
        self, artifact_id: str, spec: ArtifactSpec, *, change_summary: str
    ) -> ArtifactVersion:
        """定点修改后的重渲染：版本 +1，旧版文件与 spec 快照全部保留。"""
        manifest = self.manifest()
        record = manifest.get(artifact_id)
        if record is None:
            raise WorkspaceError(f"产物不存在：{artifact_id}")
        if spec.artifact_id != artifact_id:
            raise WorkspaceError(
                f"artifact_id 不可变更：{artifact_id} != {spec.artifact_id}"
            )
        if spec.kind != record.kind:
            raise WorkspaceError(f"产物类型不可变更：{record.kind} != {spec.kind}")
        version = self._write_version(spec, record.current_version + 1, change_summary)
        record.versions.append(version)
        record.current_version = version.v
        record.title = spec.title
        self._save_manifest(manifest)
        self.save_evidence()
        return version

    def _validate_evidence_refs(self, spec: ArtifactSpec) -> None:
        """溯源回链完整性：spec 引用的 evidence 必须真实存在。

        真实事故：report-builder 自造语义化 id（ev-match-deepseek、
        ev-cp-2022-03-17-rally），页面锚点全部悬空。确定性拒绝 + 报错指导，
        让它在循环内自我修正。
        """
        known = {ev.id for ev in self.evidence.items()}
        dangling: set[str] = set()

        def _collect(refs: list[str]) -> None:
            dangling.update(r for r in refs if r not in known)

        for block in spec.blocks:
            _collect(getattr(block, "evidence_refs", None) or [])
            for event in getattr(block, "events", None) or []:
                _collect(event.evidence_refs)
            for cp in getattr(block, "changepoints", None) or []:
                _collect(cp.evidence_refs)
        if dangling:
            sample = "、".join(sorted(dangling)[:6])
            raise WorkspaceError(
                f"evidence 引用不存在（共 {len(dangling)} 个悬空）：{sample}…。"
                "evidence_refs 只能引用溯源记录中真实存在的 id"
                f"（形如 ev-{self.session_id}-<序号>，见任务材料），禁止自造语义化 id；"
                "数据集引用请用 data_ref 字段而非 evidence_refs。"
            )

    def _validate_event_sources(self, spec: ArtifactSpec) -> None:
        """PRD 硬要求：产物中每个事件标注必须带至少一条可点击原文 URL，
        且 URL 必须真实出自溯源记录（evidence 的 source_url/urls 集合）。

        确定性校验而非 prompt 自律（评审指出的口径落差）：schema 允许空列表
        是为了不破坏结构化输出兼容性，落盘前在此拦截。

        真实事故（s-20260704-20c3）：事件材料在传递中丢了 sources，
        report-builder 为通过"必须带 URL"校验凭记忆编造了 reuters/bloomberg
        链接，打开全是 404——存在性校验挡不住"编得像"的 URL，必须做成员校验。
        同时事件的 evidence_refs 被整体挂到行情数据 evidence 上，回链失真，
        故一并要求事件引用中至少一条 news/search 类 evidence。
        """
        known_urls = self.evidence.known_urls()
        evidence_kinds = {ev.id: ev.kind for ev in self.evidence.items()}
        missing: list[str] = []
        fabricated: list[str] = []
        misattributed: list[str] = []
        for block in spec.blocks:
            for event in getattr(block, "events", None) or []:
                http_urls = [
                    s.url for s in event.sources
                    if s.url.startswith(("http://", "https://"))
                ]
                if not http_urls:
                    missing.append(f"{event.date} {event.title}")
                else:
                    unknown = [u for u in http_urls if u not in known_urls]
                    if unknown:
                        fabricated.append(f"{event.date} {event.title} → {unknown[0]}")
                if event.evidence_refs and not any(
                    evidence_kinds.get(r) in ("news", "search") for r in event.evidence_refs
                ):
                    misattributed.append(f"{event.date} {event.title}")
        if missing:
            sample = "；".join(missing[:5])
            raise WorkspaceError(
                f"以下事件缺少可点击的原始来源 URL（共 {len(missing)} 个）：{sample}…。"
                "每个事件的 sources 至少一条 http(s) 链接——没有来源支撑的事件不得进产物。"
            )
        if fabricated:
            sample = "；".join(fabricated[:5])
            raise WorkspaceError(
                f"以下事件的来源 URL 不在溯源记录中（共 {len(fabricated)} 个，疑似编造）：{sample}…。"
                "sources.url 只能逐字复制材料中事件研究给出的真实 URL，"
                "禁止凭记忆构造或\"修正\"链接；材料里没有 URL 就要求上游补齐，而不是编一个。"
            )
        if misattributed:
            sample = "；".join(misattributed[:5])
            raise WorkspaceError(
                f"以下事件的 evidence_refs 未包含任何资讯/检索类 evidence（共 {len(misattributed)} 个）：{sample}…。"
                "事件回链必须指向事件研究产生的 news/search evidence（材料中逐事件给出），"
                "不要把行情数据 evidence 整体挂到所有事件上。"
            )

    def _resolve_material_refs(self, spec: ArtifactSpec) -> ArtifactSpec:
        """kline_chart 的 events_material / changepoints_material → 注入全量。

        真实事故：report-builder 把 24 事件 + 40 变化点逐条抄写进 spec 参数
        （≈33KB ≈ 14-16K tokens），超 max_output_tokens 被截断，
        "Invalid JSON input" 确定性死循环。按引用挂材料后 LLM 只写结构与叙事，
        全量内容由本层从工作区材料确定性注入；内联条目按键覆盖材料条目。
        """
        resolved = spec.model_copy(deep=True)
        for block in resolved.blocks:
            if block.type != "kline_chart":
                continue
            if block.events_material:
                payload = self.load_material(block.events_material)
                if not isinstance(payload.get("events"), list):
                    raise WorkspaceError(
                        f"材料 {block.events_material} 不是事件列表材料（缺 events 数组）；"
                        "events_material 应填 run_event_researcher 返回的 material_id。"
                    )
                material_events = [EventAnnotation.model_validate(e) for e in payload["events"]]
                inline_keys = {(e.date, e.title.strip()) for e in block.events}
                block.events = sorted(
                    [e for e in material_events if (e.date, e.title.strip()) not in inline_keys]
                    + list(block.events),
                    key=lambda e: e.date,
                )
            if block.changepoints_material:
                payload = self.load_material(block.changepoints_material)
                datasets = payload.get("datasets")
                if not isinstance(datasets, list) or not datasets:
                    raise WorkspaceError(
                        f"材料 {block.changepoints_material} 不是市场数据材料（缺 datasets）；"
                        "changepoints_material 应填 run_data_collector 返回的 material_id。"
                    )
                entry = next(
                    (d for d in datasets if d.get("dataset_id") == block.data_ref), datasets[0]
                )
                material_cps = [
                    ChangepointMarker.model_validate(c) for c in entry.get("changepoints", [])
                ]
                inline_keys = {(c.date, c.kind) for c in block.changepoints}
                block.changepoints = sorted(
                    [c for c in material_cps if (c.date, c.kind) not in inline_keys]
                    + list(block.changepoints),
                    key=lambda c: c.date,
                )
        return resolved

    def _write_version(self, spec: ArtifactSpec, v: int, change_summary: str) -> ArtifactVersion:
        resolved = self._resolve_material_refs(spec)  # 校验与渲染都吃注入后的全量
        self._validate_evidence_refs(resolved)
        self._validate_event_sources(resolved)
        content = self._render(resolved)  # 先渲染后落盘：渲染失败不产生任何文件
        ext = _ARTIFACT_EXT[spec.kind]
        slug = spec.artifact_id.replace("-", "_")
        file_rel = f"artifacts/{slug}_v{v}.{ext}"
        spec_rel = f"specs/{spec.artifact_id}_v{v}.json"
        self._write_text(self._join("artifacts", f"{slug}_v{v}.{ext}"), content)
        self._write_text(
            self._join("specs", f"{spec.artifact_id}_v{v}.json"),
            spec.model_dump_json(indent=2),
        )
        return ArtifactVersion(
            v=v, file=file_rel, spec=spec_rel, created_at=_utcnow(),
            change_summary=change_summary,
        )

    def read_artifact_spec(self, artifact_id: str, version: int | None = None) -> ArtifactSpec:
        record = self.manifest().get(artifact_id)
        if record is None:
            raise WorkspaceError(f"产物不存在：{artifact_id}")
        v = version or record.current_version
        matches = [item for item in record.versions if item.v == v]
        if not matches:
            raise WorkspaceError(f"版本不存在：{artifact_id} v{v}")
        path = self._join("specs", Path(matches[0].spec).name)
        return ArtifactSpec.model_validate_json(path.read_text(encoding="utf-8"))

    def list_artifacts(self) -> list[dict[str, Any]]:
        """manifest 摘要（供 agent 的 list_artifacts 工具与 UI 产物面板）。"""
        summary = []
        for record in self.manifest().artifacts:
            current = record.versions[-1]
            summary.append(
                {
                    "artifact_id": record.artifact_id,
                    "kind": record.kind,
                    "title": record.title,
                    "current_version": record.current_version,
                    "file": str(self.dir / current.file),
                    "updated_at": current.created_at,
                    "change_summary": current.change_summary,
                    "history": [
                        {"v": item.v, "created_at": item.created_at,
                         "change_summary": item.change_summary}
                        for item in record.versions
                    ],
                }
            )
        return summary
