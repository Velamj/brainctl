/**
 * brainctl-tool-log — OpenCode tool.execute.after hook.
 *
 * Writes a compact `observation` event for each tool execution. We log only
 * the tool name + a one-line input summary + success/failure, not the full
 * tool output — keeps brain.db lean and avoids leaking file contents.
 *
 * Every brainctl call is wrapped in try/catch. A failed event_add MUST NOT
 * break the OpenCode session.
 */
import type { Plugin } from "@opencode-ai/plugin";

const AGENT_ID_PREFIX = "opencode";
const MAX_INPUT_CHARS = 200;
// Tools whose input is noisy / uninteresting for memory.
const SKIP_TOOLS = new Set([
  "TodoWrite",
  "Glob",
  "Grep",
  "glob",
  "grep",
  "list",
  "list_directory",
  "ls",
]);

function shortId(id: string | undefined | null): string {
  if (!id || typeof id !== "string") return "unknown";
  return id.replace(/[^a-z0-9_-]/gi, "").slice(0, 8) || "unknown";
}

function projectScope(directory: string | undefined): string {
  if (!directory) return "default";
  const parts = directory.split("/").filter(Boolean);
  return parts[parts.length - 1] || "default";
}

function summarizeInput(args: any): string {
  if (!args || typeof args !== "object") return "";
  // Common keys across OpenCode's built-in tools and any third-party MCP
  // tools that share the broader Claude/Cursor convention.
  const keys = [
    "filePath",
    "file_path",
    "absolute_path",
    "path",
    "command",
    "shell_command",
    "pattern",
    "query",
    "url",
    "description",
  ];
  for (const k of keys) {
    const v = (args as Record<string, unknown>)[k];
    if (typeof v === "string" && v.trim()) {
      return `${k}=${v.trim().slice(0, MAX_INPUT_CHARS)}`;
    }
  }
  // Fallback: stringify shallowly.
  try {
    return JSON.stringify(args).slice(0, MAX_INPUT_CHARS);
  } catch {
    return "";
  }
}

function detectError(output: any): boolean {
  if (!output || typeof output !== "object") return false;
  const o = output as Record<string, any>;
  if (o.error) return true;
  if (o.is_error === true || o.isError === true) return true;
  if (typeof o.status === "string" && o.status.toLowerCase() === "error")
    return true;
  return false;
}

export const BrainctlToolLog: Plugin = async ({ client, $, directory }) => {
  return {
    "tool.execute.after": async (input: any, output: any) => {
      try {
        const sessionId =
          input?.sessionID ??
          input?.session_id ??
          input?.session?.id ??
          input?.id ??
          "";
        const agentId = `${AGENT_ID_PREFIX}-${shortId(sessionId)}`;
        const project = projectScope(directory);

        // OpenCode passes the tool descriptor on `input.tool` (a string in
        // most builds; a richer object in some). Read defensively.
        const tool =
          (typeof input?.tool === "string" ? input.tool : null) ??
          input?.tool?.id ??
          input?.tool?.name ??
          input?.toolName ??
          input?.name ??
          "";
        if (!tool || SKIP_TOOLS.has(String(tool))) return;

        const args =
          (output && typeof output === "object" ? output.args : null) ??
          input?.args ??
          input?.toolInput ??
          {};
        const isError = detectError(output);
        const summary = `tool:${tool} [${isError ? "error" : "ok"}] ${summarizeInput(args)}`
          .trim()
          .slice(0, 500);
        const eventType = isError ? "error" : "observation";

        await $`brainctl --agent ${agentId} event add ${summary} --type ${eventType} --project ${project} --importance 0.3`
          .quiet()
          .nothrow();
      } catch (err) {
        try {
          (client as any)?.app?.log?.({
            service: "brainctl",
            level: "warn",
            message: `tool-log hook failed: ${String(err).slice(0, 200)}`,
          });
        } catch {}
      }
    },
  };
};

export default BrainctlToolLog;
