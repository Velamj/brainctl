"""SQL-injection regression tests for the 28 mcp_tools_*.py extension modules.

Two layers of defense are exercised here:

1. Behavioral tests for the two tool entry points hardened in this branch:
   - tool_task_update (mcp_tools_agents)        — _build_task_update_sql allowlist
   - tool_expertise_update (mcp_tools_expertise) — _build_expertise_update_sql allowlist

   Each gets:
     - a malicious-key call (column name attempts SQL injection)
     - a valid-key call (legitimate update)
   to prove the allowlist drops the injection attempt and the legitimate
   path still works.

2. Static "no f-string SQL without nosec" lint over all 28 mcp_tools_*.py
   modules. AST-based: walks every JoinedStr (f-string) node whose constant
   parts contain a SQL keyword AND whose formatted_values include at least
   one variable interpolation, then requires `# nosec B608` to appear within
   the line range of that node. Fails if a new violation is introduced.

The lint mirrors Worker C's nosec marker convention from src/agentmemory/
mcp_server.py:1406-1407 (see _build_trigger_update_sql).
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain
import agentmemory.mcp_tools_agents as agents_mod
import agentmemory.mcp_tools_expertise as expertise_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patched_db(tmp_path, monkeypatch):
    """Initialize a fresh brain.db schema and patch both modules to use it."""
    db_file = tmp_path / "brain.db"
    Brain(str(db_file))  # creates schema
    monkeypatch.setattr(agents_mod, "DB_PATH", db_file)
    monkeypatch.setattr(expertise_mod, "DB_PATH", db_file)
    # Seed an agent FK target for expertise rows
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, display_name, agent_type, status, "
        "created_at, updated_at) VALUES (?, 'Test', 'test', 'active', "
        "strftime('%Y-%m-%dT%H:%M:%S','now'), strftime('%Y-%m-%dT%H:%M:%S','now'))",
        ("sqli-test-agent",),
    )
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# Behavioral tests: tool_task_update allowlist
# ---------------------------------------------------------------------------

class TestTaskUpdateAllowlist:
    """Verify _build_task_update_sql intersects column names with the allowlist."""

    def test_helper_drops_unknown_columns(self):
        """A confused-LLM injection attempt via column name is silently dropped."""
        sql, params = agents_mod._build_task_update_sql([
            ("status", "completed"),
            ("'; DROP TABLE tasks;--", "ignored"),
            ("priority", "high"),
        ])
        # Only the two allowlisted columns survive.
        assert sql == "UPDATE tasks SET status = ?, priority = ? WHERE id = ?"
        assert params == ["completed", "high"]

    def test_helper_returns_none_when_all_keys_rejected(self):
        """All-bad keys → no SQL is emitted (caller surfaces 'no fields')."""
        sql, params = agents_mod._build_task_update_sql([
            ("evil_col", "x"),
            ("DROP TABLE", "y"),
        ])
        assert sql is None
        assert params == []

    def test_helper_accepts_only_documented_columns(self):
        """The frozenset is the source of truth — every entry must be valid."""
        for col in agents_mod._TASK_UPDATE_ALLOWED_COLUMNS:
            sql, params = agents_mod._build_task_update_sql([(col, "val")])
            assert sql is not None, f"col {col!r} unexpectedly rejected"
            assert col in sql

    def test_end_to_end_valid_update(self, patched_db):
        """Legitimate update path produces correct SQL and rowcount."""
        conn = sqlite3.connect(str(patched_db))
        conn.execute(
            "INSERT INTO tasks (id, title, status, priority) "
            "VALUES (?, ?, 'pending', 'medium')",
            (101, "Test task"),
        )
        conn.commit()
        conn.close()

        result = agents_mod.tool_task_update(
            agent_id="sqli-test-agent", id=101, status="completed",
        )
        assert result["ok"] is True
        # Verify update landed
        conn = sqlite3.connect(str(patched_db))
        row = conn.execute(
            "SELECT status, completed_at FROM tasks WHERE id = ?", (101,)
        ).fetchone()
        conn.close()
        assert row[0] == "completed"
        assert row[1] is not None  # completed_at populated

    def test_end_to_end_malicious_kwarg_does_not_inject(self, patched_db):
        """A malicious **kw key never reaches the SQL string."""
        conn = sqlite3.connect(str(patched_db))
        conn.execute(
            "INSERT INTO tasks (id, title, status, priority) "
            "VALUES (?, ?, 'pending', 'low')",
            (102, "Injection target"),
        )
        conn.commit()
        conn.close()

        # Inject via **kw with a SQL-fragment-shaped key. The dispatcher would
        # splat this through; the function must not iterate it into SQL.
        injection_kwargs = {"\"; DROP TABLE tasks; --": "evil"}
        result = agents_mod.tool_task_update(
            agent_id="sqli-test-agent",
            id=102,
            status="in_progress",
            **injection_kwargs,
        )
        assert result["ok"] is True

        # Sanity: tasks table still exists with the row.
        conn = sqlite3.connect(str(patched_db))
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (102,)
        ).fetchone()
        # Also confirm the unrelated row 101 (if test order matters) and
        # the table itself survived.
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        assert row is not None
        assert row[0] == "in_progress"
        assert n >= 1


# ---------------------------------------------------------------------------
# Behavioral tests: tool_expertise_update allowlist
# ---------------------------------------------------------------------------

class TestExpertiseUpdateAllowlist:
    """Verify _build_expertise_update_sql intersects column names with the allowlist."""

    def test_helper_drops_unknown_columns(self):
        sql, params = expertise_mod._build_expertise_update_sql([
            ("brier_score", 0.5),
            ("DROP TABLE agent_expertise", "x"),
            ("strength", 0.8),
        ])
        assert sql == "UPDATE agent_expertise SET brier_score=?, strength=? WHERE agent_id=? AND domain=?"
        assert params == [0.5, 0.8]

    def test_helper_returns_none_when_all_keys_rejected(self):
        sql, params = expertise_mod._build_expertise_update_sql([
            ("evil", "x"),
        ])
        assert sql is None
        assert params == []

    def test_helper_accepts_only_documented_columns(self):
        for col in expertise_mod._EXPERTISE_UPDATE_ALLOWED_COLUMNS:
            sql, params = expertise_mod._build_expertise_update_sql([(col, "val")])
            assert sql is not None, f"col {col!r} unexpectedly rejected"
            assert col in sql

    def test_end_to_end_valid_update(self, patched_db):
        # Seed an expertise row first via the helper that creates the table.
        conn = sqlite3.connect(str(patched_db))
        expertise_mod._ensure_expertise_table(conn)
        conn.execute(
            "INSERT INTO agent_expertise (agent_id, domain, strength, evidence_count) "
            "VALUES (?, ?, ?, ?)",
            ("sqli-test-agent", "python", 0.5, 3),
        )
        conn.commit()
        conn.close()

        result = expertise_mod.tool_expertise_update(
            agent_id="sqli-test-agent", domain="python",
            brier=0.42, strength=0.75,
        )
        assert result["ok"] is True
        assert result["brier_score"] == 0.42
        assert result["strength"] == 0.75

        conn = sqlite3.connect(str(patched_db))
        row = conn.execute(
            "SELECT brier_score, strength FROM agent_expertise "
            "WHERE agent_id=? AND domain=?",
            ("sqli-test-agent", "python"),
        ).fetchone()
        conn.close()
        assert row[0] == 0.42
        assert row[1] == 0.75

    def test_end_to_end_malicious_kwarg_does_not_inject(self, patched_db):
        conn = sqlite3.connect(str(patched_db))
        expertise_mod._ensure_expertise_table(conn)
        conn.execute(
            "INSERT INTO agent_expertise (agent_id, domain, strength, evidence_count) "
            "VALUES (?, ?, ?, ?)",
            ("sqli-test-agent", "rust", 0.6, 2),
        )
        conn.commit()
        conn.close()

        injection_kwargs = {"\"; DROP TABLE agent_expertise; --": "evil"}
        result = expertise_mod.tool_expertise_update(
            agent_id="sqli-test-agent", domain="rust",
            brier=0.3,
            **injection_kwargs,
        )
        assert result["ok"] is True

        conn = sqlite3.connect(str(patched_db))
        # Table still exists with our row
        n = conn.execute(
            "SELECT COUNT(*) FROM agent_expertise WHERE domain='rust'"
        ).fetchone()[0]
        conn.close()
        assert n == 1


# ---------------------------------------------------------------------------
# Static lint: every f-string SQL must be marked # nosec B608
# ---------------------------------------------------------------------------

# SQL keywords that mark a string literal as SQL. Conservative — keyword
# anywhere in the f-string fragments triggers the check.
_SQL_KEYWORDS = (
    "SELECT", "UPDATE", "INSERT", "DELETE", "CREATE", "DROP",
    "WHERE", "FROM", "SET", "JOIN", "VALUES",
)


def _module_files() -> list[Path]:
    """All mcp_tools_*.py extension modules under src/agentmemory/.

    Count is enforced by `test_module_count_matches_ext_modules_registry`
    against the actual `_EXT_MODULES` list in mcp_server.py — that's the
    canonical source of truth for which modules are wired into the server.
    """
    base = Path(__file__).resolve().parent.parent / "src" / "agentmemory"
    return sorted(base.glob("mcp_tools_*.py"))


def _fstring_has_sql(node: ast.JoinedStr) -> bool:
    """True if any constant part of the f-string contains a SQL keyword."""
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            text = part.value.upper()
            if any(kw in text for kw in _SQL_KEYWORDS):
                return True
    return False


def _fstring_has_interpolation(node: ast.JoinedStr) -> bool:
    """True if the f-string contains a {var}-style formatted value (not just constants)."""
    return any(isinstance(p, ast.FormattedValue) for p in node.values)


def _line_range(node: ast.AST) -> tuple[int, int]:
    """Inclusive (start_line, end_line) for an AST node."""
    start = node.lineno
    end = getattr(node, "end_lineno", node.lineno)
    return start, end


def _has_nosec_marker(source_lines: list[str], start: int, end: int) -> bool:
    """True if any line in [start, end] (1-indexed) contains '# nosec B608'."""
    # Allow the marker on the line immediately following the f-string too —
    # multi-line execute() calls often put it on the closing arg line.
    lo = max(1, start)
    hi = min(len(source_lines), end + 1)
    for i in range(lo, hi + 1):
        if "# nosec B608" in source_lines[i - 1]:
            return True
    return False


def _collect_execute_call_arg_ranges(tree: ast.AST) -> set[tuple[int, int]]:
    """Return inclusive line ranges of every f-string passed as the SQL arg
    to a .execute()/.executemany()/.executescript() call.

    This is the precise targeting rule: an f-string is "SQL" only if it lands
    in the SQL position of an execute call, not just because it happens to
    contain the word SELECT in an error message.
    """
    ranges: set[tuple[int, int]] = set()
    EXEC_METHODS = {"execute", "executemany", "executescript"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in EXEC_METHODS):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            ranges.add(_line_range(first))
        elif isinstance(first, ast.BinOp):
            # Handle e.g. .execute("a" + f"b" + "c") — recurse into operands
            for sub in ast.walk(first):
                if isinstance(sub, ast.JoinedStr):
                    ranges.add(_line_range(sub))
    return ranges


def test_no_unmarked_fstring_sql_in_tool_modules():
    """Every f-string passed to .execute()/etc. must carry a `# nosec B608`
    rationale (or be parameterized with ?-placeholders only — i.e., have no
    {var} interpolations at all).

    The rule:
    - parse each mcp_tools_*.py with ast,
    - find every Call to .execute()/.executemany()/.executescript() whose
      first arg is an f-string,
    - if that f-string has any {var} interpolation, require '# nosec B608'
      to appear within (or one line after) the f-string's source range.

    A failure here means: somebody added a new dynamic SQL fragment to an
    execute() call without either (a) eliminating the interpolation in
    favor of ?-binding or (b) marking why it's safe. The marker forces the
    safety judgment to be conscious and reviewable.
    """
    failures: list[str] = []
    for path in _module_files():
        text = path.read_text()
        source_lines = text.splitlines()
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as e:  # pragma: no cover — defensive
            failures.append(f"{path}: failed to parse ({e})")
            continue

        exec_fstring_ranges = _collect_execute_call_arg_ranges(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            rng = _line_range(node)
            if rng not in exec_fstring_ranges:
                continue  # not the SQL-arg of an execute()
            if not _fstring_has_interpolation(node):
                continue  # f-string with no {var} parts is just a string
            if not _fstring_has_sql(node):
                # Belt-and-suspenders: even an execute() arg should look like SQL.
                # If it doesn't have a SQL keyword, it's almost certainly not
                # something an attacker can twist into injection (e.g.,
                # "PRAGMA foreign_keys = ON"). Skip to avoid noisy fails.
                continue
            start, end = rng
            if not _has_nosec_marker(source_lines, start, end):
                snippet = source_lines[start - 1].strip()[:100]
                failures.append(
                    f"{path.name}:{start}: f-string SQL passed to execute() without `# nosec B608` marker:\n    {snippet}"
                )

    if failures:
        msg = (
            "Found f-string SQL inside execute() calls without a "
            "`# nosec B608` rationale marker in the mcp_tools_*.py modules. "
            "Either parameterize the SQL (use ? placeholders, drop the "
            "f-prefix) or, if the f-string is provably safe (column name "
            "from an allowlist, placeholder string '?,?,...', etc.), add a "
            "`# nosec B608 - <reason>` comment on the same line as the "
            "interpolation (or the line immediately after, for multi-line "
            "execute calls).\n\nViolations:\n" + "\n".join(failures)
        )
        pytest.fail(msg)


def test_module_count_matches_ext_modules_registry():
    """Sanity: the 28 mcp_tools_*.py files must match _EXT_MODULES in mcp_server."""
    base = Path(__file__).resolve().parent.parent / "src" / "agentmemory"
    found = {p.stem for p in base.glob("mcp_tools_*.py")}

    server_text = (base / "mcp_server.py").read_text()
    # _EXT_MODULES is a list of bare identifiers like "mcp_tools_agents"; pull
    # them out via a forgiving regex rather than parsing the whole file.
    import re
    block_match = re.search(
        r"_EXT_MODULES\s*=\s*\[(.*?)\]", server_text, re.DOTALL
    )
    assert block_match, "Could not locate _EXT_MODULES list in mcp_server.py"
    block = block_match.group(1)
    declared = set(re.findall(r"\bmcp_tools_\w+\b", block))

    missing_in_files = declared - found
    extra_in_files = found - declared
    assert not missing_in_files, (
        f"_EXT_MODULES declares modules with no source file: {missing_in_files}"
    )
    assert not extra_in_files, (
        f"Source has mcp_tools_*.py files not in _EXT_MODULES: {extra_in_files}"
    )
