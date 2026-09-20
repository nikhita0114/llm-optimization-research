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
        return log
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
    return log

def _write_log(plan_path, log):
    BATCH_LOG.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    p = BATCH_LOG / f"{Path(plan_path).stem}_{stamp}.log"
    p.write_text("\n".join(f"{k}\t{c}\t{r}" for k, c, r in log) + "\n")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    log = run_plan(args[0], dry_run="--dry-run" in sys.argv)
    # run_plan returns the log; the CLI maps it to the documented night exit
    # codes: 3 = aborted before any cell (cluster down), 2 = cell failure abort,
    # 0 = every cell skipped or passed.
    pre = any(k == "ABORT" and c == "-" for k, c, _ in log)
    sys.exit(3 if pre else (2 if any(k in ("FAIL", "ABORT") for k, _, _ in log) else 0))
