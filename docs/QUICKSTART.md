# quickstart

Four steps. Under 60 seconds.

---

## step 1 — install

```bash
pip install brainctl
```

Expected output:
```
Successfully installed brainctl-2.4.0
```

If you want the MCP server (Claude Desktop, Cursor, VS Code):

```bash
pip install brainctl[mcp]
```

---

## step 2 — store your first memory

```bash
brainctl memory add "project uses OAuth 2.0 PKCE, client ID stored in .env" -c integration
```

Expected output:
```
memory saved  id=1  category=integration
```

The Python equivalent, if you prefer:

```python
from agentmemory import Brain
brain = Brain(agent_id="my-agent")
brain.remember("project uses OAuth 2.0 PKCE, client ID stored in .env", category="integration")
```

---

## step 3 — search

```bash
brainctl search "oauth"
```

Expected output:
```
[1] integration  0.91  project uses OAuth 2.0 PKCE, client ID stored in .env
```

---

## step 4 — sign and export a memory bundle

No Solana wallet? Create a local one first:

```bash
brainctl wallet new
```

Expected output:
```
wallet created  path=~/.brainctl/wallet.json  address=<pubkey>
keep this file safe — it is not backed up by brainctl
```

Then export a signed bundle:

```bash
brainctl export --sign -o my-memories.json
```

Expected output:
```
exported  memories=1  signed=true  bundle=my-memories.json
```

Verify the bundle on any machine — no brainctl required:

```bash
brainctl verify my-memories.json
# or, without brainctl, use the 30-line recipe in docs/SIGNED_EXPORTS.md
```

Expected output:
```
signature ok  signer=<pubkey>  memories=1
```

---

## next steps

- **Understand the feature set and how brainctl compares to alternatives** → [docs/COMPARISON.md](COMPARISON.md)
- **Wire up the MCP server** (Claude Desktop, Cursor, VS Code) → [MCP_SERVER.md](../MCP_SERVER.md)
- **Integrate into an agent** (orient / wrap_up lifecycle, framework plugins) → [docs/AGENT_ONBOARDING.md](AGENT_ONBOARDING.md)
- **Signed exports — threat model and bundle spec** → [docs/SIGNED_EXPORTS.md](SIGNED_EXPORTS.md)
- **Run the retrieval benchmark** → `python3 -m tests.bench.run`
