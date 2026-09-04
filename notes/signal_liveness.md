# Signal Liveness Record (spec §6.5 step 3 / §12)

Recorded 2026-09-05, Task 6. Sim: `llm-d-inference-sim v0.9.0`, 1 replica, `max-num-seqs=8`, `max-model-len=4096`, `enable-kvcache`, kv-cache-size default (1024 blocks × 16 tokens = 16 384 tokens).

## Verdict: all 5 signal arms LIVE — no §12 substitution needed

No arm was dropped or re-mapped; `experiments/arms/queries.yaml` is unchanged from Task 5.

## Idle vs load evidence

90 s @ 5 req/s constant, 256/128 tokens (Prometheus instant queries via `check_signals.sh`), plus mid-flight /metrics polls:

| Arm query | Idle | Under load | Notes |
|---|---|---|---|
| cpu (cores, sim containers) | 0.0032 | 0.0346 | ~11×; sim is CPU-light |
| rps (req/s succeeded) | ~0 | 0.40 | capacity-bound (see below); query is live, offered was 5 |
| queue (waiting) | 0 | 401 after 90 s | grows linearly while saturated |
| kv (256/128 prompts) | 0 | 0.116 plateau | 8 running × ~384 tokens / 16 384 ≈ 12 % |
| kv (2048/512 prompts) | 0 | **0.922** plateau | 8 running × ~2 560 tokens, capped |
| ttft p95 [2m] | 0.447* | 0.733 | *idle read had residual history; NaN only on cold start — KEDA skips NaN, acceptable |

Single-replica capacity at 256/128 with these latency settings ≈ 0.4–0.5 req/s (8 concurrent × ~16 s/request under `time-factor-under-load=3.0`) — this is the anchor Task 11 calibrates around.

## Deviations found & fixed during the check (rig config, not arm changes)

1. **guidellm 0.7.3 output syntax**: `--output kind=json,path=X` (plan's `json path=X` is rejected: "invalid config value").
2. **guidellm client tokenizer**: defaults to the backend model name and tries to download `dummy-model` from HF → must pass `--tokenizer kind=hf_auto,model=openai-community/gpt2` (one-time ~2 MB cache). The server does not care which tokenizer the client uses; only client-side token accounting uses it.
3. **`kv_cache_usage_perc` is structurally 0 without `--enable-kvcache`** (off by default). Enabling it also requires `POD_IP` in the container env (fieldRef `status.podIP`) — without it the sim crashes at startup: "IP should be defined in the environment (POD_IP) for KV cache to work".
4. **Default `max-model-len=1024`** rejects the longctx shape (2048+512=2560 → HTTP 400, requests never queued). Set to 4096 in `experiments/sim/deployment.yaml`.

## Calibration input for Task 11 (threshold reachability)

With the default kv-cache-size (16 384 tokens/replica):

- Short patterns (ramp/spike/diurnal, 256/128): kv plateaus ≈ **0.12** at full per-replica load — far below the arm threshold 0.70 and WVA τ_kv 0.8. The kv arm would never fire on short patterns.
- Long pattern (longctx, 2048/512): kv plateaus ≈ **0.92** — τ_kv reachable.

⇒ Task 11 must shrink `--kv-cache-size` (operating point, recorded in `frozen.yaml` `sim:`) so short-pattern full load (~8 × 384 = 3 072 tokens) lands at ≈ 0.8–0.9 occupancy: **~224 blocks (3 584 tokens)** is the starting candidate; measure and freeze. longctx then pegs kv at ~1.0 (semantically correct: extreme KV pressure). Composite τ_q=5 is already reachable (queue ≫ 5 under saturation).

## KV sizing decision (Task 11, 2026-09-05) — per-pattern

Measured KV behavior of the sim (all with `--enable-kvcache`):

| Config | Observation |
|---|---|
| 1024 blocks (16 384 tok), 256/128 | full load occupancy ≈ 0.12 (thresholds unreachable) |
| 224 blocks, 256/128 | full load occupancy 0.53 |
| **140 blocks (2 240 tok), 256/128** | **full load occupancy 0.85**; 8 running admitted; 0 errors |
| 140 blocks, 2048/512 | **28/30 requests 4xx-rejected**: admission requires the request to fit FREE KV; one 2 048-prompt request holds ≈ 1 424 tok |
| 1024 blocks, 2048/512 | 8 running admitted, occupancy **0.923**, 0 errors |

Decision: **per-pattern KV size, frozen in `frozen.yaml` `sim.kv_cache_blocks`** — `baseline: 140` (ramp/spike/diurnal), `longctx: 1024`. Rationale:

- The sim 4xx-rejects requests that do not fit free KV (it does not queue them) — one KV size cannot both make short-pattern occupancy threshold-reachable AND sustain 8-concurrent 2 048-token bursts.
- Within each pattern all 6 arms run an identical sim config — the "signal is the only differing variable" invariant (spec §1) holds. KV size is part of the pattern's workload envelope (a long-context workload is provisioned with a larger cache), not an arm variable.
- kv-arm threshold 0.70 and WVA τ_kv 0.8 are reachable in both families (0.85 / 0.92).
- `repro.sh` (Task 12) patches `--kv-cache-size` per pattern before each run; deployment default is 140.

One more sim behavior discovered: under `time-factor-under-load=3.0`, long outputs take ~46–60 s per request (0.6 s TTFT + 384–512 × 120 ms) — 90 s longctx bursts still complete requests, but 30 s probes show `incomplete`>0 with 0 errors. Not a rejection problem; expected saturation dynamics.
