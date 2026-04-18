#!/usr/bin/env python3
"""Install brainctl into Pi (badlogic/pi-mono) via the pi-mcp-adapter.

Pi deliberately ships without built-in MCP support (anti-bloat). The
community-standard escape hatch is `pi-mcp-adapter` (nicobailon/pi-mcp-adapter),
which reads `~/.pi/agent/mcp.json` and exposes a single `mcp` proxy tool that
lazy-loads MCP servers on first call. This installer:

  1. Verifies pi-mcp-adapter is installed (or installs it with
     --auto-install-adapter).
  2. Verifies `brainctl-mcp` is on PATH (skip with --no-validate).
  3. Merges the brainctl block into the adapter's mcp.json, preserving any
     other mcpServers entries.

Usage:
    python3 plugins/pi/brainctl/install.py
    python3 plugins/pi/brainctl/install.py --dry-run
    python3 plugins/pi/brainctl/install.py --force          # overwrite divergent block
    python3 plugins/pi/brainctl/install.py --uninstall
    python3 plugins/pi/brainctl/install.py --auto-install-adapter
    python3 plugins/pi/brainctl/install.py --config /path/to/mcp.json
    python3 plugins/pi/brainctl/install.py --no-validate    # skip brainctl-mcp PATH check
    python3 plugins/pi/brainctl/install.py --yes            # non-TTY safe
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
FRAGMENT_PATH = PLUGIN_DIR / "pi-mcp.json.fragment"


# --- path resolution --------------------------------------------------------

def pi_agent_dir() -> Path:
    """Return Pi's per-user agent dir.

    `PI_CODING_AGENT_DIR` overrides the whole base path (replaces
    `~/.pi/agent`, not appended to it).
    """
    override = os.environ.get("PI_CODING_AGENT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pi" / "agent"


def mcp_config_path(override: str | None) -> Path:
    """Resolve mcp.json. Precedence: --config > $PI_CODING_AGENT_DIR/mcp.json > ~/.pi/agent/mcp.json."""
    if override:
        return Path(override).expanduser()
    return pi_agent_dir() / "mcp.json"


# --- adapter detection ------------------------------------------------------

def detect_adapter() -> tuple[bool, str]:
    """Return (installed, how). Checks Pi's extensions dir first, then npm -g."""
    ext_dir = pi_agent_dir() / "extensions"
    if ext_dir.is_dir():
        for child in ext_dir.iterdir():
            if "pi-mcp-adapter" in child.name:
                return True, f"pi-extension at {child}"
    npm = shutil.which("npm")
    if npm:
        try:
            out = subprocess.run(
                [npm, "list", "-g", "--depth=0", "pi-mcp-adapter"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and "pi-mcp-adapter@" in out.stdout:
                return True, "npm -g"
        except (subprocess.TimeoutExpired, OSError):
            pass
    return False, ""


def install_adapter(dry: bool) -> int:
    """Run `pi install npm:pi-mcp-adapter`. Returns shell exit code (0 in dry-run)."""
    pi = shutil.which("pi")
    if pi is None:
        print(
            "[brainctl] ERROR: --auto-install-adapter needs the `pi` CLI on PATH.\n"
            "           Install Pi first:\n"
            "               npm i -g @mariozechner/pi-coding-agent",
            file=sys.stderr,
        )
        return 1
    cmd = [pi, "install", "npm:pi-mcp-adapter"]
    if dry:
        print(f"[brainctl] --dry-run: would run {' '.join(cmd)}")
        return 0
    print(f"[brainctl] running: {' '.join(cmd)}")
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(f"[brainctl] ERROR: `pi install npm:pi-mcp-adapter` exited {rc}", file=sys.stderr)
    return rc


# --- preflight --------------------------------------------------------------

def preflight_brainctl_mcp(skip: bool) -> int:
    if skip:
        return 0
    if shutil.which("brainctl-mcp") is not None:
        return 0
    print(
        "[brainctl] ERROR: `brainctl-mcp` not on PATH.\n"
        "           Install brainctl with the MCP extra:\n"
        "               pip install 'brainctl[mcp]>=2.4.2'\n"
        "           (or rerun with --no-validate to skip this check)",
        file=sys.stderr,
    )
    return 1


# --- json io ----------------------------------------------------------------

def load_fragment() -> dict:
    if not FRAGMENT_PATH.exists():
        print(f"[brainctl] fragment missing: {FRAGMENT_PATH}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[brainctl] failed to parse fragment: {exc}", file=sys.stderr)
        sys.exit(2)


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text or "{}")
    except Exception as exc:
        print(f"[brainctl] failed to parse {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --- merge / remove ---------------------------------------------------------

def plan_merge(current: dict, fragment: dict, force: bool) -> tuple[dict, str]:
    """Return (merged_config, status) where status is 'noop' | 'add' | 'replace' | 'conflict'."""
    new_block = (fragment.get("mcpServers") or {}).get("brainctl")
    if not new_block:
        print("[brainctl] fragment has no mcpServers.brainctl block — bailing.", file=sys.stderr)
        sys.exit(2)
    merged = dict(current)
    servers = dict(merged.get("mcpServers") or {})
    existing = servers.get("brainctl")
    if existing is None:
        servers["brainctl"] = new_block
        merged["mcpServers"] = servers
        return merged, "add"
    if json.dumps(existing, sort_keys=True) == json.dumps(new_block, sort_keys=True):
        return current, "noop"
    if not force:
        return current, "conflict"
    servers["brainctl"] = new_block
    merged["mcpServers"] = servers
    return merged, "replace"


def plan_remove(current: dict) -> tuple[dict, str]:
    servers = dict(current.get("mcpServers") or {})
    if "brainctl" not in servers:
        return current, "noop"
    servers.pop("brainctl", None)
    merged = dict(current)
    if servers:
        merged["mcpServers"] = servers
    else:
        merged.pop("mcpServers", None)
    return merged, "remove"


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", "--dry", dest="dry", action="store_true",
                    help="Preview without writing anything.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing mcpServers.brainctl block that differs.")
    ap.add_argument("--uninstall", action="store_true",
                    help="Remove brainctl from mcpServers (leaves other servers intact).")
    ap.add_argument("--auto-install-adapter", action="store_true",
                    help="Run `pi install npm:pi-mcp-adapter` if the adapter is missing.")
    ap.add_argument("--config", default=None,
                    help="Explicit path to mcp.json (overrides PI_CODING_AGENT_DIR).")
    ap.add_argument("--no-validate", action="store_true",
                    help="Skip the brainctl-mcp PATH check.")
    ap.add_argument("--yes", action="store_true",
                    help="Non-TTY safe (reserved; no interactive prompts today).")
    args = ap.parse_args()

    cfg_path = mcp_config_path(args.config)

    # 1. adapter detection (unless we're uninstalling — let users clean up
    # even if they later removed the adapter)
    if not args.uninstall:
        ok, how = detect_adapter()
        if not ok:
            if args.auto_install_adapter:
                rc = install_adapter(args.dry)
                if rc != 0:
                    return rc
                if not args.dry:
                    ok, how = detect_adapter()
                    if not ok:
                        print("[brainctl] ERROR: pi-mcp-adapter still not detected after install.",
                              file=sys.stderr)
                        return 1
            else:
                print(
                    "[brainctl] ERROR: pi-mcp-adapter not installed.\n"
                    "           Pi has no built-in MCP support; brainctl rides on the adapter.\n"
                    "           Install it first:\n"
                    "               pi install npm:pi-mcp-adapter\n"
                    "           Or rerun this installer with --auto-install-adapter.",
                    file=sys.stderr,
                )
                return 1
        else:
            print(f"[brainctl] pi-mcp-adapter detected ({how}).", flush=True)

    # 2. brainctl-mcp PATH check (skip on uninstall — removal shouldn't require
    # the binary to be present)
    if not args.uninstall:
        rc = preflight_brainctl_mcp(args.no_validate)
        if rc != 0:
            return rc

    # 3. plan + write
    fragment = load_fragment()
    current = load_config(cfg_path)

    if args.uninstall:
        merged, status = plan_remove(current)
        if status == "noop":
            print(f"[brainctl] {cfg_path}: no mcpServers.brainctl entry — nothing to do.")
            return 0
        if args.dry:
            print(f"[brainctl] --dry-run: would write {cfg_path}:")
            print(json.dumps(merged, indent=2))
            return 0
        save_config(cfg_path, merged)
        print(f"[brainctl] {cfg_path}: removed mcpServers.brainctl entry.")
        return 0

    merged, status = plan_merge(current, fragment, args.force)
    if status == "noop":
        print(f"[brainctl] {cfg_path}: mcpServers.brainctl already up to date.")
        return 0
    if status == "conflict":
        print(
            f"[brainctl] ERROR: mcpServers.brainctl already exists at {cfg_path} and differs\n"
            f"           from the shipped fragment. Pass --force to overwrite, or edit by hand.",
            file=sys.stderr,
        )
        return 1
    if args.dry:
        verb = "would add" if status == "add" else "would replace"
        print(f"[brainctl] --dry-run: {verb} mcpServers.brainctl in {cfg_path}:")
        print(json.dumps(merged, indent=2))
        return 0
    save_config(cfg_path, merged)
    verb = "added" if status == "add" else "replaced"
    print(f"[brainctl] {cfg_path}: {verb} mcpServers.brainctl block.")
    print("[brainctl] restart Pi (or open a new session) so the adapter picks up the new server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
