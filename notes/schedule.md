# Week-2 Factorial Schedule (frozen at week-1 pilot gate)

Written 2026-09-05 after the pilot gate passed (see `notes/pilot_gate.md`).
This file is the input to the week-2 plan.

## Grid

**6 arms × 4 patterns × 2 seeds = 48 runs**, strictly sequential (2 physical
cores), one k3d cluster, identical rig per run (`experiments/repro.sh`).

- Arms: `cpu`, `rps`, `queue`, `kv`, `ttft` + `composite`
- Patterns: `ramp`, `spike`, `diurnal`, `longctx`
- Seeds: `1`, `2` (schedule seed = guidellm seed = `PYTHONHASHSEED`)
- Per run: ~18–30 min load (pattern-dependent) + ≤ 5 min reset ⇒ ~25–35 min
- Night batches: 4 nights × 12 runs ≈ 6–7 h per night (spec §7.3 trim order
  applies if a night overruns: drop trailing runs, never reorder)

## Markers (verbatim from the plan / spec)

1. **The composite arm is a reference condition, `excluded_from_ranking: true`
   (spec §6.2/§5) — it never enters single-signal rankings, hypothesis tests,
   or claims.**
2. **H-TTFT (spec §6.6) is recorded as a week-3 analysis input — its
   observables are exactly the per-run `metrics.json` fields `slo` (violation
   rates) and `thrash`/`overshoot` (loop instability), so week-1 data
   collection already covers it with no extra instrumentation.**

## Batch ordering

Within a night, interleave arms across patterns (arm-major order: all 6 arms ×
pattern P × seed S before moving on) so any overnight abort loses at most one
stratum, and each night's batch ends with the longest pattern first-run to
surface duration drift early. Exact ordering is the week-2 plan's job.

## Adaptive top-up criteria (from spec)

- If any arm shows zero scale events across ALL its runs (a mechanically dead
  arm at this operating point), stop and re-check threshold reachability
  against `notes/signal_liveness.md` before wasting further runs.
- If TTFT-p95 SLO violation is ~0 for every arm on a pattern (pattern cannot
  discriminate), note it and re-allocate that pattern's remaining seed-2 runs
  per §7.3 trim order.
- If a run's `raw/replicas.csv` shows no scale event AND the arm is not
  `cpu`-like-inert, check KEDA operator logs for Prometheus resolution errors
  (the `mon-kube-prometheus-stack-prometheus` naming bit us once).

## Rig quirks the week-2 plan must carry forward

- `--kv-cache-size` is patched per pattern (`frozen.yaml sim.kv_cache_blocks`:
  baseline 140 / longctx 1024) — `repro.sh` does this automatically; manual
  runs must too.
- The 300 s HPA scale-down stabilization outlasts the ~270 s spike burst
  cycle: replicas persist at 2 across bursts (late-burst `scaleout.latency_s`
  is legitimately `null`). Analysis must treat `null` as "already scaled",
  not missing data.
- `requests.csv` contains only completed requests (guidellm `incomplete`
  rows carry no latency); at ≤1.5×cap the abandoned tail is small but
  nonzero — record per-run `successful/incomplete` counts in the batch log.
