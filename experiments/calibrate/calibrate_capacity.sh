#!/usr/bin/env bash
# experiments/calibrate/calibrate_capacity.sh — single replica, NO ScaledObject.
# Drive each offered rate for 120 s from a VERIFIED-clean queue (wait until
# waiting=0 between points — the sim's time-factor-under-load makes saturated
# backlogs cascade and contaminate subsequent points if not fully drained).
# cap_rps = highest rate whose max waiting stays 0.
# Usage: calibrate_capacity.sh [rate ...]   (default clean set below)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
RATES=${@:-"0.125 0.375 0.5 0.625 0.75"}
PROM=http://localhost:30090/api/v1/query
mkdir -p results/calibration
kubectl -n serving delete scaledobject --all 2>/dev/null || true
kubectl -n serving scale deploy/llm-sim --replicas=1

wait_drained() {   # verified drain: poll Prometheus until waiting is 0
  for i in $(seq 1 90); do
    W=$(curl -sG "$PROM" --data-urlencode \
        'query=sum(vllm:num_requests_waiting{namespace="serving"})' \
        | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p')
    [ "${W:-1}" = "0" ] && return 0
    sleep 10
  done
  echo "WARNING: queue never drained (waiting=${W:-?})" >&2
}

wait_drained
for RATE in $RATES; do
  echo "=== rate=$RATE ==="
  .venv/bin/guidellm run \
    --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
    --profile kind=constant,rate=$RATE \
    --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
    --constraint kind=max_duration,seconds=120 \
    --tokenizer kind=hf_auto,model=openai-community/gpt2 \
    --output kind=json,path=results/calibration/clean_${RATE}.json >/dev/null
  sleep 30   # let the queue state reflect the full segment
  curl -sG "$PROM" --data-urlencode \
    'query=max_over_time(vllm:num_requests_waiting{namespace="serving"}[2m])' \
    | tee -a results/calibration/waiting_clean.log
  echo " rate=$RATE" >> results/calibration/waiting_clean.log
  wait_drained   # verified drain before the next point
done
