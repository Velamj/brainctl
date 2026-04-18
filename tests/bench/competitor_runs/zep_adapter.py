"""Zep adapter via Zep Cloud (zep-cloud SDK, formerly zep_python for self-hosted).

Pinned to zep-cloud==3.20.0. Zep docs: https://help.getzep.com/

Zep's memory primitive is a **session** scoped under a **user**. We
treat each LOCOMO conversation as one Zep user with one session, and
each LongMemEval entry the same way. Zep's recommended retrieval call
is ``memory.search_sessions(text=query, search_scope="messages", limit=k)``
which combines BM25 + cosine over the session's stored messages.

Honesty knobs:
  * Zep auto-runs a "fact synthesis" pipeline that collapses messages
    into long-term semantic facts. We DISABLE that synthesis by reading
    from ``search_scope="messages"`` (raw messages) instead of
    ``"facts"`` (LLM-distilled). With ``"facts"`` the [key=...] marker
    would be stripped during synthesis.
  * Zep tracks "session metadata" — we don't add any so the dataset's
    speaker/timestamp lives only in the message body (mirrors brainctl).

Cost: Zep Cloud Free tier: 100k tokens/mo. Above that:
  * messages.add:     $0.20 per 1k messages
  * memory.search:    $0.10 per 1k queries
LOCOMO full: 5882 writes + 1982 queries * 2 runs => ~$3 total. Tight.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .common import (
    CompetitorUnavailable,
    require_env,
    short_text_for,
    wrap_result,
)


class ZepAdapter:
    name = "zep"
    pinned_version = "3.20.0"
    needs_api_key = True
    cost_per_1k_writes_usd = 0.20
    cost_per_1k_queries_usd = 0.10

    def __init__(self) -> None:
        try:
            from zep_cloud.client import Zep  # type: ignore
        except ImportError as exc:
            raise CompetitorUnavailable(
                self.name,
                f"zep-cloud not installed (pip install zep-cloud=={self.pinned_version}): {exc!r}",
            ) from exc
        api_key = require_env("ZEP_API_KEY", self.name)
        try:
            self._client = Zep(api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"Zep init failed: {exc!r}"
            ) from exc
        self._user_id: str = ""
        self._session_id: str = ""

    def setup(self, tenant_id: str) -> None:
        self._user_id = f"brainctl-bench-{tenant_id}"
        self._session_id = f"{self._user_id}-session"
        try:
            self._client.user.add(user_id=self._user_id)
        except Exception:
            # Already exists or transient — try session creation anyway.
            pass
        try:
            self._client.memory.add_session(
                session_id=self._session_id, user_id=self._user_id
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"add_session failed: {exc!r}"
            ) from exc

    def ingest(
        self,
        key: str,
        text: str,
        speaker: str = "",
        timestamp: str = "",
    ) -> None:
        from zep_cloud.types import Message  # type: ignore

        body = short_text_for(key, text, speaker, timestamp)
        try:
            self._client.memory.add(
                session_id=self._session_id,
                messages=[Message(role=speaker or "user", role_type="user", content=body)],
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"memory.add failed: {exc!r}"
            ) from exc

    def search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        try:
            results = self._client.memory.search_sessions(
                session_ids=[self._session_id],
                user_id=self._user_id,
                text=query,
                search_scope="messages",   # raw, not LLM-distilled
                limit=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            raise CompetitorUnavailable(
                self.name, f"search_sessions failed: {exc!r}"
            ) from exc

        out: List[Dict[str, Any]] = []
        for r in (results.results or []) if hasattr(results, "results") else (results or []):
            msg = getattr(r, "message", None)
            text = getattr(msg, "content", None) if msg else getattr(r, "content", "")
            out.append(wrap_result(text or "", score=getattr(r, "score", 0.0)))
        return out

    def teardown(self) -> None:
        if not self._user_id:
            return
        try:
            self._client.user.delete(user_id=self._user_id)
        except Exception:
            pass
