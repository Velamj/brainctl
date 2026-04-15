#!/usr/bin/env python3
"""Install the brainctl skill + AGENTS.md snippet into an OpenClaw workspace.

OpenClaw auto-injects `AGENTS.md`, `SOUL.md`, and `TOOLS.md` from the workspace
root into every Pi session, and loads skills from
`<workspace>/skills/<skill-name>/SKILL.md`. This installer uses that surface:
it copies a `brainctl` skill file into `skills/brainctl/SKILL.md` and merges a
short sentinel-wrapped "Persistent memory" section into the workspace
`AGENTS.md` so Pi knows the memory skill exists and reaches for it.

This is a filesystem installer, not an MCP installer. Pi invokes brainctl by
shelling out to the `brainctl` CLI — no MCP server is registered, and the
root `~/.openclaw/openclaw.json` is never touched.

Usage:
    python3 plugins/openclaw/brainctl/install.py              # install
    python3 plugins/openclaw/brainctl/install.py --dry        # preview, no write
    python3 plugins/openclaw/brainctl/install.py --uninstall  # remove skill + block
    python3 plugins/openclaw/brainctl/install.py --path WORKSPACE
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# HTML-comment sentinels so the block is invisible when AGENTS.md is rendered.
SENTINEL_START = "<!-- >>> brainctl >>> -->"
SENTINEL_END = "<!-- <<< brainctl <<< -->"

HERE = Path(__file__).resolve().parent
SKILL_TEMPLATE = HERE / "SKILL.md.template"
AGENTS_SNIPPET = HERE / "AGENTS.md.snippet"


def workspace_path(override: str | None) -> Path:
    """Return the OpenClaw workspace dir, honoring --path and $OPENCLAW_HOME."""
    if override:
        return Path(override).expanduser()
    base = os.environ.get("OPENCLAW_HOME")
    if base:
        return Path(base).expanduser() / "workspace"
    return Path.home() / ".openclaw" / "workspace"


def read_text(path: Path) -> str:
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


def merge(existing: str, snippet_body: str) -> str:
    """Return AGENTS.md with a single fresh sentinel-wrapped brainctl block appended."""
    cleaned = strip_block(existing)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    if cleaned and not cleaned.endswith("\n\n"):
        cleaned += "\n"
    body = snippet_body.strip("\n")
    block = f"{SENTINEL_START}\n{body}\n{SENTINEL_END}\n"
    return cleaned + block


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".brainctl.bak")
    shutil.copy2(path, bak)
    return bak


def preflight_brainctl() -> None:
    """Warn (do not fail) if the `brainctl` CLI isn't on PATH."""
    if shutil.which("brainctl") is None:
        print(
            "[brainctl] WARNING: `brainctl` not found on PATH.\n"
            "           Pi will fail to shell out to the CLI until you run:\n"
            "               pip install 'brainctl>=1.2.0'",
            file=sys.stderr,
        )


def install(workspace: Path, dry: bool) -> int:
    if not SKILL_TEMPLATE.exists():
        print(f"[brainctl] missing template: {SKILL_TEMPLATE}", file=sys.stderr)
        return 2
    if not AGENTS_SNIPPET.exists():
        print(f"[brainctl] missing snippet: {AGENTS_SNIPPET}", file=sys.stderr)
        return 2

    preflight_brainctl()

    skill_target = workspace / "skills" / "brainctl" / "SKILL.md"
    agents_target = workspace / "AGENTS.md"

    new_skill = SKILL_TEMPLATE.read_text(encoding="utf-8")
    existing_skill = read_text(skill_target)
    skill_changed = new_skill != existing_skill

    snippet_body = AGENTS_SNIPPET.read_text(encoding="utf-8")
    existing_agents = read_text(agents_target)
    new_agents = merge(existing_agents, snippet_body)
    agents_changed = new_agents != existing_agents

    if dry:
        print(f"[brainctl] --dry: workspace = {workspace}")
        if skill_changed:
            print(f"[brainctl] --dry: would write {skill_target}")
        else:
            print(f"[brainctl] --dry: {skill_target} already up to date.")
        if agents_changed:
            print(f"[brainctl] --dry: would rewrite {agents_target}:")
            print("---")
            sys.stdout.write(new_agents)
            print("---")
        else:
            print(f"[brainctl] --dry: {agents_target} already up to date.")
        return 0

    if skill_changed:
        skill_target.parent.mkdir(parents=True, exist_ok=True)
        skill_target.write_text(new_skill, encoding="utf-8")
        print(f"[brainctl] installed skill file: {skill_target}")
    else:
        print(f"[brainctl] skill file: {skill_target} (already up to date)")

    if agents_changed:
        agents_target.parent.mkdir(parents=True, exist_ok=True)
        bak = backup(agents_target)
        agents_target.write_text(new_agents, encoding="utf-8")
        print(f"[brainctl] merged brainctl block into {agents_target}"
              + (f" (backup: {bak})" if bak else ""))
    else:
        print(f"[brainctl] {agents_target} already up to date.")

    return 0


def uninstall(workspace: Path, dry: bool) -> int:
    skill_target = workspace / "skills" / "brainctl" / "SKILL.md"
    agents_target = workspace / "AGENTS.md"

    existing_agents = read_text(agents_target)
    has_block = SENTINEL_START in existing_agents
    has_skill = skill_target.exists()

    if not has_block and not has_skill:
        print(f"[brainctl] nothing to remove under {workspace}.")
        return 0

    if dry:
        if has_skill:
            print(f"[brainctl] --dry: would remove {skill_target}")
        if has_block:
            print(f"[brainctl] --dry: would strip brainctl block from {agents_target}")
        return 0

    if has_skill:
        skill_target.unlink()
        # Remove the now-empty skills/brainctl dir if possible.
        try:
            skill_target.parent.rmdir()
        except OSError:
            pass
        print(f"[brainctl] removed {skill_target}")

    if has_block:
        new_text = strip_block(existing_agents).rstrip() + "\n"
        bak = backup(agents_target)
        agents_target.write_text(new_text, encoding="utf-8")
        print(f"[brainctl] stripped brainctl block from {agents_target}"
              + (f" (backup: {bak})" if bak else ""))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install brainctl skill + AGENTS.md snippet into an OpenClaw workspace."
    )
    parser.add_argument("--dry", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the skill file and AGENTS.md brainctl block.")
    parser.add_argument("--path", dest="path",
                        help="Workspace override (default: ~/.openclaw/workspace).")
    args = parser.parse_args()

    workspace = workspace_path(args.path)

    if args.uninstall:
        return uninstall(workspace, args.dry)
    return install(workspace, args.dry)


if __name__ == "__main__":
    raise SystemExit(main())
