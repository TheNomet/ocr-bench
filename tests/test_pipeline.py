import json
from pathlib import Path

import pytest

from ocrbench import config as config_mod
from ocrbench.backends import Response
from ocrbench.backends.openai_compat import _der_to_raw
from ocrbench.extract import parse_json
from ocrbench.postprocess import strip_grounding
from ocrbench.score import field_correct, normalise, page_metrics

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    assert cfg.bucket == f"{cfg.prefix}-artifacts"
    assert cfg.selfhosted() == ["unlimited-ocr"]
    assert cfg.endpoint_name("unlimited-ocr") == f"{cfg.prefix}-unlimited-ocr"
    assert cfg.plan("smoke")
    for it in cfg.plan("suite"):
        assert it["backend"] in cfg.backends


def test_env_expansion(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text(
        "name_prefix: abc-${X:-dflt}\naws: {account_id: '1', region: '${R}'}\n"
        "backends: {m: {kind: bedrock, model_id: x}}\nbench: {}\n"
    )
    monkeypatch.setenv("R", "eu-north-1")
    cfg = config_mod.load(p)
    assert cfg.prefix == "abc-dflt" and cfg.region == "eu-north-1"
    monkeypatch.delenv("R")
    with pytest.raises(KeyError):
        config_mod.load(p)


def test_strip_grounding():
    raw = "<|det|>text [1, 2, 3, 4]<|/det|>Hei <|ref|>verden<|/ref|>"
    assert strip_grounding(raw) == "Hei verden"


def test_postprocess_registry():
    from ocrbench import postprocess

    assert postprocess.apply(["strip_think", "unwrap_code_fence"], "<think>hmm</think>```md\nHei\n```") == "Hei"
    assert postprocess.apply([], "<|ref|>x<|/ref|>") == "<|ref|>x<|/ref|>"  # nothing configured: raw output


def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text("name_prefix: abc\naws: {account_id: '1', region: r}\n" + text)
    return p


def test_old_config_shape_is_upgraded(tmp_path):
    p = _write(
        tmp_path,
        "ocr: {name: unlimited, served_model_name: m, request: {}, strip_grounding_tokens: true}\n"
        "llm_backends: {s: {provider: bedrock, model_id: x}}\n"
        "bench: {transcribers: [ocr, s], extraction: {agent: s, pipelines: [ocr->agent]}, plans: {p: [{backend: ocr}]}}\n",
    )
    cfg = config_mod.load(p)
    assert cfg.selfhosted() == ["ocr"]  # the name results always used, whatever ocr.name said
    assert cfg.backend("ocr")["postprocess"] == ["strip_grounding"] and cfg.kind("s") == "bedrock"
    assert "transcribers" not in cfg["bench"]


@pytest.mark.parametrize(
    "body, msg",
    [
        ("backends: {m: {kind: gpu}}\nbench: {}\n", "kind must be one of"),
        ("backends: {My_Model: {kind: selfhosted}}\nbench: {}\n", "names AWS resources"),
        ("backends: {m: {kind: bedrock, postprocess: [nope]}}\nbench: {}\n", "unknown step"),
        ("backends: {m: {kind: bedrock}}\nbench: {plans: {p: [{backend: x}]}}\n", "not defined under backends"),
        ("backends: {m: {kind: bedrock}}\nbench: {extraction: {agent: x}}\n", "is not a backend"),
    ],
)
def test_config_validation(tmp_path, body, msg):
    with pytest.raises(ValueError, match=msg):
        config_mod.load(_write(tmp_path, body))


def test_one_selfhosted(tmp_path):
    p = _write(tmp_path, "backends: {a: {kind: selfhosted}, b: {kind: selfhosted}, c: {kind: bedrock}}\nbench: {}\n")
    cfg = config_mod.load(p)
    assert cfg.one_selfhosted("b") == "b"
    with pytest.raises(SystemExit, match="name one"):
        cfg.one_selfhosted()
    with pytest.raises(SystemExit, match="not a self-hosted"):
        cfg.one_selfhosted("c")


def test_run_json_v1_is_upgraded():
    from ocrbench import prompts, runs

    v1 = {
        "id": "r",
        "ocr": {
            "name": "x",
            "hf_repo": "a/B",
            "served_model_name": "a/B",
            "prompt": "<image>p",
            "price_per_hour_usd": 1.5,
        },
        "llm_backends": {"s": {"provider": "openai_compatible", "model": "m", "price_per_mtok": None}},
    }
    m = runs.upgrade(v1)
    assert m["schema_version"] == runs.SCHEMA_VERSION and "ocr" not in m and "llm_backends" not in m
    assert m["backends"]["ocr"]["kind"] == "selfhosted" and m["backends"]["ocr"]["postprocess"] == ["strip_grounding"]
    assert m["backends"]["ocr"]["request"]["prompt"] == "<image>p" and m["backends"]["s"]["kind"] == "openai_compatible"
    assert m["prompts"] == prompts.DEFAULTS
    assert runs.upgrade(m) is m


def test_describe_instance_fallback():
    from ocrbench.runs import describe_instance

    assert describe_instance("ml.g6e.xlarge") == {"gpu": "NVIDIA L40S, 48 GB", "host": "4 vCPU, 32 GB RAM"}
    assert describe_instance("ml.g5.2xlarge")["host"] == "8 vCPU, 32 GB RAM"


def test_prompts_override_and_unknown_fields():
    from ocrbench import prompts

    p = prompts.resolve({"prompts": {"extract": {"max_tokens": 512}}})
    assert p["extract"]["max_tokens"] == 512 and p["extract"]["system"] == prompts.DEFAULTS["extract"]["system"]
    with pytest.raises(ValueError):
        prompts.resolve({"prompts": {"extract": {"max_token": 512}}})


def test_normalise_and_metrics():
    assert normalise("| a | b |\n|---|---|\n**c**") == "a b c"
    m = page_metrics("Sum 1 250,00 kr", ["1 250,00"], "Sum 1 250,00 kr")
    assert m["cer"] == 0 and m["num_recall"] == 1 and m["num_hallucinated"] == 0
    m = page_metrics("Sum 1 250,00 kr", ["1 250,00"], "Sum 1 260,00 kr")
    assert m["num_recall"] == 0 and m["num_hallucinated"] >= 1


def test_field_correct():
    assert field_correct("15 937,50", "kr 15 937,50")
    assert field_correct(["Bading", "Spa"], ["spa", "Bading"])
    assert not field_correct(["Bading"], ["Bading", "Spa"])
    assert not field_correct("12.07.2026", None)


def test_parse_json():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": "x"} thanks') == {"a": "x"}
    assert parse_json('{"a": 1}\n\nNote: {"b": 2}') == {"a": 1}


def test_der_to_raw():
    r, s = (5).to_bytes(1, "big"), (0x80 | 1).to_bytes(1, "big")
    der = bytes([0x30, 2 + len(r) + 2 + len(s) + 1, 0x02, 1]) + r + bytes([0x02, 2, 0]) + s
    raw = _der_to_raw(der)
    assert len(raw) == 64 and raw[31] == 5 and raw[63] == 0x81


class GroundTruthBackend:
    """Fake transcriber/agent: returns the ground truth (transcribe) or fields (extract)."""

    def __init__(self, name, docs):
        self.name, self.model, self.docs = name, f"fake:{name}", docs
        self.instance_type = None
        self.max_tokens = []

    async def warmup(self):
        pass

    async def complete(self, req):
        self.max_tokens.append(req.max_tokens)
        if req.images:
            img = req.images[0]
            doc_dir = img.parent
            if "extract these fields" in req.prompt:
                return Response(json.dumps(json.loads((doc_dir / "fields.json").read_text())["fields"]), 10, 5)
            n = img.name.split(".")[0]
            return Response((doc_dir / f"{n}.gt.txt").read_text(), 100, 50, "stop")
        doc_id = req.prompt.split("Document type: ")[1].split(" ")[0]
        f = next(self.docs.glob(f"{doc_id}-*/fields.json"))
        return Response(json.dumps(json.loads(f.read_text())["fields"]), 10, 5)


def test_end_to_end_run_folder(fake_docs, tmp_path, monkeypatch):
    """A full run in runs/<id>/: transcribe -> extract -> score -> summarize, then report + experiments index."""
    from ocrbench import cli, extract, runs, summarize, transcribe

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    cfg.raw["runner"]["mode"] = "local"
    # two self-hosted models in one run: nothing about a self-hosted backend's name is special
    cfg.raw["backends"]["other-ocr"] = {**cfg.backend("unlimited-ocr"), "postprocess": []}
    cfg.raw["bench"]["plans"]["t"] = [
        {"backend": "unlimited-ocr", "concurrency": 2},
        {"backend": "other-ocr", "concurrency": 1},
    ]
    cfg.raw["bench"]["extraction"]["pipelines"] = ["unlimited-ocr->agent", "other-ocr->agent", "image->agent"]
    cfg.raw["bench"]["prompts"] = {"extract": {"max_tokens": 1000}}
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    fake = {}

    def get(c, n):
        return fake.setdefault(n, GroundTruthBackend(n, fake_docs))

    monkeypatch.setattr(transcribe, "get_backend", get)
    monkeypatch.setattr(extract, "get_backend", get)

    class Writer(GroundTruthBackend):
        async def complete(self, req):
            assert "## Bottom line" in req.prompt and "# Key facts" in req.prompt and "cheapest pipeline" in req.prompt
            return Response("## Bottom line\nUse image->agent, 1234 is made up.\n\n## Caveats\nx", 900, 40)

    monkeypatch.setattr(summarize, "get_backend", lambda c, n: Writer(n, fake_docs))
    recorded = []

    def record(c, d, name):
        recorded.append(name)
        meta = runs.load(d)
        meta["backends"][name].update(instance_type="ml.g5.xlarge", price_per_hour_usd=1.5, gpu="g", host="h")
        runs.save(d, meta)
        return meta["backends"][name]

    monkeypatch.setattr(runs, "record_endpoint", record)

    class A:
        plan, id, stages, docs = "t", None, "all", str(fake_docs)

    cli.cmd_run(cfg, A())
    (d,) = (tmp_path / "runs").iterdir()
    meta = runs.load(d)
    assert recorded == ["unlimited-ocr", "other-ocr"]
    assert meta["schema_version"] == runs.SCHEMA_VERSION and meta["prompts"]["extract"]["max_tokens"] == 1000
    assert set(meta["stages"]) == {"transcribe", "extract", "score", "summarize"}
    assert max(fake["sonnet-4.5"].max_tokens) == 1000  # the recorded prompt settings were used
    assert (d / "docs" / "manifest.json").exists() and (d / "config.yaml").exists()
    s = json.loads((d / "scores.json").read_text())
    assert all(a["field_accuracy"] == 1 for a in s["extraction"]["aggregate"])
    assert {a["pipeline"] for a in s["extraction"]["aggregate"]} == {
        "unlimited-ocr->agent",
        "other-ocr->agent",
        "image->agent",
    }
    basis = s["method"]["cost_basis"]
    assert "best sustained" in basis["unlimited-ocr"]["basis"] and "best sustained" in basis["other-ocr"]["basis"]
    report = (d / "report.md").read_text()
    for needle in (
        "## Recommendation",
        "LLM-written",
        "## Setup",
        "### GPU",
        "### Pipelines (the arrows)",
        "### Document types",
        "`image->agent`",
        "holiday_survey",
        "## Results",
        "`other-ocr` runs on its own GPU",
        "max 1,000 output tokens",
    ):
        assert needle in report, needle
    index = (tmp_path / "site" / "index.html").read_text()
    assert meta["id"] in index and "Use image-&gt;agent" in index and "input → extractor</th>" in index
    # "Latest result" right under the h1; the guide comes after the experiments list.
    assert '<details class="run-card">' in index and index.index('id="runs"') < index.index('id="guide"')
    assert index.index("<h1>") < index.index('class="latest"') < index.index('id="runs"')
    if 'id="compare"' in index:
        assert index.index('id="compare"') < index.index('id="runs"')
    assert "<h4" not in index and "<caption>Models</caption>" in index and "<h3>GPU" in index
    assert "What is this?" in index and 'id="guide"' in index and "Document types" in index
    assert "extraction agent" in index and "Reads the document type" in index
    run_page = (tmp_path / "site" / meta["id"] / "index.html").read_text()
    assert "Experiments</a>" in run_page and 'id="legend"' not in run_page and "index.html#guide" in run_page

    # resume: only re-summarize
    class B:
        plan, id, stages, docs = None, meta["id"], "summarize", str(fake_docs)

    cli.cmd_run(cfg, B())
    assert "summarize" in runs.load(d)["stages"]


def test_unverified_numbers():
    from ocrbench.summarize import unverified_numbers

    src = "accuracy 98.8% at $7.44/1k docs, ~307 pages/hour, 1 686 pages, CER 0.044"
    assert unverified_numbers("98.8% and 99% and $7.44 and 307 and 1,686 and 1 686 and 0.04", src) == []
    assert unverified_numbers("costs 3x more, 280 pages/hour", src) == ["280", "3"]
    assert unverified_numbers("4 runs, 2 bullets", src) == []  # small counts are not claims
    assert unverified_numbers("98,8 %", src) == []  # Norwegian decimal comma
    assert unverified_numbers("16\n32", "16 and 32") == []  # a newline never joins numbers
    assert unverified_numbers("99.5%", src) == ["99.5"]  # not a rounding of 98.8


def test_key_facts_use_readable_pipeline_labels():
    from ocrbench import view
    from ocrbench.facts import compute, to_md
    from ocrbench.summarize import build_prompt, unverified_numbers

    meta = {
        "id": "r",
        "transcribers": ["ocr"],
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/Some-OCR", "hf_revision": "0123456789abcdef"},
            "agent5": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5", "temperature": 0.0},
        },
        "extraction": {"agent": "agent5", "pipelines": ["image->agent", "ocr->agent"]},
        "documents": {"count": 1, "page_images": 2, "types": {"invoice": {}}},
    }
    agg = [
        {
            "pipeline": p,
            "variant": "clean",
            "field_accuracy": acc,
            "end_to_end_latency_s": 2.5,
            "cost_per_1k_docs_usd": 7.4,
        }
        for p, acc in (("image->agent", 0.97), ("ocr->agent", 0.955))
    ]
    scores = {
        "speed": [],
        "transcription": {"aggregate": [], "pages": []},
        "extraction": {"aggregate": agg, "docs": []},
    }
    md = to_md(compute(meta, scores), view.pipeline_labels(meta))
    assert "page images → Claude Sonnet 5.5 (`image->agent`): 97.0%" in md
    assert "| Some-OCR text → Claude Sonnet 5.5 (`ocr->agent`) | 95.5%" in md
    assert "| `image->agent` |" in to_md(compute(meta, scores))  # without labels: the raw key, as before
    prompt, source = build_prompt(meta, scores)
    assert "page images → Claude Sonnet 5.5" in prompt
    # the recommendation may quote raw keys (with their digits) and the facts' numbers
    rec = "Choose `image->agent` (97.0%), not `ocr->agent` (95.5%); Claude Sonnet 5.5 is 2.5 s/doc."
    assert unverified_numbers(rec, source) == []
    assert unverified_numbers("`image->agent` reaches 98.1%", source) == ["98.1"]


def test_temperature_override():
    from ocrbench.backends.base import Backend, Request

    b = Backend()
    assert b.temperature(Request("s", "p")) == 0.0
    b._init_temperature({"temperature": 1})
    assert b.temperature(Request("s", "p")) == 1
    b2 = Backend()
    b2._init_temperature({"temperature": None})
    assert b2.temperature(Request("s", "p")) is None


def test_named_extractor_pipeline(fake_docs, tmp_path, monkeypatch):
    import asyncio

    from ocrbench import extract

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    used = []

    def get(c, n):
        used.append(n)
        return GroundTruthBackend(n, fake_docs)

    monkeypatch.setattr(extract, "get_backend", get)
    meta = {"backends": {}}
    asyncio.run(extract.run(cfg, fake_docs, tmp_path, meta=meta, pipelines=["image->agent", "image->haiku-4.5"]))
    recs = [json.loads(x) for x in (tmp_path / "extractions.jsonl").read_text().splitlines()]
    assert {r["agent"] for r in recs} == {cfg["bench"]["extraction"]["agent"], "haiku-4.5"}
    assert sorted(set(used)) == sorted({cfg["bench"]["extraction"]["agent"], "haiku-4.5"})


def test_comparison_matches_on_model_not_key():
    from ocrbench import reporting
    from ocrbench.report import RunCard

    def run(rid, agent_model, acc):
        meta = {
            "id": rid,
            "backends": {
                "o": {
                    "kind": "selfhosted",
                    "hf_repo": "x/ocr",
                    "hf_revision": "abcdef12",
                    "instance_type": "ml.g5.xlarge",
                },
                "a": {"kind": "bedrock", "model": agent_model},
            },
            "extraction": {"agent": "a", "agents": {"image->agent": "a"}},
        }
        scores = {
            "extraction": {"aggregate": [{"pipeline": "image->agent", "variant": "clean", "field_accuracy": acc}]},
            "transcription": {"aggregate": []},
            "speed": [{"backend": "o", "concurrency": 1, "pages_per_min": 9.0}],
        }
        return RunCard(meta=meta, card_meta=meta, scores=scores, headline={})

    c = reporting.comparison(
        [run("r2", "anthropic.claude-sonnet-5-5", 0.99), run("r1", "anthropic.claude-sonnet-4-5-20250929-v1:0", 0.97)]
    )
    md = reporting.comparison_md(c)
    assert "page images → Claude Sonnet 5.5" in md and "page images → Claude Sonnet 4.5 (2025-09-29)" in md
    assert "ml.g5.xlarge" in md and "| ocr |" in md  # self-hosted throughput row, labelled by model


def test_same_model_pipelines_hidden():
    from ocrbench import view

    meta = {
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/Unlimited-OCR"},
            "s": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5"},
            "h": {"kind": "bedrock", "model": "anthropic.claude-haiku-5-5"},
        },
        "extraction": {
            "agent": "s",
            "pipelines": ["s->agent", "h->agent", "image->agent", "ocr->h"],
            "agents": {"s->agent": "s", "h->agent": "s", "image->agent": "s", "ocr->h": "h"},
        },
    }
    assert view.same_model(meta, "s->agent") and not view.same_model(meta, "h->agent")
    assert view.pipeline_label(meta, "ocr->h") == "Unlimited-OCR text → Claude Haiku 5.5"
    meta["backends"]["h"]["label"] = "Haiku (pinned)"
    assert view.pipeline_label(meta, "ocr->h") == "Unlimited-OCR text → Haiku (pinned)"
    agg = [{"pipeline": p, "variant": "clean"} for p in meta["extraction"]["pipelines"]]
    vis = view.visible(meta, {"extraction": {"aggregate": agg, "docs": []}})
    assert [a["pipeline"] for a in vis["extraction"]["aggregate"]] == ["h->agent", "image->agent", "ocr->h"]


def test_focus_hides_repeat_backends_but_not_in_comparison():
    from ocrbench import view

    meta = {
        "id": "r",
        "report": {"hide_backends": ["old"]},
        "transcribers": ["ocr", "old", "new"],
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/ocr"},
            "old": {"kind": "bedrock", "model": "anthropic.claude-sonnet-4-5"},
            "new": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5"},
        },
        "extraction": {
            "agent": "old",
            "pipelines": ["image->agent", "image->new", "ocr->new"],
            "agents": {"image->agent": "old", "image->new": "new", "ocr->new": "new"},
        },
    }
    agg = [{"pipeline": p, "variant": "clean", "field_accuracy": 1.0} for p in meta["extraction"]["pipelines"]]
    tr = [{"backend": b, "variant": "clean", "num_recall": 1.0} for b in ("ocr", "old", "new")]
    scores = {
        "speed": [],
        "transcription": {"aggregate": tr, "pages": []},
        "extraction": {"aggregate": agg, "docs": []},
    }
    fm, v = view.focus(meta, scores)
    assert [a["pipeline"] for a in v["extraction"]["aggregate"]] == ["image->new", "ocr->new"]
    assert [a["backend"] for a in v["transcription"]["aggregate"]] == ["ocr", "new"]
    assert fm["extraction"]["agent"] == "new" and fm["transcribers"] == ["ocr", "new"]
    # the stand-in for the hidden default agent is not called "the agent"
    assert not view.default_agent_shown(fm) and view.default_agent_shown(meta)
    note = view.agent_note({**fm, "prompts": {}})
    assert note.startswith("Extractors: Claude Sonnet 5.5 (`new`") and "(default extractor)" not in note
    roles = {r.key: r.roles for r in view.model_rows(fm)}
    assert view.ROLE_AGENT not in roles["new"] and view.ROLE_EXTRACTOR in roles["new"]
    assert "the extractor reads the page images" in view.pipeline_text(fm, "image->new")
    assert "the extraction agent reads the page images" in view.pipeline_text(meta, "image->agent")
    full = view.visible(meta, scores, focus=False)
    assert len(full["extraction"]["aggregate"]) == 3


def test_bedrock_only_plan_runs_locally_without_selfhosted_pipelines(fake_docs, tmp_path, monkeypatch):
    """The README's no-GPU quick start: example config, runner.mode local, plan bedrock-only."""
    from ocrbench import cli, extract, runs, summarize, transcribe

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    cfg.raw["runner"]["mode"] = "local"
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    fake = {}

    def get(c, n):
        assert c.kind(n) == "bedrock"  # nothing else is called
        return fake.setdefault(n, GroundTruthBackend(n, fake_docs))

    monkeypatch.setattr(transcribe, "get_backend", get)
    monkeypatch.setattr(extract, "get_backend", get)
    monkeypatch.setattr(summarize, "get_backend", lambda c, n: GroundTruthBackend(n, fake_docs))
    monkeypatch.setattr(runs, "record_endpoint", lambda *a: pytest.fail("no endpoint in a Bedrock-only run"))

    class A:
        plan, id, stages, docs = "bedrock-only", None, "transcribe,extract,score", str(fake_docs)

    cli.cmd_run(cfg, A())
    (d,) = (tmp_path / "runs").iterdir()
    meta = runs.load(d)
    assert meta["extraction"]["pipelines"] == ["haiku-4.5->agent", "image->agent"]
    s = json.loads((d / "scores.json").read_text())
    assert all(a["coverage"] == 1 and a["field_accuracy"] == 1 for a in s["extraction"]["aggregate"])
    assert "No self-hosted model in this run" in (d / "report.md").read_text()


def test_throughput_facts_keep_plan_order():
    from ocrbench.facts import throughput_facts

    sp = [
        {"backend": b, "concurrency": 1, "pages_per_min": 1.0, "p50_s": 1.0}
        for b in ("zeta", "alpha", "mid", "beta", "omega")
    ]
    assert list(throughput_facts(sp)) == ["zeta", "alpha", "mid", "beta", "omega"]


def test_unknown_plan_raises_before_creating_a_run(fake_docs, tmp_path, monkeypatch):
    from ocrbench import cli, runs

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    with pytest.raises(KeyError, match="'nope' not defined under bench.plans"):
        cfg.plan("nope")
    with pytest.raises(KeyError):
        runs.create(cfg, "nope", fake_docs)
    assert not (tmp_path / "runs").exists()

    class A:
        plan, id, stages, docs = "nope", None, "all", str(fake_docs)

    with pytest.raises(SystemExit, match="nope"):
        cli.cmd_run(cfg, A())


def test_config_plan_helpers_and_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "name_prefix: abc\naws: {account_id: '1', region: eu-north-1}\n"
        "backends: {a: {kind: bedrock, model_id: x}, w: {kind: bedrock, model_id: y}}\n"
        "bench:\n  extraction: {agent: a, pipelines: []}\n  summary: {agent: null}\n"
        "  docs: {seed: 3}\n  plans: {p: [{backend: a}]}\n"
        "  plan_descriptions: {p: desc}\n  plan_report: {p: {hide_backends: [a]}}\n"
    )
    cfg = config_mod.load(p)
    assert cfg.summary_agent == "a"  # explicit null = not set
    assert cfg.plan_description("p") == "desc" and cfg.plan_description("q") == ""
    assert cfg.plan_report("p") == {"hide_backends": ["a"]} and cfg.plan_report("q") == {}
    assert cfg["bench"]["max_retries"] == 4 and cfg["bench"]["extraction"]["concurrency"] == 8
    assert cfg["bench"]["docs"] == {"seed": 3, "per_type": 6, "scale": 2.0}  # deep-merged, the file wins
    cfg.raw["bench"]["summary"] = {"agent": "w"}
    assert cfg.summary_agent == "w"
    p.write_text("name_prefix: abc\naws: {account_id: '1', region: eu-north-1}\nbackends: {}\n")
    with pytest.raises(ValueError, match="missing top-level key 'bench'"):
        config_mod.load(p)


def test_resumed_extract_uses_the_recorded_extractor(fake_docs, tmp_path, monkeypatch):
    import asyncio

    from ocrbench import extract

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    assert cfg["bench"]["extraction"]["agent"] != "haiku-4.5"
    used = []

    def get(c, n):
        used.append(n)
        return GroundTruthBackend(n, fake_docs)

    monkeypatch.setattr(extract, "get_backend", get)
    meta = {"backends": {}, "extraction": {"agent": "haiku-4.5", "pipelines": ["image->agent"]}}
    asyncio.run(extract.run(cfg, fake_docs, tmp_path, meta=meta, pipelines=["image->agent"]))
    assert used == ["haiku-4.5"]


def test_pipeline_names():
    from ocrbench import view

    assert view.source("image->agent") == "image" and view.source("a-b->c") == "a-b"
    assert view.extractor_key("s", "x->agent") == "s" and view.extractor_key("s", "x->h") == "h"
    meta = {"extraction": {"agent": "s", "agents": {"x->agent": "t"}}}
    assert view.extractor(meta, "x->agent") == "t" and view.extractor(meta, "y->agent") == "s"
    assert view.extractor(meta, "y->h") == "h"


def test_read_jsonl_skips_blank_and_broken_lines(tmp_path, capsys):
    from ocrbench.fileio import read_jsonl

    f = tmp_path / "x.jsonl"
    f.write_text('{"a": 1}\n\n   \n{"a": 2\n[1]\n{"a": 3}\n', encoding="utf-8")
    assert read_jsonl(f) == [{"a": 1}, {"a": 3}]
    err = capsys.readouterr().err
    assert "x.jsonl:4" in err and "x.jsonl:5" in err
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_boto_clients_make_a_single_attempt():
    from ocrbench.backends.bedrock import BedrockBackend

    b = BedrockBackend("b", {"model_id": "m", "region": "eu-north-1"})
    assert b.client.meta.config.retries["total_max_attempts"] == 1


def test_record_endpoint_warns_when_price_is_unknown(tmp_path, monkeypatch, capsys):
    from ocrbench import aws, runs

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    d = tmp_path / "r"
    d.mkdir()
    runs.save(d, {"schema_version": runs.SCHEMA_VERSION, "backends": {"unlimited-ocr": {}}})
    monkeypatch.setattr(aws, "endpoint_info", lambda c, n: {"instance_type": "ml.zz.xlarge", "image_digest": None})
    monkeypatch.setattr(aws, "sagemaker_hourly_usd", lambda r, t: None)
    monkeypatch.setattr(aws, "instance_specs", lambda r, t: {})
    b = runs.record_endpoint(cfg, d, "unlimited-ocr")
    assert b["price_per_hour_usd"] is None and "no hourly price" in capsys.readouterr().err
    monkeypatch.setattr(aws, "endpoint_info", lambda c, n: {})
    runs.record_endpoint(cfg, d, "unlimited-ocr")
    assert "could not read the instance type" in capsys.readouterr().err
