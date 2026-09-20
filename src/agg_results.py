# src/agg_results.py — QA gate + cross-run aggregation for the factorial.
# Composite arm carries role=reference everywhere (spec §6.2/§5): present in
# tables, excluded from rankings/tests.
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

FROZEN_PATH = "experiments/config/frozen.yaml"
ARMS = ("cpu", "rps", "queue", "kv", "ttft", "composite")
PATTERNS = ("ramp", "spike", "diurnal", "longctx")

def check_run(run_dir, frozen):
    reasons = []
    mpath = Path(run_dir) / "metrics.json"
    if not mpath.exists():
        return {"ok": False, "reasons": ["metrics.json missing"], "run": str(run_dir)}
    m = json.loads(mpath.read_text())
    for key in ("slo", "replica_seconds", "overshoot", "scaleout", "thrash", "host", "fingerprint"):
        if key not in m:
            reasons.append(f"missing {key}")
    if not reasons:
        # slo sub-blocks are load-bearing: aggregate() KeyErrors without them.
        for k in ("ttft", "tpot", "either", "ttft_percentiles", "tpot_percentiles", "n_requests"):
            if k not in m["slo"]:
                reasons.append(f"missing slo.{k}")
        if m["slo"].get("n_requests", 0) < 10:
            reasons.append(f"n_requests={m['slo'].get('n_requests')} < 10")
        if "delta" not in m.get("host", {}).get("swap", {}):
            reasons.append("no swap delta (repro.sh predates run?)")
        fp = m["fingerprint"]
        if fp.get("calibrated") is not True:
            reasons.append("fingerprint not calibrated")
        if fp.get("cap_rps") != frozen["capacity"]["cap_rps"]:
            reasons.append(f"fingerprint cap_rps {fp.get('cap_rps')} != frozen")
    return {"ok": not reasons, "reasons": reasons, "run": str(run_dir)}

def aggregate(results_dir="results"):
    rows = []
    for d in sorted(Path(results_dir).glob("*_*_seed*")):
        parts = d.name.rsplit("_seed", 1)
        if len(parts) != 2 or parts[0].split("_", 1)[0] not in ARMS:
            continue
        arm, pattern = parts[0].split("_", 1)
        if arm not in ARMS or pattern not in PATTERNS:
            continue
        m = json.loads((d / "metrics.json").read_text())
        so = m["scaleout"]
        rows.append({
            "arm": arm, "pattern": pattern, "seed": int(parts[1]),
            "role": "reference" if arm == "composite" else "treatment",
            "either_viol": m["slo"]["either"], "ttft_viol": m["slo"]["ttft"],
            "tpot_viol": m["slo"]["tpot"],
            "ttft_p95": m["slo"]["ttft_percentiles"]["p95"],
            "tpot_p95": m["slo"]["tpot_percentiles"]["p95"],
            "n_requests": m["slo"]["n_requests"],
            "replica_seconds": m["replica_seconds"],
            "overshoot_max": m["overshoot"]["max"],
            "overshoot_int": m["overshoot"]["integral_replica_seconds"],
            "scaleout_n": len(so),
            "scaleout_lat_mean": (float(np.mean([e["latency_s"] for e in so
                              if e["latency_s"] is not None]))
                              if any(e["latency_s"] is not None for e in so) else None),
            "thrash_events": m["thrash"]["scale_events_per_min"],
            "thrash_reversals": m["thrash"]["direction_reversals"],
            "swap_touched": m["host"]["swap"]["delta"]["swap_touched"],
        })
    return pd.DataFrame(rows)

def cell_variance(df):
    out = []
    for (arm, pattern), g in df.groupby(["arm", "pattern"]):
        row = {"arm": arm, "pattern": pattern, "n_seeds": len(g),
               "either_mean": g.either_viol.mean(),
               "either_cv": _cv(g.either_viol), "thrash_cv": _cv(g.thrash_events),
               "replicas_cv": _cv(g.replica_seconds)}
        row["topup_flag"] = bool((row["either_cv"] is not None and row["either_cv"] > 0.5)
                                 or (row["thrash_cv"] is not None and row["thrash_cv"] > 0.5))
        out.append(row)
    return pd.DataFrame(out)

def _cv(s):
    return float(np.std(s) / np.mean(s)) if len(s) > 1 and np.mean(s) > 0 else None

def _qa_all():
    frozen = yaml.safe_load(Path(FROZEN_PATH).read_text())
    failed = False
    for d in sorted(Path("results").glob("*_*_seed*")):
        r = check_run(str(d), frozen)
        print(("PASS " if r["ok"] else "FAIL ") + d.name + (" " + "; ".join(r["reasons"]) if r["reasons"] else ""))
        failed |= not r["ok"]
    return 1 if failed else 0

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "qa"
    if cmd == "qa":
        sys.exit(_qa_all())
    df = aggregate()
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if cmd == "summary":
        df.to_csv(out, index=False) if out else print(df.to_string())
    elif cmd == "variance":
        v = cell_variance(df)
        v.to_csv(out, index=False) if out else print(v.to_string())
    else:
        sys.exit(f"unknown command {cmd!r}: qa | summary | variance")
