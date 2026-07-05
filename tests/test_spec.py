"""ArtifactSpec 校验单测：合法 spec 通过，非法结构在 pydantic 层被拒。"""

import pytest
from pydantic import ValidationError

from finance_agent.artifacts.spec import ArtifactSpec


def minimal_spec(**overrides):
    payload = {
        "artifact_id": "nvda-kline-report",
        "kind": "html",
        "title": "NVDA 复盘",
        "blocks": [{"type": "heading", "text": "一、结论"}],
    }
    payload.update(overrides)
    return payload


def test_valid_spec_roundtrip():
    spec = ArtifactSpec.model_validate(minimal_spec())
    assert spec.blocks[0].type == "heading"
    assert ArtifactSpec.model_validate(spec.model_dump()) == spec


def test_unknown_block_type_rejected():
    with pytest.raises(ValidationError, match="kline_chart"):
        ArtifactSpec.model_validate(minimal_spec(blocks=[{"type": "hologram", "text": "x"}]))


def test_bad_artifact_id_rejected():
    for bad in ("NVDA Report", "-lead-dash", "a", "含中文"):
        with pytest.raises(ValidationError):
            ArtifactSpec.model_validate(minimal_spec(artifact_id=bad))


def test_empty_blocks_rejected():
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate(minimal_spec(blocks=[]))


def test_event_impact_bounds():
    chart = {
        "type": "kline_chart", "data_ref": "ds-1", "ticker": "NVDA",
        "events": [{"date": "2022-11-30", "title": "ChatGPT 发布", "impact": 6}],
    }
    with pytest.raises(ValidationError, match="impact"):
        ArtifactSpec.model_validate(minimal_spec(blocks=[chart]))


def test_data_ref_is_logical_id_not_path():
    chart = {
        "type": "kline_chart", "data_ref": "ds-nvda-ohlcv-5y", "ticker": "NVDA",
    }
    spec = ArtifactSpec.model_validate(minimal_spec(blocks=[chart]))
    assert spec.blocks[0].data_ref == "ds-nvda-ohlcv-5y"
