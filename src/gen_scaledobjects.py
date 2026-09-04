# src/gen_scaledobjects.py — frozen.yaml + queries.yaml -> KEDA ScaledObjects.
# Mechanism uniformity: everything but name/query/threshold/activation is
# constant across arms (spec §1: the signal is the only differing variable).
# Timing semantics (spec §6.3): the 1→N scale-down stabilization is the HPA's
# behavior.scaleDown.stabilizationWindowSeconds (pinned to 300 s), NOT KEDA's
# cooldownPeriod — which only gates scale-to-zero and is inert here because
# minReplicaCount is 1. cooldownPeriod is still set to its default and
# documented as inert.
# Composite arm: TWO triggers (KV + queue), each with its WVA threshold;
# KEDA takes the max over triggers = WVA's threshold-OR (spec §6.2).
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parents[1]
UNIFORM = dict(
    minReplicaCount=1, maxReplicaCount=6, pollingInterval=30, cooldownPeriod=300,
    advanced={"horizontalPodAutoscalerConfig": {"behavior": {
        "scaleDown": {"stabilizationWindowSeconds": 300},
        "scaleUp": {"stabilizationWindowSeconds": 0},
    }}},
)

def _prometheus_trigger(query, threshold, activation):
    return {"type": "prometheus",
            "metadata": {"serverAddress": "http://mon-kube-prometheus-prometheus.monitoring.svc:9090",
                         "query": query, "threshold": str(threshold),
                         "activationThreshold": str(activation)}}

def generate_all(frozen_path, out_dir):
    frozen = yaml.safe_load(Path(frozen_path).read_text())
    queries = yaml.safe_load((HERE / "experiments/arms/queries.yaml").read_text())["arms"]
    th = frozen["thresholds"]
    out = []
    for arm, spec in queries.items():
        trigger_specs = spec.get("triggers") or [spec]   # composite: list; single arms: self
        triggers, ok = [], True
        for t in trigger_specs:
            v = th.get(t["threshold_key"])
            if v is None:
                ok = False; break                        # arm not calibratable yet
            triggers.append(_prometheus_trigger(t["query"], v, th[t["activation_key"]]))
        if not ok:
            continue
        doc = {
            "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
            "metadata": {"name": f"sim-{arm}", "namespace": "serving"},
            "spec": {"scaleTargetRef": {"name": "llm-sim"}, **UNIFORM, "triggers": triggers},
        }
        p = Path(out_dir) / f"{arm}-scaledobject.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(doc, sort_keys=True))
        out.append(str(p))
    return out

if __name__ == "__main__":
    for f in generate_all(str(HERE / "experiments/config/frozen.yaml"),
                          str(HERE / "experiments/arms/generated")):
        print(f)
