"""Tests for `brainctl update` (cmd_update + agentmemory.update helpers).

Strategy
--------
Calls cmd_update(args) **directly** with argparse.Namespace objects and
mocks the underlying subprocess.run (and the small wrapper helpers in
agentmemory.update). This is dramatically faster than spawning a real
brainctl process and keeps the tests deterministic — no real pip,
no real shell-out.

What we cover
-------------
- detect_install_mode: dev (editable), pipx, pip, unknown, dev (worktree fallback)
- --dry-run: prints planned action, no executes
- virgin-tracker-with-drift: short-circuits, exits 2, doesn't call migrate
- virgin-tracker-clean: proceeds normally
- --json: well-formed JSON summary
- --skip-migrate: upgrade only, no doctor/migrate calls
- --pre: forwarded to pip but NOT to pipx
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make src/ importable
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmemory import update as upd  # noqa: E402
from agentmemory._impl import cmd_update  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────


def _ns(**kw):
    """Build argparse.Namespace with sensible defaults."""
    base = dict(dry_run=False, pre=False, skip_migrate=False, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _capture_stdout(fn, *args, **kwargs):
    """Run ``fn`` and return (rc, stdout_text, stderr_text). Catches SystemExit."""
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with patch("sys.stdout", out), patch("sys.stderr", err):
        try:
            fn(*args, **kwargs)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


# Standard pip show outputs for the detection matrix.
PIP_SHOW_PIP = """\
Name: brainctl
Version: 2.2.3
Summary: stuff
Location: /opt/homebrew/lib/python3.14/site-packages
Requires: typing-extensions
"""

PIP_SHOW_PIPX = """\
Name: brainctl
Version: 2.2.3
Location: /Users/someone/.local/pipx/venvs/brainctl/lib/python3.12/site-packages
Requires:
"""

PIP_SHOW_DEV_EDITABLE = """\
Name: brainctl
Version: 2.2.3
Location: /opt/homebrew/lib/python3.14/site-packages
Editable project location: /Users/r4vager/agentmemory
Requires:
"""

PIP_SHOW_DEV_NO_MARKER = """\
Name: brainctl
Version: 2.2.3
Location: /Users/r4vager/agentmemory/src
Requires:
"""


# ── detection matrix ────────────────────────────────────────────────────────


class TestDetectInstallMode:
    def test_dev_editable_marker(self):
        mode, info = upd.detect_install_mode(pip_show_output=PIP_SHOW_DEV_EDITABLE)
        assert mode == "dev"
        assert info["editable_location"] == "/Users/r4vager/agentmemory"

    def test_pipx_venv_path(self):
        mode, info = upd.detect_install_mode(pip_show_output=PIP_SHOW_PIPX)
        assert mode == "pipx"
        assert "pipx/venvs" in info["location"]

    def test_plain_pip(self):
        mode, info = upd.detect_install_mode(pip_show_output=PIP_SHOW_PIP)
        assert mode == "pip"
        assert info["editable_location"] is None

    def test_unknown_when_pip_show_empty(self):
        mode, info = upd.detect_install_mode(pip_show_output="")
        assert mode == "unknown"

    def test_dev_via_cwd_under_install_location(self, tmp_path):
        """If the install Location lives under the user's cwd (or vice
        versa), classify as dev even without the editable marker.
        """
        location = tmp_path / "src"
        location.mkdir()
        fake_show = (
            "Name: brainctl\n"
            "Version: 2.2.3\n"
            f"Location: {location}\n"
        )
        mode, info = upd.detect_install_mode(pip_show_output=fake_show, cwd=tmp_path)
        assert mode == "dev"
        assert "sits under" in info["reason"]


# ── --dry-run ───────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_prints_plan_executes_nothing(self):
        with patch.object(upd, "detect_install_mode", return_value=("pip", {"reason": "test", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade") as mock_pip, \
             patch.object(upd, "run_pipx_upgrade") as mock_pipx, \
             patch.object(upd, "run_doctor_json") as mock_doctor, \
             patch.object(upd, "run_brainctl_migrate") as mock_migrate, \
             patch.object(upd, "run_brainctl_version") as mock_ver:
            rc, stdout, _ = _capture_stdout(cmd_update, _ns(dry_run=True))
        assert rc == 0
        # No subprocess wrappers should have fired
        for m in (mock_pip, mock_pipx, mock_doctor, mock_migrate, mock_ver):
            m.assert_not_called()
        # Plan should be visible in human output
        assert "Plan: pip install -U brainctl" in stdout

    def test_dry_run_with_pre_flag_includes_pre(self):
        with patch.object(upd, "detect_install_mode", return_value=("pip", {"reason": "x"})):
            rc, stdout, _ = _capture_stdout(cmd_update, _ns(dry_run=True, pre=True))
        assert rc == 0
        assert "--pre" in stdout

    def test_dry_run_dev_install_skips_upgrade(self):
        with patch.object(upd, "detect_install_mode",
                          return_value=("dev", {"reason": "editable", "editable_location": "/a"})):
            rc, stdout, _ = _capture_stdout(cmd_update, _ns(dry_run=True))
        assert rc == 0
        assert "Dev install detected" in stdout
        assert "git pull" in stdout


# ── virgin tracker short-circuit ────────────────────────────────────────────


class TestVirginTrackerShortCircuit:
    def test_drift_state_aborts_without_migrating(self):
        """The dangerous state — refuse to run migrate, exit 2."""
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade",
                          return_value={"ok": True, "kind": "pip_upgrade",
                                        "cmd": [], "returncode": 0,
                                        "stdout": "", "stderr": ""}), \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json",
                          return_value={"ok": True, "migrations": {"state": "virgin-tracker-with-drift"}}), \
             patch.object(upd, "run_brainctl_migrate") as mock_migrate:
            rc, _, stderr = _capture_stdout(cmd_update, _ns())

        assert rc == 2, "should exit 2 to signal virgin-tracker abort"
        mock_migrate.assert_not_called()
        assert "virgin migration tracker" in stderr.lower() or "virgin tracker" in stderr.lower()

    def test_clean_state_proceeds_to_migrate(self):
        """virgin-tracker-clean is safe — migrate should still fire."""
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade",
                          return_value={"ok": True, "kind": "pip_upgrade",
                                        "cmd": [], "returncode": 0,
                                        "stdout": "", "stderr": ""}), \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json",
                          return_value={"ok": True, "migrations": {"state": "virgin-tracker-clean"}}), \
             patch.object(upd, "run_brainctl_migrate",
                          return_value={"ok": True, "applied": 3,
                                        "_subprocess": {"kind": "migrate", "cmd": [],
                                                        "returncode": 0, "stderr": ""}}) as mock_migrate:
            rc, _, _ = _capture_stdout(cmd_update, _ns())
        assert rc == 0
        mock_migrate.assert_called_once()


# ── --json output ───────────────────────────────────────────────────────────


class TestJsonOutput:
    def test_json_summary_well_formed(self):
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade",
                          return_value={"ok": True, "kind": "pip_upgrade",
                                        "cmd": ["pip"], "returncode": 0,
                                        "stdout": "", "stderr": ""}), \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json",
                          return_value={"ok": True, "migrations": {"state": "up-to-date"}}), \
             patch.object(upd, "run_brainctl_migrate",
                          return_value={"ok": True, "applied": 0,
                                        "_subprocess": {"kind": "migrate", "cmd": [],
                                                        "returncode": 0, "stderr": ""}}):
            rc, stdout, _ = _capture_stdout(cmd_update, _ns(json=True))
        assert rc == 0
        data = json.loads(stdout)
        # Contract fields
        for k in ("ok", "old_version", "new_version", "install_mode",
                  "upgrade", "migrations_applied", "warnings"):
            assert k in data, f"missing key {k} in JSON summary"
        assert data["install_mode"] == "pip"
        assert data["new_version"] == "2.2.4"
        assert data["migrations_applied"] == 0
        assert isinstance(data["warnings"], list)


# ── flag interactions ───────────────────────────────────────────────────────


class TestFlagInteractions:
    def test_skip_migrate_does_not_call_doctor_or_migrate(self):
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade",
                          return_value={"ok": True, "kind": "pip_upgrade",
                                        "cmd": [], "returncode": 0,
                                        "stdout": "", "stderr": ""}), \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json") as mock_doctor, \
             patch.object(upd, "run_brainctl_migrate") as mock_migrate:
            rc, _, _ = _capture_stdout(cmd_update, _ns(skip_migrate=True))
        assert rc == 0
        mock_doctor.assert_not_called()
        mock_migrate.assert_not_called()

    def test_pre_flag_forwarded_to_pip(self):
        captured = {}
        def _fake_pip_upgrade(pre=False):
            captured["pre"] = pre
            return {"ok": True, "kind": "pip_upgrade", "cmd": [],
                    "returncode": 0, "stdout": "", "stderr": ""}
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade", side_effect=_fake_pip_upgrade), \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json",
                          return_value={"ok": True, "migrations": {"state": "up-to-date"}}), \
             patch.object(upd, "run_brainctl_migrate",
                          return_value={"ok": True, "applied": 0, "_subprocess": {}}):
            _capture_stdout(cmd_update, _ns(pre=True))
        assert captured.get("pre") is True

    def test_pre_flag_NOT_forwarded_to_pipx(self):
        """pipx upgrade does not honor --pre; we must not pretend it does."""
        with patch.object(upd, "detect_install_mode",
                          return_value=("pipx", {"reason": "x", "location": "/x/pipx/venvs/brainctl"})), \
             patch.object(upd, "run_pipx_upgrade",
                          return_value={"ok": True, "kind": "pipx_upgrade",
                                        "cmd": [], "returncode": 0,
                                        "stdout": "", "stderr": ""}) as mock_pipx, \
             patch.object(upd, "run_brainctl_version", return_value="2.2.4"), \
             patch.object(upd, "run_doctor_json",
                          return_value={"ok": True, "migrations": {"state": "up-to-date"}}), \
             patch.object(upd, "run_brainctl_migrate",
                          return_value={"ok": True, "applied": 0, "_subprocess": {}}):
            _capture_stdout(cmd_update, _ns(pre=True))
        # The pipx wrapper takes no args at all — no --pre to leak
        mock_pipx.assert_called_once_with()


# ── upgrade failure path ────────────────────────────────────────────────────


class TestUpgradeFailure:
    def test_pip_upgrade_failure_aborts_before_migrate(self):
        with patch.object(upd, "detect_install_mode",
                          return_value=("pip", {"reason": "x", "location": "/x"})), \
             patch.object(upd, "run_pip_upgrade",
                          return_value={"ok": False, "kind": "pip_upgrade",
                                        "cmd": ["pip"], "returncode": 1,
                                        "stdout": "", "stderr": "boom"}), \
             patch.object(upd, "run_doctor_json") as mock_doctor, \
             patch.object(upd, "run_brainctl_migrate") as mock_migrate:
            rc, _, _ = _capture_stdout(cmd_update, _ns(json=True))
        assert rc == 1
        mock_doctor.assert_not_called()
        mock_migrate.assert_not_called()
