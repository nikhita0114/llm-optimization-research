# tests/test_collect_run.py — swap evidence per run (closes the monitoring
# gap found at the pilot gate: headroom/pods logs never sampled swap).
from src.collect_run import swap_snapshot, swap_delta

MEMINFO = """MemTotal:        8000000 kB
SwapTotal:       4194300 kB
SwapFree:        2097100 kB
SwapCached:       204800 kB
"""
VMSTAT = """nr_free_pages 12345
pswpin 621749
pswpout 1173307
pgsteal_kswapd 9786670
"""

def test_swap_snapshot_parses_meminfo_and_vmstat(tmp_path):
    mi, vm = tmp_path / "meminfo", tmp_path / "vmstat"
    mi.write_text(MEMINFO); vm.write_text(VMSTAT)
    s = swap_snapshot(str(mi), str(vm))
    assert s["swap_total_kb"] == 4194300
    assert s["swap_free_kb"] == 2097100
    assert s["swap_cached_kb"] == 204800
    assert s["swap_used_kb"] == 4194300 - 2097100   # used = total - free
    assert s["pswpin"] == 621749 and s["pswpout"] == 1173307

def test_swap_snapshot_tolerates_legacy_vmstat_names(tmp_path):
    mi, vm = tmp_path / "meminfo", tmp_path / "vmstat"
    mi.write_text(MEMINFO)
    vm.write_text("pswp_in 100\npswp_out 200\n")
    s = swap_snapshot(str(mi), str(vm))
    assert s["pswpin"] == 100 and s["pswpout"] == 200

def test_swap_delta_over_run_window():
    start = {"swap_used_kb": 1100000, "pswpin": 600000, "pswpout": 1100000}
    end = {"swap_used_kb": 2400000, "pswpin": 621749, "pswpout": 1173307}
    d = swap_delta(start, end)
    assert d["swap_used_delta_kb"] == 1300000
    assert d["pswpin_delta"] == 21749 and d["pswpout_delta"] == 73307
    assert d["swap_touched"] is True
    assert swap_delta(start, dict(start, pswpin=start["pswpin"], pswpout=start["pswpout"]))["swap_touched"] is False
