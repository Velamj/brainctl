#!/usr/bin/env python3
"""Install the brainctl MCP server into Cursor's mcp.json.

Merges an idempotent `brainctl` entry into the top-level `mcpServers`
object of `~/.cursor/mcp.json` (or `$CURSOR_HOME/mcp.json`, or a
project-local `./.cursor/mcp.json` with `--project`). The merge touches
only the `brainctl` key so the rest of the user's Cursor MCP
configuration is left alone.

Usage:
    python3 plugins/cursor/brainctl/install.py              # install (global)
    python3 plugins/cursor/brainctl/install.py --project    # install into ./.cursor/mcp.json
    python3 plugins/cursor/brainctl/install.py --path PATH  # explicit target
    python3 plugins/cursor/brainctl/install.py --dry        # preview, no write
    python3 plugins/cursor/brainctl/install.py --print      # print entry only
    python3 plugins/cursor/brainctl/install.py --uninstall  # remove entry
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ENTRY: dict = {
    "command": "brainctl-mcp",
    "args": [],
    "env": {"BRAINCTL_AGENT_ID": "cursor"},
}


def global_config_path() -> Path:
    base = os.environ.get("CURSOR_HOME")
    if base:
        return Path(base).expanduser() / "mcp.json"
    return Path.home() / ".cursor" / "mcp.json"


def project_config_path() -> Path:
    return Path.cwd() / ".cursor" / "mcp.json"


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit(f"[brainctl] {path} is not a JSON object; refusing to edit.")
    return data


def merge(existing: dict) -> dict:
    """Return a copy of existing with the brainctl entry set under mcpServers."""
    out = dict(existing)
    servers = dict(out.get("mcpServers") or {})
    servers["brainctl"] = ENTRY
    out["mcpServers"] = servers
    return out


def remove_entry(existing: dict) -> dict:
    """Return a copy with the brainctl entry removed; drop mcpServers if empty."""
    out = dict(existing)
    servers = dict(out.get("mcpServers") or {})
    if "brainctl" not in servers:
        return out
    servers.pop("brainctl", None)
    if servers:
        out["mcpServers"] = servers
    else:
        out.pop("mcpServers", None)
    return out


def serialize(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".brainctl.bak")
    shutil.copy2(path, bak)
    return bak


def preflight_brainctl_mcp() -> None:
    """Warn (do not fail) if brainctl-mcp isn't on PATH."""
    if shutil.which("brainctl-mcp") is None:
        print(
            "[brainctl] WARNING: `brainctl-mcp` not found on PATH.\n"
            "           Cursor will fail to start the server until you run:\n"
            "               pip install 'brainctl[mcp]>=1.3.0'",
            file=sys.stderr,
        )


def resolve_path(args: argparse.Namespace) -> Path:
    if args.path:
        return Path(args.path).expanduser()
    if args.project:
        return project_config_path()
    return global_config_path()


def main() -> int:
    parser = argparse.ArgumentParser(description="Install brainctl MCP into Cursor.")
    parser.add_argument("--project", action="store_true",
                        help="Target ./.cursor/mcp.json instead of the global config.")
    parser.add_argument("--path", default=None,
                        help="Explicit path to the mcp.json file to edit.")
    parser.add_argument("--dry", action="store_true", help="Preview changes without writing.")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print the JSON entry to stdout and exit.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the brainctl entry from mcp.json.")
    args = parser.parse_args()

    if args.print_only:
        sys.stdout.write(json.dumps({"mcpServers": {"brainctl": ENTRY}}, indent=2) + "\n")
        return 0

    path = resolve_path(args)
    existing = read_config(path)

    if args.uninstall:
        servers = (existing.get("mcpServers") or {}) if isinstance(existing, dict) else {}
        if "brainctl" not in servers:
            print(f"[brainctl] no brainctl entry found in {path} — nothing to do.")
            return 0
        new_data = remove_entry(existing)
        if args.dry:
            print(f"[brainctl] --dry: would rewrite {path} without the brainctl entry.")
            return 0
        bak = backup(path)
        path.write_text(serialize(new_data), encoding="utf-8")
        print(f"[brainctl] removed brainctl entry from {path}"
              + (f" (backup: {bak})" if bak else ""))
        return 0

    preflight_brainctl_mcp()
    new_data = merge(existing)

    if new_data == existing:
        print(f"[brainctl] {path} already up to date.")
        return 0

    if args.dry:
        print(f"[brainctl] --dry: would write {path}:")
        print("---")
        sys.stdout.write(serialize(new_data))
        print("---")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    bak = backup(path)
    path.write_text(serialize(new_data), encoding="utf-8")
    print(f"[brainctl] installed brainctl MCP entry into {path}"
          + (f" (backup: {bak})" if bak else ""))
    print("[brainctl] next: copy plugins/cursor/brainctl/rules.mdc.template "
          "into your project's .cursor/rules/brainctl.mdc to enable session bookends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
