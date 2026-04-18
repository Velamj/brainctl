"""brainctl adapter — exposes Brain.search and cmd_search behind the
same ``CompetitorAdapter`` protocol so the run harness can score them
side-by-side with the external systems.

This is the reference adapter: any future competitor wrapping should
match its lifecycle exactly.
"""
from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sure brainctl is importable when the harness runs as a script
# from a fresh worktree without an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from .common import (
    CompetitorUnavailable,
    short_text_for,
    wrap_result,
)


class _BrainAdapterBase:
    """Common lifecycle for both backends — ingest into a fresh tmp DB."""

    name: str = "brainctl"
    pinned_version: str = "2.3.2"
    needs_api_key = False
    cost_per_1k_writes_usd = 0.0      # local SQLite — zero LLM spend
    cost_per_1k_queries_usd = 0.0

    def __init__(self) -> None:
        try:
            from agentmemory.brain import Brain  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name, f"agentmemory.brain not importable: {exc!r}"
            ) from exc
        self._Brain = Brain
        self._tmp: Optional[tempfile.TemporaryDirectory] = None
        self._db_path: Optional[Path] = None
        self._brain = None

    def setup(self, tenant_id: str) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "brainctl.db"
        self._brain = self._Brain(
            db_path=str(self._db_path),
            agent_id=f"competitor-bench-{tenant_id}",
        )

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        body = short_text_for(key, text, speaker, timestamp)
        self._brain.remember(body, category="observation")

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        # Subclasses override — base class is abstract.
        raise NotImplementedError

    def teardown(self) -> None:
        try:
            if self._brain is not None:
                self._brain.close()
        except Exception:
            pass
        self._brain = None
        gc.collect()
        if self._tmp is not None:
            self._tmp.cleanup()
        self._tmp = None


class BrainSearchAdapter(_BrainAdapterBase):
    """brainctl Brain.search — FTS5-only, fast & deterministic."""

    name = "brainctl-brain"

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        results = self._brain.search(query, limit=top_k)
        return [wrap_result(r.get("content", ""), id=r.get("id"))
                for r in results]


class CmdSearchAdapter(_BrainAdapterBase):
    """brainctl cmd_search — full hybrid pipeline (FTS5 + vec + RRF + reranker).

    Matches the ``_build_cmd_search_fn`` closure in
    ``tests/bench/locomo_eval.py`` so numbers stay aligned with the
    existing baseline JSON.
    """

    name = "brainctl-cmd"

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        import contextlib
        import io
        import types

        import agentmemory._impl as _impl
        _impl.DB_PATH = self._db_path

        captured: list = []

        def _capture(data, compact=False):
            captured.append(data)

        args = types.SimpleNamespace(
            query=query, limit=top_k,
            tables="memories,events,context",
            no_recency=True, no_graph=True,
            budget=None, min_salience=None,
            mmr=False, mmr_lambda=0.7, explore=False,
            profile=None, pagerank_boost=0.0,
            quantum=False, benchmark=False,
            agent="competitor-bench", format="json",
            oneline=False, verbose=False,
        )

        saved_json = _impl.json_out
        saved_oneline = _impl.oneline_out
        _impl.json_out = _capture
        _impl.oneline_out = _capture
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    _impl.cmd_search(args)
                except Exception:
                    return []
        finally:
            _impl.json_out = saved_json
            _impl.oneline_out = saved_oneline

        if not captured:
            return []
        payload = captured[0] if isinstance(captured[0], dict) else {}
        flat: List[Dict[str, Any]] = []
        for bucket in ("memories", "events", "context", "entities", "decisions"):
            flat.extend(payload.get(bucket, []) or [])
        flat.sort(key=lambda r: r.get("final_score", 0.0), reverse=True)
        return [wrap_result(r.get("content", ""), score=r.get("final_score", 0.0),
                            id=r.get("id"))
                for r in flat[:top_k]]

    def setup(self, tenant_id: str) -> None:
        super().setup(tenant_id)

    def teardown(self) -> None:
        # cmd_search opens its own connection; make sure the writer
        # connection is checkpointed and closed before tear-down so
        # WAL files don't outlive the tmpdir.
        try:
            if self._brain is not None:
                conn = self._brain._get_conn()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        super().teardown()
