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


def test_known_urls_unions_source_url_and_candidate_urls():
    log = EvidenceLog("s1")
    log.record(
        "search",
        source_url="https://reuters.example/a",
        urls=["https://reuters.example/a", "https://cnbc.example/b"],
    )
    # 非 http 的 source_url（如 mock:// 或 API 定位串）不进对照集，urls 仍进
    log.record("news", source_url="mock://hn-offline", urls=["https://openai.example/c"])
    assert log.known_urls() == {
        "https://reuters.example/a",
        "https://cnbc.example/b",
        "https://openai.example/c",
    }


def test_load_accepts_legacy_items_without_urls(tmp_path):
    # 旧会话的 evidence.json 没有 urls 字段，回读不得报错
    path = tmp_path / "evidence.json"
    path.write_text(
        '{"run_id": "s1", "items": [{"id": "ev-s1-1", "kind": "news", '
        '"source_url": "https://a", "fetched_at": "2026-07-04T00:00:00+00:00"}]}',
        encoding="utf-8",
    )
    restored = EvidenceLog.load(path)
    assert restored.get("ev-s1-1").urls == []
