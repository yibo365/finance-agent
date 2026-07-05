"""样例产物一致性看护：samples/ 是面试交付物的一部分，也要可复核。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def test_sample_dataset_evidence_matches_ticker_and_specs_reference_it():
    """数据集 evidence 必须回链到同一标的，Excel 数据/指标 block 要引用对应 evidence。"""
    for sample in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        index_path = sample / "data" / "index.json"
        evidence_path = sample / "evidence.json"
        specs_dir = sample / "specs"
        if not (index_path.is_file() and evidence_path.is_file() and specs_dir.is_dir()):
            continue

        index = json.loads(index_path.read_text(encoding="utf-8"))
        evidence_items = json.loads(evidence_path.read_text(encoding="utf-8"))["items"]
        evidence = {item["id"]: item for item in evidence_items}

        for dataset_id, meta in index.items():
            ev = evidence.get(meta["evidence_id"])
            assert ev is not None, f"{sample.name}/{dataset_id} evidence 不存在"
            assert ev["kind"] == "market_data", f"{sample.name}/{dataset_id} evidence 类型错误"
            assert ev["query"].get("ticker") == meta["ticker"], (
                f"{sample.name}/{dataset_id} 指向 {ev['query'].get('ticker')} evidence，"
                f"但 dataset ticker 是 {meta['ticker']}"
            )

        for spec_path in sorted(specs_dir.glob("*.json")):
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            for block in spec.get("blocks", []):
                refs = set(block.get("evidence_refs") or [])
                if block.get("type") == "data_sheet":
                    data_ref = block["data_ref"]
                    expected = index[data_ref]["evidence_id"]
                    assert expected in refs, f"{spec_path.name} 的 {data_ref} 未引用自身 evidence"
                if block.get("type") == "metrics_sheet":
                    expected = {index[data_ref]["evidence_id"] for data_ref in block["data_refs"]}
                    assert expected <= refs, f"{spec_path.name} 指标 sheet 未覆盖全部数据源 evidence"
