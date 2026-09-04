# tests/test_extract_metrics.py
import pandas as pd
from src.extract_metrics import (slo_violation_rate, replica_seconds, overshoot,
                                 scaleout_latency, thrash)

def test_slo_violation_rate():
    reqs = pd.DataFrame({"ttft_s": [0.1, 0.2, 3.0], "tpot_s": [0.05, 0.05, 0.4],
                         "e2e_s": [1, 1, 10], "output_tokens": [16, 16, 16]})
    r = slo_violation_rate(reqs, ttft_target_s=1.0, tpot_target_s=0.1)
    assert abs(r["ttft"] - 1/3) < 1e-9 and abs(r["tpot"] - 1/3) < 1e-9
    assert abs(r["either"] - 1/3) < 1e-9
    assert r["ttft_percentiles"]["p99"] >= r["ttft_percentiles"]["p95"]

def test_replica_seconds_trapezoid():
    assert abs(replica_seconds([0, 60, 120], [2, 4, 4]) - (60*3 + 60*4)) < 1e-9

def test_overshoot_against_required():
    ts, vals = [0, 300, 600], [2, 6, 4]
    man = pd.DataFrame([{"idx": 0, "label": "x", "rate": 8.0, "prompt_tokens": 256,
                         "output_tokens": 128, "start_epoch": 0, "end_epoch": 300},
                        {"idx": 1, "label": "y", "rate": 2.0, "prompt_tokens": 256,
                         "output_tokens": 128, "start_epoch": 300, "end_epoch": 600}])
    o = overshoot(ts, vals, man, cap_rps=4.0)   # R_req: ceil(8/4)=2, ceil(2/4)=1
    assert o["max"] == 5.0                       # 6 observed vs 1 required
    assert o["integral_replica_seconds"] > 0

def test_scaleout_latency_detects_and_misses():
    man = pd.DataFrame([{"idx": 0, "label": "a", "rate": 2.0, "start_epoch": 0,   "end_epoch": 180,
                         "prompt_tokens": 256, "output_tokens": 128},
                        {"idx": 1, "label": "b", "rate": 8.0, "start_epoch": 180, "end_epoch": 270,
                         "prompt_tokens": 256, "output_tokens": 128}])
    # replicas rise to 3 at t=200 (20 s after the 180 s load step)
    rose = scaleout_latency(man, [0, 100, 200, 300, 400], [1, 1, 3, 3, 3])
    assert rose[0]["segment_idx"] == 1 and 19 <= rose[0]["latency_s"] <= 21
    never = scaleout_latency(man, [0, 100, 200, 300, 400], [1, 1, 1, 1, 1])
    assert never[0]["latency_s"] is None

def test_thrash_counts_events_and_reversals():
    r = thrash([0, 60, 120, 180, 240], [1, 2, 1, 2, 1], window_s=240)
    assert r["scale_events_per_min"] == 1.0      # 4 changes / 4 min
    assert r["direction_reversals"] == 3         # up,down,up,down
