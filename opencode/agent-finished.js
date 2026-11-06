/**
 * agent-finished: desktop notification when an OpenCode agent finishes.
 *
 * V2 plugin (`{ id, setup(ctx) }`). On terminal execution events it spawns
 * ~/.local/bin/agent-finished-notify.py, which resolves agent name / duration /
 * tokens from the DB read-only and shows:
 *
 *   "<agent-name> has finished in <time>, consuming <tokens>"
 *
 * Event names (verified against v2.2.5 with a diagnostic plugin, 2026-11-01):
 * v2.2.5 emits `session.execution.succeeded` / `session.execution.failed` /
 * `session.execution.interrupted` when a run ends. It does NOT emit
 * `session.idle` any more (kept in the set so older binaries still work) —
 * a filter on `session.idle` alone means this plugin never fires on v2.2.5.
 *
 * All guards (subagent-only, stale-replay skip, dedupe per session state)
 * live in the python script — this adapter only decides WHEN to invoke it.
 *
 * Env:
 *   AGENT_NOTIFY=0             disable entirely
 */

"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const SCRIPT = path.join(os.homedir(), ".local", "bin", "agent-finished-notify.py");

// Terminal events that mean "the agent run has ended". session.execution.failed
// is included on purpose: a failed dispatch is exactly when a notification is
// most useful. interrupted (user abort) is deliberately NOT notified - the user
// aborted, so they already know. This differs from session-archive.js, which
// DOES archive interrupted turns because an aborted turn is still worth keeping.
const TERMINAL_EVENTS = new Set([
  "session.execution.succeeded",
  "session.execution.failed",
  "session.idle", // legacy (< 2.2.5) name, kept for backward compatibility
]);

function enabled() {
  if (process.env.AGENT_NOTIFY === "0") return false;
  try {
    return fs.existsSync(SCRIPT);
  } catch {
    return false;
  }
}

function notify(sessionID) {
  if (!sessionID || typeof sessionID !== "string") return;
  let child;
  try {
    child = spawn("python3", [SCRIPT, sessionID], {
      stdio: ["ignore", "ignore", "pipe"],
      detached: false,
    });
  } catch (err) {
    console.error(`[agent-finished] spawn failed: ${err.message}`);
    return;
  }
  child.stderr.on("data", (buf) => {
    const text = String(buf).trim();
    if (text) console.error(`[agent-finished] ${text}`);
  });
  child.on("error", (err) => {
    console.error(`[agent-finished] ${err.message}`);
  });
  // Do not hold the event loop open for the child.
  if (typeof child.unref === "function") child.unref();
}

const plugin = {
  id: "agent-finished",
  setup(ctx) {
    if (!enabled()) {
      console.error("[agent-finished] disabled (script missing or AGENT_NOTIFY=0)");
      return;
    }

    const controller = new AbortController();

    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          signal: controller.signal,
        })) {
          if (!TERMINAL_EVENTS.has(event.type)) continue;
          const props = event.properties || event.data || {};
          const sessionID = props.sessionID || event.sessionID;
          notify(sessionID);
        }
      } catch (err) {
        if (err && err.name !== "AbortError") {
          console.error(`[agent-finished] event stream error: ${err.message}`);
        }
      }
    })();

    return () => controller.abort();
  },
};

module.exports = plugin;
