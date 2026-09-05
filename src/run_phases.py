# src/run_phases.py — execute a materialized schedule one segment at a time.
# guidellm has no native time-varying profile; phase segmentation gives us an
# exact offered-load step function (which overshoot's R_required needs) using
# only documented guidellm features.
#
# Report schema (guidellm 0.7.3, verified against
# tests/fixtures/segment_sample.json captured 2026-09-05): per-request records
# live at doc["benchmarks"][0]["requests"]["successful"]. Fields:
# time_to_first_token_ms (float ms), request_latency (float s), output_tokens
# (int). Requests still in flight at the max_duration cutoff are listed under
# "incomplete" and by definition carry no request_latency — they are EXCLUDED
# from the DataFrame (their count stays visible in report.json). At calibrated
# loads (<=1.5x cap, 90-180 s segments) in-flight abandonment is a small tail
# share; the 20 s super-saturated fixture overrepresents it.
# If guidellm's schema changed on upgrade, fix _request_records and the
# extraction lines — never the DataFrame contract.
import json, os, subprocess, time
from pathlib import Path
import pandas as pd

def _request_records(doc):
    return [(r, "successful") for r in doc["benchmarks"][0]["requests"]["successful"]]

def parse_segment(path):
    doc = json.load(open(path))
    rows = []
    for r, status in _request_records(doc):
        ttft = float(r["time_to_first_token_ms"]) / 1000.0
        e2e = float(r["request_latency"])
        outn = int(r["output_tokens"])
        rows.append({"ttft_s": ttft, "e2e_s": e2e,
                     "tpot_s": (e2e - ttft) / max(outn - 1, 1),
                     "output_tokens": outn, "status": status})
    return pd.DataFrame(rows)

def stitch(run_dir):
    run_dir = Path(run_dir)
    labels = {}
    manifest = run_dir / "segment_manifest.csv"
    if manifest.exists():
        mdf = pd.read_csv(manifest)
        labels = dict(zip(mdf.idx, mdf.label))
    frames = []
    for segdir in sorted(Path(run_dir).glob("seg_*")):
        idx = int(segdir.name.split("_")[1])
        df = parse_segment(segdir / "report.json")
        df["segment_idx"] = idx
        df["label"] = labels.get(idx, "")
        frames.append(df)
    out = run_dir / "requests.csv"
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
            # each flag and its value must be SEPARATE argv elements (a
            # combined "--backend kind=..." string makes click parse the
            # option name as "--backend kind" and die with exit 2)
            cmd = [guidellm_bin, "run",
                   "--backend", f"kind=openai_http,target={target_url},model={model}",
                   "--profile", f"kind=constant,rate={s['rate']}",
                   "--data", f"kind=synthetic_text,prompt_tokens={s['prompt_tokens']},output_tokens={s['output_tokens']}",
                   "--constraint", f"kind=max_duration,seconds={s['duration_s']}",
                   # client-side tokenizer only; default resolves to the backend
                   # model name and dies on the HF lookup (Task 6 finding)
                   "--tokenizer", "kind=hf_auto,model=openai-community/gpt2",
                   "--seed", f"kind=static,value={schedule['seed']}",
                   "--output", f"kind=json,path={segdir}/report.json"]
            env = dict(os.environ, HF_HUB_OFFLINE="1")   # tokenizer is cached;
            # online HF-hub checks flake (transient exit 2) mid-batch
            try:
                subprocess.run(cmd, check=True, capture_output=True, env=env)
            except subprocess.CalledProcessError as e:
                tail = (e.stderr or b"")[-2000:].decode(errors="replace")
                raise RuntimeError(f"guidellm failed on segment {s['idx']} "
                                   f"(exit {e.returncode}):\n{tail}") from e
        else:
            time.sleep(s["duration_s"])           # offered-load gap
        manifest.append({k: s[k] for k in
                         ("idx", "label", "rate", "prompt_tokens", "output_tokens")} |
                        {"start_epoch": start, "end_epoch": time.time()})
    mdf = pd.DataFrame(manifest)
    mdf.to_csv(run_dir / "segment_manifest.csv", index=False)
    stitch(str(run_dir))
    return str(run_dir / "segment_manifest.csv")
