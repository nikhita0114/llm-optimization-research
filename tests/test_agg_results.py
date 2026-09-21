# tests/test_agg_results.py
import json
import pandas as pd
import pytest
import yaml
from src.agg_results import check_run, aggregate, cell_variance

FROZEN = yaml.safe_load(open("experiments/config/frozen.yaml"))

def _mk_run(base, arm="queue", pattern="spike", seed="1", **overrides):
    d = base / f"{arm}_{pattern}_seed{seed}"
    d.mkdir(parents=True)
    metrics = {
        "slo": {"ttft": 0.2, "tpot": 0.1, "either": 0.25,
                "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
                "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12},
                "n_requests": 400},
        "replica_seconds": 1800.0,
        "overshoot": {"max": 1.0, "integral_replica_seconds": 240.0},
        "scaleout": [{"segment_idx": 1, "latency_s": 95.3}],
        "thrash": {"scale_events_per_min": 0.15, "direction_reversals": 2.0},
        "host": {"swap": {"end": {}, "delta": {"swap_touched": False}}},
        "fingerprint": {"cap_rps": 0.5, "ttft_target_s": 0.42, "tpot_target_s": 0.082,
                        "calibrated": True},
    }
    metrics.update(overrides)
    (d / "metrics.json").write_text(json.dumps(metrics))
    return d

def test_check_run_passes_a_complete_run(tmp_path):
    d = _mk_run(tmp_path)
    assert check_run(str(d), FROZEN)["ok"] is True

def test_check_run_catches_fingerprint_drift_and_missing_keys(tmp_path):
    d = _mk_run(tmp_path, fingerprint={"cap_rps": 0.25, "calibrated": True})
    r = check_run(str(d), FROZEN)
    assert r["ok"] is False and any("fingerprint" in x for x in r["reasons"])
    d2 = _mk_run(tmp_path, seed="2", slo={"n_requests": 5})   # missing blocks + low n
    r2 = check_run(str(d2), FROZEN)
    assert r2["ok"] is False and len(r2["reasons"]) >= 2

def test_aggregate_rows_and_roles(tmp_path):
    _mk_run(tmp_path, arm="queue", pattern="spike", seed="1")
    _mk_run(tmp_path, arm="queue", pattern="spike", seed="2", slo={"either": 0.9,
               "ttft": 0.5, "tpot": 0.5, "n_requests": 300,
               "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
               "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12}})
    _mk_run(tmp_path, arm="composite", pattern="spike", seed="1")
    df = aggregate(str(tmp_path))
    assert len(df) == 3
    row = df[(df.arm == "queue") & (df.seed == 1)].iloc[0]
    assert row.either_viol == 0.25 and row.n_requests == 400 and row.role == "treatment"
    assert df[df.arm == "composite"].iloc[0].role == "reference"
    assert {"arm", "pattern", "seed", "role", "either_viol", "replica_seconds",
            "overshoot_int", "thrash_events", "n_requests"} <= set(df.columns)

def test_aggregate_skips_aborted_cell_dir_without_metrics(tmp_path):
    _mk_run(tmp_path)                                   # one complete run
    (tmp_path / "cpu_ramp_seed1").mkdir()               # aborted overnight cell: no metrics.json
    df = aggregate(str(tmp_path))
    assert len(df) == 1 and df.iloc[0].arm == "queue"   # no exception, exactly 1 row

def test_cell_variance_flags_high_cv(tmp_path):
    _mk_run(tmp_path, arm="ttft", pattern="ramp", seed="1")                       # 0.25
    _mk_run(tmp_path, arm="ttft", pattern="ramp", seed="2",
            slo={"either": 0.9, "ttft": 0.5, "tpot": 0.5, "n_requests": 300,
                 "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
                 "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12}})     # 0.9
    _mk_run(tmp_path, arm="cpu", pattern="ramp", seed="1")
    _mk_run(tmp_path, arm="cpu", pattern="ramp", seed="2")                        # identical
    v = cell_variance(aggregate(str(tmp_path)))
    ttft = v[(v.arm == "ttft")].iloc[0]
    cpu = v[(v.arm == "cpu")].iloc[0]
    assert ttft.either_cv == pytest.approx(0.80, abs=0.01)      # 0.7993: sample std (ddof=1)
    assert bool(ttft.topup_flag) is True
    assert bool(cpu.topup_flag) is False
    assert set(v.columns) >= {"arm", "pattern", "n_seeds", "either_mean", "either_cv",
                              "thrash_cv", "topup_flag"}
