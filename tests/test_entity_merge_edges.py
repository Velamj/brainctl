"""Smoke tests for Bug 6 — entity merge knowledge_edges integrity.

Two paths are exercised:

1. Cross-DB merge via :mod:`agentmemory.merge` (ATTACH-based). When src and
   target share an entity by (name, scope) but have different ids, edges
   referencing those entities must be remapped — not copied verbatim — so
   they don't dangle (Bug 6a).

2. In-DB merge via :func:`agentmemory.mcp_tools_reconcile.tool_entity_merge`.
   When edges exist in both directions between the primary and the
   duplicate (e.g. ``dup -> X`` AND ``X -> dup``) with the same relation
   type, the redirect must not produce duplicate (source, target, relation)
   tuples (Bug 6b).

Both paths verify the same three invariants on the post-merge state:
    (a) no orphan edges (no edge points at a retired or non-existent entity)
    (b) no duplicate (source_table, source_id, target_table, target_id,
        relation_type) tuples
    (c) all distinct relation_types from the input survive (no information
        loss beyond the documented self-loop drop and weight-max collapse)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain
import agentmemory.merge as merge_mod
import agentmemory.mcp_tools_reconcile as reconcile_mod


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
    src_id: int,
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
        "('entities', ?, 'entities', ?, ?, ?, ?, '2024-01-01T00:00:00Z')",
        (src_id, tgt_id, relation, weight, agent_id),
    )
    return cur.lastrowid


def _audit_edge_integrity(conn: sqlite3.Connection) -> dict:
    """Return diagnostics suitable for assertions."""
    # All entity-referencing edges
    edges = conn.execute(
        "SELECT id, source_table, source_id, target_table, target_id, "
        "relation_type, weight FROM knowledge_edges"
    ).fetchall()

    # Active entity ids
    active_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM entities WHERE retired_at IS NULL"
        ).fetchall()
    }
    all_ids = {
        row[0]
        for row in conn.execute("SELECT id FROM entities").fetchall()
    }

    orphans: list[tuple] = []  # edges pointing at retired/non-existent entities
    self_loops: list[tuple] = []
    seen_keys: dict[tuple, list[int]] = {}

    for e in edges:
        eid, st, si, tt, ti, rt, w = e
        key = (st, si, tt, ti, rt)
        seen_keys.setdefault(key, []).append(eid)

        if st == "entities":
            if si not in active_ids:
                orphans.append((eid, "source", si, "active" if si in all_ids else "missing"))
        if tt == "entities":
            if ti not in active_ids:
                orphans.append((eid, "target", ti, "active" if ti in all_ids else "missing"))

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
# Bug 6a — cross-DB merge.py path
# ---------------------------------------------------------------------------

class TestCrossDBMergeEdgeIntegrity:
    """The merge.py path: src and target are different brain.db files."""

    def _make_db(self, path: Path, agent_id: str) -> str:
        Brain(db_path=str(path), agent_id=agent_id)
        return str(path)

    def test_redirected_edges_no_orphans_no_dupes(self, tmp_path):
        # Setup: src has entities Alice (id=A1) and Bob (id=B1), edge A1->B1
        # ('knows', weight=0.7). Target has Alice (id=A2, same name+scope)
        # and Bob (id=B2), with edge A2->B2 ('knows', weight=0.4).
        #
        # After merge: src's Alice maps to target's A2, src's Bob maps to
        # target's B2. The src edge A1->B1 redirects to A2->B2 — colliding
        # with the existing edge. Highest-weight rule should keep weight=0.7.
        src_path = self._make_db(tmp_path / "src.db", "src-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "dst-agent")

        # Populate src
        sconn = sqlite3.connect(src_path)
        a1 = _insert_entity(sconn, "Alice", scope="shared", agent_id="src-agent")
        b1 = _insert_entity(sconn, "Bob", scope="shared", agent_id="src-agent")
        _insert_edge(sconn, a1, b1, "knows", weight=0.7, agent_id="src-agent")
        # An additional edge that has no analog in target — redirect must
        # still preserve it (different relation_type).
        _insert_edge(sconn, a1, b1, "mentored", weight=0.5, agent_id="src-agent")
        sconn.commit()
        sconn.close()

        # Populate target
        dconn = sqlite3.connect(dst_path)
        a2 = _insert_entity(dconn, "Alice", scope="shared", agent_id="dst-agent")
        b2 = _insert_entity(dconn, "Bob", scope="shared", agent_id="dst-agent")
        _insert_edge(dconn, a2, b2, "knows", weight=0.4, agent_id="dst-agent")
        dconn.commit()
        dconn.close()

        # Sanity: the src ids should differ from target ids so a verbatim
        # copy of edges WOULD orphan them. (If by chance they collided we'd
        # still want the test to be meaningful — assert the redirect path is
        # exercised.)
        # In practice this is true because both DBs auto-increment from 1
        # and the agent insert orderings differ; we don't strictly need it
        # for the audit invariants to hold.

        report = merge_mod.merge(source_path=src_path, target_path=dst_path)
        assert report["dry_run"] is False

        # === Audit target ===
        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        check.close()

        assert diag["orphans"] == [], (
            f"redirected edges must not point at unknown entities; got {diag['orphans']}"
        )
        assert diag["self_loops"] == [], (
            f"no self-loops should be produced by the remap; got {diag['self_loops']}"
        )
        assert diag["duplicates"] == {}, (
            f"unique (src,tgt,relation) tuples must not duplicate; got {diag['duplicates']}"
        )

        # The 'knows' edge must exist exactly once with the higher weight.
        check = sqlite3.connect(dst_path)
        edges_knows = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='entities' "
            "AND source_id=? AND target_table='entities' AND target_id=? "
            "AND relation_type=?",
            (a2, b2, "knows"),
        ).fetchall()
        edges_mentored = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_table='entities' "
            "AND source_id=? AND target_table='entities' AND target_id=? "
            "AND relation_type=?",
            (a2, b2, "mentored"),
        ).fetchall()
        check.close()

        assert len(edges_knows) == 1, "knows edge must collapse to a single row"
        # Highest-weight-wins: src had 0.7, dst had 0.4 → keep 0.7.
        assert abs(edges_knows[0][0] - 0.7) < 1e-9, (
            f"highest-weight-wins violated: expected 0.7, got {edges_knows[0][0]}"
        )
        assert len(edges_mentored) == 1, (
            "the src-only 'mentored' relation_type must be preserved (no info loss)"
        )

    def test_self_loops_dropped_on_remap_collapse(self, tmp_path):
        # When src has an edge X1 -> Y1 and BOTH X1 and Y1 map to the same
        # target id (they happen to share name+scope under the merge), the
        # post-remap edge would be a self-loop. We drop it.
        src_path = self._make_db(tmp_path / "src.db", "src-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "dst-agent")

        sconn = sqlite3.connect(src_path)
        # Use scope="shared" twice with the same name only on the SRC side —
        # the unique index on entities is on (name, scope) WHERE retired_at
        # IS NULL via the active partial. We use distinct names in src but
        # with the same target-side mapping… Actually, the simpler way to
        # provoke the collapse is: src has entity "Alice" (a1) and the
        # target also has entity "Alice", so a1 -> a2. If we want both ends
        # to collapse to a2, src needs two entities both mapping to Alice.
        # But (name, scope) uniqueness on src precludes that. So the only
        # way to provoke a self-loop in the cross-DB path is via two
        # different src entities whose remap accidentally collapses — which
        # only happens if target already has them under the same id, which
        # again is impossible.
        #
        # In the cross-DB path, self-loops therefore only arise if the SOURCE
        # had a self-loop to begin with. Test that case: src has Alice with
        # an edge Alice -> Alice ('self_ref'); the redirect to target's
        # Alice id must drop it.
        a1 = _insert_entity(sconn, "Alice", scope="shared", agent_id="src-agent")
        _insert_edge(sconn, a1, a1, "self_ref", weight=0.9, agent_id="src-agent")
        sconn.commit()
        sconn.close()

        dconn = sqlite3.connect(dst_path)
        _insert_entity(dconn, "Alice", scope="shared", agent_id="dst-agent")
        dconn.commit()
        dconn.close()

        merge_mod.merge(source_path=src_path, target_path=dst_path)

        check = sqlite3.connect(dst_path)
        diag = _audit_edge_integrity(check)
        check.close()
        assert diag["self_loops"] == [], (
            f"a src-side self-loop must be dropped after remap; got {diag['self_loops']}"
        )

    def test_idempotent_re_merge(self, tmp_path):
        # Merging the same src twice must not create new duplicates and
        # must not orphan anything.
        src_path = self._make_db(tmp_path / "src.db", "src-agent")
        dst_path = self._make_db(tmp_path / "dst.db", "dst-agent")

        sconn = sqlite3.connect(src_path)
        a = _insert_entity(sconn, "Alice", scope="g", agent_id="src-agent")
        b = _insert_entity(sconn, "Bob", scope="g", agent_id="src-agent")
        _insert_edge(sconn, a, b, "knows", weight=0.6, agent_id="src-agent")
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


# ---------------------------------------------------------------------------
# Bug 6b — in-DB tool_entity_merge path
# ---------------------------------------------------------------------------

class TestInDBEntityMergeEdgeIntegrity:
    """The reconcile MCP tool path: merge happens within one brain.db."""

    @pytest.fixture
    def db_file(self, tmp_path):
        path = tmp_path / "brain.db"
        Brain(db_path=str(path), agent_id="test-agent")
        reconcile_mod.DB_PATH = path
        return path

    def test_cross_direction_collision_collapses_no_dupes(self, db_file):
        # Primary=alice_p, dup=alice_d, plus a third entity X.
        # Edges:
        #   alice_d -> X  (knows, weight=0.6)
        #   X -> alice_d  (knows, weight=0.4)
        #   alice_p -> X  (knows, weight=0.5)   <-- collides with first redirect
        #   X -> alice_p  (knows, weight=0.3)   <-- collides with second redirect
        #
        # After merge: alice_d's edges redirect to alice_p. Both source-side
        # AND target-side collisions are present. The naive code would either
        # crash on the unique index or leave duplicates. The fix must keep
        # exactly one edge in each direction with MAX(weight).
        conn = sqlite3.connect(str(db_file))
        alice_p = _insert_entity(conn, "Alice", scope="agent:p")
        alice_d = _insert_entity(conn, "Alice", scope="agent:d")
        x_id = _insert_entity(conn, "Project-X", scope="global")
        _insert_edge(conn, alice_d, x_id, "knows", weight=0.6)
        _insert_edge(conn, x_id, alice_d, "knows", weight=0.4)
        _insert_edge(conn, alice_p, x_id, "knows", weight=0.5)
        _insert_edge(conn, x_id, alice_p, "knows", weight=0.3)
        conn.commit()
        conn.close()

        result = reconcile_mod.tool_entity_merge(
            primary_id=alice_p, duplicate_ids=[alice_d], dry_run=False
        )
        assert result["ok"] is True, result
        assert result["dry_run"] is False

        check = sqlite3.connect(str(db_file))
        diag = _audit_edge_integrity(check)

        assert diag["orphans"] == [], f"no orphans allowed, got {diag['orphans']}"
        assert diag["self_loops"] == [], f"no self-loops, got {diag['self_loops']}"
        assert diag["duplicates"] == {}, (
            f"unique (src,tgt,rel) tuples must not duplicate; got {diag['duplicates']}"
        )

        # Verify highest-weight-wins per direction.
        forward = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_id=? "
            "AND target_id=? AND relation_type='knows'",
            (alice_p, x_id),
        ).fetchall()
        backward = check.execute(
            "SELECT weight FROM knowledge_edges WHERE source_id=? "
            "AND target_id=? AND relation_type='knows'",
            (x_id, alice_p),
        ).fetchall()
        check.close()

        assert len(forward) == 1, f"alice_p->X must collapse to one edge, got {forward}"
        assert len(backward) == 1, f"X->alice_p must collapse to one edge, got {backward}"
        # Forward winners: dup had 0.6, primary had 0.5 → 0.6
        assert abs(forward[0][0] - 0.6) < 1e-9, (
            f"forward MAX violated; expected 0.6 got {forward[0][0]}"
        )
        # Backward winners: dup had 0.4, primary had 0.3 → 0.4
        assert abs(backward[0][0] - 0.4) < 1e-9, (
            f"backward MAX violated; expected 0.4 got {backward[0][0]}"
        )

    def test_self_loop_when_dup_points_at_primary(self, db_file):
        # Edge dup -> primary directly. Post-redirect this becomes
        # primary -> primary, which we drop as a self-loop.
        conn = sqlite3.connect(str(db_file))
        p = _insert_entity(conn, "Alice", scope="agent:p")
        d = _insert_entity(conn, "Alice", scope="agent:d")
        _insert_edge(conn, d, p, "alias_of", weight=1.0)
        _insert_edge(conn, p, d, "knows", weight=0.5)  # also collapses
        conn.commit()
        conn.close()

        reconcile_mod.tool_entity_merge(
            primary_id=p, duplicate_ids=[d], dry_run=False
        )

        check = sqlite3.connect(str(db_file))
        diag = _audit_edge_integrity(check)
        check.close()
        assert diag["self_loops"] == []
        assert diag["orphans"] == []
        assert diag["duplicates"] == {}

    def test_distinct_relation_types_preserved(self, db_file):
        # Multiple distinct relation_types between dup and X must each
        # survive (no information loss for unrelated relations).
        conn = sqlite3.connect(str(db_file))
        p = _insert_entity(conn, "Alice", scope="agent:p")
        d = _insert_entity(conn, "Alice", scope="agent:d")
        x = _insert_entity(conn, "Project-X", scope="global")
        _insert_edge(conn, d, x, "knows", weight=0.6)
        _insert_edge(conn, d, x, "works_on", weight=0.7)
        _insert_edge(conn, d, x, "owns", weight=0.8)
        conn.commit()
        conn.close()

        reconcile_mod.tool_entity_merge(
            primary_id=p, duplicate_ids=[d], dry_run=False
        )

        check = sqlite3.connect(str(db_file))
        rels = {
            r[0]
            for r in check.execute(
                "SELECT relation_type FROM knowledge_edges WHERE source_id=?",
                (p,),
            ).fetchall()
        }
        check.close()
        assert {"knows", "works_on", "owns"}.issubset(rels), (
            f"all relation_types must survive; got {rels}"
        )

    def test_no_orphans_after_dup_retired(self, db_file):
        # The duplicate is retired after merge. Any edge still pointing at
        # the retired entity is an orphan. Verify all dup-referencing edges
        # have either been redirected or dropped.
        conn = sqlite3.connect(str(db_file))
        p = _insert_entity(conn, "Alice", scope="agent:p")
        d = _insert_entity(conn, "Alice", scope="agent:d")
        x = _insert_entity(conn, "X", scope="global")
        y = _insert_entity(conn, "Y", scope="global")
        _insert_edge(conn, d, x, "knows", weight=0.5)
        _insert_edge(conn, y, d, "mentions", weight=0.4)
        conn.commit()
        conn.close()

        reconcile_mod.tool_entity_merge(
            primary_id=p, duplicate_ids=[d], dry_run=False
        )

        check = sqlite3.connect(str(db_file))
        # Any remaining edge with source_id=d or target_id=d on entities is
        # an orphan since d is now retired.
        bad_src = check.execute(
            "SELECT id FROM knowledge_edges WHERE source_table='entities' "
            "AND source_id=?",
            (d,),
        ).fetchall()
        bad_tgt = check.execute(
            "SELECT id FROM knowledge_edges WHERE target_table='entities' "
            "AND target_id=?",
            (d,),
        ).fetchall()
        check.close()
        assert bad_src == [], f"edges still reference retired dup as source: {bad_src}"
        assert bad_tgt == [], f"edges still reference retired dup as target: {bad_tgt}"
