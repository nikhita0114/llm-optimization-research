# Week-1 Pilot Gate (spec §7.1) — PASSED 2026-09-05

## 1. E2E run clean ✓

`make -C experiments run ARM=queue PATTERN=spike SEED=1`
→ `results/queue_spike_seed1/metrics.json` (full log: `results/e2e_queue_spike.log`)

- Wall clock ≈ 22 min (spike schedule 18.0 min + guidellm per-segment startup + reset)
- All five §6.4 metrics present and non-null:
  - `slo`: ttft violation 29.2 %, tpot 27.1 %, either 33.3 %; TTFT p95 12.0 s / p99 17.3 s (bursts discriminate: baseline p95 is 0.28 s)
  - `replica_seconds`: 1867.5
  - `overshoot`: max 1.0, integral 240.0 replica-seconds (vs proper R_req step)
  - `scaleout`: 4 burst events; latencies 95.3 s and 76.8 s (numeric ✓); later bursts `null` — replicas already held at 2 by the 300 s scale-down stabilization across the ~270 s burst cycle (real rig physics, not missing data)
  - `thrash`: 0.147 events/min, 2 reversals
- `raw/replicas.csv` shows ≥1 scale event: 1 → 2 (t=300 s) → 1 (t=630 s) → 2 (t=900 s)

## 2. Durations within ±20 % ✓

| Pattern | seed 1 | seed 2 | Target |
|---|---|---|---|
| ramp | 19.9 min | 20.3 min | ~20 min |
| spike | 18.0 | 18.3 | ~20 min |
| diurnal | 30.0 | 30.5 | ~30 min |
| longctx | 18.0 | 18.3 | ~20 min |

(From `trace_gen` schedule sums; measured spike run wall clock 22 min incl. per-segment tool startup — within budget.)

## 3. Node CPU < 70 % at 6 replicas under burst-rate load ✓

60-min constant 0.75 req/s (= 1.5 × cap_rps, the spike burst rate) with 6 replicas:
max node CPU **0.343** (`results/calibration/node_cpu.log`, 60 samples). 0 restarts.

## 4. Thermals over 1 h sustained ✓

Busy-core clock across 60 min: first-quarter mean 2 728 MHz → last-quarter 2 910 MHz
(**−6.7 % "decline"**, i.e. clocks rose; gate is < 10 % decline). Min busy-clock
2 391 MHz (`results/calibration/clock.log`). No throttling collapse.

## 5. Swap present, no OOM ✓

`swapon --show`: 4 GiB **swap file** (`/swap.img`) — substitution for the spec's
zram, recorded in `notes/environment.md`; same anti-OOM purpose. Zero OOM/kill
events cluster-wide during all week-1 runs.

## Deviations encountered and resolved during week 1 (all documented in notes/)

| # | Issue | Resolution |
|---|---|---|
| 1 | guidellm 0.7.3 CLI syntax (`--output kind=json,path=`, `--seed kind=static,value=`, flag/value argv split, pinned client tokenizer) | `run_phases.py` + `notes/environment.md` |
| 2 | sim KV cache off by default; `POD_IP` required; default `max-model-len` 1024 rejects longctx | deployment args fixed; `notes/signal_liveness.md` |
| 3 | sim 4xx-rejects requests that don't fit free KV → per-pattern KV sizing (baseline 140 / longctx 1024 blocks) | `frozen.yaml sim.kv_cache_blocks`, `repro.sh` patches per pattern |
| 4 | Prometheus in-cluster svc name is `mon-kube-prometheus-stack-prometheus` (chart 89.x) | generator + tests fixed |
| 5 | KEDA 2.20 ignores `pollingInterval`/`cooldownPeriod` at minReplicaCount ≥ 1; effective cadence = HPA sync 15 s | recorded in `frozen.yaml` timing block |
| 6 | First capacity sweep contaminated by incomplete inter-point drains (queue cascade) → cap underestimated 2× | clean re-sweep with verified drains (`waiting_clean.log`); contaminated set archived |
| 7 | cpu-arm threshold 0.35 unreachable (sim is latency-bound, full-load ≈ 0.035 cores/replica) | calibrated to 0.025 |
| 8 | collect epoch-unit mismatch (absolute manifest epochs vs t0-rebased series) nullified scaleout latencies | rebased in `collect_run.py` |

**Verdict: pilot gate PASSED; the week-2 factorial schedule is frozen** (`notes/schedule.md`).
