"""skill 加载器单测：扫描、frontmatter、索引、按需加载、外部目录覆盖。"""

import pytest

from finance_agent.skills.loader import (
    SkillError,
    index_lines,
    load_skill,
    parse_frontmatter,
    scan_skills,
)


def test_builtin_kline_skill_discovered():
    skills = scan_skills()
    assert "kline-html-report" in skills
    info = skills["kline-html-report"]
    assert info.kind == "html"
    assert "kline_chart" in info.blocks
    assert (info.assets_dir / "plotly.min.js").is_file()
    assert (info.templates_dir / "report_template.html").is_file()


def test_index_lines_one_per_skill():
    skills = scan_skills()
    lines = index_lines(skills)
    assert len(lines) == len(skills)
    assert any("kline-html-report" in line for line in lines)


def test_load_skill_returns_methodology_body():
    skills = scan_skills()
    body = load_skill("kline-html-report", skills)
    assert "方法论" in body
    assert "---" not in body.split("\n")[0]  # frontmatter 已剥离


def test_load_unknown_skill_raises():
    with pytest.raises(SkillError, match="不存在"):
        load_skill("no-such-skill", scan_skills())


def test_parse_frontmatter_rejects_missing_or_unclosed():
    with pytest.raises(SkillError, match="frontmatter"):
        parse_frontmatter("# 没有 frontmatter")
    with pytest.raises(SkillError, match="未闭合"):
        parse_frontmatter("---\nname: x\n正文没有结束线")


def test_extra_dir_overrides_builtin(tmp_path):
    custom = tmp_path / "kline-html-report"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        "---\nname: kline-html-report\ndescription: 用户定制版\nkind: html\n---\n\n自定义方法论",
        encoding="utf-8",
    )
    skills = scan_skills(extra_dir=tmp_path)
    assert skills["kline-html-report"].description == "用户定制版"


def test_extra_dir_from_env(tmp_path, monkeypatch):
    custom = tmp_path / "my-skill"
    custom.mkdir()
    (custom / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 外部技能\nkind: docx\n---\n\n正文",
        encoding="utf-8",
    )
    monkeypatch.setenv("FINANCE_AGENT_SKILLS_DIR", str(tmp_path))
    skills = scan_skills()
    assert "my-skill" in skills
    assert "kline-html-report" in skills  # 内置仍在
