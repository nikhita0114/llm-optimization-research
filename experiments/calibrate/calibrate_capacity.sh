#!/usr/bin/env bash
# experiments/calibrate/calibrate_capacity.sh — single replica, NO ScaledObject.
# Sweep offered rate around the knee anchor K ($1, default 0.5): for each
# K*FRAC, drive 120 s, then read max waiting over the window. cap_rps is the
# highest rate whose waiting stays 0 (Task 11 Step 1 analysis).
# guidellm 0.7.3 syntax (notes/environment.md): kind=json,path= + pinned
# client tokenizer.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
K=${1:-0.5}
PROM=http://localhost:30090/api/v1/query
mkdir -p results/calibration
kubectl -n serving delete scaledobject --all 2>/dev/null || true
kubectl -n serving scale deploy/llm-sim --replicas=1

for FRAC in 0.25 0.50 0.75 1.00 1.25 1.50 1.75 2.00; do
  RATE=$(python3 -c "print(round($K*$FRAC, 3))")
  echo "=== frac=$FRAC rate=$RATE ==="
  .venv/bin/guidellm run \
    --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
    --profile kind=constant,rate=$RATE \
    --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
    --constraint kind=max_duration,seconds=120 \
    --tokenizer kind=hf_auto,model=openai-community/gpt2 \
    --output kind=json,path=results/calibration/frac_${FRAC}.json >/dev/null
  sleep 30   # let the queue state reflect the full segment
  curl -sG "$PROM" --data-urlencode \
    'query=max_over_time(vllm:num_requests_waiting{namespace="serving"}[2m])' \
    | tee -a results/calibration/waiting.log
  echo " rate=$RATE" >> results/calibration/waiting.log
  sleep 60   # drain before next point
done
