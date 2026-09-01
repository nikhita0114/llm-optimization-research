# Design Spec — Controlled Ablation of Kubernetes Autoscaling Signals for LLM Inference Serving

- **Date:** 2026-09-02
- **Status:** Design approved; pending implementation plan
- **Author:** Nikhita, with Claude as research collaborator
- **Timeline:** ~4 weeks (target completion ~2026-10-02)
- **Deliverable shape:** empirical ablation + design guidance + released harness. Artifact-first; venue optional.

---

## 1. Problem and Motivation

LLM inference serving workloads are bursty and their latency profile is unusual
(TTFT and per-token latency respond to KV-cache pressure and queueing, not CPU
utilization). The Kubernetes ecosystem has responded with LLM-aware autoscalers
(AIBrix, llm-d/WVA, KEDA-based setups), but these systems each *advocate* a
scaling signal; none of them reports a controlled comparison of which
K8s-exportable signal actually serves latency SLOs best under bursty traffic,
or what stability each signal costs. Deployers therefore pick scaling signals
by vendor guidance and folklore.

This study supplies that comparison: a single-variable ablation in which the
autoscaling signal is the only differing variable across arms, evaluated under
bursty workload patterns, measuring SLO violations, scaling responsiveness,
stability, and cost.

## 2. Research Question

> Which Kubernetes-exportable autoscaling signal — used as the **sole** scaling
> input under otherwise-identical autoscaler configuration — best maintains
> latency SLOs for LLM inference serving across bursty workload patterns, and
> at what stability cost?

**Scope guards:** single-signal treatments only (the composite arm is a
reference, not a contribution); results claimed as *relative* signal quality
only; K8s-native signals only; one month, solo, $0, CPU-only laptop.

## 3. Novelty Position (verified 2026-09-02)

The exact cell — controlled single-variable ablation of K8s-exportable signals
× bursty workload patterns on a K8s-native LLM-serving stack — is empty in the
literature as of this date. Verification established:

- **HeteroScale** (arXiv:2508.19559) claims "first large-scale empirical
  analysis of autoscaling metrics," but for P/D-disaggregated production GPU
  serving, with 8 candidate metrics (prefill/decode TPS, GPU utilization, SM
  activity, TTFT, TBT) under 8-hour diurnal production traces. CPU, RPS, queue
  depth, KV-cache %, and `num_requests_waiting` were **not** evaluated
  (KV-cache is named as future work). We cite and differentiate: different
  signal set, different workload regime, single-pool K8s vs. disaggregated GPU
  fleet.
- **MDPI *Technologies* 14(6):350** compares exactly two signals (CPU HPA vs.
  one custom inference-latency histogram) on **encoder** models
  (DistilBERT/RoBERTa) under constant load, with no LLM-specific signals. Its
  threats-to-validity explicitly defers "queue length-based scaling,
  throughput-driven policies, or latency percentile-based triggers" — i.e.,
  this study — as beyond its scope and future work.
- **WVA/llm-d** (arXiv:2603.09730) scales on KV-cache + queue depth against a
  single HPA configuration, with no signal ablation. **AIBrix**
  (arXiv:2504.03648), **Chiron** (arXiv:2501.08090), **SageServe**
  (arXiv:2502.14617) each advocate a policy without comparing signal families.
  **TokenScale** (arXiv:2512.03416) compares whole systems embodying metric
  families, not isolated signals. **OpScale** (arXiv:2608.13499) varies
  scaling granularity, not signals. A Red Hat OpenShift AI post compares KEDA
  vs. Knative under constant load (vendor blog, two systems, not an ablation).
  **TEAS** (arXiv:2511.02248) proposes one metric without evaluating
  alternatives.
- **llm-d WVA issue #1525** exists and is open, but is a scoped exploration
  epic; it is evidence of upstream interest, **not** cited as evidence of a
  community-acknowledged gap.
- **BatchBench** (arXiv:2605.12272) argues cross-autoscaler comparison is
  confounded by implementation differences; this motivates our single-variable
  design (identical mechanism, varying only the signal) rather than an
  autoscaler shootout.

## 4. Contributions

1. **First controlled single-variable ablation of K8s-exportable autoscaling
   signals for LLM serving** across bursty workload patterns (vs. HeteroScale's
   production-diurnal disaggregated study; vs. MDPI's two-signal encoder
   study).
2. **A signal × pattern interaction characterization with mechanism
   explanations** — *why* a signal fails a pattern (e.g., CPU lags KV-cache
   pressure; a latency-triggered signal thrashes under spikes), not merely a
   ranking.
3. **An open-source, one-command reproducible harness** (k3d + KEDA +
   `llm-d-inference-sim` + Guidellm) that others can extend with new signals or
   patterns.

## 5. Non-Claims

- No absolute latency truth: laptop-scale simulation supports relative
  comparisons only. Absolute numbers are not comparable to production.
- No composite-policy contribution; the composite arm is a calibration
  reference point.
- Not production-GPU results. Validating the signal ranking on GPU hardware is
  named as future work.
- Not a cross-autoscaler benchmark (HPA vs. KEDA vs. WVA); the mechanism is
  held fixed by design.

## 6. Experimental Design

### 6.1 Rig

| Component | Choice | Rationale |
|---|---|---|
| Cluster | **k3d** (k3s in Docker), single node | Lightest control plane available locally; k3s uses SQLite instead of etcd. See §9 for the kind-substitution limitation. |
| Model server | **`llm-d-inference-sim`** | OpenAI-compatible API; exposes vLLM-style Prometheus metrics (`num_requests_waiting`, cache-utilization gauge, TTFT/ITL histograms); models TTFT/ITL-vs-concurrency coupling; GPU-free; simulation methodology validated in WVA. |
| Autoscaling mechanism | **KEDA** `ScaledObject` with a Prometheus scaler, one per arm | Uniform mechanism across all arms — the signal is the only differing variable. Ecosystem-standard for LLM serving. |
| Metrics | Prometheus (15 s scrape) | Uniform across arms; part of the measured system, so parameters are fixed and documented. |
| Load generator | **Guidellm** | Used by llm-d upstream; emits OpenAI-compatible request streams from trace schedules. |
| Host | Dell Latitude E7240, i7-4600U (2C/4T), 8 GiB RAM, Ubuntu 24.04 | Runs are **strictly sequential** (2-way parallelism is ruled out on 2 physical cores). zram enabled; sleep inhibition for overnight batches; batches run headless. |

### 6.2 Factors

**Signal arms (treatment, 5):**

| Arm | Scaling input (Prometheus query source) |
|---|---|
| CPU | Container CPU utilization vs. requests (kubelet/cAdvisor) |
| RPS | Request rate (sim request counter, `rate()` over stable window) |
| Queue depth | `num_requests_waiting` gauge (vLLM-style; this arm deliberately merges the queue-depth and waiting-requests family — they are the same metrics-surface signal) |
| KV-cache % | Cache-utilization gauge (sim exports a vLLM-style `gpu_cache_usage_perc` equivalent; documented naming quirk) |
| TTFT | p95 TTFT over a rolling window (sim TTFT histogram) |

**Composite reference arm (1, non-contribution):** KV-cache % + queue depth,
llm-d/WVA-style weighting (weights pinned to WVA's public defaults at
implementation time) — run at 2 seeds only, used to sanity-check that the best
single signals sit near current best practice.

**Workload patterns (4, synthetic parameterized generators):**

| Pattern | Trace length | Shape |
|---|---|---|
| Ramp | ~20 min | Linear load increase, enough for 2–3 scale-up/down cycles |
| Spike/burst | ~20 min | Baseline + 3–4 spikes with recovery gaps |
| Long-context skew | ~25 min | Spike-like load plus context-length mix shift |
| Diurnal | ~30 min | One compressed diurnal cycle (3–4 oscillations at KEDA's 5-min scale-down stabilization) |

Stretch (only if slack remains): adapt the public Azure LLM inference trace as
a fifth, empirical pattern for robustness.

### 6.3 Fixed Parameters

- Replica range **1–6** (ceiling lowered from 10 for node CPU headroom; see §6.5).
- KEDA poll interval and HPA sync period: defaults, **identical across arms**, documented.
- Scale-down stabilization window: KEDA default (5 min), identical across arms.
- SLO targets: **frozen at the end of week 1 calibration**, expressed as
  multiples of the calibrated low-load baseline for TTFT p99 and TPOT
  (candidate defaults: TTFT p99 ≤ 1.5× and TPOT ≤ 2× baseline; exact
  multipliers fixed once, before any factorial run); identical across all arms
  and patterns thereafter.
- Trace seeds recorded per run; analysis seeds fixed and versioned.
- Node CPU target headroom: **peak load keeps node CPU < 70% at max replicas.**

### 6.4 Response Metrics

| Metric | Definition |
|---|---|
| SLO violation rate | Fraction of requests exceeding the frozen TTFT / TPOT targets; percentiles (p50/p95/p99) reported alongside |
| Scale-out latency | Time from a trace load change to the first new replica **Ready** |
| Overshoot | `R_observed(t) − R_required(t)`, where `R_required(t) = ceil(offered_load(t) / calibrated per-replica capacity)`; report max and time-integrated |
| Thrash | Scale events per minute; direction reversals per pattern period |
| Replica-seconds | Area under the replica-count curve (cost proxy) |

Every cell reports variance across seeds. Metrics are exported per-run as small
CSV/JSON summaries plus raw Prometheus snapshots (release assets, not git).

### 6.5 Calibration (week 1)

The sim is **not** calibrated to published H100-scale curves — arrival rates
that high would saturate a 2-core node and compress all signal contrasts into
CPU-starvation noise. Instead:

1. Measure per-replica capacity on this host at rising concurrency; pick the
   profile whose peak-replica load keeps node CPU < 70%.
2. Record the low-load TTFT/TPOT baseline; freeze SLO targets as multiples of it.
3. Verify the sim's timing coupling (TTFT rises with concurrency; queue depth
   responds to bursts) before any factorial run.
4. Record control-plane CPU/RAM budget at idle and peak.

All relative comparisons are valid under any internally consistent operating
point; the absolute operating point differs from production, which §9 declares.

## 7. Run Protocol and Schedule

### 7.1 Pilot gate (end of week 1)

The factorial schedule is frozen only after pilots demonstrate, with numbers:

1. An end-to-end run completes and all five metrics are collected cleanly.
2. Run durations within ±20% of the estimates in §7.3.
3. Node CPU < 70% at max replicas under the spike pattern.
4. Thermals stable over a 1 h sustained batch (no progressive throttling).
5. No OOM events with zram active.

If the headroom check fails, the fallback is a lower concurrency ceiling —
smaller absolute scale, still internally valid. If k3d itself proves unstable
under load, that is a **stop-and-rethink** gate, not a silent degrade.

### 7.2 Seeding policy

- **2 seeds for every (arm × pattern) cell** in the base factorial.
- **Adaptive top-up:** a 3rd seed is added only where the coefficient of
  variation across the first two seeds is high enough to change the ranking
  narrative. Top-ups are budgeted, not open-ended.

### 7.3 Wall-clock budget

Per seed (6 arms × 4 patterns): 6 × 95 min of traces + 24 resets × 5 min ≈
**11.5 h sequential**. Two seeds ≈ 23 h; + ~15% adaptive top-up + ~15% rerun
buffer ≈ **~31 h ≈ 4 nights at 8 h**. Week 2 therefore carries **3 nights of
slack** in addition to week 4's reserved days.

**Trim order if wall-clock overruns** (pre-committed, in order): (1) cut
adaptive top-ups; (2) cut the composite reference arm to 1 seed; (3) only then
reduce diurnal further. Signal arms and workload patterns are **never** cut —
they are the contribution.

## 8. Four-Week Plan

| Week | Focus | Exit criterion |
|---|---|---|
| 1 | Rig bring-up: k3d + KEDA + sim + Prometheus + Guidellm; verify all 5 signal arms expressible as queries; calibration (§6.5); trace generators; metric pipeline; pilot gate; one-command repro script from day 1 | Pilot gate passed with recorded numbers; schedule frozen |
| 2 | Factorial execution: ~4 overnight batches + reruns/top-ups in slack nights; analysis notebooks in daytime (no cluster needed) | All cells present with variance reported |
| 3 | Analysis: rankings, signal × pattern interactions, mechanism explanations; threats-to-validity drafted; methods/protocol sections written while reruns execute | Draft analysis complete; figures sketched |
| 4 | **3 days:** writeup, portfolio-grade README, figures, release tagging. **2 days:** reserved slack | Tagged release; report + harness public-ready |

## 9. Limitations and Threats to Validity

1. **k3d-for-kind substitution.** We substitute k3d for kind due to hardware
   constraints (2-core laptop); this is expected to reduce control-plane
   measurement noise rather than change signal rankings, but that equivalence
   is an undemonstrated assumption, not something this study validates.
2. **Laptop-scale calibration.** The operating point is internally consistent
   but not production-scale; claims are relative signal quality only (§6.5).
3. **Simulated serving.** `llm-d-inference-sim` models timing coupling, not
   real model compute; its suitability for *relative* comparisons is inherited
   from WVA's published use and is not independently re-validated here beyond
   the §6.5 checks.
4. **Modest seed counts.** 2 seeds + adaptive top-up supports ranking and
   interaction claims, not fine-grained percentage claims between close arms.
5. **Compressed workload patterns.** Diurnal in particular is one compressed
   cycle, not a production 24 h trace.
6. **Single node, single pool, no GPU.** Findings do not address P/D
   disaggregated serving (HeteroScale's setting) or GPU-specific signals.
7. **Single host.** No cross-machine replication; all runs share one thermal
   and contention profile.

## 10. Artifact Plan

Repo layout (maps onto existing directories):

```
experiments/   harness: k3d cluster config, KEDA ScaledObjects per arm,
               trace generators invocation, run scripts
src/           trace generators, metric extraction, analysis code
results/       per-run CSV/JSON summaries (git); raw snapshots (release assets)
figures/       paper + README figures
manuscript/    report / writeup
notes/         this spec, experiment log
README.md      front door: research question, one headline figure,
               one-command reproduction instructions
```

A tagged release carries bulky raw data. The one-command repro is maintained
from week 1 (it is how the overnight batches run), so week 4 is polish, not
archaeology.

## 11. Success Criteria

1. Factorial complete: every (arm × pattern) cell present with ≥2 seeds and
   reported variance.
2. At least one signal × pattern interaction characterized **mechanistically**
   (a causal explanation, not only a ranking).
3. Any figure in the writeup regenerates from raw data with one command.
4. Writeup with explicit threats-to-validity, adequate for a workshop /
   technical-report submission — portfolio-grade regardless of venue.

## 12. Risks and Fallbacks

| Risk | Mitigation / fallback |
|---|---|
| Thermal throttling on 2013 hardware | Pilot-gate thermal check (§7.1); shorter batch segments; night splits |
| Node CPU saturation compresses signal contrasts | §6.5 headroom rule (<70%); lower concurrency ceiling if needed |
| k3d instability under load | Stop-and-rethink gate (§7.1) — not a silent degrade |
| Sim lacks a metric an arm needs (e.g., TTFT histogram) | Week-1 verification; substitute nearest exported metric and document in the arm table |
| Schedule slip | Pre-committed trim order (§7.3); slack in week 2 nights and week 4 days |
| Free-tier / external dependency drift | None — the study has zero external service dependencies by design |

## 13. References

- HeteroScale — arXiv:2508.19559
- WVA / llm-d — arXiv:2603.09730 · github.com/llm-d/llm-d-inference-sim · llm-d WVA issue #1525
- AIBrix — arXiv:2504.03648
- Chiron — arXiv:2501.08090
- SageServe — arXiv:2502.14617
- OpScale — arXiv:2608.13499
- TokenScale — arXiv:2512.03416
- TEAS — arXiv:2511.02248
- BatchBench — arXiv:2605.12272
- Joyce & Sebastian, "Inference-Time-Driven Autoscaling for Inference
  Workloads," *Technologies* 14(6):350, 2026. doi:10.3390/technologies14060350
- DistServe — arXiv:2401.09670 · Sarathi-Serve — arXiv:2403.02310 (serving
  context; not competitors)
