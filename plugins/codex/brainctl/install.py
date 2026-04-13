#!/usr/bin/env python3
"""Install the brainctl MCP server into Codex CLI's config.toml.

Merges an idempotent `[mcp_servers.brainctl]` block into
`~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) wrapped in sentinel
comments so it can be updated or removed cleanly without touching the rest
of the user's Codex configuration.

Usage:
    python3 plugins/codex/brainctl/install.py              # install
    python3 plugins/codex/brainctl/install.py --dry        # preview, no write
    python3 plugins/codex/brainctl/install.py --print      # print block only
    python3 plugins/codex/brainctl/install.py --uninstall  # remove block
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SENTINEL_START = "# >>> brainctl-mcp >>>"
SENTINEL_END = "# <<< brainctl-mcp <<<"

BLOCK = """\
# >>> brainctl-mcp >>>
# brainctl persistent memory — https://github.com/TSchonleber/brainctl
# Manage this block with: plugins/codex/brainctl/install.py
[mcp_servers.brainctl]
command = "brainctl-mcp"
args = []
startup_timeout_sec = 15
tool_timeout_sec = 60
env = { BRAINCTL_AGENT_ID = "codex" }
# <<< brainctl-mcp <<<
"""


def config_path() -> Path:
    base = os.environ.get("CODEX_HOME")
    if base:
        return Path(base).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def read_config(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def strip_block(text: str) -> str:
    """Remove any existing sentinel-wrapped brainctl block, return the rest."""
    if SENTINEL_START not in text:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.rstrip() == SENTINEL_START:
            skipping = True
            continue
        if skipping:
            if line.rstrip() == SENTINEL_END:
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def merge(existing: str, block: str) -> str:
    """Return the existing config with a single fresh brainctl block appended."""
    cleaned = strip_block(existing)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    if cleaned and not cleaned.endswith("\n\n"):
        cleaned += "\n"
    return cleaned + block


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
            "           Codex will fail to start the server until you run:\n"
            "               pip install 'brainctl[mcp]>=1.3.0'",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install brainctl MCP into Codex CLI.")
    parser.add_argument("--dry", action="store_true", help="Preview changes without writing.")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print the TOML block to stdout and exit.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the brainctl block from config.toml.")
    args = parser.parse_args()

    if args.print_only:
        sys.stdout.write(BLOCK)
        return 0

    path = config_path()
    existing = read_config(path)

    if args.uninstall:
        if SENTINEL_START not in existing:
            print(f"[brainctl] no brainctl block found in {path} — nothing to do.")
            return 0
        new_text = strip_block(existing).rstrip() + "\n"
        if args.dry:
            print(f"[brainctl] --dry: would rewrite {path} without the brainctl block.")
            return 0
        bak = backup(path)
        path.write_text(new_text, encoding="utf-8")
        print(f"[brainctl] removed brainctl block from {path}"
              + (f" (backup: {bak})" if bak else ""))
        return 0

    preflight_brainctl_mcp()
    new_text = merge(existing, BLOCK)

    if new_text == existing:
        print(f"[brainctl] {path} already up to date.")
        return 0

    if args.dry:
        print(f"[brainctl] --dry: would write {path}:")
        print("---")
        sys.stdout.write(new_text)
        print("---")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    bak = backup(path)
    path.write_text(new_text, encoding="utf-8")
    print(f"[brainctl] installed brainctl MCP block into {path}"
          + (f" (backup: {bak})" if bak else ""))
    print("[brainctl] next: copy plugins/codex/brainctl/AGENTS.md.template "
          "into your project's AGENTS.md to enable session bookends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
