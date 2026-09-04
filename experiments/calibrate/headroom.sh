#!/usr/bin/env bash
# experiments/calibrate/headroom.sh — 6 replicas at 1.5x cap load for 10 min:
# node CPU < 70%, no OOM/restarts, no thermal collapse.
# Usage: headroom.sh <cap_rps>
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
CAP=${1:?usage: headroom.sh <cap_rps>}
PROM=http://localhost:30090/api/v1/query
mkdir -p results/calibration
kubectl -n serving delete scaledobject --all 2>/dev/null || true
kubectl -n serving scale deploy/llm-sim --replicas=6
sleep 60
RATE=$(python3 -c "print(1.5*float('$CAP'))")
echo "driving 600 s at $RATE req/s (1.5x cap) with 6 replicas"
.venv/bin/guidellm run \
  --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
  --profile kind=constant,rate=$RATE \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --constraint kind=max_duration,seconds=600 \
  --tokenizer kind=hf_auto,model=openai-community/gpt2 \
  --output kind=json,path=results/calibration/headroom.json >/dev/null &
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
