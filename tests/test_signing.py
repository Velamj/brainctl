"""Tests for the signed-export pipeline (agentmemory.signing + CLI).

Covers:
  * round-trip build → sign → verify
  * tamper detection across every bundle field that matters
  * filter combinations (agent_id, category, scope, dates, explicit ids)
  * canonical JSON reproducibility (same DB + filter → same hash, run-to-run)
  * pubkey-mismatch detection
  * missing-keystore graceful error
  * missing-solders graceful error (install hint)
  * forward-compat: v1 bundle on a v2-aware brainctl
  * mock Solana RPC for pin_onchain / verify_onchain (no live mainnet)
  * end-to-end CLI: spawn ``brainctl export --sign`` against temp DB
"""
from __future__ import annotations

import builtins
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Skip the entire module gracefully if solders isn't available — the
# missing-solders behaviour is exercised explicitly elsewhere via a
# monkey-patched import.
solders = pytest.importorskip("solders", reason="brainctl[signing] required")
from solders.keypair import Keypair  # noqa: E402

from agentmemory import signing  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
INIT_SCHEMA = ROOT / "db" / "init_schema.sql"


def _make_db(tmp_path: Path, *, rows=None) -> Path:
    """Create a temp brain.db with the production schema and N test rows."""
    db_path = tmp_path / "brain.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(INIT_SCHEMA.read_text())
    ts = "2026-04-16T00:00:00"
    conn.execute(
        "INSERT INTO agents(id,display_name,agent_type,status,created_at,updated_at) "
        "VALUES (?, ?, 'test', 'active', ?, ?)",
        ("default", "default", ts, ts),
    )
    conn.execute(
        "INSERT INTO agents(id,display_name,agent_type,status,created_at,updated_at) "
        "VALUES (?, ?, 'test', 'active', ?, ?)",
        ("alt", "alt", ts, ts),
    )
    if rows is None:
        rows = [
            ("default", "project",   "global",     "alpha", "2026-01-01T00:00:00"),
            ("default", "lesson",    "project:x",  "beta",  "2026-02-01T00:00:00"),
            ("alt",     "project",   "global",     "gamma", "2026-03-01T00:00:00"),
            ("default", "preference","global",     "delta", "2026-04-01T00:00:00"),
        ]
    for agent, cat, scope, content, created in rows:
        conn.execute(
            "INSERT INTO memories(agent_id,category,scope,content,created_at,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent, cat, scope, content, created, created),
        )
    conn.commit()
    conn.close()
    return db_path


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def db_path(tmp_path):
    return _make_db(tmp_path)


@pytest.fixture
def keypair():
    return Keypair()


@pytest.fixture
def keystore_file(tmp_path, keypair):
    p = tmp_path / "keystore.json"
    p.write_text(json.dumps(list(bytes(keypair))))
    return p


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_build_sign_verify(db_path, keypair):
    conn = _open(db_path)
    bundle = signing.build_bundle(conn, generated_at="2026-04-16T00:00:00+00:00")
    assert len(bundle["memories"]) == 4
    assert bundle["version"] == 1
    signed = signing.sign_bundle(bundle, keypair, signed_at="2026-04-16T00:00:00+00:00")
    res = signing.verify_bundle(signed)
    assert res["ok"] is True
    assert res["error"] is None
    assert res["memories_count"] == 4
    assert res["signer_pubkey"] == str(keypair.pubkey())


def test_signed_bundle_shape(db_path, keypair):
    bundle = signing.build_bundle(_open(db_path))
    signed = signing.sign_bundle(bundle, keypair)
    assert set(signed.keys()) == {
        "version", "bundle", "bundle_hash_hex",
        "signature_b58", "signer_pubkey_b58", "signed_at",
    }
    assert signed["version"] == 1
    assert len(signed["bundle_hash_hex"]) == 64  # sha256 hex


def test_bundle_is_json_serialisable(db_path, keypair):
    """The whole signed bundle must round-trip through json.dumps/loads."""
    signed = signing.sign_bundle(signing.build_bundle(_open(db_path)), keypair)
    txt = json.dumps(signed, default=str)
    re_parsed = json.loads(txt)
    res = signing.verify_bundle(re_parsed)
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# 2. Tamper detection
# ---------------------------------------------------------------------------

def _signed(db_path, keypair, **kw):
    bundle = signing.build_bundle(_open(db_path), generated_at="2026-04-16T00:00:00+00:00", **kw)
    return signing.sign_bundle(bundle, keypair, signed_at="2026-04-16T00:00:00+00:00")


def test_tamper_memory_content(db_path, keypair):
    s = _signed(db_path, keypair)
    s["bundle"]["memories"][0]["content"] = "EVIL"
    res = signing.verify_bundle(s)
    assert res["ok"] is False
    assert "tampered" in res["error"]


def test_tamper_memory_id(db_path, keypair):
    s = _signed(db_path, keypair)
    s["bundle"]["memories"][0]["id"] = 99999
    assert signing.verify_bundle(s)["ok"] is False


def test_tamper_filter_used(db_path, keypair):
    s = _signed(db_path, keypair)
    s["bundle"]["filter_used"]["category"] = "fake"
    assert signing.verify_bundle(s)["ok"] is False


def test_tamper_generated_at(db_path, keypair):
    s = _signed(db_path, keypair)
    s["bundle"]["generated_at"] = "1999-01-01T00:00:00+00:00"
    assert signing.verify_bundle(s)["ok"] is False


def test_tamper_claimed_hash_only(db_path, keypair):
    """Mutating only the outer claimed hash field should fail loudly."""
    s = _signed(db_path, keypair)
    s["bundle_hash_hex"] = "0" * 64
    res = signing.verify_bundle(s)
    assert res["ok"] is False
    assert "tampered" in res["error"]


def test_swap_signature_for_another_signed_bundle(db_path, keypair):
    """A signature from a different bundle by the same key must reject."""
    s1 = _signed(db_path, keypair)
    s2 = _signed(db_path, keypair, category="lesson")
    s1["signature_b58"] = s2["signature_b58"]
    # Same hash field, but signature now corresponds to a different message.
    res = signing.verify_bundle(s1)
    assert res["ok"] is False


def test_pubkey_mismatch_detected(db_path, keypair):
    """Replacing the signer pubkey with a different one fails."""
    s = _signed(db_path, keypair)
    other = Keypair()
    s["signer_pubkey_b58"] = str(other.pubkey())
    res = signing.verify_bundle(s)
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# 3. Filter combinations
# ---------------------------------------------------------------------------

def test_filter_by_agent(db_path):
    b = signing.build_bundle(_open(db_path), agent_id="alt")
    assert [m["agent_id"] for m in b["memories"]] == ["alt"]


def test_filter_by_category(db_path):
    b = signing.build_bundle(_open(db_path), category="project")
    assert {m["category"] for m in b["memories"]} == {"project"}
    assert len(b["memories"]) == 2  # default+alt both have a 'project'


def test_filter_by_scope(db_path):
    b = signing.build_bundle(_open(db_path), scope="project:x")
    assert len(b["memories"]) == 1
    assert b["memories"][0]["scope"] == "project:x"


def test_filter_by_date_range(db_path):
    b = signing.build_bundle(
        _open(db_path),
        created_after="2026-02-15T00:00:00",
        created_before="2026-03-31T00:00:00",
    )
    assert len(b["memories"]) == 1
    assert b["memories"][0]["content"] == "gamma"


def test_filter_by_explicit_ids(db_path):
    b = signing.build_bundle(_open(db_path), ids=[1, 3])
    ids = [m["id"] for m in b["memories"]]
    assert ids == [1, 3]


def test_filter_combined(db_path):
    b = signing.build_bundle(_open(db_path), agent_id="default", category="project")
    assert all(m["agent_id"] == "default" and m["category"] == "project" for m in b["memories"])
    assert len(b["memories"]) == 1


def test_empty_id_list_is_empty_bundle(db_path):
    b = signing.build_bundle(_open(db_path), ids=[])
    assert b["memories"] == []


# ---------------------------------------------------------------------------
# 4. Canonical-JSON reproducibility
# ---------------------------------------------------------------------------

def test_hash_is_reproducible_across_runs(db_path):
    g = "2026-04-16T00:00:00+00:00"
    b1 = signing.build_bundle(_open(db_path), generated_at=g)
    b2 = signing.build_bundle(_open(db_path), generated_at=g)
    assert signing.bundle_hash(b1) == signing.bundle_hash(b2)


def test_hash_changes_when_generated_at_changes(db_path):
    b1 = signing.build_bundle(_open(db_path), generated_at="2026-04-16T00:00:00+00:00")
    b2 = signing.build_bundle(_open(db_path), generated_at="2026-04-17T00:00:00+00:00")
    assert signing.bundle_hash(b1) != signing.bundle_hash(b2)


def test_canonical_json_kwargs_documented():
    """Verifiers in other languages depend on these exact kwargs."""
    obj = {"b": 2, "a": 1}
    canon = signing.canonical_json(obj)
    # sort_keys → "a" comes before "b"; separators → no whitespace.
    assert canon == b'{"a":1,"b":2}'


def test_canonical_json_ascii_escapes_unicode():
    """ensure_ascii=True is the cross-platform reproducibility lever."""
    canon = signing.canonical_json({"x": "café"})
    assert b"\\u00e9" in canon  # é escaped, not raw bytes
    assert "café".encode("utf-8") not in canon


# ---------------------------------------------------------------------------
# 5. Forward-compat: version dispatch
# ---------------------------------------------------------------------------

def test_unknown_version_returns_structured_error():
    s = {"version": 99, "bundle": {}, "signature_b58": "x", "signer_pubkey_b58": "y"}
    res = signing.verify_bundle(s)
    assert res["ok"] is False
    assert "unsupported bundle version: 99" in res["error"]


def test_v1_still_verifies_when_version_dispatch_grows(db_path, keypair, monkeypatch):
    """Simulate a future brainctl that knows about v2 — v1 bundles still pass."""
    s = _signed(db_path, keypair)
    # Pretend the codebase has grown a v2 verifier.
    monkeypatch.setattr(signing, "_verify_v2", lambda b: {"ok": True}, raising=False)
    res = signing.verify_bundle(s)
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# 6. Keystore loading
# ---------------------------------------------------------------------------

def test_load_keystore_round_trips(keystore_file, keypair):
    loaded = signing.load_keystore(str(keystore_file))
    assert loaded.pubkey() == keypair.pubkey()


def test_load_keystore_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        signing.load_keystore(str(tmp_path / "nope.json"))


def test_load_keystore_invalid_format(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([1, 2, 3]))  # too short
    with pytest.raises(ValueError, match="64 ints"):
        signing.load_keystore(str(bad))


def test_load_keystore_non_int_entries(tmp_path):
    bad = tmp_path / "bad2.json"
    bad.write_text(json.dumps(["not", "ints"] + [0] * 62))
    with pytest.raises(ValueError):
        signing.load_keystore(str(bad))


def test_resolve_keystore_path_env_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAINCTL_SIGNING_KEY_PATH", str(tmp_path / "k.json"))
    assert signing.resolve_keystore_path(None) == str(tmp_path / "k.json")


def test_resolve_keystore_path_no_input(monkeypatch):
    monkeypatch.delenv("BRAINCTL_SIGNING_KEY_PATH", raising=False)
    with pytest.raises(FileNotFoundError, match="no keystore"):
        signing.resolve_keystore_path(None)


# ---------------------------------------------------------------------------
# 7. Missing-solders graceful error
# ---------------------------------------------------------------------------

def test_missing_solders_prints_install_hint(monkeypatch, capsys):
    """If solders is absent, _require_solders exits 1 with an install hint."""
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "solders" or name.startswith("solders."):
            raise ImportError("No module named 'solders'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        signing._require_solders()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "brainctl[signing]" in err


# ---------------------------------------------------------------------------
# 8. Mock Solana RPC: pin_onchain / verify_onchain
# ---------------------------------------------------------------------------

class _FakeRpc:
    """Substitutes for signing._rpc_call. Records calls + returns canned data."""

    def __init__(self, *, blockhash="11111111111111111111111111111111",
                 send_signature="aaaaaaaa", sigs_for=None, tx_log=None):
        self.calls = []
        self.blockhash = blockhash
        self.send_signature = send_signature
        self.sigs_for = sigs_for or []
        self.tx_log = tx_log

    def __call__(self, url, method, params, *, timeout=30.0):
        self.calls.append((method, params))
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": self.blockhash, "lastValidBlockHeight": 1}}
        if method == "sendTransaction":
            return self.send_signature
        if method == "getSignatureStatuses":
            return {"value": [{"slot": 12345, "confirmations": None}]}
        if method == "getSignaturesForAddress":
            return self.sigs_for
        if method == "getTransaction":
            return {"meta": {"logMessages": [self.tx_log] if self.tx_log else []}}
        raise AssertionError(f"unexpected RPC method {method}")


def test_pin_onchain_with_mock_rpc(db_path, keypair, monkeypatch):
    s = _signed(db_path, keypair)
    fake = _FakeRpc(send_signature="testsig123")
    monkeypatch.setattr(signing, "_rpc_call", fake)

    pin = signing.pin_onchain(s, keypair, rpc_url="http://fake")
    assert pin["ok"] is True
    assert pin["signature"] == "testsig123"
    assert pin["slot"] == 12345

    methods = [m for m, _ in fake.calls]
    assert "getLatestBlockhash" in methods
    assert "sendTransaction" in methods


def test_pin_onchain_rpc_error_returns_structured_failure(db_path, keypair, monkeypatch):
    def boom(url, method, params, *, timeout=30.0):
        raise RuntimeError("simulated mainnet down")

    monkeypatch.setattr(signing, "_rpc_call", boom)
    s = _signed(db_path, keypair)
    pin = signing.pin_onchain(s, keypair, rpc_url="http://fake")
    assert pin["ok"] is False
    assert "simulated mainnet down" in pin["error"]


def test_verify_onchain_finds_matching_memo(db_path, keypair, monkeypatch):
    s = _signed(db_path, keypair)
    needle = f"{signing.MEMO_PREFIX}:{s['bundle_hash_hex']}:{s['signer_pubkey_b58']}"
    fake = _FakeRpc(
        sigs_for=[{"signature": "txsig111"}],
        tx_log=f'Program log: Memo (len 92): "{needle}"',
    )
    monkeypatch.setattr(signing, "_rpc_call", fake)
    res = signing.verify_onchain(s["bundle_hash_hex"], s["signer_pubkey_b58"], rpc_url="http://fake")
    assert res["found"] is True
    assert res["tx_signature"] == "txsig111"


def test_verify_onchain_no_match(db_path, keypair, monkeypatch):
    fake = _FakeRpc(
        sigs_for=[{"signature": "txsig111"}],
        tx_log='Program log: Memo (len 4): "noop"',
    )
    monkeypatch.setattr(signing, "_rpc_call", fake)
    res = signing.verify_onchain("a" * 64, str(keypair.pubkey()), rpc_url="http://fake")
    assert res["found"] is False
    assert res["error"] is None


def test_verify_onchain_handles_rpc_failure(monkeypatch, keypair):
    def boom(url, method, params, *, timeout=30.0):
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(signing, "_rpc_call", boom)
    res = signing.verify_onchain("a" * 64, str(keypair.pubkey()), rpc_url="http://fake")
    assert res["found"] is False
    assert "rpc unavailable" in res["error"]


# ---------------------------------------------------------------------------
# 9. End-to-end CLI
# ---------------------------------------------------------------------------

def _run_cli(argv, *, db_path, env_extra=None):
    env = {
        **os.environ,
        "BRAIN_DB": str(db_path),
        "PYTHONPATH": str(SRC),
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agentmemory.cli", *argv],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_cli_export_and_verify_round_trip(tmp_path, keystore_file):
    db_p = _make_db(tmp_path)
    out = tmp_path / "bundle.json"

    r = _run_cli(
        ["export", "--sign", "--keystore", str(keystore_file),
         "-o", str(out), "--json"],
        db_path=db_p,
    )
    assert r.returncode == 0, f"export failed: {r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["memories_count"] == 4
    assert out.exists()

    r2 = _run_cli(["verify", str(out), "--json"], db_path=db_p)
    assert r2.returncode == 0
    vp = json.loads(r2.stdout)
    assert vp["ok"] is True


def test_cli_verify_tamper_exits_1(tmp_path, keystore_file):
    db_p = _make_db(tmp_path)
    out = tmp_path / "bundle.json"
    _run_cli(["export", "--sign", "--keystore", str(keystore_file),
              "-o", str(out), "--json"], db_path=db_p)

    s = json.loads(out.read_text())
    s["bundle"]["memories"][0]["content"] = "EVIL"
    out.write_text(json.dumps(s))

    r = _run_cli(["verify", str(out), "--json"], db_path=db_p)
    assert r.returncode == 1
    vp = json.loads(r.stdout)
    assert vp["ok"] is False
    assert "tampered" in vp["error"]


def test_cli_verify_check_onchain_missing_exits_2(tmp_path, keystore_file, monkeypatch):
    """The --check-onchain flag must produce exit-2 when no receipt exists.

    Run in-process (not via subprocess) so we can patch the RPC layer.
    """
    db_p = _make_db(tmp_path)
    bundle = signing.build_bundle(_open(db_p))
    kp = signing.load_keystore(str(keystore_file))
    signed = signing.sign_bundle(bundle, kp)

    out = tmp_path / "b.json"
    out.write_text(json.dumps(signed))

    monkeypatch.setattr(
        signing, "_rpc_call",
        lambda url, m, p, *, timeout=30.0: [] if m == "getSignaturesForAddress" else {},
    )

    from agentmemory.commands.sign import cmd_verify

    class _Args:
        bundle_path = str(out)
        check_onchain = True
        rpc_url = "http://fake"
        json = True

    with pytest.raises(SystemExit) as exc:
        cmd_verify(_Args())
    assert exc.value.code == 2


def test_cli_export_without_keystore_exits_1(tmp_path, monkeypatch):
    db_p = _make_db(tmp_path)
    monkeypatch.delenv("BRAINCTL_SIGNING_KEY_PATH", raising=False)
    # 2.3.2 added a managed-wallet auto-discovery path. Force the
    # wallet location to a tmp path that doesn't exist so this test
    # actually exercises the "no keystore at all" branch — otherwise
    # a real ~/.brainctl/wallet.json on the dev machine would silently
    # satisfy the resolver and the test would assert the wrong thing.
    r = _run_cli(["export", "--sign", "--json"],
                 db_path=db_p,
                 env_extra={
                     "BRAINCTL_SIGNING_KEY_PATH": "",
                     "BRAINCTL_WALLET_PATH": str(tmp_path / "nonexistent-wallet.json"),
                 })
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    # 2.3.2 changed the error wording: it now mentions both "wallet new"
    # and "--keystore" as recovery paths. Match on either keyword so the
    # test stays informative across both phrasings.
    assert ("keystore" in payload["error"]) or ("wallet" in payload["error"])


def test_cli_export_without_sign_exits_2(tmp_path, keystore_file):
    db_p = _make_db(tmp_path)
    r = _run_cli(["export", "--keystore", str(keystore_file), "--json"], db_path=db_p)
    # We deliberately reject unsigned exports for 2.3.0.
    assert r.returncode == 2
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert "--sign" in payload["error"]
