/**
 * brainctl-orient — OpenCode session.created hook.
 *
 * Pulls the pending handoff packet, recent events, active triggers, and top
 * memories from brainctl and pushes them into the session as a toast (best
 * surface OpenCode currently exposes for plugin-side context injection
 * without re-prompting the model). Also writes a `session_start` event so
 * the brain's own audit trail records that the session opened.
 *
 * IMPORTANT: every brainctl call is wrapped in try/catch. A failed brainctl
 * call MUST NEVER break an OpenCode session — at worst the user loses the
 * memory context for that one run.
 *
 * Implementation notes:
 *   - We shell out to the `brainctl` CLI rather than calling the
 *     `brainctl-mcp` server directly. The MCP server is registered in the
 *     `mcp` block of opencode.json so the *model* can use its 200+ tools;
 *     plugin code itself talks to brainctl via the CLI subcommands
 *     (`brainctl orient`, `brainctl wrap-up`, `brainctl event add`) which
 *     are stable, scriptable, and don't require an MCP client library.
 *   - If/when OpenCode exposes `client.mcp.tool({ server, tool, args })`
 *     for plugins, we can switch to that — search the file for
 *     `TODO(client.mcp.tool)`.
 *   - Payload shapes are read defensively because the official docs do not
 *     publish stable input/output schemas for every event.
 */
import type { Plugin } from "@opencode-ai/plugin";

const AGENT_ID_PREFIX = "opencode";
const ORIENT_TIMEOUT_MS = 8000;

function shortId(id: string | undefined | null): string {
  if (!id || typeof id !== "string") return "unknown";
  // First 8 chars is enough to disambiguate across concurrent sessions.
  return id.replace(/[^a-z0-9_-]/gi, "").slice(0, 8) || "unknown";
}

function projectScope(directory: string | undefined): string {
  if (!directory) return "default";
  const parts = directory.split("/").filter(Boolean);
  return parts[parts.length - 1] || "default";
}

function buildContextBlock(orient: any): string {
  const lines: string[] = ["brainctl session context"];
  const handoff = orient?.handoff;
  if (handoff) {
    if (handoff.goal) lines.push(`goal: ${String(handoff.goal).slice(0, 200)}`);
    if (handoff.next_step)
      lines.push(`next: ${String(handoff.next_step).slice(0, 200)}`);
    if (handoff.open_loops)
      lines.push(`open: ${String(handoff.open_loops).slice(0, 200)}`);
  }
  const events = Array.isArray(orient?.recent_events)
    ? orient.recent_events.slice(0, 3)
    : [];
  for (const ev of events) {
    const summary = String(ev?.summary ?? "").slice(0, 120);
    if (summary) lines.push(`event: ${summary}`);
  }
  const memories = Array.isArray(orient?.memories)
    ? orient.memories.slice(0, 3)
    : [];
  for (const m of memories) {
    const cat = String(m?.category ?? "?");
    const head = String(m?.content ?? "").split("\n")[0]?.slice(0, 120) ?? "";
    if (head) lines.push(`mem[${cat}]: ${head}`);
  }
  return lines.join(" | ");
}

export const BrainctlOrient: Plugin = async ({ client, $, directory }) => {
  return {
    "session.created": async (input: any, _output: any) => {
      try {
        const sessionId =
          input?.session?.id ??
          input?.sessionID ??
          input?.session_id ??
          input?.id ??
          "";
        const agentId = `${AGENT_ID_PREFIX}-${shortId(sessionId)}`;
        const project = projectScope(directory);

        // 1) call brainctl orient and capture compact JSON.
        // bun's $ throws on non-zero exit by default; .quiet() suppresses
        // stdio bleed-through; .nothrow() prevents the throw so we can
        // inspect exit code ourselves.
        const orientProc =
          await $`brainctl --agent ${agentId} orient --project ${project} --compact`
            .quiet()
            .nothrow();
        if (orientProc.exitCode !== 0) {
          // Brain might not be installed / migrated. Don't break the session.
          return;
        }
        let orient: any = null;
        try {
          orient = JSON.parse(orientProc.stdout.toString().trim());
        } catch {
          orient = null;
        }
        if (!orient || orient.ok === false) return;

        // 2) record session_start event for the audit trail.
        const evtSummary = `opencode session ${shortId(sessionId)} created`;
        await $`brainctl --agent ${agentId} event add ${evtSummary} --type session_start --project ${project} --importance 0.4`
          .quiet()
          .nothrow();

        // 3) surface a tiny breadcrumb to the user via the OpenCode client.
        // We deliberately avoid pushing the full orient payload into the
        // chat — it's logged into brain.db already; the user / model can
        // pull richer context with mcp__brainctl__agent_orient on demand.
        const block = buildContextBlock(orient);
        if (block && (client as any)?.app?.log) {
          try {
            await (client as any).app.log({
              service: "brainctl",
              level: "info",
              message: block.slice(0, 400),
            });
          } catch {
            // logging is best-effort; ignore.
          }
        }
        // TODO(client.mcp.tool): when OpenCode exposes a way to invoke MCP
        // tools from a plugin, switch the orient call above to:
        //   await client.mcp.tool({ server: "brainctl", tool: "agent_orient",
        //     args: { agent_id, project } })
        // — that path skips the CLI shell-out entirely.
      } catch (err) {
        // Catch-all so the user's session continues no matter what.
        try {
          (client as any)?.app?.log?.({
            service: "brainctl",
            level: "warn",
            message: `orient hook failed: ${String(err).slice(0, 200)}`,
          });
        } catch {}
      }
      // session.created handlers don't need to return anything.
    },
  };
};

// Default export so OpenCode's plugin loader picks it up regardless of
// whether the loader looks at default or named exports.
export default BrainctlOrient;
