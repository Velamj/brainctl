"""Tests for brainctl obsidian export/import/status commands."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory.brain import Brain
import agentmemory.commands.obsidian as obs_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def brain_db(tmp_path):
    """Fresh brain.db with a few memories and an entity."""
    db_file = tmp_path / "brain.db"
    brain = Brain(db_path=str(db_file), agent_id="test-agent")
    brain.remember("Python type hints improve readability", category="convention")
    brain.remember("Always write tests before merging", category="workflow")
    brain.remember("Use atomic commits for clean history", category="workflow")

    # Add entity manually (must use agent_id already registered by brain)
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO entities (name, entity_type, properties, observations, "
            "agent_id, confidence, scope, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("Alice", "person", '{"role": "engineer"}', '["Joined team 2023"]',
             "test-agent", 1.0, "global", "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()

    return brain, db_file


@pytest.fixture
def vault(tmp_path):
    """Empty Obsidian vault directory."""
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def mock_db_path(monkeypatch, brain_db):
    """Patch _get_db_path to return our test db."""
    _, db_file = brain_db
    monkeypatch.setattr(obs_mod, "_get_db_path", lambda: db_file)
    return db_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs):
    """Build a fake argparse namespace with sensible defaults."""
    import argparse
    defaults = dict(
        agent="test-agent",
        force=False,
        scope=None,
        category=None,
        dry_run=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------


class TestSlug:
    def test_basic(self):
        assert obs_mod._slug("Hello World") == "hello-world"

    def test_strips_special(self):
        s = obs_mod._slug("foo/bar:baz!")
        assert "/" not in s
        assert "!" not in s

    def test_max_len(self):
        long = "a" * 100
        assert len(obs_mod._slug(long)) <= 40

    def test_empty(self):
        assert obs_mod._slug("") == "memory"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestObsidianExport:
    def test_creates_vault_structure(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        assert (vault / "brainctl" / "memories").exists()
        assert (vault / "brainctl" / "entities").exists()
        assert (vault / "brainctl" / "events").exists()
        assert (vault / "brainctl" / "README.md").exists()

    def test_exports_memories_as_md(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        mem_dir = vault / "brainctl" / "memories"
        files = list(mem_dir.glob("*.md"))
        assert len(files) == 3

    def test_memory_frontmatter(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        mem_dir = vault / "brainctl" / "memories"
        for f in mem_dir.glob("*.md"):
            text = f.read_text()
            assert "brainctl_id:" in text
            assert "brainctl_type: memory" in text
            assert "category:" in text
            assert "confidence:" in text

    def test_memory_content_in_body(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        mem_dir = vault / "brainctl" / "memories"
        all_text = " ".join(f.read_text() for f in mem_dir.glob("*.md"))
        assert "Python type hints" in all_text
        assert "Always write tests" in all_text

    def test_entity_exported(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        ent_dir = vault / "brainctl" / "entities"
        files = list(ent_dir.glob("*.md"))
        assert len(files) >= 1
        all_text = " ".join(f.read_text() for f in files)
        assert "Alice" in all_text
        assert "brainctl_type: entity" in all_text

    def test_force_flag_overwrites(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        # First export: files exist; second export without force: 0 new
        # Second export with force: files are overwritten
        args_force = _make_args(vault_path=str(vault), force=True)
        obs_mod.cmd_obsidian_export(args_force)  # should not crash

        mem_dir = vault / "brainctl" / "memories"
        files = list(mem_dir.glob("*.md"))
        assert len(files) == 3  # same count

    def test_category_filter(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault), category="convention", force=True)
        obs_mod.cmd_obsidian_export(args)

        mem_dir = vault / "brainctl" / "memories"
        files = list(mem_dir.glob("*.md"))
        assert len(files) == 1  # only convention memory

    def test_readme_contains_last_exported(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_export(args)

        readme = (vault / "brainctl" / "README.md").read_text()
        assert "Last exported" in readme

    def test_nonexistent_db_exits(self, tmp_path, vault, monkeypatch):
        monkeypatch.setattr(obs_mod, "_get_db_path", lambda: tmp_path / "missing.db")
        args = _make_args(vault_path=str(vault))
        with pytest.raises(SystemExit):
            obs_mod.cmd_obsidian_export(args)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class TestObsidianImport:
    def test_no_brainctl_dir_exits(self, brain_db, tmp_path, monkeypatch):
        _, db_file = brain_db
        monkeypatch.setattr(obs_mod, "_get_db_path", lambda: db_file)
        empty_vault = tmp_path / "empty_vault"
        empty_vault.mkdir()
        args = _make_args(vault_path=str(empty_vault))
        with pytest.raises(SystemExit):
            obs_mod.cmd_obsidian_import(args)

    def test_import_new_note(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        # Export first to create brainctl/ dir
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))

        # Create a new note without brainctl_id
        new_note = vault / "brainctl" / "memories" / "my-new-idea.md"
        new_note.write_text("This is a brand new insight I wrote in Obsidian.")

        before = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]

        obs_mod.cmd_obsidian_import(_make_args(vault_path=str(vault)))

        after = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]
        assert after > before

    def test_dry_run_does_not_write(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))

        new_note = vault / "brainctl" / "memories" / "dry-run-note.md"
        new_note.write_text("This should not be imported in dry-run mode.")

        before = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]

        obs_mod.cmd_obsidian_import(_make_args(vault_path=str(vault), dry_run=True))

        after = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]
        assert after == before  # nothing written

    def test_skips_exported_files(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))

        before = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]

        # Import with no new files — exported files have brainctl_id and are skipped
        obs_mod.cmd_obsidian_import(_make_args(vault_path=str(vault)))

        after = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]
        assert after == before

    def test_skips_short_content(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))

        short_note = vault / "brainctl" / "memories" / "short.md"
        short_note.write_text("hi")  # too short

        before = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]

        obs_mod.cmd_obsidian_import(_make_args(vault_path=str(vault)))

        after = sqlite3.connect(str(db_file)).execute(
            "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
        ).fetchone()[0]
        assert after == before


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestObsidianStatus:
    def test_status_no_vault(self, brain_db, tmp_path, mock_db_path, capsys):
        _, db_file = brain_db
        empty_vault = tmp_path / "empty_vault"
        empty_vault.mkdir()
        args = _make_args(vault_path=str(empty_vault))
        obs_mod.cmd_obsidian_status(args)
        out = capsys.readouterr().out
        assert "not yet exported" in out

    def test_status_after_export(self, brain_db, vault, mock_db_path, capsys):
        _, db_file = brain_db
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_status(args)
        out = capsys.readouterr().out
        assert "Memories" in out
        assert "Entities" in out

    def test_status_shows_drift(self, brain_db, vault, mock_db_path, capsys):
        _, db_file = brain_db
        # Export first
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))
        # Add a new memory (not yet exported)
        brain, _ = brain_db
        brain.remember("New memory not in vault", category="general")

        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_status(args)
        out = capsys.readouterr().out
        # Should show positive drift
        assert "un-exported" in out or "+" in out

    def test_status_missing_db(self, tmp_path, vault, monkeypatch, capsys):
        monkeypatch.setattr(obs_mod, "_get_db_path", lambda: tmp_path / "missing.db")
        args = _make_args(vault_path=str(vault))
        obs_mod.cmd_obsidian_status(args)
        out = capsys.readouterr().out
        assert "NOT FOUND" in out


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


class TestRenderMemoryMd:
    def _make_row(self, **kwargs):
        defaults = {
            "id": 1, "content": "Test content", "category": "general",
            "confidence": 0.9, "tags": "a, b", "scope": "global",
            "created_at": "2024-01-01T00:00:00", "replay_priority": 0.0,
            "file_path": None, "file_line": None,
        }
        defaults.update(kwargs)
        # sqlite3.Row-like: use a simple dict-access object
        return type("Row", (), {"__getitem__": lambda s, k: defaults[k],
                                "get": lambda s, k, d=None: defaults.get(k, d)})()

    def test_frontmatter_present(self):
        row = self._make_row()
        md = obs_mod._render_memory_md(row)
        assert md.startswith("---")
        assert "brainctl_id: 1" in md
        assert "category: general" in md

    def test_content_in_body(self):
        row = self._make_row(content="My special content")
        md = obs_mod._render_memory_md(row)
        assert "My special content" in md

    def test_tags_in_frontmatter(self):
        row = self._make_row(tags="alpha, beta")
        md = obs_mod._render_memory_md(row)
        assert "alpha" in md
        assert "beta" in md

    def test_file_anchor_shown(self):
        row = self._make_row(file_path="/src/main.py", file_line=42)
        md = obs_mod._render_memory_md(row)
        assert "main.py" in md
        assert "42" in md

    def test_no_replay_priority_when_zero(self):
        row = self._make_row(replay_priority=0.0)
        md = obs_mod._render_memory_md(row)
        assert "replay_priority" not in md


# ---------------------------------------------------------------------------
# v1.6.1 regression coverage: SQL injection, entity import, frontmatter,
# brain reuse, valid categories
# ---------------------------------------------------------------------------


class TestFrontmatterParser:
    """The simple `key: value` YAML parser added in v1.6.1."""

    def test_no_frontmatter(self):
        meta, body = obs_mod._parse_frontmatter("plain text body")
        assert meta == {}
        assert body == "plain text body"

    def test_basic_frontmatter(self):
        text = "---\ncategory: lesson\ntags: alpha\n---\n\nactual body"
        meta, body = obs_mod._parse_frontmatter(text)
        assert meta == {"category": "lesson", "tags": "alpha"}
        assert body == "actual body"

    def test_quoted_values_unwrapped(self):
        text = '---\ncategory: "lesson"\nname: \'Alice\'\n---\nbody'
        meta, body = obs_mod._parse_frontmatter(text)
        assert meta["category"] == "lesson"
        assert meta["name"] == "Alice"

    def test_unterminated_frontmatter_returns_full_text(self):
        text = "---\ncategory: lesson\nbody never closes"
        meta, body = obs_mod._parse_frontmatter(text)
        assert meta == {}
        assert "body never closes" in body

    def test_comment_lines_ignored(self):
        text = "---\n# this is a comment\ncategory: project\n---\nbody"
        meta, _ = obs_mod._parse_frontmatter(text)
        assert meta == {"category": "project"}


class TestCategoryFromMetadata:
    def test_valid_category_passes_through(self):
        assert obs_mod._category_from_metadata({"category": "lesson"}) == "lesson"

    def test_invalid_category_falls_back(self):
        assert (
            obs_mod._category_from_metadata({"category": "general"})
            == obs_mod._DEFAULT_CATEGORY
        )

    def test_empty_metadata_falls_back(self):
        assert obs_mod._category_from_metadata({}) == obs_mod._DEFAULT_CATEGORY

    def test_explicit_fallback(self):
        assert (
            obs_mod._category_from_metadata({}, fallback="identity")
            == "identity"
        )

    def test_default_category_is_documented(self):
        # The default must be in the documented enum, otherwise downstream
        # rerank profiles and decay constants break silently.
        assert obs_mod._DEFAULT_CATEGORY in obs_mod._VALID_CATEGORIES


class TestExportSqlInjection:
    """Regression test for the v1.6.0 SQL injection vulnerability in the
    export command's --scope and --category flags."""

    def test_scope_arg_does_not_run_sql(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        # Classic SQLi payload — if this gets f-string-interpolated into
        # the WHERE clause, sqlite will choke on the syntax error or
        # (worse) execute the DROP. Either way the test should fail.
        # With proper parameterization, the literal string is bound as
        # a value and sqlite just returns zero rows.
        evil = "x' OR 1=1; DROP TABLE memories--"
        args = _make_args(vault_path=str(vault), scope=evil)
        # Should run cleanly and produce zero memory exports
        obs_mod.cmd_obsidian_export(args)
        # And the memories table must still exist
        conn = sqlite3.connect(str(db_file))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
            ).fetchone()
            assert row is not None, "memories table was dropped — SQL injection succeeded"
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            assert count >= 3, "memories were deleted by injected SQL"
        finally:
            conn.close()

    def test_category_arg_does_not_run_sql(self, brain_db, vault, mock_db_path):
        _, db_file = brain_db
        evil = "convention'; DELETE FROM memories WHERE 1=1--"
        args = _make_args(vault_path=str(vault), category=evil)
        obs_mod.cmd_obsidian_export(args)
        conn = sqlite3.connect(str(db_file))
        try:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            assert count >= 3, "memories were deleted by injected SQL"
        finally:
            conn.close()

    def test_legitimate_scope_filter_still_works(self, brain_db, vault, mock_db_path):
        # Scope filter should work for real queries, just safely.
        args = _make_args(vault_path=str(vault), scope="global")
        obs_mod.cmd_obsidian_export(args)
        # Should produce output without crashing
        mem_dir = vault / "brainctl" / "memories"
        assert mem_dir.exists()


class TestEntityImportCreatesEntity:
    """v1.6.1: an entity-shaped markdown file in vault/brainctl/entities/
    must round-trip into the entities table, not the memories table."""

    def test_new_entity_file_creates_entity_row(
        self, brain_db, vault, mock_db_path
    ):
        _, db_file = brain_db

        # First export so the brainctl/ directory structure exists
        obs_mod.cmd_obsidian_export(_make_args(vault_path=str(vault)))

        # Drop a fresh entity-shaped note (no brainctl_id) into entities/
        ent_dir = vault / "brainctl" / "entities"
        new_entity = ent_dir / "bob.md"
        new_entity.write_text(
            "---\n"
            "entity_type: person\n"
            "---\n"
            "\n"
            "# Bob\n"
            "\n"
            "Backend engineer, joined 2024.\n",
            encoding="utf-8",
        )

        # Count entities before import
        conn = sqlite3.connect(str(db_file))
        before_entities = conn.execute(
            "SELECT COUNT(*) FROM entities"
        ).fetchone()[0]
        before_memories = conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        conn.close()

        # Run import
        obs_mod.cmd_obsidian_import(_make_args(vault_path=str(vault)))

        # Verify a NEW entity exists, no spurious memory was created
        conn = sqlite3.connect(str(db_file))
        try:
            after_entities = conn.execute(
                "SELECT COUNT(*) FROM entities"
            ).fetchone()[0]
            after_memories = conn.execute(
                "SELECT COUNT(*) FROM memories"
            ).fetchone()[0]
            bob = conn.execute(
                "SELECT name FROM entities WHERE name = 'Bob'"
            ).fetchone()
        finally:
            conn.close()

        assert after_entities == before_entities + 1, (
            "expected exactly one new entity row"
        )
        assert after_memories == before_memories, (
            "import should not create memories for entity files"
        )
        assert bob is not None, "Bob entity was not created"


class TestExtractEntityName:
    def test_h1_heading_used(self):
        text = "Some intro\n\n# Alice Smith\n\nbody"
        assert obs_mod._extract_entity_name_from_md(text, "fallback") == "Alice Smith"

    def test_first_h1_wins(self):
        text = "# First\n\n# Second"
        assert obs_mod._extract_entity_name_from_md(text, "fallback") == "First"

    def test_falls_back_to_filename_stem(self):
        assert obs_mod._extract_entity_name_from_md("no headings here", "alice") == "alice"
