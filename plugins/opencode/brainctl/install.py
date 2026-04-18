#!/usr/bin/env python3
"""Install the brainctl plugin into OpenCode.

OpenCode (anomalyco/opencode, formerly sst/opencode — 145k stars) supports
both MCP servers AND a TypeScript plugin / hook system. This installer ships
the brainctl integration for both surfaces:

    1. Merges an `mcp.brainctl` block into OpenCode's config
       (`~/.config/opencode/opencode.json` global, or `./opencode.json`
       project-local). The brainctl-mcp server exposes 200+ tools.

    2. Copies three TypeScript hook plugins into OpenCode's plugins dir
       (`~/.config/opencode/plugins/` global, or `./.opencode/plugins/`
       project-local):

           brainctl-orient.ts     — session.created
           brainctl-wrap-up.ts    — session.idle / session.deleted
           brainctl-tool-log.ts   — tool.execute.after

       Each plugin shells out to the `brainctl` CLI; they require `bun`
       (which OpenCode bundles) at runtime.

Usage:
    python3 install.py                       # full install, global scope
    python3 install.py --scope project       # install into ./opencode.json + ./.opencode/plugins/
    python3 install.py --config <path>       # override config location
    python3 install.py --dry-run             # print plan, write nothing
    python3 install.py --uninstall           # remove the mcp block + delete the .ts plugins we shipped
    python3 install.py --mcp-only            # register MCP server only, skip TS plugins
    python3 install.py --plugins-only        # install hooks only, skip MCP merge
    python3 install.py --force               # overwrite existing files / mcp entries
    python3 install.py --yes                 # non-TTY safe (no interactive prompts)
    python3 install.py --no-validate         # skip the brainctl-mcp PATH check
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Static layout — files we ship.
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(__file__).resolve().parent
FRAGMENT_PATH = PLUGIN_DIR / "opencode.json.fragment"
TS_PLUGIN_NAMES = (
    "brainctl-orient.ts",
    "brainctl-wrap-up.ts",
    "brainctl-tool-log.ts",
)
TS_PLUGIN_PATHS = tuple(PLUGIN_DIR / "plugins" / name for name in TS_PLUGIN_NAMES)


# ---------------------------------------------------------------------------
# Path resolution.
# ---------------------------------------------------------------------------

def opencode_global_dir() -> Path:
    """`~/.config/opencode/` (XDG-respecting)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else (Path.home() / ".config")
    return root / "opencode"


def resolve_config_path(scope: str, override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    if scope == "project":
        # Project config lives next to the user's CWD.
        return Path.cwd() / "opencode.json"
    return opencode_global_dir() / "opencode.json"


def resolve_plugins_dir(scope: str, override_config: str | None) -> Path:
    """Resolve where the TS plugins should land.

    For project scope we use `./.opencode/plugins/` (per OpenCode docs).
    For global scope we use `~/.config/opencode/plugins/`.
    If `--config` is passed we infer scope from the parent dir name —
    `<x>/.opencode/...` looks project-y; everything else falls back to the
    config file's parent + `plugins/`.
    """
    if override_config:
        cfg = Path(override_config).expanduser().resolve()
        # Local-style override sitting next to `./.opencode/`?
        if cfg.parent.name == ".opencode":
            return cfg.parent / "plugins"
        # Otherwise drop `plugins/` next to the config file.
        return cfg.parent / "plugins"
    if scope == "project":
        return Path.cwd() / ".opencode" / "plugins"
    return opencode_global_dir() / "plugins"


# ---------------------------------------------------------------------------
# JSON helpers.
# ---------------------------------------------------------------------------

def load_fragment() -> dict:
    if not FRAGMENT_PATH.exists():
        print(f"[brainctl] missing fragment: {FRAGMENT_PATH}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[brainctl] failed to parse fragment: {exc}", file=sys.stderr)
        sys.exit(2)


def load_config(path: Path) -> dict:
    """Load opencode.json, returning {} if absent.

    OpenCode also accepts opencode.jsonc — we don't try to round-trip
    JSONC comments here (json module would lose them). If the config has
    `//` comments the parse fails fast with a clear message; the user can
    re-run with `--config` pointing at a vanilla JSON copy.
    """
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        # Detect the common "JSONC comments" footgun and explain it.
        snippet = raw.lstrip()[:80]
        if snippet.startswith("//") or "\n//" in raw[:2000]:
            print(
                f"[brainctl] {path} appears to contain JSONC comments — "
                "we can't safely round-trip them. Strip comments or pass "
                "--config <vanilla.json>.",
                file=sys.stderr,
            )
        else:
            print(f"[brainctl] failed to parse {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP merge / unmerge.
# ---------------------------------------------------------------------------

def merge_mcp(config: dict, fragment: dict, force: bool) -> tuple[dict, bool, str]:
    """Merge fragment.mcp.brainctl into config.mcp.brainctl.

    Returns (config, changed, message). Refuses to overwrite an existing,
    *different* `brainctl` entry without `--force`.
    """
    server = (fragment.get("mcp") or {}).get("brainctl")
    if not server:
        print("[brainctl] fragment missing mcp.brainctl block", file=sys.stderr)
        sys.exit(2)
    mcp = config.setdefault("mcp", {})
    existing = mcp.get("brainctl")
    if existing is not None:
        if json.dumps(existing, sort_keys=True) == json.dumps(server, sort_keys=True):
            return config, False, "mcp.brainctl already up to date"
        if not force:
            return config, False, (
                "mcp.brainctl already present and differs — re-run with "
                "--force to overwrite"
            )
    mcp["brainctl"] = server
    return config, True, "merged mcp.brainctl"


def unmerge_mcp(config: dict) -> tuple[dict, bool]:
    mcp = config.get("mcp") or {}
    if "brainctl" not in mcp:
        return config, False
    mcp.pop("brainctl", None)
    if not mcp:
        config.pop("mcp", None)
    return config, True


# ---------------------------------------------------------------------------
# TS plugin copy / remove.
# ---------------------------------------------------------------------------

def plan_ts_copies(target_dir: Path) -> list[tuple[Path, Path, bool]]:
    """For each ts plugin we own, return (src, dst, dst_already_exists)."""
    out: list[tuple[Path, Path, bool]] = []
    for src in TS_PLUGIN_PATHS:
        dst = target_dir / src.name
        out.append((src, dst, dst.exists()))
    return out


def copy_ts_plugins(target_dir: Path, force: bool, dry: bool) -> tuple[list[Path], list[str]]:
    """Copy the three TS plugins; honor --force on existing files.

    Returns (written_paths, skipped_messages).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    skipped: list[str] = []
    for src, dst, existed in plan_ts_copies(target_dir):
        if existed and not force:
            skipped.append(f"{dst} already exists — pass --force to overwrite")
            continue
        if dry:
            written.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(dst)
    return written, skipped


def remove_ts_plugins(target_dir: Path, dry: bool) -> tuple[list[Path], list[str]]:
    removed: list[Path] = []
    missing: list[str] = []
    for name in TS_PLUGIN_NAMES:
        dst = target_dir / name
        if not dst.exists():
            missing.append(f"{dst} not present")
            continue
        if dry:
            removed.append(dst)
            continue
        dst.unlink()
        removed.append(dst)
    # Try to clean up the plugins dir if it ended up empty *and* we created
    # it — but only the deepest dir. Don't rmtree user data.
    if not dry and target_dir.exists() and not any(target_dir.iterdir()):
        try:
            target_dir.rmdir()
        except OSError:
            pass
    return removed, missing


# ---------------------------------------------------------------------------
# Preflight.
# ---------------------------------------------------------------------------

def preflight_brainctl_mcp(skip: bool) -> None:
    if skip:
        return
    if shutil.which("brainctl-mcp") is None:
        print(
            "[brainctl] WARNING: `brainctl-mcp` not found on PATH.\n"
            "           OpenCode will fail to start the server until you run:\n"
            "               pip install 'brainctl[mcp]>=2.4.2'\n"
            "           Pass --no-validate to suppress this warning.",
            file=sys.stderr,
        )


def preflight_brainctl_cli(skip: bool, doing_plugins: bool) -> None:
    """Plugins shell out to `brainctl` — warn if missing."""
    if skip or not doing_plugins:
        return
    if shutil.which("brainctl") is None:
        print(
            "[brainctl] WARNING: `brainctl` CLI not found on PATH.\n"
            "           The TS hook plugins shell out to it; without it,\n"
            "           every hook will silently no-op (sessions still work,\n"
            "           but no orient/wrap-up/event logging happens).\n"
            "               pip install 'brainctl>=2.4.2'",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Plan printer (used in dry-run + as the success summary).
# ---------------------------------------------------------------------------

def print_plan(
    *,
    do_mcp: bool,
    do_plugins: bool,
    config_path: Path,
    plugins_dir: Path,
    config_after: dict | None,
    ts_plan: list[tuple[Path, Path, bool]] | None,
    verb: str,
    extra_messages: list[str],
) -> None:
    print(f"[brainctl] {verb} plan:")
    print(f"           config: {config_path}")
    print(f"           plugins dir: {plugins_dir}")
    if do_mcp:
        if config_after is None:
            print("           - mcp: (no change)")
        else:
            print("           - mcp.brainctl: would write block:")
            for line in json.dumps(
                {"mcp": {"brainctl": config_after.get("mcp", {}).get("brainctl")}},
                indent=2,
            ).splitlines():
                print(f"               {line}")
    if do_plugins and ts_plan is not None:
        for _, dst, existed in ts_plan:
            tag = "OVERWRITE" if existed else "WRITE    "
            print(f"           - {tag} {dst}")
    for msg in extra_messages:
        print(f"           ! {msg}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--scope",
        choices=("global", "project"),
        default="global",
        help="Install into global ~/.config/opencode/ or project ./opencode.json + ./.opencode/plugins/.",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Override the opencode.json path (takes precedence over --scope).",
    )
    ap.add_argument(
        "--dry-run", "--dry", dest="dry", action="store_true",
        help="Preview the install without writing anything.",
    )
    ap.add_argument(
        "--uninstall", action="store_true",
        help="Remove the mcp block and the three .ts plugins we own.",
    )
    ap.add_argument(
        "--mcp-only", action="store_true",
        help="Only register the MCP server; skip the TS plugin install.",
    )
    ap.add_argument(
        "--plugins-only", action="store_true",
        help="Only install the TS plugin hooks; skip the MCP merge.",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing mcp.brainctl entry or .ts plugin file.",
    )
    ap.add_argument(
        "--yes", action="store_true",
        help="Non-TTY safe; never prompt (this installer is already non-interactive, so this is a no-op kept for parity with sibling installers).",
    )
    ap.add_argument(
        "--no-validate", action="store_true",
        help="Skip the brainctl-mcp PATH presence check.",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mcp_only and args.plugins_only:
        print("[brainctl] --mcp-only and --plugins-only are mutually exclusive",
              file=sys.stderr)
        return 2

    do_mcp = not args.plugins_only
    do_plugins = not args.mcp_only

    config_path = resolve_config_path(args.scope, args.config)
    plugins_dir = resolve_plugins_dir(args.scope, args.config)

    preflight_brainctl_mcp(args.no_validate or not do_mcp)
    preflight_brainctl_cli(args.no_validate, do_plugins)

    if args.uninstall:
        return run_uninstall(args, do_mcp, do_plugins, config_path, plugins_dir)
    return run_install(args, do_mcp, do_plugins, config_path, plugins_dir)


def run_install(
    args: argparse.Namespace,
    do_mcp: bool,
    do_plugins: bool,
    config_path: Path,
    plugins_dir: Path,
) -> int:
    fragment = load_fragment()
    config = load_config(config_path)

    config_after: dict | None = None
    extra: list[str] = []
    config_changed = False
    if do_mcp:
        config, config_changed, msg = merge_mcp(config, fragment, args.force)
        if config_changed:
            config_after = config
        else:
            extra.append(msg)

    ts_plan: list[tuple[Path, Path, bool]] | None = None
    if do_plugins:
        ts_plan = plan_ts_copies(plugins_dir)
        # Surface conflicts *before* we attempt to write so dry-run shows them too.
        for _, dst, existed in ts_plan:
            if existed and not args.force:
                extra.append(f"would skip {dst} (exists; pass --force to overwrite)")

    if args.dry:
        print_plan(
            do_mcp=do_mcp,
            do_plugins=do_plugins,
            config_path=config_path,
            plugins_dir=plugins_dir,
            config_after=config_after if config_changed else None,
            ts_plan=ts_plan,
            verb="--dry-run install",
            extra_messages=extra,
        )
        return 0

    # Apply.
    if do_mcp and config_changed:
        save_config(config_path, config)
        print(f"[brainctl] {config_path}: merged mcp.brainctl block.")
    elif do_mcp:
        print(f"[brainctl] {config_path}: mcp.brainctl unchanged.")

    if do_plugins:
        written, skipped = copy_ts_plugins(plugins_dir, args.force, dry=False)
        for w in written:
            print(f"[brainctl] wrote {w}")
        for s in skipped:
            print(f"[brainctl] skipped: {s}")
    print("[brainctl] restart OpenCode (or open a new session) to pick up the changes.")
    return 0


def run_uninstall(
    args: argparse.Namespace,
    do_mcp: bool,
    do_plugins: bool,
    config_path: Path,
    plugins_dir: Path,
) -> int:
    extra: list[str] = []
    config = load_config(config_path) if config_path.exists() else {}
    config_changed = False
    if do_mcp:
        config, config_changed = unmerge_mcp(config)
        if not config_changed:
            extra.append(f"{config_path}: no mcp.brainctl entry — nothing to remove")

    ts_to_remove: list[Path] = []
    if do_plugins:
        for name in TS_PLUGIN_NAMES:
            ts_to_remove.append(plugins_dir / name)

    if args.dry:
        print(f"[brainctl] --dry-run uninstall plan:")
        print(f"           config: {config_path}")
        print(f"           plugins dir: {plugins_dir}")
        if do_mcp:
            if config_changed:
                print("           - REMOVE mcp.brainctl from config")
            else:
                print("           - mcp.brainctl: not present")
        if do_plugins:
            for p in ts_to_remove:
                tag = "REMOVE" if p.exists() else "absent"
                print(f"           - {tag} {p}")
        for msg in extra:
            print(f"           ! {msg}")
        return 0

    if do_mcp and config_changed:
        save_config(config_path, config)
        print(f"[brainctl] {config_path}: removed mcp.brainctl entry.")
    elif do_mcp:
        print(f"[brainctl] {config_path}: nothing to remove.")

    if do_plugins:
        removed, missing = remove_ts_plugins(plugins_dir, dry=False)
        for r in removed:
            print(f"[brainctl] removed {r}")
        for m in missing:
            print(f"[brainctl] (skipped) {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
