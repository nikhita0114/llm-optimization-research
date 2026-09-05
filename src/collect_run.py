# src/collect_run.py — snapshot raw Prometheus series + compute metrics.json.
import json, sys
from pathlib import Path
import pandas as pd
import yaml
from src.extract_metrics import (fetch_series, slo_violation_rate, replica_seconds,
                                 overshoot, scaleout_latency, thrash)

REPLICAS = 'max(kube_deployment_status_replicas{namespace="serving",deployment="llm-sim"})'
NODE_CPU = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))'

def _arm_queries(queries):
    # single arms carry "query"; the composite reference carries "triggers"
    # (one query per signal) — snapshot each as its own series.
    out = []
    for arm, spec in queries.items():
        if "query" in spec:
            out.append((arm, spec["query"]))
        else:
            out += [(f"{arm}_{i}", t["query"]) for i, t in enumerate(spec["triggers"])]
    return out

def collect(run_dir, frozen_path="experiments/config/frozen.yaml"):
    frozen = yaml.safe_load(Path(frozen_path).read_text())
    queries = yaml.safe_load(Path("experiments/arms/queries.yaml").read_text())["arms"]
    man = pd.read_csv(Path(run_dir) / "segment_manifest.csv")
    t0, t1 = float(man.start_epoch.min()), float(man.end_epoch.max())
    prom = "http://localhost:30090"
    raw = Path(run_dir) / "raw"; raw.mkdir(exist_ok=True)
    series = {}
    for name, q in [("replicas", REPLICAS), ("node_cpu", NODE_CPU)] + _arm_queries(queries):
        ts, vs = fetch_series(prom, q, t0, t1)
        series[name] = (ts, vs)
        pd.DataFrame({"t_s": ts, "value": vs}).to_csv(raw / f"{name}.csv", index=False)
    reqs = pd.read_csv(Path(run_dir) / "requests.csv")
    cap = frozen["capacity"]["cap_rps"]; slo = frozen["slo"]
    tt, tp = slo["ttft_baseline_s"] * slo["ttft_target_mult"], slo["tpot_baseline_s"] * slo["tpot_target_mult"]
    rts, rvs = series["replicas"]
    # fetch_series returns seconds-since-t0; rebase the manifest epochs to the
    # same origin or overshoot/scaleout compare absolute epochs vs rebased ts
    man_rel = man.assign(start_epoch=man.start_epoch - t0, end_epoch=man.end_epoch - t0)
    metrics = {
        "slo": slo_violation_rate(reqs, tt, tp),
        "replica_seconds": replica_seconds(rts, rvs),
        "overshoot": overshoot(rts, rvs, man_rel, cap),
        "scaleout": scaleout_latency(man_rel, rts, rvs),
        "thrash": thrash(rts, rvs, window_s=t1 - t0),
        "fingerprint": {"cap_rps": cap, "ttft_target_s": tt, "tpot_target_s": tp,
                        "calibrated": frozen["calibrated"]},
    }
    (Path(run_dir) / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    return metrics

if __name__ == "__main__":
    print(json.dumps(collect(sys.argv[1]), indent=2, default=float))
