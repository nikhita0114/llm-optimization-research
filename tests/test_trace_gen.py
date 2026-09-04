# tests/test_trace_gen.py
from src.trace_gen import generate, materialize, PATTERNS

def test_patterns_exist_and_shapes():
    assert set(PATTERNS) == {"ramp", "spike", "diurnal", "longctx"}
    for p in PATTERNS:
        s = generate(p, seed=1)
        assert s["pattern"] == p and s["seed"] == 1
        assert 1000 <= sum(x["duration_s"] for x in s["segments"]) <= 1900  # ~17-30 min
        for x in s["segments"]:
            assert x["rate_frac"] > 0 and x["prompt_tokens"] > 0 and x["output_tokens"] > 0

def test_ramp_is_monotonic():
    rates = [x["rate_frac"] for x in generate("ramp", seed=42)["segments"]]
    assert all(b >= a * 0.9 for a, b in zip(rates, rates[1:]))  # jitter-tolerant
    assert rates[-1] / rates[0] >= 5                                            # wide sweep

def test_spike_has_bursts_and_gaps():
    s = generate("spike", seed=1)["segments"]
    labels = [x["label"] for x in s]
    assert labels.count("burst") >= 3 and labels.count("baseline") >= 3
    burst = max(x["rate_frac"] for x in s if x["label"] == "burst")
    base = min(x["rate_frac"] for x in s if x["label"] == "baseline")
    assert burst / base >= 3

def test_diurnal_oscillates():
    r = [x["rate_frac"] for x in generate("diurnal", seed=3)["segments"]]
    peaks = sum(1 for i in range(1, len(r) - 1) if r[i] > r[i-1] and r[i] > r[i+1])
    assert peaks >= 3                      # 3-4 oscillations per spec
    assert 0.05 < min(r) and max(r) <= 1.6

def test_longctx_shifts_context_mix():
    s = generate("longctx", seed=7)["segments"]
    burst = [x for x in s if x["label"] == "burst"][0]
    base = [x for x in s if x["label"] == "baseline"][0]
    assert burst["prompt_tokens"] > 4 * base["prompt_tokens"]
    assert burst["output_tokens"] > 2 * base["output_tokens"]

def test_seeding_deterministic_and_sensitive():
    assert generate("spike", seed=5) == generate("spike", seed=5)
    assert generate("spike", seed=5) != generate("spike", seed=6)

def test_materialize_converts_to_rps():
    s = materialize(generate("ramp", seed=1), cap_rps=4.0)
    assert s["cap_rps"] == 4.0
    for x in s["segments"]:
        assert abs(x["rate"] - round(x["rate_frac"] * 4.0, 3)) < 1e-9
