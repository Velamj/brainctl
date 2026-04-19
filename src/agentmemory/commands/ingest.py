"""CLI surface for ``brainctl ingest`` — currently just ``ingest code`` (2.4.5+).

Mirrors the register-parser / dispatch pattern established by
``commands/wallet.py`` and ``commands/sign.py``. Lazy-imports the core
ingest module so this file is safe to import even when the optional
``[code]`` extra isn't installed.

Graceful degradation: if the user runs ``brainctl ingest code`` without
the extra we emit a single stderr warning + exit 1 with an install hint.
Matches the behaviour the rerank module uses when sentence-transformers
is missing.

Inspired by ``safishamsi/graphify`` (CLI shape / ``.graphifyignore``
semantics). Not a code port.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _emit(data: dict, as_json: bool) -> None:
    """Pretty-print a result dict. JSON mode is what every other
    brainctl command emits on --json, terminal mode is a short human
    summary — kept intentionally small to stay shell-pipeline friendly."""
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    if not data.get("ok", False):
        print(f"error: {data.get('error', 'unknown error')}", file=sys.stderr)
        return
    stats = data.get("stats", {})
    print(
        f"files: scanned={stats.get('files_scanned', 0)} "
        f"processed={stats.get('files_processed', 0)} "
        f"cached={stats.get('files_cached', 0)} "
        f"skipped={stats.get('files_skipped', 0)}"
    )
    print(
        f"graph: +entities={stats.get('entities_written', 0)} "
        f"~entities={stats.get('entities_updated', 0)} "
        f"+edges={stats.get('edges_written', 0)}"
    )
    if stats.get("errors"):
        print(f"errors ({len(stats['errors'])}): first 5 below", file=sys.stderr)
        for e in stats["errors"][:5]:
            print(f"  {e}", file=sys.stderr)


def cmd_ingest_code(args: Any) -> None:
    """Entry point for ``brainctl ingest code <path>``."""
    # Lazy-import — keeps this module importable without the extra.
    from agentmemory import code_ingest

    if not code_ingest.AVAILABLE:
        hint = code_ingest.availability_hint() or \
            "pip install 'brainctl[code]' to enable code ingestion"
        print(f"error: {hint}", file=sys.stderr)
        sys.exit(1)

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        sys.exit(1)
    if not root.is_dir():
        print(f"error: path is not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    # Parse languages subset
    languages = None
    if getattr(args, "languages", None):
        requested = [l.strip() for l in args.languages.split(",") if l.strip()]
        allowed = set(code_ingest.EXT_TO_LANG.values())
        bad = [l for l in requested if l not in allowed]
        if bad:
            print(f"error: unknown language(s): {bad}. "
                  f"Supported: {sorted(allowed)}", file=sys.stderr)
            sys.exit(1)
        languages = requested

    verbose = bool(getattr(args, "verbose", False))
    as_json = bool(getattr(args, "json", False))

    def _progress(relpath: str, status: str) -> None:
        if verbose and not as_json:
            print(f"  [{status:9}] {relpath}", file=sys.stderr)

    try:
        stats = code_ingest.ingest(
            root,
            scope=getattr(args, "scope", None) or "global",
            agent_id=getattr(args, "agent", None) or "code-ingest",
            languages=languages,
            use_cache=not bool(getattr(args, "no_cache", False)),
            max_files=int(getattr(args, "max_files", 10000)),
            on_file=_progress,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "ok": True,
        "root": str(root),
        "scope": getattr(args, "scope", None) or "global",
        "stats": {
            "files_scanned":   stats.files_scanned,
            "files_processed": stats.files_processed,
            "files_cached":    stats.files_cached,
            "files_skipped":   stats.files_skipped,
            "entities_written": stats.entities_written,
            "entities_updated": stats.entities_updated,
            "edges_written":    stats.edges_written,
            "errors": stats.errors,
        },
    }
    _emit(payload, as_json)


def cmd_ingest(args: Any) -> None:
    """Top-level dispatch: ``brainctl ingest <subcommand>``."""
    sub = getattr(args, "ingest_command", None)
    if sub == "code":
        cmd_ingest_code(args)
    else:
        # No subcommand — print a one-liner that points at the only
        # current subcommand. When we add more (docs, tickets, …) this
        # grows into a proper help block.
        print("usage: brainctl ingest code <path> [--scope S] [--languages L] "
              "[--no-cache] [--max-files N] [--verbose] [--json]",
              file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Parser registration (called from _impl.py's build_parser)
# ---------------------------------------------------------------------------

def register_parser(sub: Any) -> None:
    """Register the ``ingest`` subcommand group.

    Called by ``_impl.py:build_parser`` right next to the other
    ``commands/*.py`` ``register_parser`` calls.
    """
    p = sub.add_parser(
        "ingest",
        help="Ingest external artifacts (source code, etc.) into the knowledge graph",
    )
    isub = p.add_subparsers(dest="ingest_command", metavar="<subcommand>")

    code_p = isub.add_parser(
        "code",
        help="Parse a source tree into entities + knowledge_edges (requires brainctl[code])",
        description=(
            "Walks a directory, parses supported source files with tree-sitter "
            "(no LLM, no GPU), and writes file / function / class entities plus "
            "`contains` and `imports` relations into brain.db. Re-runs skip "
            "unchanged files via a SHA256 cache (migration 051). "
            "Requires: pip install 'brainctl[code]'."
        ),
    )
    code_p.add_argument(
        "path",
        help="Directory to scan. Inside a git repo we use `git ls-files` to "
             "honor .gitignore; otherwise we walk with a small hardcoded "
             "exclude list (node_modules, .venv, target, …).",
    )
    code_p.add_argument("--scope", default="global",
                        help="Entity scope (e.g. 'project:foo'). Default: global.")
    code_p.add_argument("--agent", default="code-ingest",
                        help="Agent id stamped on writes. Default: code-ingest.")
    code_p.add_argument("--languages", default=None,
                        help="Comma-separated subset, e.g. 'python,go'. "
                             "Default: all supported (python,typescript,go).")
    code_p.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="Ignore the SHA256 cache and re-parse every file.")
    code_p.add_argument("--max-files", dest="max_files", type=int, default=10000,
                        help="Hard cap on files processed per run. Default: 10000.")
    code_p.add_argument("--verbose", action="store_true",
                        help="Per-file progress on stderr.")
    code_p.add_argument("--json", action="store_true",
                        help="Machine-readable summary on stdout.")


__all__ = ["register_parser", "cmd_ingest", "cmd_ingest_code"]
