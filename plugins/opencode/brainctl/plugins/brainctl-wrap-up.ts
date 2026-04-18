/**
 * brainctl-wrap-up — OpenCode session.idle / session.deleted hook.
 *
 * Writes a wrap-up handoff packet to brainctl when a session winds down.
 *
 * SUBTLETY: `session.idle` fires every time the user pauses, not only at
 * the very end. Without dedupe we'd write a handoff per pause, which would
 * spam brain.db and confuse the next agent_orient. We dedupe with a
 * tempfile flag at:
 *
 *     ${TMPDIR:-/tmp}/brainctl-opencode-wrapped/${session_id}.flag
 *
 * `session.deleted` always wins (it's the true terminal event); on
 * `session.idle` we only wrap if the flag is absent and we set it after.
 * The flag dir is wiped opportunistically (anything older than 24h) so we
 * never leak unbounded files.
 *
 * Every brainctl call is wrapped in try/catch — a failed wrap_up MUST NOT
 * break the OpenCode session.
 */
import type { Plugin } from "@opencode-ai/plugin";

const AGENT_ID_PREFIX = "opencode";

function shortId(id: string | undefined | null): string {
  if (!id || typeof id !== "string") return "unknown";
  return id.replace(/[^a-z0-9_-]/gi, "").slice(0, 8) || "unknown";
}

function projectScope(directory: string | undefined): string {
  if (!directory) return "default";
  const parts = directory.split("/").filter(Boolean);
  return parts[parts.length - 1] || "default";
}

function flagDir(): string {
  // Bun resolves env via process.env at runtime.
  const tmp =
    (typeof process !== "undefined" && process.env?.TMPDIR) || "/tmp";
  return `${tmp}/brainctl-opencode-wrapped`;
}

function flagPath(sessionId: string): string {
  return `${flagDir()}/${shortId(sessionId)}.flag`;
}

async function alreadyWrapped(
  $: any,
  sessionId: string,
): Promise<boolean> {
  try {
    const proc = await $`test -f ${flagPath(sessionId)}`
      .quiet()
      .nothrow();
    return proc.exitCode === 0;
  } catch {
    return false;
  }
}

async function markWrapped($: any, sessionId: string): Promise<void> {
  try {
    await $`mkdir -p ${flagDir()}`.quiet().nothrow();
    await $`touch ${flagPath(sessionId)}`.quiet().nothrow();
    // Opportunistic cleanup: drop flags older than a day.
    await $`find ${flagDir()} -type f -mtime +1 -delete`.quiet().nothrow();
  } catch {
    // best-effort; if the FS is hostile we just risk a duplicate wrap_up.
  }
}

async function buildSummary(
  $: any,
  agentId: string,
  project: string,
): Promise<string> {
  // Pull recent events for THIS agent and collapse into one sentence.
  // Use `event search` (not `event tail`) because tail has no --project /
  // --limit / --json flags — search emits JSON by default and supports
  // both filters.
  try {
    const proc = await $`brainctl --agent ${agentId} event search --project ${project} --limit 30`
      .quiet()
      .nothrow();
    if (proc.exitCode !== 0) return "opencode session ended";
    const raw = proc.stdout.toString().trim();
    if (!raw) return "opencode session ended";
    let rows: any[] = [];
    try {
      rows = JSON.parse(raw);
    } catch {
      return "opencode session ended";
    }
    if (!Array.isArray(rows)) return "opencode session ended";
    let tools = 0;
    let errors = 0;
    const recent: string[] = [];
    for (const r of rows) {
      const t = String(r?.event_type ?? "");
      if (t === "observation") tools += 1;
      if (t === "error") errors += 1;
      const s = String(r?.summary ?? "");
      if (s.startsWith("tool:") && recent.length < 5) {
        const head = s.split(/\s+/)[0]?.split(":", 2)[1];
        if (head) recent.push(head);
      }
    }
    const parts: string[] = [`${tools} tool calls`];
    if (errors) parts.push(`${errors} errors`);
    if (recent.length) parts.push(`tools=${recent.join(",")}`);
    return `opencode session ended: ${parts.join("; ")}`;
  } catch {
    return "opencode session ended";
  }
}

async function wrapUp(
  $: any,
  client: any,
  sessionId: string,
  directory: string | undefined,
): Promise<void> {
  const agentId = `${AGENT_ID_PREFIX}-${shortId(sessionId)}`;
  const project = projectScope(directory);
  const summary = await buildSummary($, agentId, project);
  // Pass next-step / open-loops too — brainctl wrap-up otherwise defaults
  // them to placeholders. Ours give the next agent_orient a more useful
  // breadcrumb without the model having to do anything special.
  const nextStep = "resume from last tool call; check session_start event for prior context";
  const openLoops = "session ended via opencode lifecycle hook";
  try {
    await $`brainctl --agent ${agentId} wrap-up --summary ${summary} --project ${project} --next-step ${nextStep} --open-loops ${openLoops}`
      .quiet()
      .nothrow();
  } catch (err) {
    try {
      (client as any)?.app?.log?.({
        service: "brainctl",
        level: "warn",
        message: `wrap-up failed: ${String(err).slice(0, 200)}`,
      });
    } catch {}
    return;
  }
  await markWrapped($, sessionId);
}

export const BrainctlWrapUp: Plugin = async ({ client, $, directory }) => {
  return {
    // session.idle: dedup against tempfile flag — fires repeatedly otherwise.
    "session.idle": async (input: any, _output: any) => {
      try {
        const sessionId =
          input?.session?.id ??
          input?.sessionID ??
          input?.session_id ??
          input?.id ??
          "";
        if (!sessionId) return;
        if (await alreadyWrapped($, sessionId)) return;
        await wrapUp($, client, sessionId, directory);
      } catch (err) {
        try {
          (client as any)?.app?.log?.({
            service: "brainctl",
            level: "warn",
            message: `wrap-up(idle) failed: ${String(err).slice(0, 200)}`,
          });
        } catch {}
      }
    },
    // session.deleted: always-fire terminal. Re-wraps if user manually
    // closed the session and we hadn't seen idle yet.
    "session.deleted": async (input: any, _output: any) => {
      try {
        const sessionId =
          input?.session?.id ??
          input?.sessionID ??
          input?.session_id ??
          input?.id ??
          "";
        if (!sessionId) return;
        // session.deleted bypasses dedupe — but only if idle didn't already
        // wrap. The flag check still avoids back-to-back duplicates within
        // the same teardown sequence.
        if (await alreadyWrapped($, sessionId)) return;
        await wrapUp($, client, sessionId, directory);
      } catch (err) {
        try {
          (client as any)?.app?.log?.({
            service: "brainctl",
            level: "warn",
            message: `wrap-up(deleted) failed: ${String(err).slice(0, 200)}`,
          });
        } catch {}
      }
    },
  };
};

export default BrainctlWrapUp;
