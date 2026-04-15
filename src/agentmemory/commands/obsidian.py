"""
brainctl obsidian — bidirectional sync between brain.db and an Obsidian vault.

Subcommands:
  export <vault_path>  — dump memories, entities, events to markdown
  import <vault_path>  — ingest markdown notes through the W(m) gate
  watch  <vault_path>  — watch vault for new/modified files, ingest on change
  status <vault_path>  — show sync status (counts, last export, drift)

Vault layout (all under <vault_path>/):
  brainctl/memories/<id>-<slug>.md   — one file per active memory
  brainctl/entities/<name>.md        — one file per entity (with wikilinks)
  brainctl/events/YYYY-MM-DD.md      — daily note per event date

Design follows the Karpathy "LLM Wiki" pattern:
  - Raw sources  → brain.db (SQLite, authoritative)
  - Wiki layer   → Obsidian markdown (navigable, linkable)
  - Schema layer → frontmatter YAML (machine-readable for re-import)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^\w\s-]")
_WS_RE = re.compile(r"[\s_-]+")

# Documented memory categories — kept in sync with the MCP tool enum
# in mcp_tools_lifecycle.py and the convention list in CLAUDE.md. Used
# by the import + watch paths to validate frontmatter-supplied categories
# and to fall back to a sane default.
_VALID_CATEGORIES = {
    "convention",
    "decision",
    "environment",
    "identity",
    "integration",
    "lesson",
    "preference",
    "project",
    "user",
}
_DEFAULT_CATEGORY = "project"


def _slug(text: str, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("", text.lower())
    s = _WS_RE.sub("-", s).strip("-")
    return s[:max_len] or "memory"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a leading YAML frontmatter block out of a markdown document.

    Returns ``(metadata, body)``. If there is no frontmatter, returns
    ``({}, text)``. Only handles the simple ``key: value`` line format —
    no nested structures, no multiline values, no YAML lists. That's
    enough for everything brainctl writes on export and everything an
    Obsidian user is likely to type by hand. We deliberately do not pull
    in PyYAML; the simple-case parser is ~20 lines and stays in-tree.
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing fence — it must be `\n---` on its own line.
    end = text.find("\n---", 3)
    if end == -1:
        # Unterminated frontmatter — treat the whole text as body.
        return {}, text

    block = text[3:end].lstrip("\n")
    body = text[end + 4:].lstrip("\n")

    metadata: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip simple wrapping quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            metadata[key] = value

    return metadata, body


def _category_from_metadata(metadata: dict[str, str], fallback: str = _DEFAULT_CATEGORY) -> str:
    """Pull a valid memory category out of frontmatter metadata, or fall back.

    Caller-supplied frontmatter wins as long as it names a category we
    recognize. Anything else falls through to ``fallback``.
    """
    raw = (metadata.get("category") or "").strip().lower()
    if raw and raw in _VALID_CATEGORIES:
        return raw
    return fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_db_path() -> Path:
    """Resolve brain.db path using the same env-var logic as _impl.py."""
    if os.environ.get("BRAIN_DB"):
        return Path(os.environ["BRAIN_DB"])
    if os.environ.get("BRAINCTL_HOME"):
        return Path(os.environ["BRAINCTL_HOME"]) / "db" / "brain.db"
    return Path.home() / "agentmemory" / "db" / "brain.db"


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------

def _render_memory_md(row: sqlite3.Row) -> str:
    tags_raw = row["tags"] or ""
    tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
    tags_yaml = ", ".join(tags_list) if tags_list else ""

    frontmatter = [
        "---",
        f"brainctl_id: {row['id']}",
        f"brainctl_type: memory",
        f"category: {row['category'] or 'general'}",
        f"confidence: {row['confidence']:.3f}",
    ]
    if tags_yaml:
        frontmatter.append(f"tags: [{tags_yaml}]")
    if row["scope"]:
        frontmatter.append(f"scope: {row['scope']}")
    if row["replay_priority"] and float(row["replay_priority"]) > 0:
        frontmatter.append(f"replay_priority: {row['replay_priority']:.2f}")
    created = row["created_at"] or _now_iso()
    frontmatter.append(f"created: {created}")
    frontmatter.append("---")

    content = row["content"] or ""
    body = [
        "",
        f"# Memory #{row['id']}",
        "",
        content,
    ]

    # Cross-links to source file if anchored
    try:
        fp = row["file_path"]
        line = row["file_line"]
    except (KeyError, IndexError):
        fp = None
        line = None
    if fp:
        ref = f"`{fp}`" + (f" line {line}" if line else "")
        body += ["", f"> Anchored to {ref}"]

    return "\n".join(frontmatter + body) + "\n"


def _render_entity_md(row: sqlite3.Row, observations: list[str]) -> str:
    props_raw = row["properties"] or "{}"
    try:
        props = json.loads(props_raw)
    except Exception:
        props = {}

    frontmatter = [
        "---",
        f"brainctl_id: {row['id']}",
        f"brainctl_type: entity",
        f"entity_type: {row['entity_type'] or 'concept'}",
        f"confidence: {row['confidence']:.3f}",
    ]
    if row["scope"]:
        frontmatter.append(f"scope: {row['scope']}")
    if props:
        frontmatter.append(f"properties: {json.dumps(props)}")
    created = row["created_at"] or _now_iso()
    frontmatter.append(f"created: {created}")
    frontmatter.append("---")

    name = row["name"] or f"Entity#{row['id']}"
    body = [
        "",
        f"# {name}",
    ]

    if observations:
        body += ["", "## Observations", ""]
        for obs in observations:
            body.append(f"- {obs}")

    return "\n".join(frontmatter + body) + "\n"


def _render_event_block(row: sqlite3.Row) -> str:
    ts = row["created_at"] or ""
    time_part = ts[11:19] if len(ts) >= 19 else ts
    etype = row["event_type"] or "event"
    summary = row["summary"] or ""
    lines = [f"### `{time_part}` [{etype}] {summary}"]
    if row["detail"]:
        lines.append("")
        lines.append(row["detail"])
    if row["project"]:
        lines.append(f"\n_Project: {row['project']}_")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def cmd_obsidian_export(args: Any) -> None:
    vault = Path(args.vault_path).expanduser().resolve()
    db_path = _get_db_path()

    if not db_path.exists():
        print(f"Error: brain.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    mem_dir = vault / "brainctl" / "memories"
    ent_dir = vault / "brainctl" / "entities"
    ev_dir = vault / "brainctl" / "events"
    for d in (mem_dir, ent_dir, ev_dir):
        d.mkdir(parents=True, exist_ok=True)

    conn = _open_db(db_path)

    # --- Memories ---
    # Build the WHERE clause with parameterized values — never
    # f-string-interpolate CLI args into SQL. The cols list is a
    # static string so it's fine to splice; the dynamic predicates
    # bind through `?` placeholders.
    cols = (
        "id, content, category, confidence, tags, scope, created_at, "
        "updated_at, recalled_count, replay_priority, file_path, file_line"
    )
    where_parts: list[str] = ["retired_at IS NULL"]
    where_params: list[Any] = []
    if args.scope:
        where_parts.append("scope = ?")
        where_params.append(args.scope)
    if args.category:
        where_parts.append("category = ?")
        where_params.append(args.category)

    rows = conn.execute(
        f"SELECT {cols} FROM memories WHERE {' AND '.join(where_parts)} ORDER BY id",
        where_params,
    ).fetchall()

    exported_mem = 0
    for row in rows:
        fname = f"{row['id']:06d}-{_slug(row['content'] or '')}.md"
        fpath = mem_dir / fname
        if not args.force and fpath.exists():
            continue
        fpath.write_text(_render_memory_md(row), encoding="utf-8")
        exported_mem += 1

    # --- Entities ---
    ent_rows = conn.execute(
        "SELECT id, name, entity_type, properties, observations, confidence, "
        "scope, created_at FROM entities WHERE retired_at IS NULL ORDER BY id"
    ).fetchall()

    exported_ent = 0
    for row in ent_rows:
        # Observations are stored as a JSON array in the observations column
        obs_list: list[str] = []
        try:
            raw_obs = row["observations"] or "[]"
            parsed = json.loads(raw_obs)
            if isinstance(parsed, list):
                obs_list = [str(o) for o in parsed if o]
        except Exception:
            pass

        # Relations stored in properties["relations"] if present, else empty
        rels: list = []

        name = row["name"] or f"entity-{row['id']}"
        # Prefix with the entity ID to avoid filename collisions when
        # two entities slug to the same string (e.g. "API rate limiter"
        # and "API rate limiting" both -> "api-rate-limit").
        fname = f"{row['id']:06d}-{_slug(name)}.md"
        fpath = ent_dir / fname
        if not args.force and fpath.exists():
            continue
        fpath.write_text(_render_entity_md(row, obs_list), encoding="utf-8")
        exported_ent += 1

    # --- Events (grouped by date) ---
    ev_rows = conn.execute(
        "SELECT id, summary, event_type, detail, project, created_at "
        "FROM events ORDER BY created_at, id"
    ).fetchall()

    # Group by date
    from collections import defaultdict
    by_date: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in ev_rows:
        ts = row["created_at"] or ""
        date = ts[:10] if len(ts) >= 10 else "unknown"
        by_date[date].append(row)

    exported_ev = 0
    for date, ev_list in sorted(by_date.items()):
        fpath = ev_dir / f"{date}.md"
        if not args.force and fpath.exists():
            continue
        header = [
            "---",
            f"brainctl_type: event_log",
            f"date: {date}",
            "---",
            "",
            f"# Events — {date}",
            "",
        ]
        blocks = [_render_event_block(r) for r in ev_list]
        fpath.write_text("\n".join(header) + "\n".join(blocks), encoding="utf-8")
        exported_ev += 1

    # Write vault index
    index_path = vault / "brainctl" / "README.md"
    index_path.write_text(
        f"# brainctl Brain Vault\n\n"
        f"Auto-generated by `brainctl obsidian export`.\n\n"
        f"| Layer | Location |\n"
        f"|-------|----------|\n"
        f"| Memories | [[brainctl/memories/]] |\n"
        f"| Entities | [[brainctl/entities/]] |\n"
        f"| Events | [[brainctl/events/]] |\n\n"
        f"_Last exported: {_now_iso()}_\n"
        f"_Source: `{db_path}`_\n",
        encoding="utf-8",
    )

    conn.close()
    print(
        f"Export complete → {vault}/brainctl/\n"
        f"  Memories: {exported_mem} written ({len(rows)} total active)\n"
        f"  Entities: {exported_ent} written ({len(ent_rows)} total)\n"
        f"  Event days: {exported_ev} written ({len(by_date)} total days)"
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _extract_entity_name_from_md(text: str, fallback: str) -> str:
    """Pull an entity name out of an imported markdown body.

    Looks for the first H1 (`# Name`) and falls back to the file basename
    if none is found. The H1 wins over the filename so users can rename
    by editing the heading without touching the file path.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def cmd_obsidian_import(args: Any) -> None:
    vault = Path(args.vault_path).expanduser().resolve()
    db_path = _get_db_path()

    if not db_path.exists():
        print(f"Error: brain.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    brain_dir = vault / "brainctl"
    if not brain_dir.exists():
        print(
            f"No brainctl/ directory found in {vault}.\n"
            f"Run `brainctl obsidian export {vault}` first, or point to a vault "
            f"with existing .md files.",
            file=sys.stderr,
        )
        sys.exit(1)

    agent_id = getattr(args, "agent", "obsidian-import")
    dry_run = getattr(args, "dry_run", False)

    # Collect all .md files without a brainctl_id (new notes created in Obsidian)
    new_files: list[Path] = []
    for md_file in sorted(brain_dir.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if "brainctl_id:" in text:
            continue  # already exported from brain.db — skip
        if md_file.name == "README.md":
            continue
        new_files.append(md_file)

    if not new_files:
        print("No new (non-exported) markdown files found to import.")
        return

    # Import through Brain — instantiate once and reuse so the v1.2.0
    # lazy shared sqlite connection is actually shared across files.
    # Constructing a new Brain per file recreates the connection and
    # defeats the optimization.
    from agentmemory.brain import Brain
    brain = None if dry_run else Brain(db_path=str(db_path), agent_id=agent_id)

    imported = 0
    skipped = 0
    for md_file in new_files:
        raw = md_file.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(raw)
        body = body.strip()

        if not body or len(body) < 20:
            skipped += 1
            continue

        # Determine kind (memory vs entity) from path, then category
        # from frontmatter (if supplied), with sensible per-kind defaults.
        rel = md_file.relative_to(brain_dir)
        is_entity = rel.parts[0] == "entities" if rel.parts else False
        category = _category_from_metadata(
            metadata, fallback="identity" if is_entity else _DEFAULT_CATEGORY
        )

        if dry_run:
            kind = "entity" if is_entity else "memory"
            print(
                f"[dry-run] Would import: {md_file.name} "
                f"(kind={kind}, category={category})"
            )
            imported += 1
            continue

        try:
            if is_entity:
                # Route through Brain.entity() so an Obsidian-authored
                # entity becomes an actual entity row, not a memory
                # mislabeled as 'identity'. Pull the canonical name
                # from the H1 heading, fall back to the filename slug.
                name = _extract_entity_name_from_md(body, md_file.stem)
                entity_type = (metadata.get("entity_type") or "concept").strip() or "concept"
                # Use the body (minus frontmatter) as a single observation.
                # Caller can split it later via `brainctl entity observe`.
                eid = brain.entity(
                    name=name,
                    entity_type=entity_type,
                    observations=[body],
                )
                print(f"  Imported {md_file.name} → entity #{eid} ({name})")
            else:
                mid = brain.remember(body, category=category)
                print(f"  Imported {md_file.name} → memory #{mid}")
            imported += 1
        except Exception as exc:
            print(f"  Skipped {md_file.name}: {exc}")
            skipped += 1

    suffix = " (dry-run)" if dry_run else ""
    print(
        f"\nImport complete{suffix}: {imported} imported, {skipped} skipped"
    )


# ---------------------------------------------------------------------------
# Watch
# ---------------------------------------------------------------------------

def cmd_obsidian_watch(args: Any) -> None:
    vault = Path(args.vault_path).expanduser().resolve()
    db_path = _get_db_path()

    if not db_path.exists():
        print(f"Error: brain.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
    except ImportError:
        print(
            "watchdog is required for `obsidian watch`.\n"
            "Install it: pip install watchdog\n"
            "Or install with extras: pip install brainctl[obsidian]",
            file=sys.stderr,
        )
        sys.exit(1)

    from agentmemory.brain import Brain

    agent_id = getattr(args, "agent", "obsidian-watch")
    cooldown = getattr(args, "cooldown", 5)  # seconds between processing same file

    # Hoist Brain construction OUTSIDE the per-event handler so the v1.2.0
    # lazy shared sqlite connection is actually shared across events. The
    # old per-event Brain() call rebuilt the connection on every file
    # touch — fine for one-off ingest, terrible for long-running watch.
    brain = Brain(db_path=str(db_path), agent_id=agent_id)

    # Bounded cooldown cache. Old impl kept entries forever for any file
    # ever touched, which leaked memory on long-running processes against
    # large vaults. We evict entries older than 5x the cooldown window.
    _recently_processed: dict[str, float] = {}
    _CACHE_TTL = max(cooldown * 5, 30)  # at least 30s

    def _evict_stale(now: float) -> None:
        if len(_recently_processed) < 256:
            return  # cheap path: no eviction needed for small caches
        cutoff = now - _CACHE_TTL
        stale = [k for k, t in _recently_processed.items() if t < cutoff]
        for k in stale:
            _recently_processed.pop(k, None)

    class VaultHandler(FileSystemEventHandler):
        def _handle(self, path_str: str) -> None:
            if not path_str.endswith(".md"):
                return
            path = Path(path_str)
            now = time.time()
            last = _recently_processed.get(path_str, 0)
            if now - last < cooldown:
                return
            _recently_processed[path_str] = now
            _evict_stale(now)

            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                return

            if "brainctl_id:" in raw:
                return  # our own export — skip

            metadata, body = _parse_frontmatter(raw)
            body = body.strip()

            if len(body) < 20:
                return  # too short to be meaningful

            # Honor frontmatter-supplied category if it's valid; otherwise
            # default to a documented category (was 'general' before, which
            # is NOT in the documented enum and would silently break
            # anything downstream that relies on the category constants).
            category = _category_from_metadata(metadata, fallback=_DEFAULT_CATEGORY)

            try:
                mid = brain.remember(body, category=category)
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Ingested {path.name} → memory #{mid} ({category})")
            except Exception as exc:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Skipped {path.name}: {exc}")

        def on_created(self, event: FileCreatedEvent) -> None:
            if not event.is_directory:
                self._handle(event.src_path)

        def on_modified(self, event: FileModifiedEvent) -> None:
            if not event.is_directory:
                self._handle(event.src_path)

    observer = Observer()
    observer.schedule(VaultHandler(), str(vault), recursive=True)
    observer.start()
    print(
        f"Watching {vault} for new/modified markdown files...\n"
        f"New notes will be ingested through the W(m) gate into {db_path}\n"
        f"Press Ctrl+C to stop."
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nStopped.")
    observer.join()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_obsidian_status(args: Any) -> None:
    vault = Path(args.vault_path).expanduser().resolve()
    db_path = _get_db_path()

    print(f"Vault:    {vault}")
    print(f"brain.db: {db_path}")
    print()

    if not db_path.exists():
        print("brain.db: NOT FOUND")
        return

    conn = _open_db(db_path)
    total_mem = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE retired_at IS NULL"
    ).fetchone()[0]
    total_ent = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE retired_at IS NULL"
    ).fetchone()[0]
    total_ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    brain_dir = vault / "brainctl"
    if not brain_dir.exists():
        print("Vault status: not yet exported")
        exported_mem = exported_ent = exported_ev_days = 0
    else:
        exported_mem = len(list((brain_dir / "memories").rglob("*.md"))) if (brain_dir / "memories").exists() else 0
        exported_ent = len(list((brain_dir / "entities").rglob("*.md"))) if (brain_dir / "entities").exists() else 0
        exported_ev_days = len(list((brain_dir / "events").glob("*.md"))) if (brain_dir / "events").exists() else 0

        readme = brain_dir / "README.md"
        if readme.exists():
            for line in readme.read_text().splitlines():
                if "_Last exported:" in line:
                    print(f"Last export: {line.strip().lstrip('_').rstrip('_')}")
                    break

    print(f"\n{'Resource':<20} {'brain.db':>10} {'vault':>10} {'drift':>10}")
    print("-" * 52)
    drift_mem = total_mem - exported_mem
    drift_ent = total_ent - exported_ent
    print(f"{'Memories':<20} {total_mem:>10} {exported_mem:>10} {drift_mem:>+10}")
    print(f"{'Entities':<20} {total_ent:>10} {exported_ent:>10} {drift_ent:>+10}")
    print(f"{'Event days':<20} {'—':>10} {exported_ev_days:>10} {'—':>10}")
    print(f"{'Total events':<20} {total_ev:>10} {'—':>10} {'—':>10}")

    if drift_mem > 0 or drift_ent > 0:
        print(f"\n{drift_mem + drift_ent} un-exported records — run `brainctl obsidian export {vault}` to sync.")
    else:
        print("\nVault is up to date.")


# ---------------------------------------------------------------------------
# Parser registration (called from _impl.py's build_parser)
# ---------------------------------------------------------------------------

def register_parser(sub: Any) -> None:
    """Add the `obsidian` subcommand tree to an existing subparsers object."""
    obs = sub.add_parser(
        "obsidian",
        help="Obsidian vault sync — export brain to markdown, import notes, watch for changes",
    )
    obs_sub = obs.add_subparsers(dest="obs_cmd")

    # export
    obs_export = obs_sub.add_parser(
        "export",
        help="Export brain.db to an Obsidian vault (memories, entities, events)",
    )
    obs_export.add_argument("vault_path", help="Path to the Obsidian vault directory")
    obs_export.add_argument(
        "--force", "-f", action="store_true",
        help="Overwrite existing files (default: skip existing)"
    )
    obs_export.add_argument(
        "--scope", "-s", default=None,
        help="Only export memories with this scope"
    )
    obs_export.add_argument(
        "--category", "-c", default=None,
        help="Only export memories with this category"
    )

    # import
    obs_import = obs_sub.add_parser(
        "import",
        help="Import new markdown notes from vault into brain.db (through W(m) gate)",
    )
    obs_import.add_argument("vault_path", help="Path to the Obsidian vault directory")
    obs_import.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would be imported without writing"
    )

    # watch
    obs_watch = obs_sub.add_parser(
        "watch",
        help="Watch vault for new/modified notes and ingest them automatically",
    )
    obs_watch.add_argument("vault_path", help="Path to the Obsidian vault directory")
    obs_watch.add_argument(
        "--cooldown", type=int, default=5, metavar="SECONDS",
        help="Minimum seconds between re-processing the same file (default: 5)"
    )

    # status
    obs_status = obs_sub.add_parser(
        "status",
        help="Show sync status: brain.db vs vault counts and drift",
    )
    obs_status.add_argument("vault_path", help="Path to the Obsidian vault directory")
