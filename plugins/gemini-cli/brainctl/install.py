#!/usr/bin/env python3
"""Install the brainctl extension into Gemini CLI.

By default, copies this plugin directory into `~/.gemini/extensions/brainctl/`
(or `$GEMINI_HOME/extensions/brainctl/`) so Gemini CLI loads the
`gemini-extension.json` manifest, registers the brainctl MCP server, and
wires the bundled hooks (SessionStart / SessionEnd / AfterTool).

With `--mcp-only`, skips the extension copy and merges only the
`mcpServers.brainctl` block into `~/.gemini/settings.json` — useful for
users who want the 199 MCP tools without the lifecycle hooks.

Usage:
    python3 plugins/gemini-cli/brainctl/install.py
    python3 plugins/gemini-cli/brainctl/install.py --dry-run
    python3 plugins/gemini-cli/brainctl/install.py --mcp-only
    python3 plugins/gemini-cli/brainctl/install.py --mcp-only --dry-run
    python3 plugins/gemini-cli/brainctl/install.py --uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PLUGIN_DIR / "gemini-extension.json"


def gemini_home() -> Path:
    """Return the user's Gemini CLI config dir."""
    base = os.environ.get("GEMINI_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".gemini"


def extension_target() -> Path:
    return gemini_home() / "extensions" / "brainctl"


def settings_path() -> Path:
    return gemini_home() / "settings.json"


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        print(f"[brainctl] manifest missing: {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[brainctl] failed to parse manifest: {exc}", file=sys.stderr)
        sys.exit(2)


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        print(f"[brainctl] failed to parse {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def preflight_brainctl_mcp() -> None:
    """Warn (do not fail) if brainctl-mcp isn't on PATH."""
    if shutil.which("brainctl-mcp") is None:
        print(
            "[brainctl] WARNING: `brainctl-mcp` not found on PATH.\n"
            "           Gemini CLI will fail to start the server until you run:\n"
            "               pip install 'brainctl[mcp]>=2.2.4'",
            file=sys.stderr,
        )


# --- extension install (full plugin: manifest + hooks) ----------------------

def copy_extension(dry: bool) -> tuple[Path, list[str]]:
    """Copy this plugin's files into `~/.gemini/extensions/brainctl/`."""
    target = extension_target()
    # Files we ship to the install location.
    files: list[Path] = [
        MANIFEST_PATH,
        PLUGIN_DIR / "GEMINI.md",
        PLUGIN_DIR / "README.md",
        PLUGIN_DIR / "hooks" / "hooks.json",
        PLUGIN_DIR / "hooks" / "_common.py",
        PLUGIN_DIR / "hooks" / "session_start.py",
        PLUGIN_DIR / "hooks" / "session_end.py",
        PLUGIN_DIR / "hooks" / "post_tool_use.py",
    ]
    relpaths = [str(f.relative_to(PLUGIN_DIR)) for f in files]

    if dry:
        return target, relpaths

    # Idempotent: wipe and re-copy. shutil.copytree(dirs_exist_ok=True) keeps
    # stale files from earlier versions; a clean swap avoids that drift.
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for src in files:
        dst = target / src.relative_to(PLUGIN_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return target, relpaths


def uninstall_extension(dry: bool) -> tuple[Path, bool]:
    """Remove `~/.gemini/extensions/brainctl/` if present."""
    target = extension_target()
    existed = target.exists()
    if dry or not existed:
        return target, existed
    shutil.rmtree(target)
    return target, existed


# --- mcp-only install (settings.json merge) ---------------------------------

def merge_mcp_into_settings(settings: dict, manifest: dict) -> tuple[dict, bool]:
    """Inject manifest['mcpServers']['brainctl'] into settings.json.

    Returns the (possibly mutated) settings dict and a bool indicating
    whether the settings actually changed.
    """
    server = (manifest.get("mcpServers") or {}).get("brainctl")
    if not server:
        print("[brainctl] manifest has no mcpServers.brainctl block — bailing.",
              file=sys.stderr)
        sys.exit(2)
    mcp = settings.setdefault("mcpServers", {})
    before = json.dumps(mcp.get("brainctl"), sort_keys=True)
    mcp["brainctl"] = server
    after = json.dumps(mcp.get("brainctl"), sort_keys=True)
    return settings, before != after


def remove_mcp_from_settings(settings: dict) -> tuple[dict, bool]:
    mcp = settings.get("mcpServers") or {}
    if "brainctl" not in mcp:
        return settings, False
    mcp.pop("brainctl", None)
    if not mcp:
        settings.pop("mcpServers", None)
    return settings, True


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--dry-run", "--dry", dest="dry", action="store_true",
        help="Preview the install without writing anything.",
    )
    ap.add_argument(
        "--mcp-only", action="store_true",
        help="Skip the extension; only merge mcpServers.brainctl into settings.json.",
    )
    ap.add_argument(
        "--uninstall", action="store_true",
        help="Remove the extension dir (or, with --mcp-only, the settings.json entry).",
    )
    args = ap.parse_args()

    manifest = load_manifest()
    preflight_brainctl_mcp()

    if args.mcp_only:
        sp = settings_path()
        settings = load_settings(sp)
        if args.uninstall:
            settings, changed = remove_mcp_from_settings(settings)
            verb = "would remove" if args.dry else "removed"
            if not changed:
                print(f"[brainctl] {sp}: no mcpServers.brainctl entry — nothing to do.")
                return 0
            if args.dry:
                print(f"[brainctl] --dry-run: {verb} mcpServers.brainctl from {sp}:")
                print(json.dumps(settings, indent=2))
                return 0
            save_settings(sp, settings)
            print(f"[brainctl] {sp}: removed mcpServers.brainctl entry.")
            return 0

        settings, changed = merge_mcp_into_settings(settings, manifest)
        if not changed:
            print(f"[brainctl] {sp}: mcpServers.brainctl already up to date.")
            return 0
        if args.dry:
            print(f"[brainctl] --dry-run: would write {sp}:")
            print(json.dumps(settings, indent=2))
            return 0
        save_settings(sp, settings)
        print(f"[brainctl] {sp}: merged mcpServers.brainctl block.")
        print("[brainctl] (hooks NOT installed — pass without --mcp-only for full plugin)")
        return 0

    # Full extension install / uninstall.
    if args.uninstall:
        target, existed = uninstall_extension(args.dry)
        if not existed:
            print(f"[brainctl] {target}: not present — nothing to do.")
            return 0
        verb = "would remove" if args.dry else "removed"
        print(f"[brainctl] {verb} extension at {target}.")
        return 0

    target, files = copy_extension(args.dry)
    verb = "would install" if args.dry else "installed"
    print(f"[brainctl] {verb} extension into {target}:")
    for rel in files:
        print(f"           - {rel}")
    if not args.dry:
        print("[brainctl] restart Gemini CLI (or open a new session) to pick up the extension.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
