"""brainctl memory plugin for Hermes Agent.

Implements the MemoryProvider ABC from ``agent.memory_provider`` and wraps the
``agentmemory.Brain`` API shipped by the ``brainctl`` PyPI package.

Features:
  * FTS5 full-text recall (search) plus optional vector recall (vsearch) and
    spreading-activation recall (think).
  * Auto-retain of completed turns as categorized memories.
  * Auto-prefetch of relevant context before each turn.
  * Session bookends — orient() at startup, wrap_up() at session end, creating
    handoff packets so future sessions can resume cleanly.
  * Mirrors Hermes built-in MEMORY.md / USER.md writes into brain.db via
    on_memory_write().
  * Exposes brainctl_remember, brainctl_search, brainctl_think, brainctl_log,
    brainctl_entity, brainctl_decide, brainctl_handoff tools to the model.

Config via $HERMES_HOME/brainctl/config.json (profile-scoped) or environment
variables. See README.md for the full reference.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

REMEMBER_SCHEMA = {
    "name": "brainctl_remember",
    "description": (
        "Store a long-term memory in brainctl. Use for durable facts, "
        "preferences, decisions, and conventions that should survive beyond "
        "the current session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact or observation to store."},
            "category": {
                "type": "string",
                "description": (
                    "One of: convention, decision, environment, identity, integration, "
                    "lesson, preference, project, user, general."
                ),
                "default": "general",
            },
            "tags": {"type": "string", "description": "Comma-separated tags (optional)."},
            "confidence": {"type": "number", "description": "Confidence 0.0–1.0 (default 1.0)."},
        },
        "required": ["content"],
    },
}

SEARCH_SCHEMA = {
    "name": "brainctl_search",
    "description": (
        "Full-text search of stored memories using SQLite FTS5 with porter "
        "stemming. Returns memories ranked by relevance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "limit": {"type": "integer", "description": "Max results (default 10)."},
        },
        "required": ["query"],
    },
}

THINK_SCHEMA = {
    "name": "brainctl_think",
    "description": (
        "Spreading-activation recall: seed from FTS hits, traverse the "
        "knowledge graph outward with decaying activation. Use to discover "
        "what memory associates with a topic, not just what directly matches."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Seed query."},
            "hops": {"type": "integer", "description": "Graph hops (default 2)."},
            "top_k": {"type": "integer", "description": "Max activated nodes (default 20)."},
        },
        "required": ["query"],
    },
}

LOG_SCHEMA = {
    "name": "brainctl_log",
    "description": (
        "Log an event (artifact, decision, error, handoff, result, "
        "task_update, warning, observation). Events are append-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One-line event summary."},
            "event_type": {
                "type": "string",
                "description": "Event type (default 'observation').",
                "default": "observation",
            },
            "project": {"type": "string", "description": "Optional project scope."},
            "importance": {"type": "number", "description": "0.0–1.0 (default 0.5)."},
        },
        "required": ["summary"],
    },
}

ENTITY_SCHEMA = {
    "name": "brainctl_entity",
    "description": (
        "Create or fetch an entity in the knowledge graph. Entity types: "
        "agent, concept, document, event, location, organization, person, "
        "project, service, tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Canonical entity name."},
            "entity_type": {"type": "string", "description": "Entity type."},
            "observations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Observations about this entity.",
            },
        },
        "required": ["name", "entity_type"],
    },
}

DECIDE_SCHEMA = {
    "name": "brainctl_decide",
    "description": "Record a decision with its rationale.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short decision title."},
            "rationale": {"type": "string", "description": "Why this decision was made."},
            "project": {"type": "string", "description": "Optional project scope."},
        },
        "required": ["title", "rationale"],
    },
}

HANDOFF_SCHEMA = {
    "name": "brainctl_handoff",
    "description": (
        "Create a handoff packet for session continuity. Use before ending a "
        "session to preserve working context for the next agent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "Ongoing goal."},
            "current_state": {"type": "string", "description": "Where things stand."},
            "open_loops": {"type": "string", "description": "Unfinished work."},
            "next_step": {"type": "string", "description": "What to do next."},
            "project": {"type": "string", "description": "Optional project scope."},
        },
        "required": ["goal", "current_state", "open_loops", "next_step"],
    },
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_DEFAULT_RECALL_METHOD = "search"  # search | vsearch | think
_VALID_RECALL_METHODS = {"search", "vsearch", "think"}
_VALID_MEMORY_MODES = {"hybrid", "context", "tools"}


def _load_config(hermes_home: str) -> dict:
    """Load config from profile-scoped JSON, falling back to env vars."""
    cfg_path = Path(hermes_home) / "brainctl" / "config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("brainctl: failed to parse %s: %s", cfg_path, exc)

    return {
        "db_path": os.environ.get("BRAIN_DB", ""),
        "agent_id": os.environ.get("BRAINCTL_AGENT_ID", "hermes"),
        "recall_method": os.environ.get("BRAINCTL_RECALL_METHOD", _DEFAULT_RECALL_METHOD),
        "recall_limit": int(os.environ.get("BRAINCTL_RECALL_LIMIT", "8")),
        "memory_mode": os.environ.get("BRAINCTL_MEMORY_MODE", "hybrid"),
        "auto_recall": True,
        "auto_retain": True,
        "retain_category": os.environ.get("BRAINCTL_RETAIN_CATEGORY", "conversation"),
        "retain_every_n_turns": int(os.environ.get("BRAINCTL_RETAIN_EVERY_N_TURNS", "1")),
        "session_bookends": True,
        "mirror_memory_md": True,
    }


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class BrainctlMemoryProvider(MemoryProvider):
    """brainctl-backed memory provider for Hermes Agent."""

    def __init__(self) -> None:
        self._config: dict = {}
        self._brain = None
        self._session_id = ""
        self._agent_id = "hermes"
        self._project: Optional[str] = None

        # Behavior flags
        self._auto_recall = True
        self._auto_retain = True
        self._recall_method = _DEFAULT_RECALL_METHOD
        self._recall_limit = 8
        self._memory_mode = "hybrid"
        self._retain_category = "conversation"
        self._retain_every_n_turns = 1
        self._session_bookends = True
        self._mirror_memory_md = True

        # Turn tracking
        self._turn_counter = 0
        self._pending_turns: list[tuple[str, str]] = []

        # Background workers
        self._prefetch_lock = threading.Lock()
        self._prefetch_result = ""
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

        # Startup orientation snapshot (used once in first prefetch)
        self._orient_snapshot = ""

    # -- Identification ------------------------------------------------------

    @property
    def name(self) -> str:
        return "brainctl"

    # -- Availability & setup ------------------------------------------------

    def is_available(self) -> bool:
        """Check that agentmemory is importable. No network calls."""
        try:
            import agentmemory  # noqa: F401
            return True
        except Exception:
            return False

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "db_path",
                "description": "Path to brain.db (empty = $BRAIN_DB or default)",
                "default": "",
            },
            {
                "key": "agent_id",
                "description": "Agent identifier recorded on every write",
                "default": "hermes",
            },
            {
                "key": "memory_mode",
                "description": "context | tools | hybrid",
                "default": "hybrid",
                "choices": sorted(_VALID_MEMORY_MODES),
            },
            {
                "key": "recall_method",
                "description": "search (FTS5) | vsearch (vector) | think (spreading activation)",
                "default": _DEFAULT_RECALL_METHOD,
                "choices": sorted(_VALID_RECALL_METHODS),
            },
            {
                "key": "recall_limit",
                "description": "Max memories to return per recall",
                "default": 8,
            },
            {
                "key": "auto_recall",
                "description": "Auto-prefetch memories before each turn",
                "default": True,
            },
            {
                "key": "auto_retain",
                "description": "Auto-retain completed turns",
                "default": True,
            },
            {
                "key": "retain_category",
                "description": "Category assigned to auto-retained turns",
                "default": "conversation",
            },
            {
                "key": "retain_every_n_turns",
                "description": "Retain every N turns (1 = every turn)",
                "default": 1,
            },
            {
                "key": "session_bookends",
                "description": "Call brain.orient()/wrap_up() at session boundaries",
                "default": True,
            },
            {
                "key": "mirror_memory_md",
                "description": "Mirror built-in MEMORY.md/USER.md writes into brain.db",
                "default": True,
            },
            {
                "key": "project",
                "description": "Optional project scope for events & handoffs",
                "default": "",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Persist config to $HERMES_HOME/brainctl/config.json."""
        cfg_dir = Path(hermes_home) / "brainctl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.json"

        existing: dict = {}
        if cfg_path.exists():
            try:
                existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(values)
        cfg_path.write_text(json.dumps(existing, indent=2))
        logger.info("brainctl: config saved to %s", cfg_path)

    # -- Lifecycle -----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home") or os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        platform = kwargs.get("platform", "cli")
        agent_context = kwargs.get("agent_context", "primary")

        self._session_id = session_id
        self._config = _load_config(hermes_home)

        # Apply config
        self._agent_id = self._config.get("agent_id") or "hermes"
        self._project = self._config.get("project") or None
        self._auto_recall = bool(self._config.get("auto_recall", True))
        self._auto_retain = bool(self._config.get("auto_retain", True))
        method = self._config.get("recall_method", _DEFAULT_RECALL_METHOD)
        self._recall_method = method if method in _VALID_RECALL_METHODS else _DEFAULT_RECALL_METHOD
        self._recall_limit = int(self._config.get("recall_limit", 8))
        mode = self._config.get("memory_mode", "hybrid")
        self._memory_mode = mode if mode in _VALID_MEMORY_MODES else "hybrid"
        self._retain_category = self._config.get("retain_category", "conversation")
        self._retain_every_n_turns = max(1, int(self._config.get("retain_every_n_turns", 1)))
        self._session_bookends = bool(self._config.get("session_bookends", True))
        self._mirror_memory_md = bool(self._config.get("mirror_memory_md", True))

        # Skip writes for non-primary agent contexts (cron, subagent, flush).
        # We still initialize the Brain for read-only recall.
        self._read_only = agent_context != "primary"

        # Resolve db_path. Default is profile-scoped inside hermes_home so each
        # Hermes profile gets its own brain.db.
        db_path = self._config.get("db_path") or os.environ.get("BRAIN_DB", "")
        if not db_path:
            db_dir = Path(hermes_home) / "brainctl"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "brain.db")

        try:
            from agentmemory import Brain
            self._brain = Brain(db_path=db_path, agent_id=self._agent_id)
        except Exception as exc:
            logger.warning("brainctl: failed to open brain at %s: %s", db_path, exc)
            self._brain = None
            return

        logger.info(
            "brainctl initialized: db=%s, agent_id=%s, mode=%s, recall=%s, platform=%s, read_only=%s",
            db_path, self._agent_id, self._memory_mode, self._recall_method, platform, self._read_only,
        )

        # Session bookend: orient() to pull handoff + recent events + triggers.
        # Stored for first prefetch() to inject; also logs session_start.
        if self._session_bookends and not self._read_only:
            try:
                snap = self._brain.orient(project=self._project)
                self._orient_snapshot = self._format_orient(snap)
            except Exception as exc:
                logger.debug("brainctl: orient() failed: %s", exc)

    def shutdown(self) -> None:
        """Flush pending retains and close. Session-end bookend is handled by on_session_end."""
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        self._brain = None

    # -- System prompt -------------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._brain:
            return ""
        lines = [
            "# brainctl Memory",
            f"Active (mode={self._memory_mode}). SQLite brain with FTS5, knowledge graph, and session handoffs.",
        ]
        if self._memory_mode in ("tools", "hybrid"):
            lines.append(
                "Use brainctl_remember for durable facts, brainctl_search for recall, "
                "brainctl_think for associative recall, brainctl_log for events, "
                "brainctl_decide for decisions, brainctl_entity for graph nodes, "
                "brainctl_handoff before ending a session."
            )
        if self._memory_mode in ("context", "hybrid"):
            lines.append("Relevant memories are automatically injected into context.")
        return "\n".join(lines)

    # -- Recall --------------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return any background-recalled context plus a one-shot orient snapshot."""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)

        with self._prefetch_lock:
            recalled = self._prefetch_result
            self._prefetch_result = ""

        orient_block = self._orient_snapshot
        self._orient_snapshot = ""  # consume once

        if not recalled and not orient_block:
            return ""

        parts = ["# brainctl Memory (persistent recall)"]
        if orient_block:
            parts.append(orient_block)
        if recalled:
            parts.append(recalled)
        return "\n\n".join(parts)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._brain or self._memory_mode == "tools" or not self._auto_recall:
            return
        if not query or not query.strip():
            return

        def _run() -> None:
            try:
                text = self._recall(query)
                if text:
                    with self._prefetch_lock:
                        self._prefetch_result = text
            except Exception as exc:
                logger.debug("brainctl prefetch failed: %s", exc, exc_info=True)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="brainctl-prefetch"
        )
        self._prefetch_thread.start()

    def _recall(self, query: str) -> str:
        """Run the configured recall method and format results."""
        if not self._brain:
            return ""
        if self._recall_method == "vsearch":
            results = self._brain.vsearch(query, limit=self._recall_limit)
            if not results:
                # Fall back to FTS if vector extension unavailable
                results = self._brain.search(query, limit=self._recall_limit)
        elif self._recall_method == "think":
            out = self._brain.think(query, top_k=self._recall_limit)
            activated = out.get("activated") if isinstance(out, dict) else None
            results = activated or []
        else:
            results = self._brain.search(query, limit=self._recall_limit)

        if not results:
            return ""
        lines = []
        for r in results:
            if not isinstance(r, dict):
                continue
            content = r.get("content") or r.get("text") or ""
            if content:
                lines.append(f"- {content}")
        return "\n".join(lines)

    # -- Retain --------------------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._brain or not self._auto_retain or self._read_only:
            return

        self._turn_counter += 1
        self._pending_turns.append((user_content, assistant_content))
        if self._turn_counter % self._retain_every_n_turns != 0:
            return

        to_flush = self._pending_turns
        self._pending_turns = []

        def _sync() -> None:
            try:
                for user, asst in to_flush:
                    summary = f"User: {user}\nAssistant: {asst}"
                    try:
                        self._brain.remember(
                            summary,
                            category=self._retain_category,
                            tags=f"session:{self._session_id}",
                        )
                    except Exception as exc:
                        logger.debug("brainctl remember failed: %s", exc)
            except Exception as exc:
                logger.warning("brainctl sync_turn failed: %s", exc, exc_info=True)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="brainctl-sync"
        )
        self._sync_thread.start()

    # -- Optional hooks ------------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._brain or self._read_only or not self._session_bookends:
            return
        # Flush any buffered turns before wrapping up.
        if self._pending_turns:
            for user, asst in self._pending_turns:
                try:
                    self._brain.remember(
                        f"User: {user}\nAssistant: {asst}",
                        category=self._retain_category,
                        tags=f"session:{self._session_id}",
                    )
                except Exception:
                    pass
            self._pending_turns = []

        # Build a lightweight summary from the last few messages.
        summary = self._summarize_messages(messages)
        try:
            self._brain.wrap_up(summary=summary, project=self._project)
            logger.info("brainctl: session %s wrapped up", self._session_id)
        except Exception as exc:
            logger.debug("brainctl wrap_up failed: %s", exc)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Before compression, retain a summary of what's about to be discarded."""
        if not self._brain or self._read_only:
            return ""
        summary = self._summarize_messages(messages, max_chars=1200)
        if not summary:
            return ""
        try:
            self._brain.remember(
                summary,
                category="lesson",
                tags=f"session:{self._session_id},pre_compress",
            )
        except Exception as exc:
            logger.debug("brainctl on_pre_compress remember failed: %s", exc)
        return (
            "brainctl-memory: prior context was persisted to the long-term store "
            "and can be retrieved with brainctl_search."
        )

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in MEMORY.md / USER.md writes into brain.db."""
        if not self._brain or not self._mirror_memory_md or self._read_only:
            return
        if action not in ("add", "replace"):
            return
        category = "user" if target == "user" else "identity"
        try:
            self._brain.remember(
                content,
                category=category,
                tags=f"source:builtin,target:{target}",
            )
        except Exception as exc:
            logger.debug("brainctl on_memory_write mirror failed: %s", exc)

    # -- Tools ---------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._memory_mode == "context":
            return []
        return [
            REMEMBER_SCHEMA,
            SEARCH_SCHEMA,
            THINK_SCHEMA,
            LOG_SCHEMA,
            ENTITY_SCHEMA,
            DECIDE_SCHEMA,
            HANDOFF_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._brain:
            return json.dumps({"error": "brainctl not initialized"})

        try:
            if tool_name == "brainctl_remember":
                content = args.get("content", "")
                if not content:
                    return json.dumps({"error": "Missing required parameter: content"})
                mid = self._brain.remember(
                    content,
                    category=args.get("category", "general"),
                    tags=args.get("tags"),
                    confidence=float(args.get("confidence", 1.0)),
                )
                return json.dumps({"result": "stored", "id": mid})

            if tool_name == "brainctl_search":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                results = self._brain.search(query, limit=int(args.get("limit", 10)))
                if not results:
                    return json.dumps({"result": "No memories found."})
                lines = [
                    f"{i}. [{r.get('category', '?')}] {r.get('content', '')}"
                    for i, r in enumerate(results, 1)
                ]
                return json.dumps({"result": "\n".join(lines)})

            if tool_name == "brainctl_think":
                query = args.get("query", "")
                if not query:
                    return json.dumps({"error": "Missing required parameter: query"})
                out = self._brain.think(
                    query,
                    hops=int(args.get("hops", 2)),
                    top_k=int(args.get("top_k", 20)),
                )
                return json.dumps({"result": out})

            if tool_name == "brainctl_log":
                summary = args.get("summary", "")
                if not summary:
                    return json.dumps({"error": "Missing required parameter: summary"})
                eid = self._brain.log(
                    summary,
                    event_type=args.get("event_type", "observation"),
                    project=args.get("project") or self._project,
                    importance=float(args.get("importance", 0.5)),
                )
                return json.dumps({"result": "logged", "id": eid})

            if tool_name == "brainctl_entity":
                name_ = args.get("name", "")
                etype = args.get("entity_type", "")
                if not name_ or not etype:
                    return json.dumps({"error": "name and entity_type required"})
                eid = self._brain.entity(
                    name_,
                    entity_type=etype,
                    observations=args.get("observations") or [],
                )
                return json.dumps({"result": "upserted", "id": eid})

            if tool_name == "brainctl_decide":
                title = args.get("title", "")
                rationale = args.get("rationale", "")
                if not title or not rationale:
                    return json.dumps({"error": "title and rationale required"})
                did = self._brain.decide(
                    title,
                    rationale,
                    project=args.get("project") or self._project,
                )
                return json.dumps({"result": "recorded", "id": did})

            if tool_name == "brainctl_handoff":
                hid = self._brain.handoff(
                    goal=args.get("goal", ""),
                    current_state=args.get("current_state", ""),
                    open_loops=args.get("open_loops", ""),
                    next_step=args.get("next_step", ""),
                    project=args.get("project") or self._project,
                )
                return json.dumps({"result": "handoff created", "id": hid})

        except Exception as exc:
            logger.warning("brainctl tool %s failed: %s", tool_name, exc, exc_info=True)
            return json.dumps({"error": f"{tool_name} failed: {exc}"})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _format_orient(snap: Dict[str, Any]) -> str:
        """Render orient() output as a system-prompt-friendly block."""
        if not snap:
            return ""
        lines: list[str] = []
        handoff = snap.get("handoff")
        if handoff:
            lines.append("## Pending handoff")
            lines.append(f"- goal: {handoff.get('goal', '')}")
            lines.append(f"- state: {handoff.get('current_state', '')}")
            lines.append(f"- open_loops: {handoff.get('open_loops', '')}")
            lines.append(f"- next_step: {handoff.get('next_step', '')}")
        triggers = snap.get("triggers") or []
        if triggers:
            lines.append("## Active triggers")
            for t in triggers[:5]:
                lines.append(
                    f"- [{t.get('priority', 'medium')}] {t.get('trigger_condition', '')}"
                    f" → {t.get('action', '')}"
                )
        events = snap.get("recent_events") or []
        if events:
            lines.append("## Recent events")
            for e in events[:5]:
                lines.append(f"- {e.get('event_type', '')}: {e.get('summary', '')}")
        stats = snap.get("stats") or {}
        if stats:
            lines.append(
                f"_stats: {stats.get('active_memories', 0)} memories, "
                f"{stats.get('total_events', 0)} events, "
                f"{stats.get('total_entities', 0)} entities_"
            )
        return "\n".join(lines)

    @staticmethod
    def _summarize_messages(messages: List[Dict[str, Any]], max_chars: int = 800) -> str:
        """Cheap heuristic: take the last few user/assistant turns, truncate."""
        if not messages:
            return ""
        tail = messages[-6:]
        parts: list[str] = []
        for m in tail:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                # multi-part content — stringify text chunks only
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                )
            if content and role in ("user", "assistant"):
                parts.append(f"{role}: {content}")
        summary = "\n".join(parts).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "…"
        return summary


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register brainctl as a memory provider plugin with Hermes."""
    ctx.register_memory_provider(BrainctlMemoryProvider())
