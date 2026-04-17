"""Smoke tests for Bug 6c — memory merge knowledge_edges integrity.

Mirrors ``test_entity_merge_edges.py`` for the memory path. When ``merge.py``
copies memories from a source DB to a target DB, memory rows get fresh ids
on insert (``INSERT INTO memories`` does not preserve ids). Any
``knowledge_edges`` row with ``source_table='memories'`` or
``target_table='memories'`` referencing the old src memory id would be
orphaned in the target DB.

The fix: ``_merge_memories`` returns a ``memory_id_map: dict[int, int]``
(src memory id → target memory id) populated in both branches — the
dedup-match branch AND the fresh-insert branch via ``cursor.lastrowid``.
``_merge_knowledge_edges`` accepts this map and applies it whenever
``*_table == 'memories'``, using the same self-loop drop and
``ON CONFLICT ... DO UPDATE SET weight = MAX(weight, excluded.weight)``
folding as the entity path.

Each test verifies three invariants on the post-merge target DB:
    (a) no orphan edges (no edge points at a non-existent memory id)
    (b) no duplicate (source_table, source_id, target_table, target_id,
        relation_type) tuples
    (c) all distinct relation_types from the input survive (no information
        loss beyond the documented self-loop drop and weight-max collapse)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain
import agentmemory.merge as merge_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, "
        "created_at, updated_at) VALUES "
        "(?, ?, 'mcp', 'active', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')",
        (agent_id, agent_id),
    )


def _insert_memory(
    conn: sqlite3.Connection,
    content: str,
    *,
    category: str = "project",
    scope: str = "global",
    agent_id: str = "test-agent",
    confidence: float = 0.8,
) -> int:
    """Insert a memory via raw SQL, bypassing the W(m) gate.

    Brain.remember() runs the worthiness gate and may reject synthetic
    fixture inserts; raw SQL guarantees the row lands. We populate only
    the NOT NULL columns explicitly — all others use table defaults.
    """
    _ensure_agent(conn, agent_id)
    cur = conn.execute(
        "INSERT INTO memories (agent_id, category, scope, content, confidence, "
        "created_at, updated_at) VALUES "
        "(?, ?, ?, ?, ?, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')",
        (agent_id, category, scope, content, confidence),
    )
    return cur.lastrowid


def _insert_entity(
    conn: sqlite3.Connection,
    name: str,
    *,
    entity_type: str = "concept",
    scope: str = "global",
    agent_id: str = "test-agent",
) -> int:
    _ensure_agent(conn, agent_id)
    cur = conn.execute(
        "INSERT INTO entities (name, entity_type, observations, properties, "
        "agent_id, scope, created_at, updated_at) VALUES "
        "(?, ?, '[]', '{}', ?, ?, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')",
        (name, entity_type, agent_id, scope),
    )
    return cur.lastrowid


def _insert_edge(
    conn: sqlite3.Connection,
    src_table: str,
    src_id: int,
    tgt_table: str,
    tgt_id: int,
    relation: str,
    *,
    weight: float = 1.0,
    agent_id: str = "test-agent",
) -> int:
    _ensure_agent(conn, agent_id)
    cur = conn.execute(
        "INSERT INTO knowledge_edges "
        "(source_table, source_id, target_table, target_id, relation_type, "
        "weight, agent_id, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, '2024-01-01T00:00:00Z')",
        (src_table, src_id, tgt_table, tgt_id, relation, weight, agent_id),
    )
    return cur.lastrowid


def _audit_edge_integrity(conn: sqlite3.Connection) -> dict:
    """Return diagnostics suitable for assertions, for both entity- and
    memory-referencing edges. Mirror of the helper in
    ``test_entity_merge_edges.py`` with memory-id lookup added.
    """
    edges = conn.execute(
        "SELECT id, source_table, source_id, target_table, target_id, "
        "relation_type, weight FROM knowledge_edges"
    ).fetchall()

    # Active entity ids (retired_at IS NULL)
    active_entity_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM entities WHERE retired_at IS NULL"
        ).fetchall()
    }
    all_entity_ids = {
        row[0]
        for row in conn.execute("SELECT id FROM entities").fetchall()
    }

    # Active memory ids (retired_at IS NULL)
    active_memory_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM memories WHERE retired_at IS NULL"
        ).fetchall()
    }
    all_memory_ids = {
        row[0]
        for row in conn.execute("SELECT id FROM memories").fetchall()
    }

    orphans: list[tuple] = []  # edges pointing at retired/non-existent rows
    self_loops: list[tuple] = []
    seen_keys: dict[tuple, list[int]] = {}

    for e in edges:
        eid, st, si, tt, ti, rt, w = e
        key = (st, si, tt, ti, rt)
        seen_keys.setdefault(key, []).append(eid)

        if st == "entities":
            if si not in active_entity_ids:
                orphans.append(
                    (eid, "source", si, "retired" if si in all_entity_ids else "missing")
                )
        elif st == "memories":
            if si not in active_memory_ids:
                orphans.append(
                    (eid, "source", si, "retired" if si in all_memory_ids else "missing")
                )

        if tt == "entities":
            if ti not in active_entity_ids:
                orphans.append(
                    (eid, "target", ti, "retired" if ti in all_entity_ids else "missing")
                )
        elif tt == "memories":
            if ti not in active_memory_ids:
                orphans.append(
                    (eid, "target", ti, "retired" if ti in all_memory_ids else "missing")
                )

        if st == tt and si == ti:
            self_loops.append(e)

    duplicates = {k: v for k, v in seen_keys.items() if len(v) > 1}

    return {
        "total_edges": len(edges),
        "orphans": orphans,
        "self_loops": self_loops,
        "duplicates": duplicates,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# Bug 6c — cross-DB merge.py path for memory-referencing edges
# ---------------------------------------------------------------------------

class TestCrossDBMemoryMergeEdgeIntegrity:
    """merge.py path: src and target are different brain.db files; edges
    reference memory ids that won't survive an unmapped copy.
    """

    def _make_db(self, path: Path, agent_id: str) -> str:
        Brain(db_path=str(path), agent_id=agent_id)
        return str(path)

    # --- 1. Edge on src side with memory as SOURCE ----------------------

    def test_edge_referencing_src_memory_as_source_redirects(self, tmp_path):
        # src has memory M (src-side id) and entity X; edge M -> X ('supports',
        # weight=0.7). Target has the same memory by (content, agent_id,
        # category) with a different id, and no such edge.
        #
        # After merge: src's M dedups to target's M' (different id). The
        # src edge M -> X must redirect to M' -> X in the target, not copy
        # verbatim (which would orphan). The memory-side id MUST be remapped.
        src_path = self._make_db(tmp_path / "src.db", "shared-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "shared-agent")

        sconn = sqlite3.connect(src_path)
        # Pad the dst memory ids ahead of src so the src M's id is
        # guaranteed NOT to already exist in target — that's what makes
        # the un-remapped path orphan (the test is meaningful).
        m_src = _insert_memory(
            sconn, "the alice lives at 42", category="project",
            agent_id="shared-agent", confidence=0.9,
        )
        # Entity on src side, same name+scope as target entity so it
        # dedups and resolves cleanly (not the focus of this test).
        x_src = _insert_entity(sconn, "AliceProject", scope="global", agent_id="shared-agent")
        _insert_edge(
            sconn, "memories", m_src, "entities", x_src, "supports",
            weight=0.7, agent_id="shared-agent",
        )
        sconn.commit()
        sconn.close()

        dconn = sqlite3.connect(dst_path)
        # Pre-pad dst memories so its autoincrement id differs from src.
        _insert_memory(dconn, "unrelated pad #1", agent_id="shared-agent")
        _insert_memory(dconn, "unrelated pad #2", agent_id="shared-agent")
        m_dst = _insert_memory(
            dconn, "the alice lives at 42", category="project",
            agent_id="shared-agent", confidence=0.6,
        )
        x_dst = _insert_entity(dconn, "AliceProject", scope="global", agent_id="shared-agent")
        dconn.commit()
        dconn.close()

        # Sanity: src and dst memory ids differ, so an un-remapped copy
        # would insert an edge whose source_id points at nothing (or worse,
        # at an unrelated pad row).
        assert m_src != m_dst, (
            "fixtures must produce different memory ids between src and dst "
            "so the test exercises the remap path"
        )

        report = merge_mod.merge(source_path=src_path, target_path=dst_path)
        assert report["dry_run"] is False

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        # The redirected edge must exist exactly once, pointing at the
        # target-side memory id.
        edges_supports = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='memories' "
            "AND source_id=? AND target_table='entities' AND target_id=? "
            "AND relation_type='supports'",
            (m_dst, x_dst),
        ).fetchall()
        check.close()

        assert diag["orphans"] == [], (
            f"redirected edge must not orphan; got {diag['orphans']}"
        )
        assert diag["self_loops"] == []
        assert diag["duplicates"] == {}
        assert len(edges_supports) == 1, (
            f"the 'supports' edge must be remapped to target memory id "
            f"{m_dst}; got {edges_supports}"
        )

    # --- 2. Edge on src side with memory as TARGET ----------------------

    def test_edge_referencing_src_memory_as_target_redirects(self, tmp_path):
        # Mirror case: edge X -> M ('mentions', weight=0.5). The target_id
        # remap is what's exercised here.
        src_path = self._make_db(tmp_path / "src.db", "shared-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "shared-agent")

        sconn = sqlite3.connect(src_path)
        m_src = _insert_memory(
            sconn, "bob debounces the webhook", category="lesson",
            agent_id="shared-agent", confidence=0.85,
        )
        x_src = _insert_entity(sconn, "WebhookService", scope="global", agent_id="shared-agent")
        _insert_edge(
            sconn, "entities", x_src, "memories", m_src, "mentions",
            weight=0.5, agent_id="shared-agent",
        )
        sconn.commit()
        sconn.close()

        dconn = sqlite3.connect(dst_path)
        _insert_memory(dconn, "pad a", agent_id="shared-agent")
        _insert_memory(dconn, "pad b", agent_id="shared-agent")
        _insert_memory(dconn, "pad c", agent_id="shared-agent")
        m_dst = _insert_memory(
            dconn, "bob debounces the webhook", category="lesson",
            agent_id="shared-agent", confidence=0.6,
        )
        x_dst = _insert_entity(dconn, "WebhookService", scope="global", agent_id="shared-agent")
        dconn.commit()
        dconn.close()

        assert m_src != m_dst

        merge_mod.merge(source_path=src_path, target_path=dst_path)

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        edges_mentions = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='entities' "
            "AND source_id=? AND target_table='memories' AND target_id=? "
            "AND relation_type='mentions'",
            (x_dst, m_dst),
        ).fetchall()
        check.close()

        assert diag["orphans"] == [], (
            f"target-side memory id must be remapped; got {diag['orphans']}"
        )
        assert diag["self_loops"] == []
        assert diag["duplicates"] == {}
        assert len(edges_mentions) == 1, (
            f"edge must land on target memory id {m_dst}; got {edges_mentions}"
        )

    # --- 3. Self-loop produced by src-side memory self-reference --------

    def test_src_side_memory_self_loop_dropped_after_remap(self, tmp_path):
        # Src has a memory M with an edge M -> M ('related', weight=0.6).
        # After remap to target id M', the edge is still a self-loop, and
        # our documented rule drops it (the entity path does the same).
        src_path = self._make_db(tmp_path / "src.db", "shared-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "shared-agent")

        sconn = sqlite3.connect(src_path)
        m_src = _insert_memory(
            sconn, "self-referential claim", category="lesson",
            agent_id="shared-agent", confidence=0.9,
        )
        _insert_edge(
            sconn, "memories", m_src, "memories", m_src, "related",
            weight=0.6, agent_id="shared-agent",
        )
        sconn.commit()
        sconn.close()

        dconn = sqlite3.connect(dst_path)
        _insert_memory(dconn, "pad one", agent_id="shared-agent")
        m_dst = _insert_memory(
            dconn, "self-referential claim", category="lesson",
            agent_id="shared-agent", confidence=0.5,
        )
        dconn.commit()
        dconn.close()

        merge_mod.merge(source_path=src_path, target_path=dst_path)

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        # The self-loop edge must NOT exist in target.
        loops = check.execute(
            "SELECT id FROM knowledge_edges WHERE source_table='memories' "
            "AND source_id=? AND target_table='memories' AND target_id=? "
            "AND relation_type='related'",
            (m_dst, m_dst),
        ).fetchall()
        check.close()

        assert diag["self_loops"] == [], (
            f"src-side memory self-loop must be dropped; got {diag['self_loops']}"
        )
        assert loops == [], (
            f"the remapped self-loop edge must not land in target; got {loops}"
        )
        assert diag["orphans"] == []

    # --- 4. Cross-direction collision: MAX weight wins, no duplicates ---

    def test_memory_edge_collision_collapses_with_max_weight(self, tmp_path):
        # Src has memory M and entity X with edge M -> X ('supports',
        # weight=0.9). Target has the same memory (dedups) and same entity
        # (dedups) with edge M' -> X' ('supports', weight=0.4).
        # After merge the redirected src edge collides with the target
        # edge — highest-weight wins → keep 0.9.
        #
        # Additionally: an unrelated 'cites' edge on src with weight=0.3
        # must be preserved (no information loss for distinct relations).
        src_path = self._make_db(tmp_path / "src.db", "shared-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "shared-agent")

        sconn = sqlite3.connect(src_path)
        m_src = _insert_memory(
            sconn, "payment path 2xx on retry", category="project",
            agent_id="shared-agent", confidence=0.9,
        )
        x_src = _insert_entity(sconn, "PaymentService", scope="global", agent_id="shared-agent")
        _insert_edge(
            sconn, "memories", m_src, "entities", x_src, "supports",
            weight=0.9, agent_id="shared-agent",
        )
        _insert_edge(
            sconn, "memories", m_src, "entities", x_src, "cites",
            weight=0.3, agent_id="shared-agent",
        )
        sconn.commit()
        sconn.close()

        dconn = sqlite3.connect(dst_path)
        _insert_memory(dconn, "pad-1", agent_id="shared-agent")
        _insert_memory(dconn, "pad-2", agent_id="shared-agent")
        m_dst = _insert_memory(
            dconn, "payment path 2xx on retry", category="project",
            agent_id="shared-agent", confidence=0.7,
        )
        x_dst = _insert_entity(dconn, "PaymentService", scope="global", agent_id="shared-agent")
        _insert_edge(
            dconn, "memories", m_dst, "entities", x_dst, "supports",
            weight=0.4, agent_id="shared-agent",
        )
        dconn.commit()
        dconn.close()

        assert m_src != m_dst

        merge_mod.merge(source_path=src_path, target_path=dst_path)

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        supports_rows = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='memories' "
            "AND source_id=? AND target_table='entities' AND target_id=? "
            "AND relation_type='supports'",
            (m_dst, x_dst),
        ).fetchall()
        cites_rows = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='memories' "
            "AND source_id=? AND target_table='entities' AND target_id=? "
            "AND relation_type='cites'",
            (m_dst, x_dst),
        ).fetchall()
        check.close()

        assert diag["orphans"] == [], diag["orphans"]
        assert diag["self_loops"] == []
        assert diag["duplicates"] == {}, (
            f"collision must collapse; got duplicates {diag['duplicates']}"
        )
        assert len(supports_rows) == 1, (
            f"colliding 'supports' edge must collapse to one row, got {supports_rows}"
        )
        # HIGHEST WEIGHT WINS: src=0.9, dst=0.4 → 0.9
        assert abs(supports_rows[0][0] - 0.9) < 1e-9, (
            f"MAX weight rule violated; expected 0.9, got {supports_rows[0][0]}"
        )
        # The distinct 'cites' relation_type must survive.
        assert len(cites_rows) == 1, (
            f"src-only 'cites' relation must be preserved, got {cites_rows}"
        )
        assert abs(cites_rows[0][0] - 0.3) < 1e-9

    # --- 5. Idempotency: re-merging must not orphan or duplicate --------

    def test_idempotent_re_merge_memory_edges(self, tmp_path):
        src_path = self._make_db(tmp_path / "src.db", "shared-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "shared-agent")

        sconn = sqlite3.connect(src_path)
        m_src = _insert_memory(
            sconn, "idempotent claim", category="lesson",
            agent_id="shared-agent", confidence=0.8,
        )
        x_src = _insert_entity(sconn, "IdempotentThing", scope="g", agent_id="shared-agent")
        _insert_edge(
            sconn, "memories", m_src, "entities", x_src, "relates_to",
            weight=0.5, agent_id="shared-agent",
        )
        sconn.commit()
        sconn.close()

        merge_mod.merge(source_path=src_path, target_path=dst_path)
        merge_mod.merge(source_path=src_path, target_path=dst_path)  # again

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        check.close()
        assert diag["orphans"] == []
        assert diag["self_loops"] == []
        assert diag["duplicates"] == {}
