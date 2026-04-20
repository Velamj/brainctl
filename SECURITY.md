# Security Policy

## Reporting a Vulnerability

If you find a security issue in brainctl, **do not open a public issue.**

Report it through one of these channels:

- **Email:** security@brainctl.org (aligned with the domain's CAA iodef record)
- **GitHub private advisories:** https://github.com/TSchonleber/brainctl/security/advisories/new

We'll acknowledge within 48 hours and aim to patch within 7 days for critical issues.

## Scope

brainctl stores data in a local SQLite file. Security considerations:

- **brain.db contains agent memories, events, and decisions.** Treat it like any database with sensitive data.
- **The MCP server (`brainctl-mcp`) runs over stdio** — it does not open network ports by default.
- **The web UI (`brainctl ui`) binds to localhost:3939** — not exposed externally by default.
- **No data leaves your machine.** brainctl makes zero network calls unless you explicitly use vector search with Ollama (local) or configure external integrations.

## Best Practices

- Keep `brain.db` permissions restrictive (`chmod 600`)
- Don't commit `brain.db` to public repos
- Use `BRAIN_DB` env var to keep the database outside your project directory
- Rotate any API keys stored in agent memories
