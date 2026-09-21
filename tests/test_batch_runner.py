# tests/test_batch_runner.py
import json
import signal
import subprocess
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
    monkeypatch.setattr(br, "BATCH_LOG", tmp_path / "batch_log")
    log = br.run_plan(_plan(tmp_path, [("queue", "spike", 1), ("cpu", "ramp", 1)]), dry_run=True)
    kinds = {cell: kind for kind, cell, _ in log}
    assert kinds["queue_spike_seed1"] == "SKIP"
    assert kinds["cpu_ramp_seed1"] == "TODO"

def test_run_plan_executes_missing_cell_and_qa_gates_it(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "cluster_healthy", lambda: True)
    monkeypatch.setattr(br, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(br, "BATCH_LOG", tmp_path / "batch_log")
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
    monkeypatch.setattr(br, "BATCH_LOG", tmp_path / "batch_log")
    monkeypatch.setattr(br, "_repro", lambda a, p, s: None)   # never produces metrics
    log = br.run_plan(_plan(tmp_path, [("cpu", "ramp", 1), ("cpu", "spike", 1)]))
    kinds = [k for k, _, _ in log]
    assert kinds == ["RETRY", "FAIL", "ABORT"]                # 1st attempt, retry, abort

def test_repro_survives_cell_timeout(monkeypatch):
    # The load driver is repro.sh's *grandchild* (python run_phases), so a
    # timeout must kill the whole process group: SIGKILLing only bash would
    # orphan it to keep firing at the rig and contaminate the RETRY attempt.
    seen, killed = {}, {}
    class FakePopen:
        # Faithful to the real contract: the timed call raises TimeoutExpired;
        # the reap call afterwards (no timeout, process now SIGKILLed) returns.
        def __init__(self, cmd, **kw):
            seen["cmd"], seen["kw"], self.pid, self.calls = cmd, kw, 4242, 0
        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            seen["reaped"] = True
            return (None, None)
    def no_run(*a, **kw):
        raise AssertionError("_repro must not use subprocess.run: it orphans the load driver")
    monkeypatch.setattr(br.subprocess, "run", no_run)      # fail fast if it regresses
    monkeypatch.setattr(br.subprocess, "Popen", FakePopen)
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killed.update(pgid=pgid, sig=sig))
    assert br._repro("cpu", "ramp", 1) is None                # swallowed, not raised
    assert seen["reaped"]                                      # final communicate() reaped the child
    assert seen["kw"]["start_new_session"] is True            # what makes pid == pgid
    assert (killed["pgid"], killed["sig"]) == (4242, signal.SIGKILL)   # popped pid is the pgid
