# Week 1 — Rig Bring-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete week-1 rig per the spec — k3d + KEDA + `llm-d-inference-sim` + Prometheus + Guidellm harness with 5 signal arms + composite, calibration, and the pilot gate — ending with a one-command reproducible run and a frozen factorial schedule.

**Architecture:** Single-node k3d cluster on the laptop. The sim Deployment (`serving` namespace) sits behind a NodePort; KEDA ScaledObjects (generated from one frozen config) scale it — for the five single-signal arms the query is the *only* differing variable; the composite **reference condition** instead carries two triggers whose max reproduces WVA's threshold-OR (spec §6.2), and it is excluded from single-signal ranking. A host-side Python layer generates normalized workload schedules, drives Guidellm one constant-rate segment at a time (phase segmentation = exact offered-load step function), and extracts the spec's five response metrics from Guidellm reports + Prometheus range queries.

**Tech Stack:** k3d v5.x, Kubernetes (k3s default), KEDA 2.x (Helm), kube-prometheus-stack (Helm; Grafana/Alertmanager off), `ghcr.io/llm-d/llm-d-inference-sim:v0.9.0`, Guidellm (pip, venv), Python 3.12 + pytest + pandas + requests + PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-02-llm-autoscaling-signals-design.md` — the plan argues from the spec; executors read both.

**Scope note:** This plan covers week 1 only (§8 row 1, exit = pilot gate passed + schedule frozen). Weeks 2–4 (factorial execution, analysis, writeup) get their own plans after the pilot gate, because they depend on week-1 outputs (frozen schedule, SLO targets, calibrated thresholds) that do not exist yet.

## Global Constraints

Copied from the spec; every task implicitly includes these:

- Runs are **strictly sequential** — no parallel arms, batches, or clusters (2 physical cores).
- Replica range **1–6** (`minReplicaCount: 1`, `maxReplicaCount: 6`).
- Prometheus scrape interval **15 s**, identical across arms.
- **Autoscaler timing (identical across arms, pinned explicitly — spec §6.3):** KEDA `pollingInterval: 30`; HPA sync period 15 s (k3s default, unmodified, documented); **HPA `behavior.scaleDown.stabilizationWindowSeconds: 300` pinned via KEDA's `advanced.horizontalPodAutoscalerConfig.behavior`** — this is the 1→N scale-down stabilization mechanism. KEDA `cooldownPeriod: 300` is **scale-to-zero only and inert at `minReplicaCount: 1`**; documented, never relied on as stabilization.
- Peak load must keep **node CPU < 70 %** at max replicas; no OOM with zram.
- SLO targets are **multiples of the calibrated low-load baseline** (candidates: TTFT p99 ≤ 1.5×, TPOT ≤ 2×), frozen once at calibration, identical across arms/patterns thereafter.
- **$0, laptop-only, no external services.** Artifact layout per spec §10.
- Sim metric names carry the `vllm:` colon prefix (e.g. `vllm:num_requests_waiting`).
- Seeds recorded per run: schedule seed (trace generators) + `PYTHONHASHSEED` (sim jitter) + analysis seeds fixed and versioned.
- Small commits per task; repo is `~/llm-optimization-research`, branch `master`.
- Namespaces: `serving` (sim), `monitoring` (Prometheus stack, Helm release name `mon`), `keda` (operator).
- Ports: sim NodePort **30080**, Prometheus NodePort **30090** (mapped through k3d to localhost).

## File Structure

```
experiments/
  cluster/00_install_host.sh      # host tooling (k3d, kubectl, helm, venv)
  cluster/create_cluster.sh       # k3d cluster with port maps
  cluster/monitoring-values.yaml  # slim kube-prometheus-stack values
  cluster/install_stack.sh        # helm installs: monitoring + KEDA
  sim/deployment.yaml             # sim Deployment (no autoscaling by itself)
  sim/service.yaml                # NodePort 30080 + ServiceMonitor
  arms/queries.yaml               # arm -> PromQL + threshold key (single source)
  arms/generated/                 # gen_scaledobjects.py output (committed)
  config/frozen.yaml              # calibrated constants; the one file that varies
  calibrate/calibrate_capacity.sh # per-replica capacity + baselines
  calibrate/headroom.sh           # 6-replica load: CPU/RAM/thermals
  collect_run.sh                  # per-run raw Prometheus snapshots + metrics.json
  repro.sh                        # one-command full run (used by Makefile)
  Makefile                        # up / run / reset / down
src/
  trace_gen.py                    # patterns -> normalized segment schedules
  run_phases.py                   # guidellm per segment; stitch request log
  extract_metrics.py              # the 5 response metrics (pure functions)
  gen_scaledobjects.py            # frozen.yaml + queries.yaml -> ScaledObjects
  collect_run.py                  # CLI wrapper around extract + Prometheus fetch
tests/
  test_trace_gen.py  test_run_phases.py  test_extract_metrics.py
  test_gen_scaledobjects.py
  fixtures/segment_sample.json    # real guidellm report captured in Task 8
notes/
  environment.md  signal_liveness.md  pilot_gate.md  schedule.md
results/                          # per-run outputs (summaries git-tracked, raw not)
```

---

### Task 1: Host tooling install

**Files:**
- Create: `experiments/cluster/00_install_host.sh`
- Create: `.gitignore`
- Create: `notes/environment.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `~/llm-optimization-research/.venv` with `guidellm`, `pytest`, `pandas`, `requests`, `PyYAML`; `k3d`, `kubectl`, `helm` on `PATH` (versions recorded).

- [ ] **Step 1: Write the install script**

```bash
#!/usr/bin/env bash
# experiments/cluster/00_install_host.sh — host-side tooling for the rig.
# Idempotent: skips anything already present. Installs to ~/.local/bin.
set -euo pipefail
BIN="$HOME/.local/bin"; mkdir -p "$BIN"

need() { command -v "$1" >/dev/null 2>&1; }

if ! need k3d; then
  curl -sL "https://github.com/k3d-io/k3d/releases/download/v5.8.3/k3d-linux-amd64" \
    -o "$BIN/k3d" && chmod +x "$BIN/k3d"
fi
if ! need kubectl; then
  curl -sL "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl" \
    -o "$BIN/kubectl" && chmod +x "$BIN/kubectl"
fi
if ! need helm; then
  curl -sL "https://get.helm.sh/helm-v3.16.2-linux-amd64.tar.gz" | tar xz -C /tmp
  install -m 0755 /tmp/linux-amd64/helm "$BIN/helm"
fi
if [ ! -x .venv/bin/guidellm ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install "guidellm[recommended]" pytest pandas requests PyYAML
fi
echo "== versions =="
k3d version; kubectl version --client=true -o yaml | grep gitVersion; helm version --short
.venv/bin/guidellm --version; .venv/bin/python --version
```

`.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
results/**/raw/
docs/superpowers/specs/.~lock.*
```

- [ ] **Step 2: Run it**

Run: `bash experiments/cluster/00_install_host.sh`
Expected: version lines for k3d v5.8.3, kubectl v1.31.0, helm v3.16.2, guidellm, Python 3.12. If a download URL 404s, fetch the latest release tag for that tool and pin it in the script — record the substitution in `notes/environment.md`.

- [ ] **Step 3: Record the environment**

Write `notes/environment.md`: tool versions from Step 2 output, plus host facts (i7-4600U 2C/4T, 8 GiB RAM, Ubuntu 24.04, zram — confirm with `swapon --show`), date, and `guidellm run --help` output pasted verbatim (Task 8 consults it for a seed flag and output-path syntax).

- [ ] **Step 4: Clean the stale spec lock file and commit**

```bash
rm -f docs/superpowers/specs/.~lock.*
git add .gitignore notes/environment.md experiments/cluster/00_install_host.sh
git commit -m "chore: host tooling install script, environment record, gitignore"
```

---

### Task 2: k3d cluster

**Files:**
- Create: `experiments/cluster/create_cluster.sh`

**Interfaces:**
- Consumes: k3d from Task 1.
- Produces: cluster `sigscale`; NodePorts 30080/30090 reachable at `localhost:30080`/`localhost:30090`.

- [ ] **Step 1: Write the create script**

```bash
#!/usr/bin/env bash
# experiments/cluster/create_cluster.sh — single-node k3s; traefik and
# metrics-server disabled (unused; saves ~200 MiB). NodePorts are mapped
# through the k3d server container to the host.
set -euo pipefail
k3d cluster create sigscale \
  -p "30080:30080@server:0" \
  -p "30090:30090@server:0" \
  --k3s-arg "--disable=traefik@server:0" \
  --k3s-arg "--disable=metrics-server@server:0" \
  --kubeconfig-update-default
```

- [ ] **Step 2: Run and verify**

Run: `bash experiments/cluster/create_cluster.sh && kubectl get nodes`
Expected: one node `k3d-sigscale-server-0` in `Ready` state within ~60 s.

- [ ] **Step 3: Memory budget check**

Run: `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'`
Expected: k3d server container < 1.5 GiB. Record in `notes/environment.md`. If above, stop and investigate before proceeding (spec §7.1 treats rig instability as stop-and-rethink).

- [ ] **Step 4: Commit**

```bash
git add experiments/cluster/create_cluster.sh notes/environment.md
git commit -m "feat(rig): k3d single-node cluster with NodePort mappings"
```

---

### Task 3: Monitoring stack (Prometheus, 15 s)

**Files:**
- Create: `experiments/cluster/monitoring-values.yaml`
- Create: `experiments/cluster/install_stack.sh`

**Interfaces:**
- Consumes: cluster from Task 2.
- Produces: Prometheus at `http://mon-kube-prometheus-prometheus.monitoring.svc:9090` in-cluster (KEDA's `serverAddress`) and `http://localhost:30090` on host; kube-state-metrics (`kube_deployment_status_replicas`); node-exporter (`node_cpu_seconds_total`); cAdvisor scrape (`container_cpu_usage_seconds_total`).

- [ ] **Step 1: Write values**

```yaml
# experiments/cluster/monitoring-values.yaml — slim kube-prometheus-stack.
grafana:
  enabled: false
alertmanager:
  enabled: false
prometheus-pushgateway:
  enabled: false
defaultRules:
  create: false
nodeExporter:
  enabled: true
kubeStateMetrics:
  enabled: true
prometheus:
  prometheusSpec:
    scrapeInterval: 15s
    retention: 2d
    resources:
      requests: {cpu: 100m, memory: 384Mi}
      limits: {memory: 768Mi}
  service:
    type: NodePort
    nodePort: 30090
```

- [ ] **Step 2: Write install script (monitoring + KEDA)**

```bash
#!/usr/bin/env bash
# experiments/cluster/install_stack.sh — monitoring + KEDA. Idempotent.
set -euo pipefail
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm upgrade --install mon prometheus-community/kube-prometheus-stack \
  -f experiments/cluster/monitoring-values.yaml \
  -n monitoring --create-namespace
helm upgrade --install keda kedacore/keda -n keda --create-namespace
kubectl -n monitoring wait --for=condition=Available deploy --timeout=300s
kubectl -n keda wait --for=condition=Available deploy/keda-operator --timeout=300s
```

- [ ] **Step 3: Run and verify**

Run: `bash experiments/cluster/install_stack.sh`
Then:

```bash
kubectl get pods -n monitoring -n keda   # all Running (note: -n twice lists only the last; run twice)
curl -s localhost:30090/-/healthy        # Prometheus Server is Healthy.
curl -s localhost:30090/api/v1/query?query=container_cpu_usage_seconds_total | head -c 200
curl -s localhost:30090/api/v1/query?query=node_cpu_seconds_total | head -c 200
```

Expected: pods Running; healthy body `"status":"success"` with non-empty `"result"` for both metric queries (cAdvisor + node-exporter working — the CPU arm and the headroom check depend on these). Record installed chart versions (`helm list -A`) in `notes/environment.md`.

- [ ] **Step 4: Commit**

```bash
git add experiments/cluster/monitoring-values.yaml experiments/cluster/install_stack.sh notes/environment.md
git commit -m "feat(rig): slim kube-prometheus-stack (15s) + KEDA via helm"
```

---

### Task 4: Sim deployment (no autoscaler yet)

**Files:**
- Create: `experiments/sim/deployment.yaml`
- Create: `experiments/sim/service.yaml`

**Interfaces:**
- Consumes: cluster + monitoring from Tasks 2–3.
- Produces: Deployment `llm-sim` (1 replica) in ns `serving`; OpenAI API at `http://localhost:30080/v1`; `/metrics` scraped at 15 s via ServiceMonitor.

- [ ] **Step 1: Write deployment**

```yaml
# experiments/sim/deployment.yaml
apiVersion: v1
kind: Namespace
metadata: {name: serving}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-sim
  namespace: serving
spec:
  replicas: 1
  selector: {matchLabels: {app: llm-sim}}
  template:
    metadata:
      labels: {app: llm-sim}
    spec:
      containers:
        - name: sim
          image: ghcr.io/llm-d/llm-d-inference-sim:v0.9.0
          args:
            - --port=8000
            - --model=dummy-model        # regex tokenizer; no HF download
            - --max-num-seqs=8
            - --time-to-first-token=200ms
            - --time-to-first-token-std-dev=50ms
            - --inter-token-latency=40ms
            - --inter-token-latency-std-dev=10ms
            - --time-factor-under-load=3.0
            - --latency-calculator=constant
          env:
            - name: PYTHONHASHSEED       # per-run seed; set by repro.sh
              value: "0"
          ports: [{containerPort: 8000}]
          resources:
            requests: {cpu: 250m, memory: 256Mi}
            limits: {cpu: 500m, memory: 512Mi}
          readinessProbe:
            httpGet: {path: /metrics, port: 8000}
            initialDelaySeconds: 2
            periodSeconds: 5
```

Initial latency values are calibration starting points (Task 10 tunes them); they are frozen *before* any factorial run.

- [ ] **Step 2: Write service + ServiceMonitor**

```yaml
# experiments/sim/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: llm-sim
  namespace: serving
  labels: {app: llm-sim}
spec:
  type: NodePort
  selector: {app: llm-sim}
  ports:
    - name: http
      port: 80
      targetPort: 8000
      nodePort: 30080
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: llm-sim
  namespace: serving
  labels: {release: mon}        # match the helm release so the stack picks it up
spec:
  selector: {matchLabels: {app: llm-sim}}
  endpoints:
    - port: http
      interval: 15s
```

- [ ] **Step 3: Apply and verify API + metrics**

```bash
kubectl apply -f experiments/sim/
kubectl -n serving rollout status deploy/llm-sim --timeout=180s
curl -s localhost:30080/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"dummy-model","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'
curl -s localhost:30080/metrics | grep -E '^vllm:(num_requests_waiting|kv_cache_usage_perc|request_success_total)'
```

Expected: a chat completion JSON with choices; grep prints the three metric lines (`vllm:num_requests_waiting 0`, etc.). If the pod logs show a HuggingFace download attempt, the dummy model name was rejected — pick another non-HF name, update both YAMLs, and note it in `notes/environment.md`.

- [ ] **Step 4: Verify Prometheus sees the sim**

```bash
sleep 30 && curl -s localhost:30090/api/v1/query?query=vllm:num_requests_waiting | head -c 200
```

Expected: `"status":"success"` with one result series. If empty: check the ServiceMonitor `release: mon` label and `kubectl -n monitoring get servicemonitor -A`.

- [ ] **Step 5: Commit**

```bash
git add experiments/sim/
git commit -m "feat(rig): llm-d-inference-sim deployment + NodePort + ServiceMonitor"
```

---

### Task 5: Arm queries file (single source of truth)

**Files:**
- Create: `experiments/arms/queries.yaml`

**Interfaces:**
- Consumes: metric names from Task 4.
- Produces: `experiments/arms/queries.yaml` — consumed by `gen_scaledobjects.py` (Task 8) and `collect_run.py` (Task 9). Schema: `arms: {<arm>: {query: str, threshold_key: str, activation_key: str}}` for the five single-signal arms; the **composite** arm instead carries a `triggers:` list of such entries, one per signal — KEDA takes the max over triggers, which reproduces WVA's **threshold-OR** semantics (spec §6.2; WVA publishes no combination weights). Threshold keys index `experiments/config/frozen.yaml`.

- [ ] **Step 1: Write the queries**

```yaml
# experiments/arms/queries.yaml — the ONLY place arm PromQL lives.
# Aggregate semantics: every query returns a cluster-wide value; thresholds
# are per-replica targets; KEDA/HPA scaling law is therefore identical
# across arms (desired ~= ceil(current * value / threshold)).
#
# Control-loop roles (spec §6.2) — drives the §6.6 hypothesis structure:
#   rps   = demand            cpu, kv = resource-pressure
#   queue = backlog           ttft    = outcome/feedback (closed loop, §6.6)
# composite = reference condition, excluded from single-signal ranking.
arms:
  cpu:
    # total cores used by sim containers; threshold = target cores per replica
    query: 'sum(rate(container_cpu_usage_seconds_total{namespace="serving",container="sim"}[2m]))'
    threshold_key: cpu_cores_per_replica
    activation_key: cpu_activation
  rps:
    # request throughput; threshold = calibrated per-replica capacity (req/s)
    query: 'sum(rate(vllm:request_success_total{namespace="serving"}[2m]))'
    threshold_key: rps_per_replica
    activation_key: rps_activation
  queue:
    # total waiting requests across pods; threshold = backlog per replica
    query: 'sum(vllm:num_requests_waiting{namespace="serving"})'
    threshold_key: queue_per_replica
    activation_key: queue_activation
  kv:
    # mean KV-cache occupancy fraction across pods (0..1)
    query: 'avg(vllm:kv_cache_usage_perc{namespace="serving"})'
    threshold_key: kv_frac
    activation_key: kv_activation
  ttft:
    # rolling p95 TTFT in seconds
    query: 'histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket{namespace="serving"}[2m])))'
    threshold_key: ttft_p95_s
    activation_key: ttft_activation
  composite:
    # Reference condition (spec §6.2): WVA-style threshold-OR. Verified
    # 2026-09-04: WVA publishes NO combination weights — it ORs per-signal
    # thresholds (paper §V-A: tau_kv=0.8, tau_q=5; same values are the repo
    # defaults). KEDA multi-trigger max-over-triggers reproduces OR: scale
    # to whichever signal demands more.
    triggers:
      - query: 'avg(vllm:kv_cache_usage_perc{namespace="serving"})'
        threshold_key: composite_kv_frac      # WVA tau_kv = 0.8
        activation_key: kv_activation
      - query: 'sum(vllm:num_requests_waiting{namespace="serving"})'
        threshold_key: composite_queue        # WVA tau_q = 5
        activation_key: queue_activation
```

The composite's two triggers carry plain queries; their thresholds (WVA τ_kv/τ_q) live in `frozen.yaml` like every other arm's — the frozen config stays the single numeric source.

- [ ] **Step 2: Commit**

```bash
git add experiments/arms/queries.yaml
git commit -m "feat(arms): arm->PromQL mapping, aggregate semantics documented"
```

---

### Task 6: Signal liveness check (spec §6.5 step 3)

**Files:**
- Create: `experiments/arms/check_signals.sh`
- Create: `notes/signal_liveness.md`

**Interfaces:**
- Consumes: sim + Prometheus from Tasks 3–4; queries from Task 5.
- Produces: recorded evidence that every arm's series is live (varies with load) — the spec's week-1 "all 5 signal arms expressible as queries" exit, and the §12 fallback trigger if any series is inert.

- [ ] **Step 1: Write the check script**

```bash
#!/usr/bin/env bash
# experiments/arms/check_signals.sh — drive 90 s of moderate load, then show
# idle-vs-load values for every arm query. Human inspects; numbers recorded.
set -euo pipefail
PROM=http://localhost:30090/api/v1/query
q() { curl -sG "$PROM" --data-urlencode "query=$1" | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p'; }

idle() { echo "== idle =="; for a in cpu rps queue kv ttft; do
  yq ".arms.$a.query" experiments/arms/queries.yaml | xargs -I{} echo "$a: $(q '{}')"; done; }

idle
echo "== driving 90 s at 5 req/s =="
.venv/bin/guidellm run \
  --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
  --profile kind=constant,rate=5 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --constraint kind=max_duration,seconds=90 \
  --output json path=/tmp/liveness_load.json >/dev/null 2>&1 || true
for a in cpu rps queue kv ttft; do
  yq ".arms.$a.query" experiments/arms/queries.yaml | xargs -I{} echo "$a: $(q '{}')"; done
```

If `yq` is not installed, replace the `yq` reads with the five query strings copied verbatim from `queries.yaml` (keep them in sync; Task 8's generator makes this file authoritative anyway).

- [ ] **Step 2: Run and record**

Run: `bash experiments/arms/check_signals.sh`
Expected: `rps` ≈ 5 under load (≈ 0 idle); `queue` > 0 under load if rate exceeds one replica's capacity at these settings, else drives `num_requests_running` up; `cpu` rises; `kv` rises above its idle value; `ttft` reports a percentile under load (may be `NaN` when idle — KEDA skips scaling on `NaN`; acceptable, note it).

Record idle/load values per arm in `notes/signal_liveness.md`. **If any series is inert** (e.g., `kv_cache_usage_perc` flat at 0 under sustained load): per spec §12, substitute the nearest exported live metric for that arm, update `queries.yaml`, and document the substitution in `notes/signal_liveness.md` and the spec's arm table. Do not silently drop the arm.

- [ ] **Step 3: Commit**

```bash
git add experiments/arms/check_signals.sh notes/signal_liveness.md
git commit -m "feat(arms): signal liveness check with recorded idle/load evidence"
```

---

### Task 7: Trace generators (TDD)

**Files:**
- Create: `src/trace_gen.py`
- Test: `tests/test_trace_gen.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `generate(pattern: str, seed: int) -> dict` — `{"pattern", "seed", "segments": [Segment]}`; `Segment = {"idx": int, "label": str, "start_s": float, "duration_s": int, "rate_frac": float, "prompt_tokens": int, "output_tokens": int}`. Rates are **fractions of per-replica capacity** (calibration-independent).
  - `materialize(schedule: dict, cap_rps: float) -> dict` — same schedule with `rate` (req/s, rounded 3 dp) added to each segment and `cap_rps` set at top level. Consumed by `run_phases.run_schedule`.
  - Patterns: `ramp`, `spike`, `diurnal`, `longctx` (spec §6.2).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trace_gen.py
from src.trace_gen import generate, materialize, PATTERNS

def test_patterns_exist_and_shapes():
    assert set(PATTERNS) == {"ramp", "spike", "diurnal", "longctx"}
    for p in PATTERNS:
        s = generate(p, seed=1)
        assert s["pattern"] == p and s["seed"] == 1
        assert 1100 <= sum(x["duration_s"] for x in s["segments"]) <= 1900  # ~20-30 min
        for x in s["segments"]:
            assert x["rate_frac"] > 0 and x["prompt_tokens"] > 0 and x["output_tokens"] > 0

def test_ramp_is_monotonic():
    rates = [x["rate_frac"] for x in generate("ramp", seed=42)["segments"]]
    assert all(b >= a * 0.9 for a, b in zip(rates, rates[1:]))  # jitter-tolerant
    assert rates[-1] / rates[0] >= 5                                            # wide sweep

def test_spike_has_bursts_and_gaps():
    s = generate("spike", seed=1)["segments"]
    labels = [x["label"] for x in s]
    assert labels.count("burst") >= 3 and labels.count("baseline") >= 3
    burst = max(x["rate_frac"] for x in s if x["label"] == "burst")
    base = min(x["rate_frac"] for x in s if x["label"] == "baseline")
    assert burst / base >= 3

def test_diurnal_oscillates():
    r = [x["rate_frac"] for x in generate("diurnal", seed=3)["segments"]]
    peaks = sum(1 for i in range(1, len(r) - 1) if r[i] > r[i-1] and r[i] > r[i+1])
    assert peaks >= 3                      # 3-4 oscillations per spec
    assert 0.05 < min(r) and max(r) <= 1.6

def test_longctx_shifts_context_mix():
    s = generate("longctx", seed=7)["segments"]
    burst = [x for x in s if x["label"] == "burst"][0]
    base = [x for x in s if x["label"] == "baseline"][0]
    assert burst["prompt_tokens"] > 4 * base["prompt_tokens"]
    assert burst["output_tokens"] > 2 * base["output_tokens"]

def test_seeding_deterministic_and_sensitive():
    assert generate("spike", seed=5) == generate("spike", seed=5)
    assert generate("spike", seed=5) != generate("spike", seed=6)

def test_materialize_converts_to_rps():
    s = materialize(generate("ramp", seed=1), cap_rps=4.0)
    assert s["cap_rps"] == 4.0
    for x in s["segments"]:
        assert abs(x["rate"] - round(x["rate_frac"] * 4.0, 3)) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trace_gen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.trace_gen'`.

- [ ] **Step 3: Implement**

```python
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
```

Note: `longctx` keeps the spike shape but shifts the burst token mix (2048/512 vs 256/128) — the "context-length mix shift" of spec §6.2.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trace_gen.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trace_gen.py tests/test_trace_gen.py
git commit -m "feat(src): seeded normalized trace generators for 4 bursty patterns"
```

---

### Task 8: Segment runner + Guidellm schema probe (TDD on parsing)

**Files:**
- Create: `tests/fixtures/segment_sample.json` (captured)
- Create: `src/run_phases.py`
- Test: `tests/test_run_phases.py`

**Interfaces:**
- Consumes: `materialize()` from Task 7; guidellm venv from Task 1.
- Produces:
  - `run_schedule(schedule: dict, run_dir: str, target_url: str = "http://localhost:30080/v1", model: str = "dummy-model", guidellm_bin: str = ".venv/bin/guidellm") -> str` — executes the schedule segment-by-segment; writes `<run_dir>/seg_<idx>/report.json` per active segment and returns the manifest path `<run_dir>/segment_manifest.csv` with columns `idx,label,start_epoch,end_epoch,rate,prompt_tokens,output_tokens`.
  - `parse_segment(path: str) -> pandas.DataFrame` — one row per request with columns `ttft_s, tpot_s, e2e_s, output_tokens`; consumed by `extract_metrics` and `collect_run.py`.
  - `stitch(run_dir: str) -> str` — concatenates all parsed segments (adding `segment_idx`, `label`) into `<run_dir>/requests.csv`.

- [ ] **Step 1: Capture a real guidellm report as fixture**

```bash
mkdir -p tests/fixtures
.venv/bin/guidellm run \
  --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
  --profile kind=constant,rate=2 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=64 \
  --constraint kind=max_duration,seconds=20 \
  --output json path=tests/fixtures/segment_sample.json
.venv/bin/python - <<'EOF'
import json; d = json.load(open("tests/fixtures/segment_sample.json"))
print(json.dumps(d, indent=1)[:4000])   # inspect structure; find per-request records
EOF
```

Inspect the output. Note in `notes/environment.md`: (a) the exact path to per-request records and their field names (`ttft`? `ttft_ms`? `inter_token_latency` list?); (b) whether `guidellm run --help` lists a `seed` option (if yes, add `--seed <schedule seed>` to the invocation in `run_schedule` so runs are bit-reproducible; the constant-rate profile is already deterministic in cadence). The fixture file is committed and is the parser's contract.

- [ ] **Step 2: Write failing tests against the fixture**

The assertions below use the fixture's real row count dynamically; column expectations are the contract. If Step 1 showed different source field names, adjust only the extraction lines in Step 3 — never the DataFrame contract.

```python
# tests/test_run_phases.py
import pandas as pd
from src.run_phases import parse_segment, stitch

FIX = "tests/fixtures/segment_sample.json"

def test_parse_segment_contract():
    df = parse_segment(FIX)
    assert {"ttft_s", "tpot_s", "e2e_s", "output_tokens"} <= set(df.columns)
    assert len(df) >= 5                          # 20 s at 2 rps
    assert (df.ttft_s >= 0).all() and (df.tpot_s >= 0).all()
    # sim config: ITL 40ms -> tpot in a sane band
    assert df.tpot_s.between(0.001, 1.0).all()

def test_stitch_labels_and_order(tmp_path):
    (tmp_path / "seg_0").mkdir(); (tmp_path / "seg_1").mkdir()
    for i in (0, 1):
        pd.read_json(FIX).to_json(tmp_path / f"seg_{i}" / "report.json")
    out = stitch(str(tmp_path))
    df = pd.read_csv(out)
    assert {"segment_idx", "label"} <= set(df.columns)
    assert df.segment_idx.isin([0, 1]).all()
```

(`pd.read_json(FIX).to_json(...)` round-trips the fixture so `parse_segment` must tolerate both the native guidellm layout and plain JSON — it reads via `json.load`, not `pd.read_json`, so this works as long as the round-trip preserves the nested dict. If the fixture top level is a list rather than dict, adjust the fixture copy to `json.dump(json.load(open(FIX)), ...)`.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_phases.py -v`
Expected: FAIL — `No module named 'src.run_phases'`.

- [ ] **Step 4: Implement**

```python
# src/run_phases.py — execute a materialized schedule one segment at a time.
# guidellm has no native time-varying profile; phase segmentation gives us an
# exact offered-load step function (which overshoot's R_required needs) using
# only documented guidellm features.
import json, subprocess, time
from pathlib import Path
import pandas as pd

# Field names verified against tests/fixtures/segment_sample.json in Task 8
# Step 1. If guidellm's schema changed on upgrade, fix the _request_records
# path and the three extraction lines — never the DataFrame contract.
def _request_records(doc):
    return doc["benchmarks"][0]["request_data"]

def parse_segment(path):
    doc = json.load(open(path))
    rows = []
    for r in _request_records(doc):
        ttft = float(r["ttft"]); e2e = float(r["request_latency"])
        outn = int(r["output_tokens"]) if "output_tokens" in r else int(r["output_token_count"])
        rows.append({"ttft_s": ttft, "e2e_s": e2e,
                     "tpot_s": (e2e - ttft) / max(outn - 1, 1), "output_tokens": outn})
    return pd.DataFrame(rows)

def stitch(run_dir):
    frames = []
    for segdir in sorted(Path(run_dir).glob("seg_*")):
        idx = int(segdir.name.split("_")[1])
        df = parse_segment(segdir / "report.json")
        df["segment_idx"] = idx
        frames.append(df)
    out = Path(run_dir) / "requests.csv"
    pd.concat(frames).to_csv(out, index=False)
    return str(out)

def run_schedule(schedule, run_dir, target_url="http://localhost:30080/v1",
                 model="dummy-model", guidellm_bin=".venv/bin/guidellm"):
    run_dir = Path(run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for s in schedule["segments"]:
        start = time.time()
        segdir = run_dir / f"seg_{s['idx']}"; segdir.mkdir(exist_ok=True)
        if s["rate"] > 0:
            cmd = [guidellm_bin, "run",
                   f"--backend kind=openai_http,target={target_url},model={model}",
                   f"--profile kind=constant,rate={s['rate']}",
                   f"--data kind=synthetic_text,prompt_tokens={s['prompt_tokens']},output_tokens={s['output_tokens']}",
                   f"--constraint kind=max_duration,seconds={s['duration_s']}",
                   f"--output json path={segdir}/report.json"]
            subprocess.run(cmd, check=True, capture_output=True)
        else:
            time.sleep(s["duration_s"])           # offered-load gap
        manifest.append({k: s[k] for k in
                         ("idx", "label", "rate", "prompt_tokens", "output_tokens")} |
                        {"start_epoch": start, "end_epoch": time.time()})
    mdf = pd.DataFrame(manifest)
    mdf.to_csv(run_dir / "segment_manifest.csv", index=False)
    stitch(str(run_dir))
    return str(run_dir / "segment_manifest.csv")
```

Before moving on, reconcile `_request_records` and the `parse_segment` field extraction with what the fixture probe actually showed (`r["ttft"]` / `r["request_latency"]` / token-count key are the expected names per guidellm docs; verify, adjust, re-run tests).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_phases.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/run_phases.py tests/test_run_phases.py tests/fixtures/segment_sample.json notes/environment.md
git commit -m "feat(src): phase-segmented guidellm runner with fixture-driven parser"
```

---

### Task 9: Metric extraction (TDD, pure functions)

**Files:**
- Create: `src/extract_metrics.py`
- Test: `tests/test_extract_metrics.py`

**Interfaces:**
- Consumes: `requests.csv` schema from Task 8; `segment_manifest.csv` from Task 8.
- Produces (all pure except `fetch_series`):
  - `slo_violation_rate(reqs: pd.DataFrame, ttft_target_s: float, tpot_target_s: float) -> dict` — `{"ttft": float, "tpot": float, "either": float}` violation fractions, plus percentiles `p50/p95/p99` per metric in the same dict under `"ttft_percentiles"` / `"tpot_percentiles"`.
  - `replica_seconds(ts: list[float], vals: list[float]) -> float` — trapezoidal area under replica curve (`ts` seconds since run start).
  - `overshoot(ts, vals, manifest: pd.DataFrame, cap_rps: float) -> dict` — `{"max": float, "integral_replica_seconds": float}` vs `R_required(t) = clip(ceil(rate(t)/cap_rps), 1, 6)` from the manifest step function.
  - `scaleout_latency(manifest, ts, vals) -> list[dict]` — per upward segment boundary (`rate` jumps > 20 % over previous active segment): `{"segment_idx", "load_step_epoch", "first_ready_epoch", "latency_s"}`; `latency_s` is `None` if replicas never rose.
  - `thrash(ts, vals, window_s: float) -> dict` — `{"scale_events_per_min": float, "direction_reversals": float}`.
  - `fetch_series(base_url: str, query: str, start_epoch: float, end_epoch: float, step_s: int = 15) -> tuple[list[float], list[float]]` — thin Prometheus `query_range` client (timestamps re-based to seconds since `start_epoch`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_extract_metrics.py
import pandas as pd
from src.extract_metrics import (slo_violation_rate, replica_seconds, overshoot,
                                 scaleout_latency, thrash)

def test_slo_violation_rate():
    reqs = pd.DataFrame({"ttft_s": [0.1, 0.2, 3.0], "tpot_s": [0.05, 0.05, 0.4],
                         "e2e_s": [1, 1, 10], "output_tokens": [16, 16, 16]})
    r = slo_violation_rate(reqs, ttft_target_s=1.0, tpot_target_s=0.1)
    assert abs(r["ttft"] - 1/3) < 1e-9 and abs(r["tpot"] - 1/3) < 1e-9
    assert abs(r["either"] - 1/3) < 1e-9
    assert r["ttft_percentiles"]["p99"] >= r["ttft_percentiles"]["p95"]

def test_replica_seconds_trapezoid():
    assert abs(replica_seconds([0, 60, 120], [2, 4, 4]) - (60*3 + 60*4)) < 1e-9

def test_overshoot_against_required():
    ts, vals = [0, 300, 600], [2, 6, 4]
    man = pd.DataFrame([{"idx": 0, "label": "x", "rate": 8.0, "prompt_tokens": 256,
                         "output_tokens": 128, "start_epoch": 0, "end_epoch": 300},
                        {"idx": 1, "label": "y", "rate": 2.0, "prompt_tokens": 256,
                         "output_tokens": 128, "start_epoch": 300, "end_epoch": 600}])
    o = overshoot(ts, vals, man, cap_rps=4.0)   # R_req: ceil(8/4)=2, ceil(2/4)=1
    assert o["max"] == 5.0                       # 6 observed vs 1 required
    assert o["integral_replica_seconds"] > 0

def test_scaleout_latency_detects_and_misses():
    man = pd.DataFrame([{"idx": 0, "label": "a", "rate": 2.0, "start_epoch": 0,   "end_epoch": 180,
                         "prompt_tokens": 256, "output_tokens": 128},
                        {"idx": 1, "label": "b", "rate": 8.0, "start_epoch": 180, "end_epoch": 270,
                         "prompt_tokens": 256, "output_tokens": 128}])
    rose = scaleout_latency(man, [0, 100, 200, 300, 400], [1, 1, 1, 3, 3])
    assert rose[0]["segment_idx"] == 1 and 19 <= rose[0]["latency_s"] <= 21
    never = scaleout_latency(man, [0, 100, 200, 300, 400], [1, 1, 1, 1, 1])
    assert never[0]["latency_s"] is None

def test_thrash_counts_events_and_reversals():
    r = thrash([0, 60, 120, 180, 240], [1, 2, 1, 2, 1], window_s=240)
    assert r["scale_events_per_min"] == 1.0      # 4 changes / 4 min
    assert r["direction_reversals"] == 3         # up,down,up,down
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_extract_metrics.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
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
    changes = [(t, v - p) for t, p, v in zip(ts, vals[1:], vals[1:]) if v != p]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_extract_metrics.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/extract_metrics.py tests/test_extract_metrics.py
git commit -m "feat(src): §6.4 response metrics as tested pure functions"
```

---

### Task 10: ScaledObject generator + frozen config (TDD)

**Files:**
- Create: `experiments/config/frozen.yaml`
- Create: `src/gen_scaledobjects.py`
- Test: `tests/test_gen_scaledobjects.py`
- Create (generated output, committed): `experiments/arms/generated/<arm>-scaledobject.yaml` ×6

**Interfaces:**
- Consumes: `experiments/arms/queries.yaml` (Task 5 schema).
- Produces: `generate_all(frozen_path: str, out_dir: str) -> list[str]` — writes one KEDA `ScaledObject` YAML per arm into `out_dir`; consumed by `repro.sh` (`kubectl apply`). Uniform mechanism: only `name`, `query`, `threshold`, `activationThreshold` differ between arms.

- [ ] **Step 1: Write the frozen config (candidate values, `calibrated: false`)**

```yaml
# experiments/config/frozen.yaml — THE numeric source of truth. Candidate
# values now; Task 11 sets calibrated: true with measured numbers. Any change
# here is a visible git diff on the record.
calibrated: false
sim:                       # starting points; frozen by Task 11
  max_num_seqs: 8
  ttft_base_ms: 200
  itl_base_ms: 40
  time_factor_under_load: 3.0
capacity:
  cap_rps: null            # per-replica request capacity — Task 11
slo:                       # frozen at Task 11; multipliers of low-load baseline
  ttft_baseline_s: null
  tpot_baseline_s: null
  ttft_target_mult: 1.5
  tpot_target_mult: 2.0
thresholds:                # per-arm KEDA thresholds (Task 11 calibrates)
  cpu_cores_per_replica: 0.35
  rps_per_replica: null    # = cap_rps
  queue_per_replica: 5
  kv_frac: 0.70
  ttft_p95_s: null         # = ttft_baseline_s * 1.5
  composite_kv_frac: 0.80  # WVA threshold-OR tau_kv (paper §V-A; repo default)
  composite_queue: 5       # WVA threshold-OR tau_q (paper §V-A; repo default)
  cpu_activation: 0.05
  rps_activation: 0.5
  queue_activation: 1
  kv_activation: 0.1
  ttft_activation: 0.05
headroom:
  node_cpu_limit: 0.70
replicas: {min: 1, max: 6}
composite:                  # reference condition, NOT a treatment arm (spec §6.2)
  role: reference
  excluded_from_ranking: true
  semantics: max_over_triggers   # KEDA multi-trigger max == WVA threshold-OR
  thresholds_source: "WVA paper §V-A (arXiv:2603.09730v2) + llm-d-workload-variant-autoscaler deploy/configmap-saturation-scaling.yaml"
  deviation_note: null      # Task 11: set if a WVA threshold proves inert on this host
timing:                     # spec §6.3 — identical across arms; recorded for
                             # the run fingerprint so every metrics.json carries it
  keda_polling_interval_s: 30
  keda_cooldown_period_s: 300      # scale-to-zero only; inert at min=1
  hpa_sync_period_s: 15            # k3s default, unmodified
  hpa_scale_down_stabilization_s: 300   # the 1→N downscale dampener
  hpa_scale_up_stabilization_s: 0
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_gen_scaledobjects.py
import yaml
from src.gen_scaledobjects import generate_all

def test_generates_six_uniform_arms(tmp_path):
    files = generate_all("experiments/config/frozen.yaml", str(tmp_path))
    assert len(files) == 6
    docs = {f.split("/")[-1]: yaml.safe_load(open(f)) for f in files}
    for name, doc in docs.items():
        spec = doc["spec"]
        assert spec["minReplicaCount"] == 1 and spec["maxReplicaCount"] == 6
        assert spec["cooldownPeriod"] == 300 and spec["pollingInterval"] == 30
        behavior = spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]
        assert behavior["scaleDown"]["stabilizationWindowSeconds"] == 300
        assert behavior["scaleUp"]["stabilizationWindowSeconds"] == 0
        assert spec["scaleTargetRef"]["name"] == "llm-sim"
        trig = spec["triggers"][0]
        assert trig["type"] == "prometheus"
        assert "mon-kube-prometheus-prometheus.monitoring.svc:9090" in trig["metadata"]["serverAddress"]

def test_single_arms_distinct_and_composite_is_threshold_or(tmp_path):
    files = generate_all("experiments/config/frozen.yaml", str(tmp_path))
    docs = [yaml.safe_load(open(f)) for f in files]
    single = [d for d in docs if d["metadata"]["name"] != "sim-composite"]
    assert len(single) == 5
    assert len({d["spec"]["triggers"][0]["metadata"]["query"] for d in single}) == 5
    comp = [d for d in docs if d["metadata"]["name"] == "sim-composite"][0]
    trig = comp["spec"]["triggers"]                 # WVA threshold-OR (spec §6.2):
    assert len(trig) == 2                           # one trigger per signal; KEDA
    assert sorted(float(t["metadata"]["threshold"]) for t in trig) == [0.8, 5.0]  # max = OR
    queries = {t["metadata"]["query"] for t in trig}
    assert any("kv_cache_usage_perc" in q for q in queries)
    assert any("num_requests_waiting" in q for q in queries)

def test_threshold_come_from_frozen(tmp_path):
    files = generate_all("experiments/config/frozen.yaml", str(tmp_path))
    kv = [yaml.safe_load(open(f)) for f in files
          if f.endswith("kv-scaledobject.yaml")][0]
    assert float(kv["spec"]["triggers"][0]["metadata"]["threshold"]) == 0.70
```

Note: `queue_per_replica: 5` must exist in frozen before running (it does, Step 1). Tests that read `rps`/`ttft` thresholds need Task 11's numbers — they are covered by the two tests above only via absence-of-crash; if `rps_per_replica` is still `null`, the generator must raise a clear error naming the missing key (assert that in a third test only after Task 11 fills values; for now `null` thresholds mean those arms cannot be generated yet — acceptable: generator emits only arms whose thresholds are non-null, and Task 12 verifies all 6 exist).

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gen_scaledobjects.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement**

```python
# src/gen_scaledobjects.py — frozen.yaml + queries.yaml -> KEDA ScaledObjects.
# Mechanism uniformity: everything but name/query/threshold/activation is
# constant across arms (spec §1: the signal is the only differing variable).
# Timing semantics (spec §6.3): the 1→N scale-down stabilization is the HPA's
# behavior.scaleDown.stabilizationWindowSeconds (pinned to 300 s), NOT KEDA's
# cooldownPeriod — which only gates scale-to-zero and is inert here because
# minReplicaCount is 1. cooldownPeriod is still set to its default and
# documented as inert.
# Composite arm: TWO triggers (KV + queue), each with its WVA threshold;
# KEDA takes the max over triggers = WVA's threshold-OR (spec §6.2).
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parents[1]
UNIFORM = dict(
    minReplicaCount=1, maxReplicaCount=6, pollingInterval=30, cooldownPeriod=300,
    advanced={"horizontalPodAutoscalerConfig": {"behavior": {
        "scaleDown": {"stabilizationWindowSeconds": 300},
        "scaleUp": {"stabilizationWindowSeconds": 0},
    }}},
)

def _prometheus_trigger(query, threshold, activation):
    return {"type": "prometheus",
            "metadata": {"serverAddress": "http://mon-kube-prometheus-prometheus.monitoring.svc:9090",
                         "query": query, "threshold": str(threshold),
                         "activationThreshold": str(activation)}}

def generate_all(frozen_path, out_dir):
    frozen = yaml.safe_load(Path(frozen_path).read_text())
    queries = yaml.safe_load((HERE / "experiments/arms/queries.yaml").read_text())["arms"]
    th = frozen["thresholds"]
    out = []
    for arm, spec in queries.items():
        trigger_specs = spec.get("triggers") or [spec]   # composite: list; single arms: self
        triggers, ok = [], True
        for t in trigger_specs:
            v = th.get(t["threshold_key"])
            if v is None:
                ok = False; break                        # arm not calibratable yet
            triggers.append(_prometheus_trigger(t["query"], v, th[t["activation_key"]]))
        if not ok:
            continue
        doc = {
            "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
            "metadata": {"name": f"sim-{arm}", "namespace": "serving"},
            "spec": {"scaleTargetRef": {"name": "llm-sim"}, **UNIFORM, "triggers": triggers},
        }
        p = Path(out_dir) / f"{arm}-scaledobject.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(doc, sort_keys=True))
        out.append(str(p))
    return out
```

- [ ] **Step 5: Run tests and generate**

Run: `.venv/bin/python -m pytest tests/test_gen_scaledobjects.py -v && .venv/bin/python -m src.gen_scaledobjects 2>/dev/null || true`
Expected: 3 PASS. Then add a `if __name__ == "__main__": generate_all("experiments/config/frozen.yaml", "experiments/arms/generated")` block to the module, run `.venv/bin/python src/gen_scaledobjects.py`, and confirm 4 files exist (cpu, queue, kv, composite — rps/ttft await calibration).

- [ ] **Step 6: Commit**

```bash
git add experiments/config/frozen.yaml src/gen_scaledobjects.py tests/test_gen_scaledobjects.py experiments/arms/generated/
git commit -m "feat(arms): ScaledObject generator from frozen config; uniform mechanism"
```

---

### Task 11: Calibration, headroom, and freezing (spec §6.5, §7.1.3)

**Files:**
- Create: `experiments/calibrate/calibrate_capacity.sh`
- Create: `experiments/calibrate/headroom.sh`
- Modify: `experiments/config/frozen.yaml` (measured values, `calibrated: true`)
- Create: `results/calibration/` outputs

**Interfaces:**
- Consumes: Tasks 4–10 (sim without any ScaledObject applied, guidellm, `parse_segment`, Prometheus queries).
- Produces: `frozen.yaml` with `cap_rps`, `ttft_baseline_s`, `tpot_baseline_s`, `rps_per_replica`, `ttft_p95_s` filled; regen'd ScaledObjects for all 6 arms; recorded control-plane budget.

- [ ] **Step 1: Capacity calibration script**

```bash
#!/usr/bin/env bash
# experiments/calibrate/calibrate_capacity.sh — single replica, NO ScaledObject.
# Sweep offered rate; find the saturation knee: smallest rate where the queue
# is sustained (waiting > 0 for >= 2 consecutive scrapes). cap_rps is the last
# rate BELOW the knee. Also captures the low-load latency baseline.
set -euo pipefail
PROM=http://localhost:30090/api/v1/query
mkdir -p results/calibration
kubectl -n serving scale deploy/llm-sim --replicas=1
kubectl -n serving delete scaledobject --all 2>/dev/null || true

for FRAC in 0.25 0.50 0.75 1.00 1.25 1.50 1.75 2.00; do
  echo "=== frac=$FRAC ==="
  .venv/bin/guidellm run \
    --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
    --profile kind=constant,rate=$FRAC \
    --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
    --constraint kind=max_duration,seconds=120 \
    --output json path=results/calibration/frac_${FRAC}.json
  sleep 30   # let the queue state reflect the full segment
  curl -sG "$PROM" --data-urlencode \
    'query=max_over_time(vllm:num_requests_waiting{namespace="serving"}[2m])' \
    | tee -a results/calibration/waiting.log
  echo "" >> results/calibration/waiting.log
  sleep 60   # drain before next point
done
```

Rate here is the *absolute* rate; the sweep needs an anchor. Do a 3-minute manual pre-step: run at 2 req/s, 4 req/s; watch `vllm:num_requests_waiting` and TTFT p95. The knee anchor is the first rate where waiting > 0 sustains — call it `K`. Then run the script with `FRAC` values replaced by `K × 0.25 … K × 2.0`. From the logs: `cap_rps` = highest rate whose waiting stays 0; baseline = TTFT/TPOT percentiles from the 0.25-run report (`.venv/bin/python -c "from src.run_phases import parse_segment; df=parse_segment('results/calibration/frac_0.25.json'); print(df.ttft_s.median(), df.tpot_s.median())"` — substitute the lowest rate actually run).

- [ ] **Step 2: Headroom check (pilot-gate item 3, pre-flight)**

```bash
#!/usr/bin/env bash
# experiments/calibrate/headroom.sh — 6 replicas at 1.5x cap load for 10 min:
# node CPU < 70%, no OOM/restarts, no thermal collapse.
set -euo pipefail
PROM=http://localhost:30090/api/v1/query
kubectl -n serving scale deploy/llm-sim --replicas=6
sleep 60
CAP=$(grep cap_rps experiments/config/frozen.yaml | awk '{print $2}')   # after Step 3 fill-in, or pass as $1
RATE=$(python3 -c "print(1.5*float(${CAP:-$1}))")
.venv/bin/guidellm run \
  --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
  --profile kind=constant,rate=$RATE \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --constraint kind=max_duration,seconds=600 \
  --output json path=results/calibration/headroom.json &
LOAD=$!
for i in $(seq 1 10); do
  sleep 60
  curl -sG "$PROM" --data-urlencode \
    'query=1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))' >> results/calibration/node_cpu.log
  echo "" >> results/calibration/node_cpu.log
  grep MHz /proc/cpuinfo | sort | uniq -c >> results/calibration/clock.log
  kubectl -n serving get pods --no-headers | awk '{print $4, $5}' >> results/calibration/pods.log
done
wait $LOAD
kubectl -n serving get events | grep -iE 'oom|kill' || echo "no oom events"
```

Expected: every `node_cpu` sample < 0.70; no `OOMKilled`; CPU MHz in `clock.log` not trending down > 10 % across the hour-long version used in Task 12. **If CPU ≥ 70 %:** spec §7.1 fallback — lower the concurrency ceiling (reduce `cap_rps` anchor, i.e., shrink the whole operating point) and re-run Step 1; do not proceed with a saturated node.

- [ ] **Step 3: Freeze the numbers**

Edit `experiments/config/frozen.yaml`: fill `cap_rps`, `ttft_baseline_s`, `tpot_baseline_s`; set `rps_per_replica: <cap_rps>`, `ttft_p95_s: <baseline × 1.5>` (equals the SLO target — the TTFT arm scales *at* the SLO boundary); confirm multipliers 1.5 / 2.0 as the final SLO definition; set `calibrated: true`. **Composite reference condition:** the WVA thresholds (τ_kv = 0.8, τ_q = 5) are the starting values, already in `frozen.yaml`. Run the Task 6 liveness check against both composite triggers: if either WVA threshold is inert at this operating point (e.g., the KV gauge never approaches 0.8 under peak load), substitute the nearest live value, and record the substitution in `frozen.yaml`'s `composite.deviation_note` and `notes/signal_liveness.md` — the reference condition preserves WVA's OR semantics with internally consistent thresholds. Regenerate: `.venv/bin/python src/gen_scaledobjects.py` — now all **6** ScaledObjects must exist (add the rps/ttft threshold assertions to `tests/test_gen_scaledobjects.py` now that values are non-null; expect PASS).

- [ ] **Step 4: Control-plane budget snapshot**

```bash
docker stats --no-stream > results/calibration/control_plane_idle.txt
kubectl -n serving scale deploy/llm-sim --replicas=6 && sleep 120
docker stats --no-stream > results/calibration/control_plane_peak.txt
```

- [ ] **Step 5: Verify one arm scales end-to-end (smoke)**

```bash
kubectl apply -f experiments/arms/generated/queue-scaledobject.yaml
# drive 2 min at 1.5x cap; replicas should rise from 1
watch -n 5 'kubectl -n serving get pods; curl -sG http://localhost:30090/api/v1/query --data-urlencode "query=sum(vllm:num_requests_waiting{namespace=\"serving\"})"'
kubectl delete -f experiments/arms/generated/queue-scaledobject.yaml
kubectl -n serving scale deploy/llm-sim --replicas=1
```

Expected: HPA created by KEDA (`kubectl get hpa -n serving`), pod count rises while queue builds. Record observed scale-out time informally — Task 12 measures it properly.

- [ ] **Step 6: Commit**

```bash
git add experiments/calibrate/ experiments/config/frozen.yaml experiments/arms/generated/ tests/test_gen_scaledobjects.py
git commit -m "feat(calibration): measured capacity/baselines; frozen config now authoritative"
```

---

### Task 12: One-command repro, collect pipeline, pilot gate

**Files:**
- Create: `src/collect_run.py`
- Create: `experiments/collect_run.sh`
- Create: `experiments/repro.sh`
- Create: `experiments/Makefile`
- Create: `notes/pilot_gate.md`, `notes/schedule.md`

**Interfaces:**
- Consumes: everything above; `frozen.yaml` (`calibrated: true`).
- Produces: `make run ARM=<arm> PATTERN=<pattern> SEED=<n>` → `results/<arm>_<pattern>_seed<n>/` containing `requests.csv`, `segment_manifest.csv`, `raw/*.csv` (Prometheus series), `metrics.json` (all five §6.4 metrics + config fingerprint). This *is* the week-2 batch entry point.

- [ ] **Step 1: Write the collector**

```python
# src/collect_run.py — snapshot raw Prometheus series + compute metrics.json.
import json, sys
from pathlib import Path
import pandas as pd
import yaml
from src.extract_metrics import (fetch_series, slo_violation_rate, replica_seconds,
                                 overshoot, scaleout_latency, thrash)

REPLICAS = 'max(kube_deployment_status_replicas{namespace="serving",deployment="llm-sim"})'
NODE_CPU = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))'

def collect(run_dir, frozen_path="experiments/config/frozen.yaml"):
    frozen = yaml.safe_load(Path(frozen_path).read_text())
    queries = yaml.safe_load(Path("experiments/arms/queries.yaml").read_text())["arms"]
    man = pd.read_csv(Path(run_dir) / "segment_manifest.csv")
    t0, t1 = float(man.start_epoch.min()), float(man.end_epoch.max())
    prom = "http://localhost:30090"
    raw = Path(run_dir) / "raw"; raw.mkdir(exist_ok=True)
    series = {}
    for name, q in [("replicas", REPLICAS), ("node_cpu", NODE_CPU)] + \
                   [(a, s["query"]) for a, s in queries.items()]:
        ts, vs = fetch_series(prom, q, t0, t1)
        series[name] = (ts, vs)
        pd.DataFrame({"t_s": ts, "value": vs}).to_csv(raw / f"{name}.csv", index=False)
    reqs = pd.read_csv(Path(run_dir) / "requests.csv")
    cap = frozen["capacity"]["cap_rps"]; slo = frozen["slo"]
    tt, tp = slo["ttft_baseline_s"] * slo["ttft_target_mult"], slo["tpot_baseline_s"] * slo["tpot_target_mult"]
    rts, rvs = series["replicas"]
    metrics = {
        "slo": slo_violation_rate(reqs, tt, tp),
        "replica_seconds": replica_seconds(rts, rvs),
        "overshoot": overshoot(rts, rvs, man, cap),
        "scaleout": scaleout_latency(man, rts, rvs),
        "thrash": thrash(rts, rvs, window_s=t1 - t0),
        "fingerprint": {"cap_rps": cap, "ttft_target_s": tt, "tpot_target_s": tp,
                        "calibrated": frozen["calibrated"]},
    }
    (Path(run_dir) / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    return metrics

if __name__ == "__main__":
    print(json.dumps(collect(sys.argv[1]), indent=2, default=float))
```

`experiments/collect_run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
.venv/bin/python -m src.collect_run "$1"
```

- [ ] **Step 2: Write repro.sh (the one command)**

```bash
#!/usr/bin/env bash
# experiments/repro.sh — one full run: arm -> schedule -> load -> metrics.
# Usage: repro.sh ARM PATTERN SEED   (assumes cluster+stack up, sim deployed)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
ARM=$1; PATTERN=$2; SEED=$3
RUN=results/${ARM}_${PATTERN}_seed${SEED}

# reset to a clean baseline (<= 5 min, spec §7.3)
kubectl -n serving delete scaledobject --all 2>/dev/null || true
kubectl -n serving scale deploy/llm-sim --replicas=1
for i in $(seq 1 24); do
  W=$(curl -sG http://localhost:30090/api/v1/query \
      --data-urlencode 'query=sum(vllm:num_requests_waiting{namespace="serving"})' \
      | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p')
  [ "${W:-1}" = "0" ] && break; sleep 10
done
kubectl -n serving set env deploy/llm-sim PYTHONHASHSEED=$SEED
kubectl -n serving rollout status deploy/llm-sim --timeout=120s

# arm the autoscaler: the ONLY arm-dependent line
kubectl apply -f experiments/arms/generated/${ARM}-scaledobject.yaml
sleep 60      # let KEDA's first evaluation happen before load starts

.venv/bin/python - "$PATTERN" "$SEED" "$RUN" <<'EOF'
import sys, json
from pathlib import Path
from src.trace_gen import generate, materialize
from src.run_phases import run_schedule
import yaml
pattern, seed, run = sys.argv[1], int(sys.argv[2]), sys.argv[3]
frozen = yaml.safe_load(Path("experiments/config/frozen.yaml").read_text())
sched = materialize(generate(pattern, seed), cap_rps=frozen["capacity"]["cap_rps"])
Path(run).mkdir(parents=True, exist_ok=True)
json.dump(sched, open(f"{run}/schedule.json", "w"), indent=2)
run_schedule(sched, run)
EOF

bash experiments/collect_run.sh "$RUN"
echo "done: $RUN/metrics.json"
```

- [ ] **Step 3: Makefile**

```makefile
# experiments/Makefile — the rig's front door (run from repo root: make -C experiments)
up:
	bash experiments/cluster/create_cluster.sh
	bash experiments/cluster/install_stack.sh
	kubectl apply -f experiments/sim/
run:
	bash experiments/repro.sh $(ARM) $(PATTERN) $(SEED)
reset:
	-kubectl -n serving delete scaledobject --all
	kubectl -n serving scale deploy/llm-sim --replicas=1
down:
	k3d cluster delete sigscale
```

- [ ] **Step 4: End-to-end verification run**

```bash
bash experiments/cluster/create_cluster.sh && bash experiments/cluster/install_stack.sh && kubectl apply -f experiments/sim/
.venv/bin/python src/gen_scaledobjects.py
make -C experiments run ARM=queue PATTERN=spike SEED=1
```

Expected: completes in ~20 min ±20 %; `results/queue_spike_seed1/metrics.json` contains non-null `slo`, `replica_seconds`, `overshoot`, `thrash`, and ≥1 `scaleout` event with a numeric `latency_s` (the spike pattern guarantees an upward step); `raw/replicas.csv` shows ≥1 scale event. This is pilot-gate item 1.

- [ ] **Step 5: Pilot gate (spec §7.1) and record**

Run the checklist, recording numbers in `notes/pilot_gate.md`:

1. E2E run clean (Step 4 output pasted).
2. Durations: `results/*/segment_manifest.csv` spans vs. estimates (ramp/spike ≈ 20 min, longctx ≈ 20 min, diurnal ≈ 30 min) — all within ±20 %.
3. Node CPU < 70 % at 6 replicas under spike (Task 11 `headroom.sh` rerun with the spike pattern, 10 min).
4. Thermals over a 1 h sustained batch (rerun headroom for 60 min; `clock.log` MHz trend < 10 % decline).
5. No OOM with zram (`swapon --show` shows zram; `kubectl get events -A | grep -i oom` empty).

If any item fails: apply the spec's fallbacks (lower concurrency ceiling / trim order §7.3) and record the decision — the schedule is frozen **only** on pass. Then write `notes/schedule.md`: the factorial grid (5 signal arms + 1 composite reference × 4 patterns × 2 seeds = 48 runs), night batch grouping (4 nights × ~12 runs ≈ 11.5 h each per §7.3), and adaptive top-up criteria. The grid must carry two markers verbatim: the **composite arm is a reference condition, `excluded_from_ranking: true`** (spec §6.2/§5 — it never enters single-signal rankings, hypothesis tests, or claims), and **H-TTFT (spec §6.6)** is recorded as a week-3 analysis input — its observables are exactly the per-run `metrics.json` fields `slo` (violation rates) and `thrash`/`overshoot` (loop instability), so week-1 data collection already covers it with no extra instrumentation. This file is the input to the week-2 plan.

- [ ] **Step 6: Commit and tag**

```bash
git add src/collect_run.py experiments/collect_run.sh experiments/repro.sh experiments/Makefile notes/pilot_gate.md notes/schedule.md results/
git commit -m "feat(rig): one-command reproducible run + pilot gate evidence; week-1 complete"
git tag week-1-rig
```

---

## Self-Review (run before handoff)

**Spec coverage:** §6.1 rig table → Tasks 2–5 (k3d, sim, KEDA, Prometheus, Guidellm, sequential host); §6.2 arms + control-loop roles → Tasks 5 (role comments), 10; queue-arm semantic equivalence → Task 5 `queries.yaml` + spec §6.2 text; §6.3 fixed params incl. the corrected autoscaler-timing block (HPA `behavior` pin, inert `cooldownPeriod`) → Task 10 `UNIFORM`/`frozen.yaml` timing block; §6.4 metrics → Task 9 (+ Task 12 wiring); §6.5 calibration → Task 11; §6.6 H-TTFT feedback hypothesis → Task 12 Step 5 schedule markers (observables = `slo` + `thrash`/`overshoot`); §7.1 pilot gate → Task 12 Step 5; §7.2 seeding → schedule seeds + PYTHONHASHSEED (+guidellm `--seed` if it exists, Task 8 Step 1); §7.3 reset budget → repro.sh reset loop; §10 artifact layout → File Structure; §12 signal-missing fallback → Task 6 Step 2. Composite reference condition (excluded from ranking) → `frozen.yaml` `composite:` block + Task 12 Step 5 schedule markers. Weeks 2–4 explicitly out of scope (header note) — their plans follow the pilot gate.

**Placeholder scan:** none — all thresholds live in versioned `frozen.yaml` with explicit null-until-calibrated semantics enforced by the generator; all code blocks are complete.

**Type consistency:** `generate/materialize` (Task 7) ↔ `run_schedule` input (Task 8) ✓; `requests.csv` columns (Task 8) ↔ `slo_violation_rate` expectations (Task 9) ✓; `queries.yaml` schema (Task 5) ↔ `gen_scaledobjects` + `collect_run` readers ✓; `segment_manifest.csv` columns written by `run_schedule` ↔ `overshoot`/`scaleout_latency` usage ✓.
