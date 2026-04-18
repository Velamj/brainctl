"""CLI handlers for ``brainctl export --sign`` and ``brainctl verify``.

Both commands shell out to ``agentmemory.signing``. Keeping the
parser-registration + handlers in their own module mirrors the
``commands/obsidian.py`` pattern and keeps ``_impl.py`` (already 16k+
lines) from growing further.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> Path:
    """Resolve brain.db the same way _impl.py does (env aware)."""
    from agentmemory.paths import get_db_path
    return get_db_path()


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _emit(payload: Dict[str, Any], *, as_json: bool, exit_code: int = 0) -> None:
    """Print a result and exit with ``exit_code``.

    The ``--json`` flag emits a single JSON object on stdout. Default
    output is a short human-readable summary on stdout, with the same
    structured payload as JSON below it suppressed.
    """
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        # Compact human-readable rendering.
        if "ok" in payload and not payload["ok"] and payload.get("error"):
            print(f"FAIL: {payload['error']}", file=sys.stderr)
        elif payload.get("ok"):
            print("OK")
        # Useful side-fields (sorted for stability).
        for key in (
            "bundle_path", "bundle_hash", "signer_pubkey",
            "memories_count", "signed_at", "signature", "tx_signature",
            "slot", "block_time", "found", "checked_onchain",
        ):
            if key in payload and payload[key] is not None:
                print(f"  {key}: {payload[key]}")
    sys.exit(exit_code)


def _parse_ids(s: Optional[str]) -> Optional[List[int]]:
    if not s:
        return None
    out: List[int] = []
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError as e:
            raise ValueError(f"invalid id in --ids: {chunk!r}") from e
    return out


# ---------------------------------------------------------------------------
# export --sign
# ---------------------------------------------------------------------------

def cmd_export(args: Any) -> None:
    as_json = bool(getattr(args, "json", False))

    if not getattr(args, "sign", False):
        # Future-proofing: leave room for an unsigned export mode later.
        _emit({
            "ok": False,
            "error": "brainctl export currently requires --sign. "
                     "(Plain unsigned exports are out of scope for 2.3.0.)",
        }, as_json=as_json, exit_code=2)

    # Late import — keeps the package import path solders-free.
    from agentmemory import signing

    db_path = _get_db_path()
    if not db_path.exists():
        _emit({"ok": False, "error": f"brain.db not found at {db_path}"},
              as_json=as_json, exit_code=1)

    try:
        keystore_path = signing.resolve_keystore_path(getattr(args, "keystore", None))
        keypair = signing.load_keystore(keystore_path)
    except FileNotFoundError as e:
        _emit({"ok": False, "error": str(e)}, as_json=as_json, exit_code=1)
    except (ValueError, json.JSONDecodeError) as e:
        _emit({"ok": False, "error": f"invalid keystore: {e}"},
              as_json=as_json, exit_code=1)

    try:
        ids = _parse_ids(getattr(args, "ids", None))
    except ValueError as e:
        _emit({"ok": False, "error": str(e)}, as_json=as_json, exit_code=1)

    conn = _open_db(db_path)
    try:
        bundle = signing.build_bundle(
            conn,
            agent_id=getattr(args, "filter_agent", None),
            category=getattr(args, "category", None),
            scope=getattr(args, "scope", None),
            created_after=getattr(args, "created_after", None),
            created_before=getattr(args, "created_before", None),
            ids=ids,
        )
    finally:
        conn.close()

    signed = signing.sign_bundle(bundle, keypair)

    out_path_arg = getattr(args, "output", None)
    out_path = Path(out_path_arg).expanduser() if out_path_arg else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(signed, indent=2, default=str), encoding="utf-8")

    payload: Dict[str, Any] = {
        "ok": True,
        "bundle_path": str(out_path) if out_path else None,
        "bundle_hash": signed["bundle_hash_hex"],
        "signer_pubkey": signed["signer_pubkey_b58"],
        "memories_count": len(bundle["memories"]),
        "signed_at": signed["signed_at"],
        "pinned_onchain": False,
        "signature": None,
        "slot": None,
    }

    if getattr(args, "pin_onchain", False):
        rpc_url = getattr(args, "rpc_url", None) or signing.DEFAULT_RPC_URL
        pin = signing.pin_onchain(signed, keypair, rpc_url=rpc_url)
        payload["pinned_onchain"] = bool(pin.get("ok"))
        payload["signature"] = pin.get("signature")
        payload["slot"] = pin.get("slot")
        if not pin.get("ok"):
            payload["error"] = f"on-chain pin failed: {pin.get('error')}"
            # Bundle was still signed locally; don't drop that work — exit 1
            # so callers know the pin failed but the bundle is on disk.
            _emit(payload, as_json=as_json, exit_code=1)

    if not out_path and not as_json:
        # No output file and not JSON mode — dump bundle to stdout so
        # the user gets *something* (mirrors `git format-patch -` UX).
        # In JSON mode we still want the summary payload, so suppress
        # the bundle dump there.
        sys.stderr.write(  # status to stderr to keep stdout pipeable
            f"signed {payload['memories_count']} memories "
            f"(hash={payload['bundle_hash'][:16]}..., "
            f"signer={payload['signer_pubkey'][:8]}...)\n"
        )
        print(json.dumps(signed, indent=2, default=str))
        sys.exit(0)

    _emit(payload, as_json=as_json, exit_code=0)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def cmd_verify(args: Any) -> None:
    as_json = bool(getattr(args, "json", False))
    bundle_path = Path(args.bundle_path).expanduser()

    if not bundle_path.exists():
        _emit({"ok": False, "error": f"bundle not found: {bundle_path}"},
              as_json=as_json, exit_code=1)

    try:
        signed = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _emit({"ok": False, "error": f"bundle is not valid JSON: {e}"},
              as_json=as_json, exit_code=1)

    from agentmemory import signing

    res = signing.verify_bundle(signed)
    payload: Dict[str, Any] = {
        "ok": bool(res.get("ok")),
        "signer_pubkey": res.get("signer_pubkey"),
        "signed_at": res.get("signed_at"),
        "bundle_hash": res.get("bundle_hash"),
        "memories_count": res.get("memories_count"),
        "error": res.get("error"),
        "checked_onchain": False,
        "found": None,
        "tx_signature": None,
        "block_time": None,
    }

    if not res.get("ok"):
        # Exit 1 for tamper / signature failures.
        _emit(payload, as_json=as_json, exit_code=1)

    if getattr(args, "check_onchain", False):
        rpc_url = getattr(args, "rpc_url", None) or signing.DEFAULT_RPC_URL
        on = signing.verify_onchain(
            res["bundle_hash"], res["signer_pubkey"], rpc_url=rpc_url,
        )
        payload["checked_onchain"] = True
        payload["found"] = bool(on.get("found"))
        payload["tx_signature"] = on.get("tx_signature")
        payload["block_time"] = on.get("block_time")
        if not on.get("found"):
            err = on.get("error") or "no matching memo-program tx found for this signer"
            payload["error"] = f"on-chain receipt missing: {err}"
            # Spec: exit 2 when --check-onchain was passed and the
            # receipt is missing.
            _emit(payload, as_json=as_json, exit_code=2)

    _emit(payload, as_json=as_json, exit_code=0)


# ---------------------------------------------------------------------------
# Parser registration (called from _impl.py's build_parser)
# ---------------------------------------------------------------------------

def register_parser(sub: Any) -> None:
    """Attach ``export`` and ``verify`` top-level subcommands."""
    # --- export ---
    p_exp = sub.add_parser(
        "export",
        help="Export memories as a signed JSON bundle (offline-verifiable, "
             "optionally pinned to Solana via the SPL memo program)",
        description=(
            "Export a filtered subset of brain.db memories as a JSON "
            "bundle, signed with your Solana keypair. The bundle stays "
            "on your machine; with --pin-onchain only the bundle's "
            "SHA-256 hash is published as a memo-program receipt."
        ),
    )
    p_exp.add_argument("--sign", action="store_true",
                       help="Sign the bundle (required in 2.3.0)")
    p_exp.add_argument("--keystore", default=None,
                       help="Path to a Solana CLI keystore (JSON array of 64 ints). "
                            "Falls back to $BRAINCTL_SIGNING_KEY_PATH.")
    p_exp.add_argument("--filter-agent", dest="filter_agent", default=None,
                       help="Only export memories from this agent_id")
    p_exp.add_argument("--category", default=None,
                       help="Only export memories with this category")
    p_exp.add_argument("--scope", default=None,
                       help="Only export memories with this scope")
    p_exp.add_argument("--created-after", dest="created_after", default=None,
                       metavar="ISO_TS",
                       help="Only export memories created at/after this ISO timestamp")
    p_exp.add_argument("--created-before", dest="created_before", default=None,
                       metavar="ISO_TS",
                       help="Only export memories created at/before this ISO timestamp")
    p_exp.add_argument("--ids", default=None,
                       help="Comma-separated explicit memory IDs (overrides other filters)")
    p_exp.add_argument("--pin-onchain", dest="pin_onchain", action="store_true",
                       help="After signing, post the bundle hash as an SPL "
                            "memo-program transaction (~$0.001/pin)")
    p_exp.add_argument("--rpc-url", dest="rpc_url", default=None,
                       help="Solana RPC URL (default: mainnet-beta public RPC)")
    p_exp.add_argument("-o", "--output", default=None,
                       help="Path to write the signed bundle JSON (default: stdout)")
    p_exp.add_argument("--json", action="store_true",
                       help="Emit a structured JSON summary on stdout")

    # --- verify ---
    p_ver = sub.add_parser(
        "verify",
        help="Verify a signed memory bundle (offline; --check-onchain "
             "additionally confirms the Solana memo-program receipt)",
    )
    p_ver.add_argument("bundle_path",
                       help="Path to the signed bundle JSON")
    p_ver.add_argument("--check-onchain", dest="check_onchain", action="store_true",
                       help="Also confirm a matching memo-program tx exists "
                            "for the signer (exit 2 if missing)")
    p_ver.add_argument("--rpc-url", dest="rpc_url", default=None,
                       help="Solana RPC URL (default: mainnet-beta public RPC)")
    p_ver.add_argument("--json", action="store_true",
                       help="Emit a structured JSON result on stdout")
