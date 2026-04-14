"""brainctl migration runner.

Reads migration SQL files from db/migrations/, applies unapplied ones in order,
tracks applied migrations in schema_versions table.
"""
import sqlite3
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "db" / "migrations"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _get_migrations() -> list[tuple[int, str, Path]]:
    """Return sorted list of (version, name, path) for all migration files."""
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r'^(\d+)_(.+)\.sql$', f.name)
        if m:
            version = int(m.group(1))
            name = m.group(2).replace('_', ' ')
            migrations.append((version, name, f))
    return migrations


def _ensure_schema_versions(conn: sqlite3.Connection) -> None:
    """Create schema_versions tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _get_applied(conn: sqlite3.Connection) -> set[int]:
    """Return set of already-applied migration versions."""
    try:
        rows = conn.execute("SELECT version FROM schema_versions").fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def status(db_path: str) -> dict:
    """Return migration status report."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    _ensure_schema_versions(conn)
    applied = _get_applied(conn)
    migrations = _get_migrations()

    rows = conn.execute(
        "SELECT version, name, applied_at FROM schema_versions ORDER BY version"
    ).fetchall() if applied else []
    applied_rows = [dict(r) for r in rows]

    pending = [(v, n, p) for v, n, p in migrations if v not in applied]
    conn.close()

    return {
        "total": len(migrations),
        "applied": len(applied),
        "pending": len(pending),
        "applied_migrations": applied_rows,
        "pending_migrations": [{"version": v, "name": n, "file": str(p.name)} for v, n, p in pending],
    }


def run(db_path: str, dry_run: bool = False) -> dict:
    """Apply all pending migrations. Returns result dict."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema_versions(conn)
    applied_set = _get_applied(conn)
    migrations = _get_migrations()
    pending = [(v, n, p) for v, n, p in migrations if v not in applied_set]

    if not pending:
        conn.close()
        return {"ok": True, "applied": 0, "dry_run": dry_run, "message": "Already up to date."}

    applied = []
    errors = []
    for version, name, path in pending:
        sql = path.read_text()
        if dry_run:
            applied.append({"version": version, "name": name, "file": path.name, "dry_run": True})
            continue
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _utc_now_iso())
            )
            conn.commit()
            applied.append({"version": version, "name": name, "file": path.name})
        except Exception as exc:
            errors.append({"version": version, "name": name, "error": str(exc)})
            break  # stop on first error

    conn.close()
    return {
        "ok": len(errors) == 0,
        "applied": len(applied),
        "dry_run": dry_run,
        "migrations": applied,
        "errors": errors,
    }


def mark_applied_up_to(db_path: str, up_to: int, dry_run: bool = False) -> dict:
    """Backfill schema_versions for migrations 1..up_to.

    For users whose brain.db predates the migration tracking framework:
    their schema already has the effects of many migrations, but
    schema_versions is empty (or partial), so `brainctl migrate` would
    try to re-apply everything and crash on column collisions.

    This command marks migrations 1..up_to as applied (INSERT OR IGNORE)
    with a 'backfilled' suffix on the name, so the runner will skip them
    on subsequent `brainctl migrate` invocations. It does NOT execute the
    migration SQL — it only updates the tracker.

    Guard: refuses if up_to is LOWER than the max version already tracked.
    That would mark earlier migrations as applied when the user presumably
    already skipped them intentionally. Backfilling ABOVE the current
    high-water mark is always allowed — this handles the case where a
    user ran `brainctl migrate`, got a few through, crashed, and now needs
    to skip the rest.

    Args:
        db_path: path to brain.db
        up_to: highest migration version to mark as applied (inclusive)
        dry_run: preview only; no writes

    Returns:
        dict with keys: ok, dry_run, marked (list of versions), skipped
        (list of already-tracked versions), guard_error (if refused), etc.
    """
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema_versions(conn)

    migrations = _get_migrations()
    max_available = max((v for v, _, _ in migrations), default=0)

    if up_to < 1:
        conn.close()
        return {
            "ok": False,
            "dry_run": dry_run,
            "guard_error": f"--mark-applied-up-to must be >= 1 (got {up_to})",
        }
    if up_to > max_available:
        conn.close()
        return {
            "ok": False,
            "dry_run": dry_run,
            "guard_error": (
                f"--mark-applied-up-to {up_to} exceeds highest available migration "
                f"version ({max_available}). Check db/migrations/ or pass a lower N."
            ),
        }

    # Guard: refuse if up_to is lower than the max currently-tracked version.
    # Allow equal or higher — users who crashed mid-migrate need to backfill
    # above their current high-water mark.
    existing = conn.execute(
        "SELECT version, name, applied_at FROM schema_versions ORDER BY version"
    ).fetchall()
    existing_versions = {r["version"] for r in existing}
    max_tracked = max(existing_versions, default=0)

    if existing_versions and up_to < max_tracked:
        conn.close()
        return {
            "ok": False,
            "dry_run": dry_run,
            "max_tracked": max_tracked,
            "guard_error": (
                f"--mark-applied-up-to {up_to} is lower than the highest already-tracked "
                f"migration ({max_tracked}). Backfilling below the current high-water mark "
                f"would mark migrations as applied that were presumably skipped for a reason. "
                f"If you really need to rewrite tracker state, edit schema_versions directly."
            ),
        }

    # Figure out which versions to mark. Every migration version in [1..up_to]
    # that isn't already tracked gets a row. Collapse duplicate-version files
    # (e.g. 012_neuromodulation_state.sql + 012_theory_of_mind.sql) into one
    # representative row — we mark the version, not the file.
    targets: dict[int, str] = {}
    for version, name, _path in migrations:
        if version <= up_to and version not in existing_versions:
            # If there are multiple files sharing a version, prefer the
            # alphabetically first (what _get_migrations already sorted by).
            if version not in targets:
                targets[version] = name

    if not targets:
        conn.close()
        return {
            "ok": True,
            "dry_run": dry_run,
            "marked": [],
            "skipped": sorted(existing_versions & set(range(1, up_to + 1))),
            "max_tracked": max_tracked,
            "up_to": up_to,
            "message": "Nothing to backfill — schema_versions is already at or above up_to.",
        }

    if dry_run:
        conn.close()
        return {
            "ok": True,
            "dry_run": True,
            "would_mark": [{"version": v, "name": n} for v, n in sorted(targets.items())],
            "already_tracked": sorted(existing_versions),
            "up_to": up_to,
        }

    applied_at = _utc_now_iso()
    marked = []
    for version, name in sorted(targets.items()):
        tracker_name = f"{name} (backfilled)"
        conn.execute(
            "INSERT OR IGNORE INTO schema_versions (version, name, applied_at) "
            "VALUES (?, ?, ?)",
            (version, tracker_name, applied_at),
        )
        marked.append({"version": version, "name": tracker_name})
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "dry_run": False,
        "marked": marked,
        "applied_at": applied_at,
        "up_to": up_to,
        "previous_max_tracked": max_tracked,
    }


def status_verbose(db_path: str) -> dict:
    """Like status(), plus per-migration heuristic: does the schema already
    look like this migration has run?

    The heuristic is cheap and best-effort: for each migration file, parse
    ADD COLUMN statements and CREATE TABLE statements, then check sqlite_master
    / PRAGMA table_info to see if those columns/tables exist. If everything
    the migration would create already exists, mark it "likely-applied".
    If nothing exists, mark it "pending". Mixed state → "partial".

    Used by `brainctl doctor` and `brainctl migrate --status-verbose` to help
    users pick a safe value for `--mark-applied-up-to N`.
    """
    base = status(db_path)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row

    # Cheap helpers: collect existing tables and (table → columns)
    existing_tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    table_columns: dict[str, set[str]] = {}
    for t in existing_tables:
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
            table_columns[t] = cols
        except sqlite3.OperationalError:
            table_columns[t] = set()
    conn.close()

    # Regex the SQL files — imperfect but good enough for a hint
    add_col_re = re.compile(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
        re.IGNORECASE,
    )
    create_tbl_re = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )

    annotated = []
    for version, name, path in _get_migrations():
        sql = path.read_text()
        expected_cols = add_col_re.findall(sql)   # list of (table, col)
        expected_tbls = create_tbl_re.findall(sql)  # list of table names

        col_hits = 0
        col_total = 0
        for table, col in expected_cols:
            col_total += 1
            if col in table_columns.get(table, set()):
                col_hits += 1

        tbl_hits = 0
        tbl_total = 0
        for table in expected_tbls:
            tbl_total += 1
            if table in existing_tables:
                tbl_hits += 1

        total_checks = col_total + tbl_total
        hits = col_hits + tbl_hits

        if total_checks == 0:
            heuristic = "unknown"  # no DDL we can introspect (trigger-only, UPDATE-only, etc.)
        elif hits == total_checks:
            heuristic = "likely-applied"
        elif hits == 0:
            heuristic = "pending"
        else:
            heuristic = "partial"

        annotated.append({
            "version": version,
            "name": name,
            "file": path.name,
            "heuristic": heuristic,
            "ddl_hits": f"{hits}/{total_checks}",
        })

    return {
        **base,
        "migrations_verbose": annotated,
    }
