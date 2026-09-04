#!/usr/bin/env bash
# experiments/arms/check_signals.sh — drive 90 s of moderate load, then show
# idle-vs-load values for every arm query. Human inspects; numbers recorded.
# yq is not installed on this host; queries are extracted from queries.yaml
# (still the single source of truth) via the venv's python + PyYAML.
set -euo pipefail
PROM=http://localhost:30090/api/v1/query
q() { curl -sG "$PROM" --data-urlencode "query=$1" | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p'; }

probe() {
  for a in cpu rps queue kv ttft; do
    QUERY=$(.venv/bin/python -c "import yaml; print(yaml.safe_load(open('experiments/arms/queries.yaml'))['arms']['$a']['query'])")
    echo "$a: $(q "$QUERY")"
  done
}

echo "== idle =="
probe
echo "== driving 90 s at 5 req/s =="
.venv/bin/guidellm run \
  --backend kind=openai_http,target=http://localhost:30080/v1,model=dummy-model \
  --profile kind=constant,rate=5 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128 \
  --constraint kind=max_duration,seconds=90 \
  --tokenizer kind=hf_auto,model=openai-community/gpt2 \
  --output kind=json,path=/tmp/liveness_load.json >/dev/null 2>&1 \
  && echo "load driver exited 0" || echo "WARNING: load driver FAILED (exit $?)"
echo "== under/just-after load =="
probe
