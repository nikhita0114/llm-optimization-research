# Week 2 — Factorial Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the frozen 48-run factorial (6 arms × 4 patterns × 2 seeds) unattended overnight via an idempotent, resumable batch runner with a per-run QA gate, and end the week with every cell present and per-cell variance reported (spec §8 row 2 exit criterion).

**Architecture:** The week-1 rig already runs one cell via `make -C experiments run ARM=<arm> PATTERN=<pattern> SEED=<n>`. Week 2 adds three pieces: a QA checker that validates each run's `metrics.json` against the frozen config, an aggregator that builds the cross-run summary + per-cell seed-variance table (the adaptive top-up decision input), and a plan-file-driven batch runner that sequences cells, skips completed ones, retries a failed cell once, and aborts the night if the cluster itself is unhealthy. Nights are one command each; mornings are one QA command plus a commit.

**Tech Stack:** Python 3.12 + pytest + pandas (existing venv), `experiments/repro.sh` (unchanged), TSV batch-plan files, Makefile.

**Spec:** `docs/superpowers/specs/2026-09-02-llm-autoscaling-signals-design.md` (§7.2 seeding, §7.3 wall-clock + trim order, §8 row 2, §6.6 H-TTFT observables). Schedule input: `notes/schedule.md` (frozen at the week-1 pilot gate). Executors read all three.

## Global Constraints

Copied from the spec + frozen week-1 state; every task implicitly includes these:

- **`experiments/config/frozen.yaml` is FROZEN for the whole week.** Any diff to it after Night 1 starts invalidates cross-run comparability. If a rig defect is discovered mid-week: stop-and-rethink (spec §7.1), never silently change the operating point.
- Runs are **strictly sequential** — the batch runner enforces this; no parallel cells ever (2 physical cores).
- **Seeding (spec §7.2):** 2 seeds (1, 2) for every (arm × pattern) cell; a 3rd seed only where seed-CV is high enough to change the ranking narrative; top-ups budgeted at **max 8 runs**, seed 3.
- **Trim order if wall-clock overruns (spec §7.3, pre-committed, in order):** (1) cut adaptive top-ups; (2) cut the composite reference arm to 1 seed; (3) only then reduce diurnal further. Signal arms and workload patterns are **never** cut.
- **The composite arm is a reference condition, `excluded_from_ranking: true`** (spec §6.2/§5) — it appears in summary/variance tables but never in rankings or hypothesis tests.
- Night batches end with `git commit` of batch logs + summaries; raw `seg_*/` and `raw/` stay untracked (gitignore).
- `--kv-cache-size` is patched per pattern by `repro.sh` (baseline 140 / longctx 1024 blocks) — nothing extra to do, but never "simplify" it away.
- Cluster-abort rule: if `kubectl get nodes` shows NotReady or a cell fails twice, abort the night (don't burn remaining cells), record state, investigate in daylight.

## File Structure

```
src/agg_results.py        # check_run (QA gate), aggregate, cell_variance + CLI
src/batch_runner.py       # plan-file runner: skip-completed, retry-once, abort-on-unhealthy
experiments/batches/night1.tsv ... night4.tsv   # frozen run lists (18/6/18/6 cells)
experiments/Makefile      # + night1..4, qa, summary targets
results/summary.csv       # one row per completed run (git-tracked)
results/variance.csv      # per-cell across-seed variance + top-up flags (git-tracked)
results/batch_log/        # per-night runner logs + statuses (git-tracked)
notes/experiment_log.md   # morning entry per night: cells, anomalies, decisions
```

---

### Task 1: `n_requests` in metrics.json (TDD)

The aggregator needs per-run request counts; `metrics.json` currently lacks them.

**Files:**
- Modify: `src/collect_run.py` (collect())
- Test: `tests/test_collect_run.py` (extend)

**Interfaces:**
- Consumes: `collect()` as of commit d182ecb.
- Produces: `metrics["slo"]["n_requests"] == int` — read by `src/agg_results.py` (Task 2).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_collect_run.py`:

```python
def test_metrics_include_request_count():
    # collect() is integration-heavy; test the contract on a loaded
    # metrics.json from the pilot run (real artifact, committed)
    import json
    m = json.load(open("results/queue_spike_seed1/metrics.json"))
    assert m["slo"]["n_requests"] > 0
```

This fails only after a re-collection adds the key — so the test's real target is the implementation line below; verify it fails first with KeyError.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_collect_run.py -q`
Expected: FAIL — `KeyError: 'n_requests'`.

- [ ] **Step 3: Implement**

In `src/collect_run.py`, in `collect()`, change the metrics dict's `"slo"` line:

```python
        "slo": dict(slo_violation_rate(reqs, tt, tp), n_requests=int(len(reqs))),
```

- [ ] **Step 4: Re-collect the pilot run and verify**

Run: `bash experiments/collect_run.sh results/queue_spike_seed1 >/dev/null && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/collect_run.py tests/test_collect_run.py results/queue_spike_seed1/metrics.json
git commit -m "feat(agg): n_requests in per-run metrics"
```

---

### Task 2: QA gate + aggregation + variance (TDD)

**Files:**
- Create: `src/agg_results.py`
- Test: `tests/test_agg_results.py`

**Interfaces:**
- Consumes: `metrics.json` schema (slo incl. `n_requests`, replica_seconds, overshoot, scaleout, thrash, host.swap, fingerprint); `requests.csv`; `raw/replicas.csv`; `experiments/config/frozen.yaml`.
- Produces (all importable by Task 3 and week-3 analysis):
  - `check_run(run_dir: str, frozen: dict) -> dict` — `{"ok": bool, "reasons": list[str], "run": str}`
  - `aggregate(results_dir: str = "results") -> pd.DataFrame` — one row per run dir matching `<arm>_<pattern>_seed<n>` with a QA-passing-agnostic parse (columns listed in the test below; `role` = `"reference"` for composite else `"treatment"`)
  - `cell_variance(df: pd.DataFrame) -> pd.DataFrame` — per (arm, pattern): mean/sd/cv for the three outcome families, `topup_flag`
  - CLI: `python -m src.agg_results qa|summary|variance` (qa exits 1 on any failed run; `summary --out results/summary.csv`, `variance --out results/variance.csv`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agg_results.py
import json
import pandas as pd
import pytest
import yaml
from src.agg_results import check_run, aggregate, cell_variance

FROZEN = yaml.safe_load(open("experiments/config/frozen.yaml"))

def _mk_run(base, arm="queue", pattern="spike", seed="1", **overrides):
    d = base / f"{arm}_{pattern}_seed{seed}"
    d.mkdir(parents=True)
    metrics = {
        "slo": {"ttft": 0.2, "tpot": 0.1, "either": 0.25,
                "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
                "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12},
                "n_requests": 400},
        "replica_seconds": 1800.0,
        "overshoot": {"max": 1.0, "integral_replica_seconds": 240.0},
        "scaleout": [{"segment_idx": 1, "latency_s": 95.3}],
        "thrash": {"scale_events_per_min": 0.15, "direction_reversals": 2.0},
        "host": {"swap": {"end": {}, "delta": {"swap_touched": False}}},
        "fingerprint": {"cap_rps": 0.5, "ttft_target_s": 0.42, "tpot_target_s": 0.082,
                        "calibrated": True},
    }
    metrics.update(overrides)
    (d / "metrics.json").write_text(json.dumps(metrics))
    return d

def test_check_run_passes_a_complete_run(tmp_path):
    d = _mk_run(tmp_path)
    assert check_run(str(d), FROZEN)["ok"] is True

def test_check_run_catches_fingerprint_drift_and_missing_keys(tmp_path):
    d = _mk_run(tmp_path, fingerprint={"cap_rps": 0.25, "calibrated": True})
    r = check_run(str(d), FROZEN)
    assert r["ok"] is False and any("fingerprint" in x for x in r["reasons"])
    d2 = _mk_run(tmp_path, seed="2", slo={"n_requests": 5})   # missing blocks + low n
    r2 = check_run(str(d2), FROZEN)
    assert r2["ok"] is False and len(r2["reasons"]) >= 2

def test_aggregate_rows_and_roles(tmp_path):
    _mk_run(tmp_path, arm="queue", pattern="spike", seed="1")
    _mk_run(tmp_path, arm="queue", pattern="spike", seed="2", slo={"either": 0.9,
               "ttft": 0.5, "tpot": 0.5, "n_requests": 300,
               "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
               "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12}})
    _mk_run(tmp_path, arm="composite", pattern="spike", seed="1")
    df = aggregate(str(tmp_path))
    assert len(df) == 3
    row = df[(df.arm == "queue") & (df.seed == 1)].iloc[0]
    assert row.either_viol == 0.25 and row.n_requests == 400 and row.role == "treatment"
    assert df[df.arm == "composite"].iloc[0].role == "reference"
    assert {"arm", "pattern", "seed", "role", "either_viol", "replica_seconds",
            "overshoot_int", "thrash_events", "n_requests"} <= set(df.columns)

def test_cell_variance_flags_high_cv(tmp_path):
    _mk_run(tmp_path, arm="ttft", pattern="ramp", seed="1")                       # 0.25
    _mk_run(tmp_path, arm="ttft", pattern="ramp", seed="2",
            slo={"either": 0.9, "ttft": 0.5, "tpot": 0.5, "n_requests": 300,
                 "ttft_percentiles": {"p50": 0.3, "p95": 1.2, "p99": 2.0},
                 "tpot_percentiles": {"p50": 0.05, "p95": 0.1, "p99": 0.12}})     # 0.9
    _mk_run(tmp_path, arm="cpu", pattern="ramp", seed="1")
    _mk_run(tmp_path, arm="cpu", pattern="ramp", seed="2")                        # identical
    v = cell_variance(aggregate(str(tmp_path)))
    ttft = v[(v.arm == "ttft")].iloc[0]
    cpu = v[(v.arm == "cpu")].iloc[0]
    assert bool(ttft.topup_flag) is True                        # cv(0.25,0.9) ~ 0.72
    assert bool(cpu.topup_flag) is False
    assert set(v.columns) >= {"arm", "pattern", "n_seeds", "either_mean", "either_cv",
                              "thrash_cv", "topup_flag"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agg_results.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agg_results'`.

- [ ] **Step 3: Implement**

```python
# src/agg_results.py — QA gate + cross-run aggregation for the factorial.
# Composite arm carries role=reference everywhere (spec §6.2/§5): present in
# tables, excluded from rankings/tests.
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

FROZEN_PATH = "experiments/config/frozen.yaml"
ARMS = ("cpu", "rps", "queue", "kv", "ttft", "composite")
PATTERNS = ("ramp", "spike", "diurnal", "longctx")

def check_run(run_dir, frozen):
    reasons = []
    mpath = Path(run_dir) / "metrics.json"
    if not mpath.exists():
        return {"ok": False, "reasons": ["metrics.json missing"], "run": str(run_dir)}
    m = json.loads(mpath.read_text())
    for key in ("slo", "replica_seconds", "overshoot", "scaleout", "thrash", "host", "fingerprint"):
        if key not in m:
            reasons.append(f"missing {key}")
    if not reasons:
        if m["slo"].get("n_requests", 0) < 10:
            reasons.append(f"n_requests={m['slo'].get('n_requests')} < 10")
        if "delta" not in m.get("host", {}).get("swap", {}):
            reasons.append("no swap delta (repro.sh predates run?)")
        fp = m["fingerprint"]
        if fp.get("calibrated") is not True:
            reasons.append("fingerprint not calibrated")
        if fp.get("cap_rps") != frozen["capacity"]["cap_rps"]:
            reasons.append(f"fingerprint cap_rps {fp.get('cap_rps')} != frozen")
    return {"ok": not reasons, "reasons": reasons, "run": str(run_dir)}

def aggregate(results_dir="results"):
    rows = []
    for d in sorted(Path(results_dir).glob("*_*_seed*")):
        parts = d.name.rsplit("_seed", 1)
        if len(parts) != 2 or parts[0].split("_", 1)[0] not in ARMS:
            continue
        arm, pattern = parts[0].split("_", 1)
        if arm not in ARMS or pattern not in PATTERNS:
            continue
        m = json.loads((d / "metrics.json").read_text())
        so = m["scaleout"]
        rows.append({
            "arm": arm, "pattern": pattern, "seed": int(parts[1]),
            "role": "reference" if arm == "composite" else "treatment",
            "either_viol": m["slo"]["either"], "ttft_viol": m["slo"]["ttft"],
            "tpot_viol": m["slo"]["tpot"],
            "ttft_p95": m["slo"]["ttft_percentiles"]["p95"],
            "tpot_p95": m["slo"]["tpot_percentiles"]["p95"],
            "n_requests": m["slo"]["n_requests"],
            "replica_seconds": m["replica_seconds"],
            "overshoot_max": m["overshoot"]["max"],
            "overshoot_int": m["overshoot"]["integral_replica_seconds"],
            "scaleout_n": len(so),
            "scaleout_lat_mean": (float(np.mean([e["latency_s"] for e in so
                              if e["latency_s"] is not None]))
                              if any(e["latency_s"] is not None for e in so) else None),
            "thrash_events": m["thrash"]["scale_events_per_min"],
            "thrash_reversals": m["thrash"]["direction_reversals"],
            "swap_touched": m["host"]["swap"]["delta"]["swap_touched"],
        })
    return pd.DataFrame(rows)

def cell_variance(df):
    out = []
    for (arm, pattern), g in df.groupby(["arm", "pattern"]):
        row = {"arm": arm, "pattern": pattern, "n_seeds": len(g),
               "either_mean": g.either_viol.mean(),
               "either_cv": _cv(g.either_viol), "thrash_cv": _cv(g.thrash_events),
               "replicas_cv": _cv(g.replica_seconds)}
        row["topup_flag"] = bool((row["either_cv"] is not None and row["either_cv"] > 0.5)
                                 or (row["thrash_cv"] is not None and row["thrash_cv"] > 0.5))
        out.append(row)
    return pd.DataFrame(out)

def _cv(s):
    return float(np.std(s) / np.mean(s)) if len(s) > 1 and np.mean(s) > 0 else None

def _qa_all():
    frozen = yaml.safe_load(Path(FROZEN_PATH).read_text())
    failed = False
    for d in sorted(Path("results").glob("*_*_seed*")):
        r = check_run(str(d), frozen)
        print(("PASS " if r["ok"] else "FAIL ") + d.name + (" " + "; ".join(r["reasons"]) if r["reasons"] else ""))
        failed |= not r["ok"]
    return 1 if failed else 0

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "qa"
    if cmd == "qa":
        sys.exit(_qa_all())
    df = aggregate()
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if cmd == "summary":
        df.to_csv(out, index=False) if out else print(df.to_string())
    elif cmd == "variance":
        v = cell_variance(df)
        v.to_csv(out, index=False) if out else print(v.to_string())
    else:
        sys.exit(f"unknown command {cmd!r}: qa | summary | variance")
```

- [ ] **Step 4: Run tests; verify pass**

Run: `.venv/bin/python -m pytest tests/test_agg_results.py -q`
Expected: 4 PASS. Then `.venv/bin/python -m src.agg_results qa` — the pilot run `queue_spike_seed1` **FAILS with "no swap delta"**: it predates `swap_start.json` (commit d182ecb). That is correct gate behavior, and the batch runner's skip logic will auto-rerun it as a full-evidence cell in the Task 4 rehearsal. Its original metrics stay in git history and in `notes/pilot_gate.md`.

- [ ] **Step 5: Commit**

```bash
git add src/agg_results.py tests/test_agg_results.py
git commit -m "feat(agg): run QA gate + summary/variance aggregation"
```

---

### Task 3: Batch runner + night plans + Makefile targets (TDD)

**Files:**
- Create: `src/batch_runner.py`
- Create: `experiments/batches/night1.tsv`, `night2.tsv`, `night3.tsv`, `night4.tsv`
- Modify: `experiments/Makefile`
- Test: `tests/test_batch_runner.py`

**Interfaces:**
- Consumes: `agg_results.check_run` (Task 2); `experiments/repro.sh` (subprocess); `frozen.yaml`.
- Produces:
  - `parse_plan(path: str) -> list[tuple[str, str, int]]` — (arm, pattern, seed) rows from TSV (`#` comments / blank lines ignored).
  - `cell_dir(arm, pattern, seed) -> str` — `results/<arm>_<pattern>_seed<seed>`.
  - `cluster_healthy() -> bool` — `kubectl get nodes` returns a Ready node.
  - `run_plan(plan_path: str, dry_run: bool = False) -> int` — per cell: skip if `check_run` ok; else (dry-run: report) invoke `bash experiments/repro.sh ARM PATTERN SEED`, then `check_run`; one immediate retry on failure; two failures ⇒ abort the night (exit 2). Writes `results/batch_log/<plan-stem>_<UTC timestamp>.log` lines: `SKIP|RUN|PASS|RETRY|FAIL|ABORT cell reason`. Also aborts (exit 3) before any cell if `cluster_healthy()` is False.
  - CLI: `python -m src.batch_runner experiments/batches/night1.tsv [--dry-run]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_batch_runner.py
import json
import yaml
from pathlib import Path
import src.batch_runner as br
from src.agg_results import check_run

FROZEN = yaml.safe_load(open("experiments/config/frozen.yaml"))

def _plan(tmp_path, rows):
    p = tmp_path / "plan.tsv"
    p.write_text("# arm\tpattern\tseed\n" + "".join(f"{a}\t{p}\t{s}\n" for a, p, s in rows))
    return str(p)

def test_parse_plan_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / "p.tsv"
    p.write_text("# comment\n\nqueue\tspike\t1\n  \ncpu\tramp\t2\n")
    assert br.parse_plan(str(p)) == [("queue", "spike", 1), ("cpu", "ramp", 2)]

def test_run_plan_dry_run_marks_skip_and_todo(tmp_path, monkeypatch):
    done = tmp_path / "queue_spike_seed1"
    done.mkdir()
    metrics = json.load(open("results/queue_spike_seed1/metrics.json"))
    (done / "metrics.json").write_text(json.dumps(metrics))   # a real QA-passing cell
    monkeypatch.setattr(br, "cluster_healthy", lambda: True)
    monkeypatch.setattr(br, "RESULTS_DIR", str(tmp_path))     # redirect cell lookup
    log = br.run_plan(_plan(tmp_path, [("queue", "spike", 1), ("cpu", "ramp", 1)]), dry_run=True)
    kinds = {cell: kind for kind, cell, _ in log}
    assert kinds["queue_spike_seed1"] == "SKIP"
    assert kinds["cpu_ramp_seed1"] == "TODO"

def test_run_plan_executes_missing_cell_and_qa_gates_it(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "cluster_healthy", lambda: True)
    monkeypatch.setattr(br, "RESULTS_DIR", str(tmp_path))
    calls = []
    def fake_repro(arm, pattern, seed):
        calls.append((arm, pattern, seed))
        d = tmp_path / f"{arm}_{pattern}_seed{seed}"
        d.mkdir(parents=True, exist_ok=True)
        metrics = json.load(open("results/queue_spike_seed1/metrics.json"))
        (d / "metrics.json").write_text(json.dumps(metrics))
    monkeypatch.setattr(br, "_repro", fake_repro)
    log = br.run_plan(_plan(tmp_path, [("cpu", "ramp", 1)]))
    assert calls == [("cpu", "ramp", 1)]
    assert log[0][0] == "PASS"
    assert check_run(str(tmp_path / "cpu_ramp_seed1"), FROZEN)["ok"] is True

def test_run_plan_aborts_after_double_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "cluster_healthy", lambda: True)
    monkeypatch.setattr(br, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(br, "_repro", lambda a, p, s: None)   # never produces metrics
    log = br.run_plan(_plan(tmp_path, [("cpu", "ramp", 1), ("cpu", "spike", 1)]))
    kinds = [k for k, _, _ in log]
    assert kinds == ["RETRY", "FAIL", "ABORT"]                # 1st attempt, retry, abort
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_batch_runner.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# src/batch_runner.py — sequential overnight batch execution for the factorial.
# Idempotent: completed+QA-passing cells are skipped, so re-running a night
# after an abort resumes where it left off. Cluster-unhealthy => abort before
# burning cells (spec §7.1 stop-and-rethink, not silent degrade).
import subprocess, sys, time
from pathlib import Path
import yaml
from src.agg_results import check_run

HERE = Path(__file__).resolve().parents[1]
RESULTS_DIR = "results"
BATCH_LOG = HERE / "results" / "batch_log"

def parse_plan(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        arm, pattern, seed = line.split("\t")
        rows.append((arm, pattern, int(seed)))
    return rows

def cell_dir(arm, pattern, seed):
    return f"{RESULTS_DIR}/{arm}_{pattern}_seed{seed}"

def cluster_healthy():
    r = subprocess.run(["kubectl", "get", "nodes", "--no-headers"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and " Ready" in r.stdout

def _repro(arm, pattern, seed):
    subprocess.run(["bash", "experiments/repro.sh", arm, pattern, str(seed)],
                   cwd=HERE, check=False)

def run_plan(plan_path, dry_run=False):
    frozen = yaml.safe_load((HERE / "experiments/config/frozen.yaml").read_text())
    log = []
    def emit(kind, cell, reason=""):
        print(f"{kind} {cell} {reason}", flush=True)
        log.append((kind, cell, reason))

    if not cluster_healthy():
        emit("ABORT", "-", "cluster not healthy")
        _write_log(plan_path, log)
        return 3
    for arm, pattern, seed in parse_plan(plan_path):
        cell = f"{arm}_{pattern}_seed{seed}"
        r = check_run(cell_dir(arm, pattern, seed), frozen)
        if r["ok"]:
            emit("SKIP", cell); continue
        if dry_run:
            emit("TODO", cell, "; ".join(r["reasons"])); continue
        for attempt in (1, 2):
            t0 = time.time()
            _repro(arm, pattern, seed)
            r = check_run(cell_dir(arm, pattern, seed), frozen)
            if r["ok"]:
                emit("PASS", cell, f"{(time.time()-t0)/60:.1f}min")
                break
            emit("RETRY" if attempt == 1 else "FAIL", cell, "; ".join(r["reasons"]))
        else:
            emit("ABORT", cell, "two consecutive failures")
            break
    _write_log(plan_path, log)
    return 0 if not any(k in ("FAIL", "ABORT") for k, _, _ in log) else 2

def _write_log(plan_path, log):
    BATCH_LOG.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    p = BATCH_LOG / f"{Path(plan_path).stem}_{stamp}.log"
    p.write_text("\n".join(f"{k}\t{c}\t{r}" for k, c, r in log) + "\n")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    sys.exit(run_plan(args[0], dry_run="--dry-run" in sys.argv))
```

- [ ] **Step 4: Write the four night plans**

`experiments/batches/night1.tsv` (seed 1, non-diurnal; ~7.5 h):

```tsv
# arm	pattern	seed
queue	ramp	1
cpu	ramp	1
rps	ramp	1
kv	ramp	1
ttft	ramp	1
composite	ramp	1
queue	spike	1
cpu	spike	1
rps	spike	1
kv	spike	1
ttft	spike	1
composite	spike	1
queue	longctx	1
cpu	longctx	1
rps	longctx	1
kv	longctx	1
ttft	longctx	1
composite	longctx	1
```

`night2.tsv` (seed 1 diurnal + slack for reruns/top-ups; ~3.5 h):

```tsv
# arm	pattern	seed
queue	diurnal	1
cpu	diurnal	1
rps	diurnal	1
kv	diurnal	1
ttft	diurnal	1
composite	diurnal	1
```

`night3.tsv`: identical to night1 with seed `2`.
`night4.tsv`: identical to night2 with seed `2` (top-up cells get appended after the Task 7 variance review, never reordered before it).

Duration math (from measured schedule sums + ≤5 min reset + collect): night1/3 = 6×(20.3+18.3+18.3) min traces + 18×~6 min overhead ≈ 7.5 h; night2/4 = 6×30.5 + 6×6 ≈ 3.6 h. Seed total ≈ 11.1 h ≈ spec §7.3's 11.5 h.

- [ ] **Step 5: Makefile targets**

Append to `experiments/Makefile`:

```makefile
NIGHTS = night1 night2 night3 night4
$(NIGHTS):
	cd .. && .venv/bin/python -m src.batch_runner experiments/batches/$@.tsv
qa:
	cd .. && .venv/bin/python -m src.agg_results qa
summary:
	cd .. && .venv/bin/python -m src.agg_results summary --out results/summary.csv
	cd .. && .venv/bin/python -m src.agg_results variance --out results/variance.csv
.PHONY: $(NIGHTS) qa summary
```

Note `make -C experiments` chdirs to `experiments/` and the venv lives at the repo root, so every target `cd ..` first; `batch_runner`/`agg_results` resolve `results/` and `experiments/config/frozen.yaml` relative to the repo root.

- [ ] **Step 6: Run tests; verify pass**

Run: `.venv/bin/python -m pytest tests/test_batch_runner.py -q` then the full suite.
Expected: all PASS (30 total: 22 existing + 4 agg + 4 batch).

- [ ] **Step 7: Commit**

```bash
git add src/batch_runner.py tests/test_batch_runner.py experiments/batches/ experiments/Makefile
git commit -m "feat(batch): idempotent night-batch runner + frozen night plans"
```

---

### Task 4: Rehearsal (dry-run + rerun the pilot cell with full evidence)

**Files:** none new; verifies Tasks 1–3 end-to-end.

- [ ] **Step 1: Dry-run night 1**

Run: `.venv/bin/python -m src.batch_runner experiments/batches/night1.tsv --dry-run`
(Call the runner directly — `make --dry-run` means dry-run of make itself, not the runner.)
Expected: 18 `TODO` lines; `queue_spike_seed1` shows the reason `no swap delta (repro.sh predates run?)`; exit 0.

- [ ] **Step 2: Rerun the pilot cell through the runner's subprocess path**

Run: `.venv/bin/python -c "from src.batch_runner import _repro; _repro('queue','spike',1)"` then re-run the dry-run.
Expected: ~20 min; the dry-run now shows `SKIP queue_spike_seed1` + 17 `TODO`. `make -C experiments qa` passes 1 run. Verify the fresh `metrics.json` has `slo.n_requests` and `host.swap.delta`.

- [ ] **Step 3: Commit**

```bash
git add results/queue_spike_seed1/metrics.json results/queue_spike_seed1/requests.csv \
        results/queue_spike_seed1/schedule.json results/queue_spike_seed1/segment_manifest.csv \
        results/queue_spike_seed1/swap_start.json results/batch_log/
git commit -m "feat(batch): rehearsal — pilot cell rerun with full swap evidence via runner"
```

---

### Task 5: Night 1 (seed 1, non-diurnal — 18 cells)

- [ ] **Step 1: Launch (evening)**

Run: `nohup make -C experiments night1 > results/batch_log/night1_console.log 2>&1 &`
Expected: ~7.5 h unattended; aborts on double-failure or cluster fault.

- [ ] **Step 2: Morning QA**

```bash
make -C experiments qa
make -C experiments summary
git add results/ notes/experiment_log.md && git commit -m "data(batch): night1 complete"
```

Append a night-1 entry to `notes/experiment_log.md`: cells passed/failed/retried, any `swap_touched=True` cells, anomalies.

- [ ] **Step 3: Dead-arm check (frozen schedule criterion)**

`.venv/bin/python -c "from src.agg_results import aggregate; df=aggregate(); print(df.groupby('arm').scaleout_n.sum())"`
If any arm shows 0 scale events across ALL its completed runs **and** is not explainable (cpu arm on low-load patterns is expected-static; an all-zero arm is not): STOP — investigate threshold reachability (notes/signal_liveness.md) before Night 2. Do not proceed with a dead arm.

---

### Task 6: Night 2 (seed 1 diurnal) + top-up review

- [ ] **Step 1: Launch + morning QA** — same as Task 5 with `night2`. Seed 1 is now complete: 24 cells.

- [ ] **Step 2: Variance review + top-up decision**

Run: `make -C experiments summary` (writes summary.csv + variance.csv).
Rule (§7.2 "high enough to change the ranking narrative", operationalized): `topup_flag=True` cells get a seed-3 run — **max 8**, chosen by descending `either_cv`, ties broken by `thrash_cv`. Append chosen cells to `experiments/batches/night4.tsv` (never reorder existing lines). If >8 flagged: take the top 8 and record the cut in `notes/experiment_log.md` (trim order (1) applies).
Commit: `git add results/ experiments/batches/night4.tsv notes/experiment_log.md && git commit -m "data(batch): night2 + seed-1 variance review, top-ups chosen"`.

---

### Task 7: Nights 3–4 (seed 2 + top-ups)

- [ ] **Step 1:** Launch `night3`, morning QA + commit (as Task 5).
- [ ] **Step 2:** Launch `night4` (now includes appended top-up cells, seed 3), morning QA + commit.
- [ ] **Step 3:** Full-grid check:

```bash
.venv/bin/python -c "
from src.agg_results import aggregate, cell_variance
df = aggregate()
print(df.groupby(['arm','pattern']).size().unstack(fill_value=0))
assert len(df) >= 48, f'only {len(df)} runs'
assert (df.groupby(['arm','pattern']).size() >= 2).all(), 'cells missing a 2nd seed'
print(cell_variance(df).to_string())"
```

Expected: 48+ runs, every cell ≥2 seeds, variance table prints. This is the §8 week-2 exit criterion.

---

### Task 8: Freeze week-2 outputs

- [ ] **Step 1:** `make -C experiments summary` final; verify `results/summary.csv` (48+ rows) and `results/variance.csv` committed.
- [ ] **Step 2:** Write the week-2 wrap entry in `notes/experiment_log.md`: total wall-clock vs budget, failures/retries, trim decisions (if any), top-ups executed, H-TTFT observable status (`slo` + `thrash`/`overshoot` present for every cell — week 3 consumes them directly).
- [ ] **Step 3: Commit and tag**

```bash
git add results/summary.csv results/variance.csv results/batch_log/ notes/experiment_log.md
git commit -m "data(factorial): 48-cell grid complete with variance; week-2 exit met"
git tag week-2-factorial
git push origin master --tags
```

---

## Self-Review

**Spec coverage:** §7.2 seeding (2 seeds + budgeted top-up ≤8, Task 6 rule) ✓; §7.3 wall-clock (night durations computed from measured sums; trim order verbatim in Global Constraints, executed in Task 6 Step 2) ✓; §8 row 2 exit "all cells present with variance reported" → Task 7 Step 3 assertion + variance.csv ✓; composite reference handling → `role` column, never ranked (week-3 consumes `role`) ✓; H-TTFT observables → already in metrics.json, surfaced in summary.csv columns ✓; daytime-no-cluster analysis input → summary/variance CSVs are cluster-free artifacts ✓; schedule.md quirks (per-pattern KV, null-latency semantics, incomplete-count note) → Global Constraints + summary columns ✓; swap evidence gap → QA gate requires `host.swap.delta` ✓.

**Placeholder scan:** none — every step has concrete commands/code; night plans are complete TSVs.

**Type consistency:** `check_run(run_dir, frozen) -> {"ok","reasons","run"}` used identically in Tasks 2/3 ✓; `cell_dir` format matches `aggregate`'s glob `<arm>_<pattern>_seed<n>` ✓; `run_plan` log tuples `(kind, cell, reason)` match tests ✓; `n_requests` key name consistent between Tasks 1 and 2 ✓; `RESULTS_DIR` monkeypatch seam declared in the Interfaces block ✓.
