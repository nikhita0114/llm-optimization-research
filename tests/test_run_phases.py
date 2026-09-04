# tests/test_run_phases.py
import json
import pandas as pd
from src.run_phases import parse_segment, stitch

FIX = "tests/fixtures/segment_sample.json"

def test_parse_segment_contract():
    df = parse_segment(FIX)
    assert {"ttft_s", "tpot_s", "e2e_s", "output_tokens"} <= set(df.columns)
    assert len(df) >= 5                          # 20 s at 2 rps
    assert (df.ttft_s >= 0).all() and (df.tpot_s >= 0).all()
    # sim config: ITL 40ms -> tpot in a sane band
    assert df.tpot_s.between(0.001, 1.0).all()

def test_stitch_labels_and_order(tmp_path):
    (tmp_path / "seg_0").mkdir(); (tmp_path / "seg_1").mkdir()
    doc = json.load(open(FIX))
    for i in (0, 1):
        json.dump(doc, open(tmp_path / f"seg_{i}" / "report.json", "w"))
    out = stitch(str(tmp_path))
    df = pd.read_csv(out)
    assert {"segment_idx", "label"} <= set(df.columns)
    assert df.segment_idx.isin([0, 1]).all()
