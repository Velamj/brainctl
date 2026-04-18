#!/usr/bin/env python3
"""Install the brainctl extension into Goose (Block / AAIF / Linux Foundation).

Merges `extensions.brainctl` into Goose's YAML config. Idempotent: rerun safe.

Default config locations:
    Linux/macOS:  ~/.config/goose/config.yaml
    Windows:      %APPDATA%\\Block\\goose\\config\\config.yaml

Override with `--config <path>` or `GOOSE_CONFIG_PATH`.

Usage:
    python3 plugins/goose/brainctl/install.py
    python3 plugins/goose/brainctl/install.py --dry-run
    python3 plugins/goose/brainctl/install.py --uninstall
    python3 plugins/goose/brainctl/install.py --force        # overwrite existing brainctl block
    python3 plugins/goose/brainctl/install.py --no-validate  # skip brainctl-mcp PATH check
    python3 plugins/goose/brainctl/install.py --yes          # non-TTY safe (no prompts)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
FRAGMENT_PATH = PLUGIN_DIR / "goose-extension.yaml"

# What we write into extensions.brainctl. Kept as a Python literal so that the
# fallback emitter can produce identical YAML even without pyyaml installed.
BRAINCTL_BLOCK: dict = {
    "bundled": False,
    "enabled": True,
    "name": "brainctl",
    "type": "stdio",
    "timeout": 300,
    "cmd": "brainctl-mcp",
    "args": [],
    "description": "brainctl agent memory — 201 MCP tools, local-first SQLite, MIT-licensed",
    "env_keys": [],
    "envs": {"BRAINCTL_DB": "${HOME}/agentmemory/db/brain.db"},
    "available_tools": [],
}


def default_config_path() -> Path:
    """Goose's default config path for the current OS."""
    env = os.environ.get("GOOSE_CONFIG_PATH")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Block" / "goose" / "config" / "config.yaml"
    return Path.home() / ".config" / "goose" / "config.yaml"


def preflight_brainctl_mcp(skip: bool) -> None:
    """Verify brainctl-mcp is on PATH unless --no-validate."""
    if skip:
        return
    if shutil.which("brainctl-mcp") is None:
        print(
            "[brainctl] ERROR: `brainctl-mcp` not found on PATH.\n"
            "           install brainctl: pip install brainctl[mcp]\n"
            "           (or rerun with --no-validate to skip this check)",
            file=sys.stderr,
        )
        sys.exit(1)


# --- YAML I/O with optional pyyaml ------------------------------------------

def _load_yaml(path: Path) -> dict:
    """Parse the config. Empty / missing returns {}. Errors exit 2."""
    if not path.exists() or path.stat().st_size == 0:
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        # Fallback: only safe if the file already contains nothing we'd clobber.
        # If we can detect the user has unrelated extensions and pyyaml is gone,
        # we refuse to merge — better to bail than mangle their config.
        if text.strip() and "extensions:" in text:
            print(
                "[brainctl] ERROR: pyyaml not installed and config has existing\n"
                "           `extensions:` block. Install pyyaml to merge safely:\n"
                "               pip install pyyaml\n"
                "           (or remove your existing extensions block manually)",
                file=sys.stderr,
            )
            sys.exit(2)
        return {}
    except Exception as exc:
        print(f"[brainctl] ERROR: failed to parse {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _dump_yaml(data: dict) -> str:
    """Emit YAML. Prefer pyyaml; otherwise hand-render the brainctl-only shape."""
    try:
        import yaml  # type: ignore
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except ImportError:
        return _fallback_emit(data)


def _fallback_emit(data: dict) -> str:
    """Minimal YAML emitter sufficient for the brainctl-only case.

    Supports just the shape we produce ourselves: a top-level `extensions:`
    mapping containing one or more extension blocks of the brainctl shape
    (scalars, empty lists, simple `envs` map). If the user had richer YAML,
    pyyaml would have handled it in `_load_yaml`; reaching this path means
    the config was empty and we're writing fresh.
    """
    if not data:
        return ""
    out: list[str] = []
    extensions = data.get("extensions") or {}
    other = {k: v for k, v in data.items() if k != "extensions"}
    if other:
        # Best effort for any non-extensions keys the loader may have returned.
        for k, v in other.items():
            out.append(f"{k}: {_scalar(v)}")
    out.append("extensions:")
    if not extensions:
        out[-1] = "extensions: {}"
        return "\n".join(out) + "\n"
    for name, block in extensions.items():
        out.append(f"  {name}:")
        for k, v in block.items():
            if isinstance(v, dict):
                if not v:
                    out.append(f"    {k}: {{}}")
                else:
                    out.append(f"    {k}:")
                    for kk, vv in v.items():
                        out.append(f"      {kk}: {_scalar(vv)}")
            elif isinstance(v, list):
                out.append(f"    {k}: []" if not v else f"    {k}: {v!r}")
            else:
                out.append(f"    {k}: {_scalar(v)}")
    return "\n".join(out) + "\n"


def _scalar(v) -> str:
    """Render a scalar in YAML. Strings get quoted when they contain ${ or :."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in (":", "#", "$", '"')) or s != s.strip():
        return '"' + s.replace('"', '\\"') + '"'
    return s


# --- merge / remove ---------------------------------------------------------

def merge_brainctl(config: dict, force: bool) -> tuple[dict, bool, str]:
    """Inject extensions.brainctl. Returns (config, changed, reason)."""
    extensions = config.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        return config, False, "extensions key is not a mapping — refusing to clobber"
    existing = extensions.get("brainctl")
    if existing is not None and not force:
        if existing == BRAINCTL_BLOCK:
            return config, False, "brainctl extension already up to date"
        return config, False, "extensions.brainctl exists — pass --force to overwrite"
    extensions["brainctl"] = dict(BRAINCTL_BLOCK)  # copy so callers can't mutate ours
    return config, True, "merged extensions.brainctl"


def remove_brainctl(config: dict) -> tuple[dict, bool]:
    extensions = config.get("extensions")
    if not isinstance(extensions, dict) or "brainctl" not in extensions:
        return config, False
    extensions.pop("brainctl", None)
    if not extensions:
        config.pop("extensions", None)
    return config, True


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=None,
                    help="Override Goose config path (default: ~/.config/goose/config.yaml).")
    ap.add_argument("--dry-run", "--dry", dest="dry", action="store_true",
                    help="Print the resulting YAML without writing.")
    ap.add_argument("--uninstall", action="store_true",
                    help="Remove extensions.brainctl, leaving other extensions intact.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing extensions.brainctl block.")
    ap.add_argument("--no-validate", action="store_true",
                    help="Skip the brainctl-mcp PATH check.")
    ap.add_argument("--yes", action="store_true",
                    help="Assume yes to any prompts (non-TTY safe; currently informational).")
    args = ap.parse_args()
    _ = args.yes  # accepted for non-TTY safety; this installer never prompts

    config_path: Path = (args.config or default_config_path()).expanduser()
    config = _load_yaml(config_path)

    if args.uninstall:
        config, changed = remove_brainctl(config)
        if not changed:
            print(f"[brainctl] {config_path}: no extensions.brainctl entry — nothing to do.")
            return 0
        rendered = _dump_yaml(config)
        if args.dry:
            print(f"[brainctl] --dry-run: would remove brainctl from {config_path}:")
            print(rendered, end="" if rendered.endswith("\n") else "\n")
            return 0
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(rendered, encoding="utf-8")
        print(f"[brainctl] {config_path}: removed extensions.brainctl entry.")
        return 0

    preflight_brainctl_mcp(args.no_validate)

    config, changed, reason = merge_brainctl(config, args.force)
    if not changed:
        print(f"[brainctl] {config_path}: {reason}.")
        # Differentiate "already up to date" (ok) from "exists, need --force" (warn + exit 1).
        return 0 if reason == "brainctl extension already up to date" else 1

    rendered = _dump_yaml(config)
    if args.dry:
        print(f"[brainctl] --dry-run: would write {config_path}:")
        print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 0
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered, encoding="utf-8")
    print(f"[brainctl] {config_path}: {reason}.")
    print("[brainctl] restart Goose (or open a new session) to pick up the extension.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
