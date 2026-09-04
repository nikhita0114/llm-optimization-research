# src/extract_metrics.py — spec §6.4 response metrics as pure functions.
import math
import numpy as np
import pandas as pd
import requests

def slo_violation_rate(reqs, ttft_target_s, tpot_target_s):
    out = {"ttft": float((reqs.ttft_s > ttft_target_s).mean()),
           "tpot": float((reqs.tpot_s > tpot_target_s).mean())}
    out["either"] = float(((reqs.ttft_s > ttft_target_s) | (reqs.tpot_s > tpot_target_s)).mean())
    for m in ("ttft", "tpot"):
        out[f"{m}_percentiles"] = {p: float(np.percentile(reqs[f"{m}_s"], q=int(p[1:])))
                                   for p in ("p50", "p95", "p99")}
    return out

def replica_seconds(ts, vals):
    return float(np.trapezoid(vals, ts))

def _required(manifest, cap_rps):
    # step function (start_epoch, r_required) from the offered-load schedule
    steps = [(row.start_epoch, min(6, max(1, math.ceil(row.rate / cap_rps))))
             for row in manifest.itertuples()]
    def at(t):
        r = steps[0][1]
        for s, v in steps:
            if s <= t: r = v
        return r
    return at

def overshoot(ts, vals, manifest, cap_rps):
    req = _required(manifest, cap_rps)
    diffs = [v - req(t) for t, v in zip(ts, vals)]
    return {"max": float(max(diffs)),
            "integral_replica_seconds": float(np.trapezoid(diffs, ts))}

def scaleout_latency(manifest, ts, vals):
    man = manifest.sort_values("idx")
    events = []
    prev_rate = None
    for row in man.itertuples():
        if prev_rate is not None and row.rate > 1.2 * prev_rate:
            before = [v for t, v in zip(ts, vals) if t < row.start_epoch]
            base = before[-1] if before else vals[0]
            first_up = next((t for t, v in zip(ts, vals)
                             if t >= row.start_epoch and v > base), None)
            events.append({"segment_idx": int(row.idx), "load_step_epoch": float(row.start_epoch),
                           "first_ready_epoch": first_up,
                           "latency_s": None if first_up is None else float(first_up - row.start_epoch)})
        if row.rate > 0:
            prev_rate = row.rate
    return events

def thrash(ts, vals, window_s):
    changes = [(t, v - p) for t, p, v in zip(ts[1:], vals[:-1], vals[1:]) if v != p]
    events = len(changes)
    reversals = sum(1 for a, b in zip(changes, changes[1:]) if a[1] * b[1] < 0)
    return {"scale_events_per_min": events / (window_s / 60.0),
            "direction_reversals": float(reversals)}

def fetch_series(base_url, query, start_epoch, end_epoch, step_s=15):
    r = requests.get(f"{base_url}/api/v1/query_range",
                     params={"query": query, "start": start_epoch, "end": end_epoch, "step": step_s},
                     timeout=30)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return [], []
    pairs = [(float(t), float(v)) for t, v in result[0]["values"]]
    t0 = start_epoch
    return [t - t0 for t, _ in pairs], [v for _, v in pairs]
