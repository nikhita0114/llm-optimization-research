# Environment Record

Recorded 2026-09-05 (Task 1, week-1 rig bring-up).

## Tool versions (from `00_install_host.sh` output)

| Tool | Version | Note |
|---|---|---|
| k3d | v5.8.3 | as pinned in script |
| k3s (cluster, k3d default) | v1.31.5-k3s1 | |
| kubectl | v1.35.2 | **pre-existing system install at `/usr/local/bin/kubectl`** — install script skips already-present tools, so the pinned v1.31.0 was not installed. Client 1.35 vs server 1.31 skew tolerated so far; if a verb misbehaves, install v1.31.0 to `~/.local/bin/kubectl` and put it first on PATH. |
| helm | v3.16.2+g13654a5 | as pinned; at `~/.local/bin/helm` |
| guidellm | 0.7.3 | `guidellm[recommended]` pulls torch 2.14.0 + CUDA wheels → `.venv` is **6.0 GiB on disk** (164 GiB free, fine). Watch RAM when guidellm imports torch during runs. |
| Python | 3.12.3 (system) | venv at `.venv/` |
| pandas / numpy | 3.0.5 / 2.3.5 | pandas 3.x — plan code already uses numpy-2 `np.trapezoid` |
| Docker | 29.1.3 (client+server) | pre-existing |

## Host facts

- CPU: Intel Core i7-4600U @ 2.10 GHz, 2 cores / 4 threads, max 3.3 GHz
- RAM: 7.7 GiB total (5.4 GiB in use at idle-desktop, ~2.2 GiB available + 2.9 GiB reclaimable cache)
- Disk: 233 GiB, 164 GiB free
- OS: Ubuntu 24.04.4 LTS, kernel 7.0.0-30-generic
- **Swap: 4 GiB swap *file* (`/swap.img`), NOT zram** — spec §7.1 assumes zram; substitution recorded here. Same anti-OOM purpose; pilot-gate item 5 checks swap presence + no OOM events rather than zram specifically.

## `guidellm run --help` (verbatim, 2026-09-05)

```text
Usage: guidellm run [OPTIONS]

  Run a benchmark against a generative model. Supports multiple backends, data
  sources, strategies, and output formats. Configuration can be loaded from a
  scenario file or specified via options.

Options:
  -c, --config, --scenario [file|[rag|rag.json|chat|chat.json]]
                                  Builtin scenario name or path to config
                                  file. CLI options override scenario
                                  settings.
  -l, --label TEXT                Define a label as a key-value pair for the
                                  run. Example: `--label
                                  timestamp=1999-09-12@12:00:00 --label
                                  env=staging`  [repeatable]
  --backend kind=[openai_http|openai_websocket|vllm_python],...
                                  Backend configuration to define how to send
                                  requests to the model.
  --profile kind=[async|constant|poisson|concurrent|replay|sweep|synchronous|throughput],...
                                  Profile configuration to control benchmark
                                  execution.
  --constraint kind=[max_errors|max_error_rate|max_global_error_rate|max_duration|max_requests|over_saturation],...
                                  Execution constraints to enforce during
                                  benchmark execution  [repeatable]
  --tokenizer kind=[huggingface_auto|hf_auto],...
                                  Tokenizer configuration
  --data kind=[text_file|csv_file|json_file|parquet_file|arrow_file|hdf5_file|db_file|tar_file|huggingface|hf|in_memory_dict_list|in_memory_item_list|synthetic_text|synthetic_image|synthetic_video|trace_synthetic|mooncake],...
                                  List of dataset sources to use in the
                                  benchmarks  [repeatable]
  --data-column-mapper kind=[encode_media|generative_column_mapper|pooling_column_mapper|tool_calling_message_extractor],...
                                  Specify how to map dataset columns into
                                  prompts and outputs.
  --data-preprocessor kind=[encode_media|generative_column_mapper|pooling_column_mapper|tool_calling_message_extractor],...
                                  List of data preprocessors to apply to the
                                  datasets.  [repeatable]
  --data-finalizer kind=[generative],...
                                  Finalizer for preparing data samples into
                                  requests
  --data-loader kind=[pytorch],...
                                  Specify how to load the datasets into
                                  memory.
  --seed kind=[static],...        Random configuration for reproducibility
                                  (e.g., seed value)
  --output kind=[console|csv|html|plot|json|yaml],...
                                  Benchmark output formats and paths.
                                  [repeatable]
  --metrics kind=[generative],...
                                  Configuration for metrics collection and
                                  request sampling.
  --override TEXT...              Define overrides for each sub-benchmark.
                                  Currently this only supports
                                  `profile.streams` or `profile.rate`.
                                  Example: `--profile kind=concurrent
                                  --override 'profile.streams' 1,2,4,8,16`
                                  [repeatable]
  --disable-console, --disable-console-outputs
                                  Disable all outputs to the console (updates,
                                  interactive progress, results).
  --disable-console-interactive, --disable-progress
                                  Disable interactive console progress
                                  updates.
  --help                          Show this message for details.
```

## Cluster baseline (Task 2, 2026-09-05)

- `k3d-sigscale-server-0`: Ready, v1.31.5+k3s1
- Memory at idle cluster (30 s after create): server container **412.7 MiB** (budget < 1.5 GiB ✓), serverlb 7.3 MiB

## Stack versions (Task 3, 2026-09-05)

| Chart | Version | App | Note |
|---|---|---|---|
| kube-prometheus-stack (`mon`, ns `monitoring`) | 89.2.2 | v0.93.1 | slim values; scrape 15 s; NodePort 30090 |
| keda (`keda`) | 2.20.2 | 2.20.2 | **warns: tested on k8s 1.33+, cluster is 1.31** — operator reconciles (1 restart at startup, then stable, all deploys Available). If ScaledObject reconciliation misbehaves in Task 11, pin chart to a 1.31-supported KEDA (≤2.18) and record. |

Verified 2026-09-05: `/-/healthy` OK; `container_cpu_usage_seconds_total` (cAdvisor) and `node_cpu_seconds_total` (node-exporter) both return non-empty results via `localhost:30090`.

**Findings from the help (consumed by Task 8):**
1. A seed option **exists**: `--seed kind=static,...` → `run_schedule` adds `--seed kind=static,seed=<schedule seed>` for reproducibility (spec §7.2).
2. Output `kind=json,path=...` syntax confirmed.
3. `--profile kind=constant,rate=...` confirmed.
