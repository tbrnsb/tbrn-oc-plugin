#!/usr/bin/env python3
"""agent-finished-notify.py <sessionID> — notify-send when an agent finishes.

Called by ~/.config/opencode/plugins/agent-finished.js on terminal execution
events (`session.execution.succeeded` / `.failed` on v2.2.5; `session.idle`
on older builds). Reads the OpenCode DB READ-ONLY, resolves the session's
agent name, duration and token consumption, and emits:

    "<agent-name> has finished in <time>, consuming <tokens>"

Guards (so a busy event stream never spams the desktop):
  - Only subagent sessions (parent_id set AND agent set) notify by default;
    set AGENT_NOTIFY_ALL=1 to include top-level sessions.
  - Skips when the session has been idle for longer than 10 minutes (event
    replay at startup would otherwise fire a burst of stale notifications).
  - Skips when this exact (sessionID, time_updated) was already notified;
    state lives in ~/.cache/tbrn.oc-plugin/notified.json.
  - Zero tokens AND zero duration -> skip (empty/forked sessions).

Exit codes: 0 = handled (notified or deliberately skipped), 1 = error.
Diagnostics go to stderr; nothing is printed on stdout.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
STATE_PATH = os.path.expanduser("~/.cache/tbrn.oc-plugin/notified.json")
MAX_IDLE_MS = 10 * 60 * 1000  # ignore replayed idle events for stale sessions


def load_state():
    try:
        with open(STATE_PATH) as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    # Keep the map bounded: drop everything older than 7 days.
    cutoff = time.time() * 1000 - 7 * 24 * 3600 * 1000
    trimmed = {k: v for k, v in state.items() if isinstance(v, (int, float)) and v >= cutoff}
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(trimmed, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print(f"[agent-finished] state write failed: {e}", file=sys.stderr)


def fmt_duration(ms):
    s = max(1, int(round(ms / 1000)))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def fmt_tokens(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def _table_exists(con, name):
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def session_info(session_id):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        # OpenCode moved live session rows to `session_v2` on 2026-10-28
        # (v2.2.0+): the old `session` table stops receiving new rows. Prefer
        # `session_v2` and fall back to `session` for older databases.
        row = None
        for table in ("session_v2", "session"):
            if not _table_exists(con, table):
                continue
            row = con.execute(
                "SELECT agent, parent_id, time_created, time_updated, "
                "tokens_input, tokens_output, tokens_cache_read, tokens_cache_write "
                f"FROM {table} WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row:
                break
    finally:
        con.close()
    if not row:
        return None
    agent, parent_id, tc, tu, ti, to, tcr, tcw = row
    tokens = sum(x or 0 for x in (ti, to, tcr, tcw))
    return {
        "agent": agent,
        "parent_id": parent_id,
        "created": tc or 0,
        "updated": tu or 0,
        "tokens": tokens,
    }


def main():
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("usage: agent-finished-notify.py <sessionID>", file=sys.stderr)
        return 1
    session_id = sys.argv[1]

    info = session_info(session_id)
    if not info:
        return 0  # session vanished; nothing to say

    now_ms = time.time() * 1000
    if now_ms - info["updated"] > MAX_IDLE_MS:
        return 0  # stale replay

    if os.environ.get("AGENT_NOTIFY_ALL") != "1" and not info["parent_id"]:
        return 0  # top-level session; only subagent dispatches notify

    if not info["agent"]:
        return 0

    state = load_state()
    if state.get(session_id) == info["updated"]:
        return 0  # already notified for this exact state
    state[session_id] = info["updated"]
    save_state(state)

    duration_ms = max(0, info["updated"] - info["created"])
    msg = (f"{info['agent']} has finished in {fmt_duration(duration_ms)}, "
           f"consuming {fmt_tokens(info['tokens'])} tokens")
    try:
        subprocess.run(
            ["notify-send", "-a", "OpenCode", "-t", "8000", msg],
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[agent-finished] notify-send failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
