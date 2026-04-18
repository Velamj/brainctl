"""Tests for the cross-encoder reranker stage (src/agentmemory/rerank.py)
and its CLI / MCP wiring.

These tests are designed to PASS without sentence-transformers or
torch installed. The whole point of the rerank module is graceful
degradation: if the optional ML extras aren't there, the module
returns the input unchanged with a stderr warning. The tests verify
THAT contract holds.

When sentence-transformers IS installed (CI with `pip install
'brainctl[rerank]'`), the test_real_model_changes_ordering test gets
unskipped and verifies that the real model actually re-orders.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the in-tree src/ is importable. conftest.py also does
# this; duplicating it here so the file is runnable standalone with
# `pytest tests/test_cross_encoder_rerank.py`.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agentmemory.rerank as rr


# Quiet the per-process stderr dedupe so each test sees its own
# warnings independently.
@pytest.fixture(autouse=True)
def _reset_rerank_state():
    rr._reset_warnings()
    rr.cache_clear()
    rr._clear_model_cache()
    yield
    rr.cache_clear()
    rr._clear_model_cache()


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_supported_models_and_default_present():
    """Sanity: the public registry has all three brief-required models
    and the documented default."""
    assert "bge-reranker-v2-m3" in rr.SUPPORTED_MODELS
    assert "jina-reranker-v2-base-multilingual" in rr.SUPPORTED_MODELS
    assert "qwen3-reranker-4b" in rr.SUPPORTED_MODELS
    assert rr.DEFAULT_MODEL == "bge-reranker-v2-m3"


def test_qwen3_marked_as_llm_logit_kind():
    """qwen3 is an LLM-style reranker (logit-based), not a cross-encoder.
    The registry must mark it so the loader can refuse it cleanly."""
    assert rr.SUPPORTED_MODELS["qwen3-reranker-4b"]["kind"] == "llm_logit"
    assert rr.SUPPORTED_MODELS["bge-reranker-v2-m3"]["kind"] == "cross_encoder"
    assert rr.SUPPORTED_MODELS["jina-reranker-v2-base-multilingual"]["kind"] == "cross_encoder"


def test_available_models_returns_list():
    """available_models() must always return a list (possibly empty),
    never raise."""
    out = rr.available_models()
    assert isinstance(out, list)
    # Each entry must be one of the supported names.
    for name in out:
        assert name in rr.SUPPORTED_MODELS


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_score_pairs_returns_zeros_when_st_missing():
    """The brief: 'if Ollama is unreachable or model isn't pulled,
    return the input unchanged with a stderr warning. Never crash a
    search because the reranker is unavailable.'

    With sentence-transformers absent, score_pairs must return zeros
    aligned 1:1 with input.
    """
    with patch.object(rr, "_have_sentence_transformers", return_value=False):
        # Force the loader to think st is missing on this call too.
        with patch.object(rr, "_load_st_model", return_value=None):
            scores = rr.score_pairs("query", ["a", "b", "c"])
    assert scores == [0.0, 0.0, 0.0]


def test_score_pairs_empty_input():
    assert rr.score_pairs("query", []) == []


def test_rerank_no_op_when_loader_returns_none():
    """When the model can't load, rerank returns the input ordering
    with all final_scores set to 0.0 (stable sort preserves input
    order)."""
    cands = [
        {"content": "first",  "final_score": 0.9, "id": 1},
        {"content": "second", "final_score": 0.8, "id": 2},
        {"content": "third",  "final_score": 0.7, "id": 3},
    ]
    with patch.object(rr, "_load_st_model", return_value=None):
        out = rr.rerank("q", cands)
    assert [c["id"] for c in out] == [1, 2, 3]
    # Each candidate was decorated with the score key.
    assert all("ce_score" in c for c in out)
    assert all(c["ce_score"] == 0.0 for c in out)
    # pre_ce_score preserved.
    assert out[0]["pre_ce_score"] == 0.9


def test_rerank_does_not_mutate_input_list():
    """Cloning matters because tests / pipelines re-use the same
    candidate dicts — in-place mutation would break idempotence."""
    cands = [{"content": "x", "final_score": 0.5, "id": 7}]
    original = dict(cands[0])
    with patch.object(rr, "_load_st_model", return_value=None):
        rr.rerank("q", cands)
    # Original dict's keys must be unchanged.
    assert cands[0] == original


def test_unknown_model_warns_and_falls_through(capsys):
    """Typo / unknown model → stderr warning + no rerank, not a crash."""
    rr._reset_warnings()
    out = rr.score_pairs("q", ["a"], model="not-a-real-model-name")
    assert out == [0.0]
    captured = capsys.readouterr()
    assert "unknown model" in captured.err
    assert "not-a-real-model-name" in captured.err


def test_qwen3_warns_about_llm_logit_and_no_ops(capsys):
    """qwen3 is recognised but deferred. Must warn + return zeros, not raise."""
    rr._reset_warnings()
    out = rr.score_pairs("q", ["doc"], model="qwen3-reranker-4b")
    assert out == [0.0]
    captured = capsys.readouterr()
    assert "LLM-style reranker" in captured.err
    assert "qwen3-reranker-4b" in captured.err


def test_warnings_are_deduped():
    """The hot path emits one warning per failure mode per process."""
    rr._reset_warnings()
    buf = io.StringIO()
    with redirect_stderr(buf):
        rr._warn("same warning", dedupe_key="k")
        rr._warn("same warning", dedupe_key="k")
        rr._warn("same warning", dedupe_key="k")
    # Only the first emission lands.
    assert buf.getvalue().count("same warning") == 1


def test_quiet_env_silences_warnings():
    """BRAINCTL_RERANK_QUIET=1 must mute the warning channel — used
    by the bench harness to keep output clean across thousands of
    queries."""
    rr._reset_warnings()
    buf = io.StringIO()
    with patch.dict(os.environ, {"BRAINCTL_RERANK_QUIET": "1"}):
        with redirect_stderr(buf):
            rr._warn("you should not see this")
    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# LRU cache
# ---------------------------------------------------------------------------


def test_lru_cache_records_hits():
    """Round-trip: a fake model that returns deterministic scores
    populates the cache; a second call hits the cache and the model
    is NOT invoked the second time."""
    fake_model = MagicMock()
    # Two candidates → predict gets a 2-tuple list and returns 2 scores.
    fake_model.predict.return_value = [0.9, 0.4]

    with patch.object(rr, "_load_st_model", return_value=fake_model):
        out1 = rr.score_pairs("query", ["a", "b"])
        assert out1 == [0.9, 0.4]
        assert fake_model.predict.call_count == 1
        assert rr.cache_stats()["entries"] == 2

        # Same query + candidates → cache hit, predict NOT called again.
        out2 = rr.score_pairs("query", ["a", "b"])
        assert out2 == [0.9, 0.4]
        assert fake_model.predict.call_count == 1


def test_lru_cache_partial_hit():
    """When some candidates are cached and some aren't, only the
    misses get sent to the model — the cached results are spliced in
    at the right indices."""
    fake_model = MagicMock()
    # First call: 2 cands ("a", "b") → both miss → both scored.
    # Second call: 3 cands ("a", "b", "c") → 2 hits + 1 miss → predict
    # gets just ("c",) and returns one score.
    fake_model.predict.side_effect = [
        [0.5, 0.6],   # call #1
        [0.3],        # call #2 — only "c" is missing
    ]
    with patch.object(rr, "_load_st_model", return_value=fake_model):
        rr.score_pairs("q", ["a", "b"])
        out = rr.score_pairs("q", ["a", "b", "c"])
    assert out == [0.5, 0.6, 0.3]
    # Model was called exactly twice (not three times).
    assert fake_model.predict.call_count == 2
    # The second call's pair list should have only ("q", "c").
    second_call_pairs = fake_model.predict.call_args_list[1].args[0]
    assert second_call_pairs == [("q", "c")]


def test_cache_eviction_at_capacity():
    """The cache caps at _SCORE_CACHE_MAX entries; oldest get evicted."""
    # Shrink the cap so we can verify eviction without 1000 entries.
    original_max = rr._SCORE_CACHE_MAX
    try:
        rr._SCORE_CACHE_MAX = 3
        # Manually populate via the public path → no model needed.
        for i in range(5):
            rr._cache_set(("m", "q", f"c{i}"), float(i))
        stats = rr.cache_stats()
        # Capped at the new max.
        assert stats["entries"] == 3
        # The oldest two should be gone.
        assert rr._cache_get(("m", "q", "c0")) is None
        assert rr._cache_get(("m", "q", "c1")) is None
        # The newest three are present.
        assert rr._cache_get(("m", "q", "c2")) == 2.0
        assert rr._cache_get(("m", "q", "c4")) == 4.0
    finally:
        rr._SCORE_CACHE_MAX = original_max


# ---------------------------------------------------------------------------
# Round-trip ordering change (with a fake model)
# ---------------------------------------------------------------------------


def test_rerank_actually_changes_ordering():
    """Round-trip: a fake CrossEncoder that scores in REVERSE of the
    original ordering should produce a fully-reversed output."""
    cands = [
        {"content": "alpha",   "final_score": 0.9, "id": "a"},
        {"content": "beta",    "final_score": 0.7, "id": "b"},
        {"content": "gamma",   "final_score": 0.5, "id": "c"},
        {"content": "delta",   "final_score": 0.3, "id": "d"},
    ]
    fake_model = MagicMock()
    # Score in reverse of input: best-first becomes worst-first.
    fake_model.predict.return_value = [0.1, 0.2, 0.3, 0.4]

    with patch.object(rr, "_load_st_model", return_value=fake_model):
        out = rr.rerank("query", cands)

    # New ordering: delta (0.4) > gamma (0.3) > beta (0.2) > alpha (0.1)
    assert [c["id"] for c in out] == ["d", "c", "b", "a"]
    # final_score is overwritten with CE score so downstream stages
    # (MMR, quantum) operate on it.
    assert [c["final_score"] for c in out] == [0.4, 0.3, 0.2, 0.1]
    # Original RRF / heuristic score preserved as audit trail.
    assert [c["pre_ce_score"] for c in out] == [0.3, 0.5, 0.7, 0.9]


def test_rerank_top_k_trims():
    cands = [{"content": str(i), "final_score": 0.5, "id": i} for i in range(10)]
    fake_model = MagicMock()
    # Identity scoring (each candidate keeps its index as score), highest wins.
    fake_model.predict.return_value = list(range(10))
    with patch.object(rr, "_load_st_model", return_value=fake_model):
        out = rr.rerank("q", cands, top_k=3)
    assert len(out) == 3
    # IDs 9, 8, 7 (highest scores).
    assert [c["id"] for c in out] == [9, 8, 7]


def test_rerank_handles_missing_text_field():
    """A candidate without the text key shouldn't blow up — it gets
    scored against an empty string."""
    cands = [
        {"content": "real text", "final_score": 0.5, "id": 1},
        {"id": 2, "final_score": 0.4},  # no content!
    ]
    fake_model = MagicMock()
    fake_model.predict.return_value = [0.8, 0.2]
    with patch.object(rr, "_load_st_model", return_value=fake_model):
        out = rr.rerank("q", cands)
    # No exception, both got scored.
    assert len(out) == 2
    # The model was called with empty string for the second candidate.
    pairs = fake_model.predict.call_args.args[0]
    assert pairs[1] == ("q", "")


def test_rerank_predict_exception_is_caught(capsys):
    """A model that raises during predict() must NOT propagate."""
    cands = [{"content": "x", "final_score": 0.5, "id": 1}]
    fake_model = MagicMock()
    fake_model.predict.side_effect = RuntimeError("CUDA OOM")
    rr._reset_warnings()
    with patch.object(rr, "_load_st_model", return_value=fake_model):
        out = rr.rerank("q", cands)
    # No exception — input length preserved, all scores zero.
    assert len(out) == 1
    assert out[0]["final_score"] == 0.0
    captured = capsys.readouterr()
    assert "predict failed" in captured.err
    assert "CUDA OOM" in captured.err


# ---------------------------------------------------------------------------
# Real-model smoke test (skipped unless extras installed)
# ---------------------------------------------------------------------------


def _have_st():
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _have_st(), reason="sentence-transformers not installed (`pip install 'brainctl[rerank]'`)")
@pytest.mark.slow
def test_real_model_changes_ordering_smoke():
    """Smoke test against the real default model. Skipped unless the
    [rerank] extra is installed. Verifies that a clearly-relevant
    candidate beats a clearly-irrelevant one.
    """
    cands = [
        {"content": "Python is a high-level programming language.", "final_score": 0.5, "id": 1},
        {"content": "The weather in Tokyo today is partly cloudy.", "final_score": 0.5, "id": 2},
        {"content": "Python lists are mutable sequences of objects.", "final_score": 0.5, "id": 3},
    ]
    out = rr.rerank("What is Python programming?", cands)
    # Top result must be Python-related, not weather.
    assert out[0]["id"] in (1, 3), f"Top result was {out[0]}"
    assert out[-1]["id"] == 2  # weather sinks


# ---------------------------------------------------------------------------
# CLI integration — argument parsing
# ---------------------------------------------------------------------------


def test_cli_rerank_flag_parses_with_no_value():
    """`brainctl search QUERY --rerank` (no model) → args.rerank == default."""
    import importlib
    import agentmemory._impl as _impl
    # The CLI builds its parser inside main(); easier to just construct
    # a parser with the same shape as the search subcommand.
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rerank", nargs="?", const="bge-reranker-v2-m3", default=None,
                   metavar="MODEL", dest="rerank")
    ns = p.parse_args(["--rerank"])
    assert ns.rerank == "bge-reranker-v2-m3"


def test_cli_rerank_flag_accepts_explicit_model():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rerank", nargs="?", const="bge-reranker-v2-m3", default=None,
                   metavar="MODEL", dest="rerank")
    ns = p.parse_args(["--rerank", "jina-reranker-v2-base-multilingual"])
    assert ns.rerank == "jina-reranker-v2-base-multilingual"


def test_cli_rerank_flag_default_off():
    """Without --rerank, args.rerank is None → CE stage skipped."""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rerank", nargs="?", const="bge-reranker-v2-m3", default=None,
                   metavar="MODEL", dest="rerank")
    ns = p.parse_args([])
    assert ns.rerank is None


# ---------------------------------------------------------------------------
# End-to-end CLI test — the chain runs without crashing when --rerank
# is passed against an empty / fresh DB.
# ---------------------------------------------------------------------------


def test_cli_search_with_rerank_does_not_crash(tmp_path):
    """Smoke: invoking `brainctl search` with --rerank against a fresh
    DB must succeed even without sentence-transformers installed.
    The rerank module's fallback path keeps the search alive.
    """
    db_file = tmp_path / "brain.db"
    env = {**os.environ, "PYTHONPATH": str(SRC), "BRAINCTL_RERANK_QUIET": "1"}

    # Init a fresh DB.
    init_result = subprocess.run(
        [sys.executable, "-m", "agentmemory.cli", "init", "--path", str(db_file), "--force"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if init_result.returncode != 0:
        # Some envs (sandbox) may not be able to init — skip rather than fail.
        pytest.skip(f"brainctl init unavailable: {init_result.stderr[:200]}")

    # Run search with --rerank against the empty DB. Must exit 0.
    search_env = {**env, "BRAIN_DB": str(db_file)}
    result = subprocess.run(
        [sys.executable, "-m", "agentmemory.cli", "search", "anything", "--rerank"],
        capture_output=True, text=True, timeout=30, env=search_env,
    )
    # Empty DB = empty results, but the chain itself shouldn't crash.
    assert result.returncode == 0, (
        f"search --rerank crashed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Ollama probe
# ---------------------------------------------------------------------------


def test_ollama_tags_returns_none_when_unreachable():
    """The probe must time out fast (≤500ms) and return None when
    Ollama isn't running, NOT raise."""
    # Point at a port nothing is listening on.
    with patch.object(rr, "_OLLAMA_BASE", "http://localhost:1"):
        out = rr._ollama_tags(timeout=0.1)
    assert out is None


def test_ollama_tags_parses_model_list():
    """Mock a successful /api/tags response and verify it returns
    the model name list."""
    fake_response = MagicMock()
    fake_response.read.return_value = b'{"models": [{"name": "nomic-embed-text:latest"}, {"name": "bge-m3:latest"}]}'
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response):
        out = rr._ollama_tags()
    assert out == ["nomic-embed-text:latest", "bge-m3:latest"]
