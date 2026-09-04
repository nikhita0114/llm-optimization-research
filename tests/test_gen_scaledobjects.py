# tests/test_gen_scaledobjects.py
# Pre-calibration, rps_per_replica and ttft_p95_s are null → the generator
# intentionally skips those arms (4 files: cpu, queue, kv, composite). After
# Task 11 freezes numbers, test_all_six_arms_after_calibration (added then)
# asserts exactly 6.
import yaml
from src.gen_scaledobjects import generate_all

FROZEN = "experiments/config/frozen.yaml"
QUERIES = "experiments/arms/queries.yaml"

def _expected_arms():
    frozen = yaml.safe_load(open(FROZEN))
    queries = yaml.safe_load(open(QUERIES))["arms"]
    th = frozen["thresholds"]
    arms = []
    for arm, spec in queries.items():
        trig_specs = spec.get("triggers") or [spec]
        if all(th.get(t["threshold_key"]) is not None for t in trig_specs):
            arms.append(arm)
    return arms

def test_uniform_mechanism_across_generated_arms(tmp_path):
    files = generate_all(FROZEN, str(tmp_path))
    assert len(files) == len(_expected_arms())
    docs = [yaml.safe_load(open(f)) for f in files]
    for doc in docs:
        spec = doc["spec"]
        assert spec["minReplicaCount"] == 1 and spec["maxReplicaCount"] == 6
        assert spec["cooldownPeriod"] == 300 and spec["pollingInterval"] == 30
        behavior = spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]
        assert behavior["scaleDown"]["stabilizationWindowSeconds"] == 300
        assert behavior["scaleUp"]["stabilizationWindowSeconds"] == 0
        assert spec["scaleTargetRef"]["name"] == "llm-sim"
        for trig in spec["triggers"]:
            assert trig["type"] == "prometheus"
            assert "mon-kube-prometheus-prometheus.monitoring.svc:9090" in trig["metadata"]["serverAddress"]

def test_single_arms_distinct_and_composite_is_threshold_or(tmp_path):
    files = generate_all(FROZEN, str(tmp_path))
    docs = [yaml.safe_load(open(f)) for f in files]
    single = [d for d in docs if d["metadata"]["name"] != "sim-composite"]
    assert len(single) == len(_expected_arms()) - 1
    assert len({d["spec"]["triggers"][0]["metadata"]["query"] for d in single}) == len(single)
    comp = [d for d in docs if d["metadata"]["name"] == "sim-composite"][0]
    trig = comp["spec"]["triggers"]                 # WVA threshold-OR (spec §6.2):
    assert len(trig) == 2                           # one trigger per signal; KEDA
    assert sorted(float(t["metadata"]["threshold"]) for t in trig) == [0.8, 5.0]  # max = OR
    queries = {t["metadata"]["query"] for t in trig}
    assert any("kv_cache_usage_perc" in q for q in queries)
    assert any("num_requests_waiting" in q for q in queries)

def test_threshold_come_from_frozen(tmp_path):
    files = generate_all(FROZEN, str(tmp_path))
    kv = [yaml.safe_load(open(f)) for f in files
          if f.endswith("kv-scaledobject.yaml")][0]
    assert float(kv["spec"]["triggers"][0]["metadata"]["threshold"]) == 0.70
