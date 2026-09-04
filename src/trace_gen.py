# src/trace_gen.py — synthetic bursty workload patterns (spec §6.2).
# Schedules are normalized: rate_frac is a fraction of per-replica capacity.
import numpy as np

PATTERNS = ("ramp", "spike", "diurnal", "longctx")

def _seg(idx, label, start, dur, rate_frac, prompt, output):
    return {"idx": idx, "label": label, "start_s": round(float(start), 1),
            "duration_s": int(max(dur, 60)), "rate_frac": round(float(max(rate_frac, 0.05)), 4),
            "prompt_tokens": int(prompt), "output_tokens": int(output)}

def generate(pattern, seed):
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}; expected one of {PATTERNS}")
    rng = np.random.default_rng(seed)
    jitter = lambda: 1.0 + 0.05 * float(rng.standard_normal())  # ±5% seed-driven
    segs, t = [], 0.0
    if pattern == "ramp":                      # ~20 min, 8 steps up
        for i, r in enumerate(np.linspace(0.15, 1.5, 8)):
            d = 150 * jitter()
            segs.append(_seg(i, "ramp", t, d, r * jitter(), 256, 128)); t += segs[-1]["duration_s"]
    elif pattern in ("spike", "longctx"):      # 4 bursts over ~18-20 min
        plan = [("baseline", 180, 0.35, 256, 128), ("burst", 90, 1.4, 2048 if pattern == "longctx" else 256, 512 if pattern == "longctx" else 128)] * 4
        for i, (lab, d, r, p, o) in enumerate(plan):
            d = d * jitter()
            segs.append(_seg(i, lab, t, d, r * jitter(), p, o)); t += segs[-1]["duration_s"]
    else:                                      # diurnal ~30 min, 3.5 cycles
        for i in range(12):
            r = 0.55 + 0.45 * np.sin(2 * np.pi * 3.5 * i / 12)
            d = 150 * jitter()
            segs.append(_seg(i, "diurnal", t, d, r * jitter(), 256, 128)); t += segs[-1]["duration_s"]
    return {"pattern": pattern, "seed": seed, "segments": segs}

def materialize(schedule, cap_rps):
    segs = [dict(s, rate=round(s["rate_frac"] * cap_rps, 3)) for s in schedule["segments"]]
    return dict(schedule, cap_rps=cap_rps, segments=segs)
