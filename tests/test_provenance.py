"""溯源层单测：evidence 登记、id 递增、落盘回读。"""

import pytest

from finance_agent.provenance import EvidenceLog


def test_record_assigns_sequential_ids():
    log = EvidenceLog("s1")
    first = log.record("market_data", source_url="https://a", excerpt="x")
    second = log.record("news", source_url="https://b")
    assert first.id == "ev-s1-1"
    assert second.id == "ev-s1-2"
    assert [item.id for item in log.items()] == ["ev-s1-1", "ev-s1-2"]


def test_get_unknown_id_raises():
    log = EvidenceLog()
    with pytest.raises(KeyError):
        log.get("ev-run-99")


def test_save_and_load_roundtrip(tmp_path):
    log = EvidenceLog("s1")
    log.record("computation", source_url="evidence:ev-s1-0", query={"rows": 5}, excerpt="ok")
    path = tmp_path / "evidence.json"
    log.save(path)

    restored = EvidenceLog.load(path)
    assert restored.run_id == "s1"
    item = restored.get("ev-s1-1")
    assert item.kind == "computation"
    assert item.query == {"rows": 5}
