"""Helpers for the `brainctl update` subcommand.

Pure-functional detection + dispatch helpers. The orchestrating
`cmd_update` lives in `_impl.py` and calls into these functions so it
stays under ~150 lines.

Why separate? `cmd_update` has to shell out to *itself* after upgrading
(the running Python process still has the old `agentmemory` modules in
`sys.modules`; only the on-disk install has the new migration logic).
That subprocess-hand-off pattern is easier to test with the orchestration
factored out.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -- install-mode detection --------------------------------------------------

InstallMode = str  # one of: "dev", "pipx", "pip", "unknown"


def detect_install_mode(
    pip_show_output: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> Tuple[InstallMode, Dict[str, Any]]:
    """Classify how brainctl is installed.

    Returns ``(mode, info)`` where ``mode`` is one of:

    * ``"dev"`` — editable install (``pip install -e .``) OR install path
      lives under the user's checkout / a git worktree. Auto-upgrade is
      unsafe because their changes would be clobbered.
    * ``"pipx"`` — installed via ``pipx``; upgrade with ``pipx upgrade``.
    * ``"pip"`` — plain pip install; upgrade with ``pip install -U``.
    * ``"unknown"`` — fall back to dev-install behavior (skip upgrade,
      still try to run migrate). This handles "brainctl update" run from
      a checkout where the package isn't pip-registered at all.

    ``info`` carries the raw ``location``, ``editable_location`` (if any),
    and a human ``reason`` field useful for the summary output.
    """
    if pip_show_output is None:
        pip_show_output = _run_pip_show()

    info: Dict[str, Any] = {
        "location": None,
        "editable_location": None,
        "reason": None,
    }

    if not pip_show_output:
        info["reason"] = "pip show brainctl returned no output (package not registered with pip)"
        return ("unknown", info)

    location, editable = _parse_pip_show(pip_show_output)
    info["location"] = location
    info["editable_location"] = editable

    # Editable install marker is the strongest signal.
    if editable:
        info["reason"] = f"editable install at {editable}"
        return ("dev", info)

    # Belt-and-suspenders: location lives under a git worktree the user
    # is currently sitting in. Cheap to check and catches the case where
    # `pip show` lacked the editable line (older pip).
    if location and cwd is not None:
        try:
            loc_resolved = Path(location).resolve()
            cwd_resolved = Path(cwd).resolve()
            # Is location under cwd, or is cwd's parent the package root?
            if _is_under(loc_resolved, cwd_resolved) or _is_under(cwd_resolved, loc_resolved):
                info["reason"] = f"install location {location} sits under cwd {cwd}"
                return ("dev", info)
        except (OSError, RuntimeError):
            pass

    # pipx puts everything under a `.../pipx/venvs/<pkg>/...` path.
    if location and "pipx/venvs" in location.replace("\\", "/"):
        info["reason"] = f"location matches pipx venv pattern: {location}"
        return ("pipx", info)

    if location:
        info["reason"] = f"plain pip install at {location}"
        return ("pip", info)

    info["reason"] = "pip show parsed but no Location field found"
    return ("unknown", info)


def _run_pip_show() -> str:
    """Run `pip show brainctl` and return stdout (or empty string on failure)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "brainctl"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _parse_pip_show(output: str) -> Tuple[Optional[str], Optional[str]]:
    """Pull (location, editable_location) out of `pip show` output."""
    location: Optional[str] = None
    editable: Optional[str] = None
    for line in output.splitlines():
        if line.startswith("Location:"):
            location = line.split(":", 1)[1].strip()
        elif line.startswith("Editable project location:"):
            editable = line.split(":", 1)[1].strip()
    return location, editable


def _is_under(child: Path, parent: Path) -> bool:
    """True if ``child`` is the same path as ``parent`` or sits beneath it."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# -- subprocess wrappers (so cmd_update can stay terse and testable) ---------


def run_pip_upgrade(pre: bool = False) -> Dict[str, Any]:
    """Run `pip install -U brainctl [--pre]` and return a result dict."""
    cmd = [sys.executable, "-m", "pip", "install", "-U", "brainctl"]
    if pre:
        cmd.append("--pre")
    return _capture(cmd, kind="pip_upgrade")


def run_pipx_upgrade() -> Dict[str, Any]:
    """Run `pipx upgrade brainctl`. ``--pre`` is intentionally not forwarded:
    pipx upgrade doesn't honor it; users wanting pre-releases on pipx
    need to reinstall with ``pipx install --pip-args="--pre"``.
    """
    pipx_bin = shutil.which("pipx") or "pipx"
    return _capture([pipx_bin, "upgrade", "brainctl"], kind="pipx_upgrade")


def run_doctor_json() -> Dict[str, Any]:
    """Shell out to `brainctl doctor --json` to get the migrations state.

    We use the *current* (pre-upgrade) brainctl here on purpose — the
    doctor check is what guards us from running migrate against a virgin
    tracker, so we want it to read the DB in its current shape before
    any version transition.
    """
    brainctl = _find_brainctl_bin()
    if not brainctl:
        return {"ok": False, "error": "brainctl binary not found on PATH"}
    res = _capture([brainctl, "doctor", "--json"], kind="doctor")
    if not res["ok"]:
        return {"ok": False, "error": res.get("stderr") or "doctor failed", "raw": res}
    try:
        parsed = json.loads(res["stdout"])
        parsed["_subprocess"] = res
        return parsed
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"doctor output was not JSON: {exc}", "raw": res}


def run_brainctl_version() -> Optional[str]:
    """Shell out to `brainctl version` (a subcommand, not a flag) and
    return the version string, or None on failure.
    """
    brainctl = _find_brainctl_bin()
    if not brainctl:
        return None
    res = _capture([brainctl, "version"], kind="version")
    if not res["ok"]:
        return None
    try:
        return json.loads(res["stdout"]).get("version")
    except (json.JSONDecodeError, AttributeError):
        return None


def run_brainctl_migrate() -> Dict[str, Any]:
    """Run `brainctl migrate` in a subprocess against the post-upgrade
    binary. This is the load-bearing piece: the parent process has the
    OLD migrate.py loaded; the child loads the NEW one off disk.
    """
    brainctl = _find_brainctl_bin()
    if not brainctl:
        return {"ok": False, "error": "brainctl binary not found on PATH after upgrade"}
    res = _capture([brainctl, "migrate"], kind="migrate")
    parsed: Dict[str, Any] = {"ok": res["ok"], "_subprocess": res}
    if res["ok"]:
        try:
            parsed.update(json.loads(res["stdout"]))
        except json.JSONDecodeError:
            parsed["raw_stdout"] = res["stdout"]
    return parsed


# -- internal -----------------------------------------------------------------


def _find_brainctl_bin() -> Optional[str]:
    """Resolve the brainctl shim. After a pipx/pip upgrade the shim path
    may have moved (different venv prefix), so always re-resolve via PATH.
    """
    return shutil.which("brainctl")


def _capture(cmd: List[str], kind: str) -> Dict[str, Any]:
    """Run a subprocess and capture stdout/stderr/rc into a uniform dict.

    Forwards stdout/stderr live to the parent terminal too, so users
    watching `brainctl update` see pip's progress output in real time.
    The captured copies go into the JSON summary.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300,
        )
        return {
            "ok": result.returncode == 0,
            "kind": kind,
            "cmd": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False, "kind": kind, "cmd": cmd, "returncode": -1,
            "stdout": exc.stdout or "", "stderr": f"timeout after {exc.timeout}s",
        }
    except FileNotFoundError as exc:
        return {
            "ok": False, "kind": kind, "cmd": cmd, "returncode": -1,
            "stdout": "", "stderr": f"command not found: {exc}",
        }


# -- recovery message --------------------------------------------------------

VIRGIN_TRACKER_RECOVERY = """\
brainctl detected a virgin migration tracker with schema drift.

Your brain.db has columns from migrations that were never recorded in
the schema_versions table — running `brainctl migrate` directly would
crash on column collisions. This usually means the DB predates the
migration tracking framework.

Recover with:

  1. brainctl migrate --status-verbose
       (see which migrations are truly pending vs already-in-schema)
  2. apply any truly-pending migrations manually via sqlite3
  3. brainctl migrate --mark-applied-up-to N
       (backfill the rest — N is the highest version your schema already has)
  4. brainctl migrate
       (run anything above N)

Aborted update without running migrations to protect your DB.
"""
