#!/usr/bin/env bash
# experiments/repro.sh — one full run: arm -> schedule -> load -> metrics.
# Usage: repro.sh ARM PATTERN SEED   (assumes cluster+stack up, sim deployed)
# Per-pattern KV sizing (frozen.yaml sim.kv_cache_blocks): longctx runs with
# a larger cache so 8-concurrent 2048/512 bursts are admitted (Task 11).
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

# per-run seeds + per-pattern KV size: one rollout carries both
KV=$(.venv/bin/python -c "
import yaml; sim = yaml.safe_load(open('experiments/config/frozen.yaml'))['sim']
print(sim['kv_cache_blocks']['longctx'] if '$PATTERN' == 'longctx' else sim['kv_cache_blocks']['baseline'])")
kubectl -n serving set env deploy/llm-sim PYTHONHASHSEED=$SEED
kubectl -n serving patch deploy llm-sim --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/args\",\"value\":[\"--port=8000\",\"--model=dummy-model\",\"--max-num-seqs=8\",\"--max-model-len=4096\",\"--enable-kvcache\",\"--kv-cache-size=$KV\",\"--time-to-first-token=200ms\",\"--time-to-first-token-std-dev=50ms\",\"--inter-token-latency=40ms\",\"--inter-token-latency-std-dev=10ms\",\"--time-factor-under-load=3.0\",\"--latency-calculator=constant\"]}]" >/dev/null
kubectl -n serving rollout status deploy/llm-sim --timeout=120s

# arm the autoscaler: the ONLY arm-dependent line
kubectl apply -f experiments/arms/generated/${ARM}-scaledobject.yaml
sleep 60      # let KEDA's first evaluation happen before load starts

mkdir -p "$RUN"
.venv/bin/python -m src.collect_run --swap-snapshot "$RUN/swap_start.json"
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
