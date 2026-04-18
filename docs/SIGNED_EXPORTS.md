# Signed Memory Exports

`brainctl export --sign` produces a portable JSON bundle of memories
from your `brain.db`, signed with your Solana keypair. `brainctl
verify` checks the signature offline. With `--pin-onchain` /
`--check-onchain`, the bundle's hash is also pinned to (and read back
from) the Solana memo program, giving anyone in the world a tamper-
proof, timestamped receipt that you attested to that exact bundle.

This is **local-first by design**. Memory contents never leave your
machine. The on-chain footprint is tiny (one memo transaction, ~$0.001
at current SOL prices) and contains only the bundle's SHA-256 hash and
your wallet pubkey — not the memories themselves.

There is **no token gating**. Anyone with brainctl + a Solana keypair
can sign their own memories. The `$BrainCTL` token funds development
and exists for coordination/settlement utility, never to restrict
access. (See preference memory #1691.)

---

## Threat model

What signing **does** prove:

- The bundle was constructed by someone holding the private key for
  `signer_pubkey_b58` at the moment they signed it.
- No byte of the bundle has changed since the signature was made
  (any single-bit flip in any memory field breaks verification).
- With `--pin-onchain`, you also get a public, timestamped notarisation:
  "this wallet attested to this hash at slot N (block_time T)".

What signing does **not** prove:

- That the memories themselves are *true*. Signing is integrity, not
  truthfulness. Brainctl's W(m) gate, trust scoring, and Bayesian
  recall confidence handle truthfulness elsewhere.
- That the signer is the *original author* of the memories. Anyone can
  build a bundle from any rows in their DB and sign it. Signature ==
  attestation, not authorship.
- That the signer holds the wallet *now*. Compromised keys still
  produce valid signatures. Rotate via a signed "supersedes" bundle if
  this matters to you.

---

## Bundle format

The on-disk bundle is a single JSON object. The outer wrapper carries
the signature; the inner `bundle` is what gets hashed and signed:

```json
{
  "version": 1,
  "bundle": {
    "version": 1,
    "generated_at": "2026-04-16T12:00:00+00:00",
    "filter_used": {
      "agent_id": null,
      "category": "lesson",
      "scope": null,
      "created_after": null,
      "created_before": null,
      "ids": null
    },
    "memories": [
      {
        "id": 42,
        "agent_id": "claude-code-brainctl",
        "category": "lesson",
        "scope": "global",
        "content": "...",
        "confidence": 1.0,
        "tags": null,
        "created_at": "2026-04-15T18:52:36Z",
        "updated_at": "2026-04-15T18:52:36Z",
        "source_event_id": null,
        "supersedes_id": null,
        "trust_score": 1.0,
        "memory_type": "episodic"
      }
    ]
  },
  "bundle_hash_hex": "fe507e69...8e3d9e",
  "signature_b58": "...",
  "signer_pubkey_b58": "76zCYtcM7do2mrZJZdF1ZdXMyaJz8u7fwXHoS5KephDx",
  "signed_at": "2026-04-16T12:00:00+00:00"
}
```

### Canonical hashing

The `bundle_hash_hex` is the SHA-256 of the canonical JSON
serialisation of the inner `bundle` object. Canonical means **exactly**
these four `json.dumps` kwargs:

| kwarg            | value           |
| ---------------- | --------------- |
| `sort_keys`      | `True`          |
| `separators`     | `(",", ":")`    |
| `ensure_ascii`   | `True`          |
| `indent`         | (omitted)       |

`ensure_ascii=True` is the subtle one — it guarantees byte-identical
output across Python patch versions, platforms, and locales. Any
external verifier (JS, Rust, Go, Solidity-tooling) MUST reproduce the
same canonicalisation to get the same hash.

### Signature

`signature_b58` is the Ed25519 signature over the **raw 32 bytes** of
`SHA-256(canonical_bundle)`, base58-encoded. Not over the hex string.
That keeps the hashed payload small and lets external verifiers do
`ed25519_verify(pubkey, sha256(canonical_bundle), signature)` without
a hex round-trip.

---

## On-chain pinning (optional)

With `--pin-onchain`, brainctl posts one transaction to the SPL memo
program (program ID `MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`) with
the body:

```
brainctl/v1:<bundle_hash_hex>:<signer_pubkey_b58>
```

The memo prefix lets verifiers filter brainctl pins out of unrelated
memos by the same wallet. The transaction is signed and paid for by
the same keypair that signed the bundle (single-keypair flow).

Cost at the time of writing: ~5,000 lamports base fee + a tiny
priority fee = roughly **$0.001 per pin** at current SOL prices. The
pin is permanent — Solana finality, not pruned.

To verify a pin, brainctl fetches the signer's recent
`getSignaturesForAddress` results, then `getTransaction` for each, and
scans `meta.logMessages` for the matching `brainctl/v1:...` body.
`--check-onchain` does this automatically; exit code 2 means "the
bundle's signature is valid but no matching on-chain receipt was found
for this signer".

---

## Reference verification recipe (no brainctl required)

The whole point of the format is that anyone can verify a bundle
without trusting (or installing) brainctl. This 30-line snippet uses
only stdlib + `cryptography` (broader audience than `solders`):

```python
"""Verify a brainctl signed bundle without brainctl itself."""
import base58, hashlib, json, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")

def verify(path):
    signed = json.loads(open(path).read())
    if signed.get("version") != 1:
        return False, f"unsupported version {signed.get('version')}"

    bundle = signed["bundle"]
    msg = hashlib.sha256(canonical_json(bundle)).digest()

    if msg.hex() != signed["bundle_hash_hex"]:
        return False, "tampered: hash mismatch"

    pubkey = Ed25519PublicKey.from_public_bytes(
        base58.b58decode(signed["signer_pubkey_b58"])
    )
    try:
        pubkey.verify(base58.b58decode(signed["signature_b58"]), msg)
    except InvalidSignature:
        return False, "invalid signature"
    return True, f"ok ({len(bundle['memories'])} memories)"

ok, info = verify(sys.argv[1])
print(("OK: " if ok else "FAIL: ") + info)
sys.exit(0 if ok else 1)
```

`pip install base58 cryptography` and you're verifying brainctl bundles
in any Python 3 environment, no Solana stack required. Equivalent
JavaScript / Rust implementations are roughly the same size.

---

## CLI quick reference

Generate a Solana CLI keystore (one-time):

```bash
solana-keygen new -o ~/.config/solana/id.json
```

Sign + write a bundle:

```bash
brainctl export --sign \
  --keystore ~/.config/solana/id.json \
  --filter-agent claude-code-brainctl \
  --category lesson \
  -o lessons-2026-04.json \
  --json
```

Sign + pin on-chain in one shot (mainnet):

```bash
brainctl export --sign --pin-onchain \
  --keystore ~/.config/solana/id.json \
  --rpc-url https://api.mainnet-beta.solana.com \
  -o pinned.json
```

Verify offline:

```bash
brainctl verify pinned.json --json
echo "exit: $?"   # 0 = clean; 1 = tampered or malformed
```

Verify offline AND on-chain (fails with exit-2 if no receipt found):

```bash
brainctl verify pinned.json --check-onchain --json
```

---

## Honourable design choices

- **Single dependency.** `solders` covers Keypair / Pubkey / Signature
  / Hash / Instruction / Message / Transaction. JSON-RPC goes through
  stdlib `urllib.request`. We deliberately did **not** pull in
  `solana-py` (heavier, async-only, larger transitive dep set).
- **Lazy import.** `import solders` happens inside the signing
  functions, not at module top, so `from agentmemory import ...` keeps
  working for users who don't install `[signing]`. The CLI prints
  `pip install 'brainctl[signing]'` instead of crashing.
- **Single mock seam.** Tests patch `signing._rpc_call` once and that
  one stub covers `getLatestBlockhash`, `sendTransaction`,
  `getSignaturesForAddress`, and `getTransaction`. No live Solana RPC
  is required to run the test suite.
- **Forward-compat.** `verify_bundle` dispatches on the outer
  `version` field. v1 still verifies on a future brainctl that knows
  about v2. Unknown versions return a structured error rather than
  raising.
