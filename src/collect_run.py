# src/collect_run.py — snapshot raw Prometheus series + compute metrics.json.
import json, sys
from pathlib import Path
import pandas as pd
import yaml
from src.extract_metrics import (fetch_series, slo_violation_rate, replica_seconds,
                                 overshoot, scaleout_latency, thrash)

REPLICAS = 'max(kube_deployment_status_replicas{namespace="serving",deployment="llm-sim"})'
NODE_CPU = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))'

def swap_snapshot(meminfo_path="/proc/meminfo", vmstat_path="/proc/vmstat"):
    """Host swap state; pswpin/pswpout are CUMULATIVE counters (kernel 7 uses
    pswpin/pswpout; <=6 used pswp_in/pswp_out — accept both)."""
    def meminfo(key):
        for line in open(meminfo_path):
            if line.startswith(key):
                return int(line.split()[1])
        return None
    vm = {}
    for line in open(vmstat_path):
        k, _, v = line.partition(" ")
        vm[k.strip()] = v.strip()
    return {
        "swap_total_kb": meminfo("SwapTotal:"), "swap_free_kb": meminfo("SwapFree:"),
        "swap_cached_kb": meminfo("SwapCached:"),
        "swap_used_kb": (meminfo("SwapTotal:") or 0) - (meminfo("SwapFree:") or 0),
        "pswpin": int(vm.get("pswpin", vm.get("pswp_in", 0))),
        "pswpout": int(vm.get("pswpout", vm.get("pswp_out", 0))),
    }

def swap_delta(start, end):
    """Run-window swap activity: end-vs-start snapshot comparison."""
    return {
        "swap_used_delta_kb": end["swap_used_kb"] - start["swap_used_kb"],
        "pswpin_delta": end["pswpin"] - start["pswpin"],
        "pswpout_delta": end["pswpout"] - start["pswpout"],
        "swap_touched": bool(end["pswpin"] != start["pswpin"] or end["pswpout"] != start["pswpout"]),
    }

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
        "host": {"swap": _host_swap(run_dir)},
        "fingerprint": {"cap_rps": cap, "ttft_target_s": tt, "tpot_target_s": tp,
                        "calibrated": frozen["calibrated"]},
    }

def _host_swap(run_dir):
    # repro.sh writes swap_start.json at load start; delta vs end snapshot
    # answers "was swap touched DURING this run" (pilot-gate monitoring gap)
    end = swap_snapshot()
    out = {"end": end}
    start_path = Path(run_dir) / "swap_start.json"
    if start_path.exists():
        start = json.loads(start_path.read_text())
        out["start"] = start
        out["delta"] = swap_delta(start, end)
    return out
    (Path(run_dir) / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    return metrics

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--swap-snapshot":
        Path(sys.argv[2]).write_text(json.dumps(swap_snapshot(), indent=2))
        sys.exit(0)
    print(json.dumps(collect(sys.argv[1]), indent=2, default=float))
