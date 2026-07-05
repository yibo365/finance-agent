"""skill 机制：扫描、索引、按需加载（docs/technical.md §2）。

skill = 一个目录：SKILL.md（frontmatter + 方法论正文）+ templates/（渲染骨架）
+ assets/（静态资产）。内置 skill 随包分发（builtin/），外部扩展经
FINANCE_AGENT_SKILLS_DIR 追加，同名时外部覆盖内置（便于用户定制）。

渐进式披露：agent 的 prompt 只常驻 index_lines()（每个 skill 一行），
判断需要后再经 load_skill 读入完整方法论。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from finance_agent.config import BUILTIN_SKILLS_DIR

SKILLS_DIR_ENV = "FINANCE_AGENT_SKILLS_DIR"


class SkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    kind: str                  # 产物类型：html / xlsx / pptx / docx
    blocks: tuple[str, ...]    # 该 skill 声明可用的 block 类型
    path: Path                 # skill 目录

    @property
    def templates_dir(self) -> Path:
        return self.path / "templates"

    @property
    def assets_dir(self) -> Path:
        return self.path / "assets"


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 头部的 --- 包围的 key: value 段，返回 (meta, 正文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md 缺少 frontmatter（需以 --- 开头）")
    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1 :]).strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    raise SkillError("SKILL.md frontmatter 未闭合（缺少结尾 ---）")


def _load_skill_dir(path: Path) -> SkillInfo:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"{path.name}: 缺少 SKILL.md")
    meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    for required in ("name", "description", "kind"):
        if not meta.get(required):
            raise SkillError(f"{path.name}: frontmatter 缺少 {required}")
    return SkillInfo(
        name=meta["name"],
        description=meta["description"],
        kind=meta["kind"],
        blocks=tuple(b.strip() for b in meta.get("blocks", "").split(",") if b.strip()),
        path=path,
    )


def scan_skills(
    extra_dir: Path | None = None, *, builtin_dir: Path = BUILTIN_SKILLS_DIR
) -> dict[str, SkillInfo]:
    """扫描内置与外部 skill 目录。外部同名覆盖内置。"""
    skills: dict[str, SkillInfo] = {}
    search_dirs = [builtin_dir]
    env_dir = os.environ.get(SKILLS_DIR_ENV)
    if extra_dir is None and env_dir:
        extra_dir = Path(env_dir)
    if extra_dir is not None:
        search_dirs.append(extra_dir)
    for base in search_dirs:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                info = _load_skill_dir(child)
                skills[info.name] = info
    return skills


def index_lines(skills: dict[str, SkillInfo]) -> list[str]:
    """常驻 prompt 的 skill 索引：每个 skill 一行。"""
    return [
        f"- {info.name}（产物：{info.kind}）：{info.description}"
        for info in skills.values()
    ]


def load_skill(name: str, skills: dict[str, SkillInfo]) -> str:
    """按需读入完整方法论正文（不含 frontmatter）。"""
    if name not in skills:
        known = "、".join(skills) or "（无）"
        raise SkillError(f"skill 不存在：{name}。可用：{known}")
    _, body = parse_frontmatter((skills[name].path / "SKILL.md").read_text(encoding="utf-8"))
    return body
