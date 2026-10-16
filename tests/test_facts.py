"""Key facts: rankings ignore mostly-failed pipelines, capacity and break-even use the cost basis."""

from ocrbench import facts


def _agg(pipe, cov, cost, lat, acc=0.9, v="clean"):
    return {
        "pipeline": pipe,
        "variant": v,
        "coverage": cov,
        "field_accuracy": acc,
        "cost_per_1k_docs_usd": cost,
        "end_to_end_latency_s": lat,
    }


def test_cheapest_and_fastest_need_coverage():
    ea = [_agg("good", 1.0, 5.0, 10.0), _agg("broken", 0.2, 0.5, 1.0, acc=0.2), _agg("free", 1.0, None, 20.0)]
    p = facts.pipeline_facts(ea)["clean"]
    assert p["cheapest"]["pipeline"] == "good" and p["fastest"]["pipeline"] == "good"
    assert p["low_coverage"] == ["broken"] and not p["coverage_fallback"] and p["unpriced"] == ["free"]
    md = "\n".join(facts._md_winners({"clean": p}, {}))
    assert "**Coverage note:** `broken`" in md and "99%" in md
    # none qualifies: compare all, and say so
    p = facts.pipeline_facts([_agg("a", 0.5, 2.0, 3.0), _agg("b", 0.9, 1.0, 4.0)])["clean"]
    assert p["coverage_fallback"] and p["cheapest"]["pipeline"] == "b" and p["fastest"]["pipeline"] == "a"


def test_threshold_comes_from_scores():
    scores = {
        "speed": [],
        "method": {"ranking_min_coverage": 0.5, "cost_basis": {}},
        "transcription": {"aggregate": []},
        "extraction": {"aggregate": [_agg("a", 0.6, 1.0, 1.0), _agg("b", 1.0, 2.0, 2.0)], "docs": []},
    }
    f = facts.compute({"backends": {}}, scores)
    assert f["pipelines"]["clean"]["cheapest"]["pipeline"] == "a"


def _speed(backend, conc, ppm, errors=0, cost=None, p50=1.0):
    return {
        "backend": backend,
        "concurrency": conc,
        "pages": 100,
        "errors": errors,
        "pages_per_min": ppm,
        "p50_s": p50,
        "cost_per_1k_pages_usd": cost,
    }


def test_capacity_and_break_even_use_the_cost_basis():
    meta = {"backends": {"gpu": {"kind": "selfhosted", "price_per_hour_usd": 1.0}, "llm": {"kind": "bedrock"}}}
    sp = [
        _speed("gpu", 1, 10, cost=1.6),
        _speed("gpu", 4, 20, cost=0.8),
        _speed("gpu", 32, 60, errors=30, cost=0.3),  # fastest, but error-prone: not the capacity
        _speed("llm", 1, 5, cost=5.0),  # concurrency-1 row
        _speed("llm", 16, 50, cost=1.0),
    ]
    basis = {
        "gpu": {"cost_per_1k_pages_usd": 0.8, "concurrency": 4, "meets_error_threshold": True, "basis": "x"},
        "llm": {"cost_per_1k_pages_usd": 3.0, "basis": "mean"},
    }
    tp = facts.throughput_facts(sp, basis)
    assert tp["gpu"]["max_pages_per_min"] == 20 and tp["gpu"]["at_concurrency"] == 4
    assert tp["llm"]["max_pages_per_min"] == 50  # token-priced: unchanged
    sh = facts.selfhosted_facts(meta, sp, tp, basis)["gpu"]
    assert sh["capacity_pages_per_hour"] == 1200
    be = sh["break_even"]["llm"]
    assert be["llm_cost_per_1k_pages_usd"] == 3.0 and be["cheaper_above_pages_per_hour"] == 333  # not $5 of c=1
    assert be["reachable_on_one_gpu"]


def test_break_even_beyond_one_gpu_is_not_claimed():
    meta = {"backends": {"gpu": {"kind": "selfhosted", "price_per_hour_usd": 10.0}, "llm": {"kind": "bedrock"}}}
    sp = [_speed("gpu", 4, 10, cost=16.7), _speed("llm", 1, 5, cost=1.0)]
    basis = {
        "gpu": {"cost_per_1k_pages_usd": 16.7, "concurrency": 4, "basis": "x"},
        "llm": {"cost_per_1k_pages_usd": 1.0},
    }
    sh = facts.selfhosted_facts(meta, sp, facts.throughput_facts(sp, basis), basis)["gpu"]
    assert sh["capacity_pages_per_hour"] == 600 and sh["break_even"]["llm"]["cheaper_above_pages_per_hour"] == 10000
    assert sh["break_even"]["llm"]["reachable_on_one_gpu"] is False
    md = "\n".join(facts._md_selfhosted("gpu", sh))
    assert "is not cheaper than `llm`" in md and "pages/hour** sustained" not in md


def test_unsustained_throughput_is_flagged():
    sp = [_speed("gpu", 4, 10, errors=10)]
    tp = facts.throughput_facts(sp, {"gpu": {"concurrency": 4, "meets_error_threshold": False}})
    assert tp["gpu"]["meets_error_threshold"] is False
    assert "no run within 1% errors" in "\n".join(facts._md_readers({}, tp))


def test_extraction_only_run_has_no_reader_tables():
    """A run with only image->X pipelines transcribed nothing: no empty reader tables for the summary to read."""
    from ocrbench import facts

    assert facts._md_readers({}, {}) == []
    out = facts._md_readers(
        {
            "b": {
                "clean": {
                    "cer_median": 0.01,
                    "number_recall": 0.99,
                    "invented_numbers_excl_runaway": 1,
                    "runaway_pages": 0,
                    "token_limit_pages": 0,
                }
            }
        },
        {},
    )
    assert out[1].startswith("| reader | variant") and not any("p50 s/page" in x for x in out)
