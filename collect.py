#!/usr/bin/env python3
"""collect.py — emit one JSON record of OpenCode session statistics (contract v2).

Reads the OpenCode SQLite stats DB READ-ONLY and prints a single JSON object
to stdout that the QML panel (Panel.qml) renders across three tabs. Stdlib only.

Contract v2 changes (2026-10-29 user batch):
  - `daily`: FULL per-day history, ascending — the panel slices 7D/1M/ALL from
    it and draws the month/all-time heatmap from the same array.
  - `stats`: best streak, current streak, active days, avg tokens/active day
    (the "usage statistics" summary that replaced the TODAY section).
  - `agents` (top-level sessions) vs `subagents` (parent_id set) are separate.
  - Sessions with no recorded model are labelled "Forked" (verified: the
    18 NULL-model rows are forked/synthetic sessions; no model is recoverable
    from their messages, so the label is honest, not a guess).

Stability contract:
  - Never writes to the DB; opens file:...?mode=ro with uri=True.
  - A locked / missing / malformed DB yields a valid ZERO-STATE record, never
    a crash or a partial object — the panel keeps its last-good record.
  - Diagnostics go to stderr; stdout carries ONLY the JSON.

The `part` table (~177k rows) is the expensive scan for tool counts, so tool
counts are cached in ~/.cache/tbrn.oc-plugin/tools.json keyed by
max(time_updated) over `part`; recomputed only when that advances.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
CACHE_DIR = os.path.expanduser("~/.cache/tbrn.oc-plugin")
TOOLS_CACHE = os.path.join(CACHE_DIR, "tools.json")
OPENCODE_STATS_CACHE = os.path.join(CACHE_DIR, "opencode_stats.json")
OPENCODE_STATS_TTL = 300  # 5 minutes


def zero_state(now_ms=None):
    """A fully-formed empty record. Every key the panel reads is present."""
    return {
        "generatedAt": _iso(now_ms),
        "allTime": {"sessions": 0, "tokens": 0, "additions": 0, "deletions": 0, "cost": 0.0},
        "stats": {"bestStreak": 0, "currentStreak": 0, "activeDays": 0, "avgTokens": 0},
        "daily": [],
        "models": [],
        "agents": [],
        "subagents": [],
        "tools": [],
        "dispatch": [],
        "hours": [0] * 24,
        "providers": [],
        "projects": [],
    }


def _iso(now_ms):
    if now_ms is None:
        now_ms = time.time() * 1000
    return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()


def parse_model(raw):
    """session.model is JSON with providerID/id; it is sometimes NULL or garbage.

    Returns (provider, model_id, friendly_name). Never raises.
    NULL model = forked/synthetic session (verified 2026-10-29) -> "Forked".
    """
    if not raw:
        return ("?", "forked", "Forked")
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return ("?", "forked", "Forked")
        provider = str(obj.get("providerID") or "?")
        mid = str(obj.get("id") or "unknown")
        return (provider, mid, mid)
    except (ValueError, TypeError):
        return ("?", "forked", "Forked")


def _tokens(row):
    """Sum the four token columns, coercing NULLs to 0."""
    i, o, cr, cw = row
    return (i or 0) + (o or 0) + (cr or 0) + (cw or 0)


def _local_date(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _load_tools_cache():
    try:
        with open(TOOLS_CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_tools_cache(revision, tools):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(TOOLS_CACHE, "w") as f:
            json.dump({"revision": revision, "tools": tools}, f)
    except OSError as e:
        print(f"tbrn.oc-plugin: could not write tools cache: {e}", file=sys.stderr)


def _load_opencode_stats():
    """Load cached OpenCode stats JSON if fresh."""
    try:
        with open(OPENCODE_STATS_CACHE) as f:
            data = json.load(f)
        if time.time() - data.get("cached_at", 0) < OPENCODE_STATS_TTL:
            return data.get("stats")
    except (OSError, ValueError, KeyError):
        pass
    return None


def _save_opencode_stats(stats):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(OPENCODE_STATS_CACHE, "w") as f:
            json.dump({"cached_at": time.time(), "stats": stats}, f)
    except OSError as e:
        print(f"tbrn.oc-plugin: could not write opencode stats cache: {e}", file=sys.stderr)


def _fetch_opencode_stats():
    """Fetch fresh stats from `opencode stats --json`."""
    try:
        # Set TMPDIR to avoid conflicts with other opencode processes
        env = os.environ.copy()
        env["TMPDIR"] = CACHE_DIR
        result = subprocess.run(
            ["opencode", "stats", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"tbrn.oc-plugin: opencode stats failed (rc={result.returncode}): {result.stderr}", file=sys.stderr)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"tbrn.oc-plugin: opencode stats error: {e}", file=sys.stderr)
    return None


def _get_opencode_stats():
    """Get OpenCode stats, using cache if fresh."""
    cached = _load_opencode_stats()
    if cached is not None:
        return cached
    stats = _fetch_opencode_stats()
    if stats is not None:
        _save_opencode_stats(stats)
        return stats
    return None


def _compute_tools(conn, where_clause="", params=None, top=12):
    """Aggregate tool-call counts from the `part` table.

    Prefilter on the JSON text with LIKE (json_extract over all rows is slower),
    then parse. Caching by max(time_updated) keeps this ~6s work off the hot path.
    """
    if params is None:
        params = []
    try:
        cur = conn.execute(f"SELECT max(time_updated) FROM part {where_clause}", params)
        revision = cur.fetchone()[0] or 0
    except sqlite3.Error:
        revision = None

    cached = _load_tools_cache()
    if cached is not None and revision is not None and cached.get("revision") == revision:
        return cached.get("tools", [])

    counts = {}
    try:
        # Note: part table doesn't have project_id directly, so we join with session
        # For simplicity, we'll scan all parts if no project filter, or filter by joining
        if where_clause:
            # Join with session to filter by project
            rows = conn.execute(
                f"SELECT p.data FROM part p JOIN session s ON p.session_id = s.id {where_clause} AND p.data LIKE '%\"tool\"%'",
                params
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data FROM part WHERE data LIKE '%\"tool\"%'"
            ).fetchall()
    except sqlite3.Error as e:
        print(f"tbrn.oc-plugin: part scan failed: {e}", file=sys.stderr)
        if cached is not None:
            return cached.get("tools", [])
        return []

    for (data,) in rows:
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "tool":
            continue
        name = str(obj.get("tool") or "?")
        counts[name] = counts.get(name, 0) + 1

    tools = sorted(
        ({"name": k, "calls": v} for k, v in counts.items()),
        key=lambda x: x["calls"],
        reverse=True,
    )[:top]

    if revision is not None:
        _save_tools_cache(revision, tools)
    return tools


def _streaks(dates, reference_date=None):
    """(best_streak, current_streak) from a set of YYYY-MM-DD date strings.

    current = consecutive days ending at reference_date (default: most recent
    date in the set), so a streak isn't zeroed just because today has no sessions.
    """
    if not dates:
        return (0, 0)
    days = set()
    for d in dates:
        try:
            days.add(date.fromisoformat(d))
        except ValueError:
            continue
    if not days:
        return (0, 0)

    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if (prev is not None and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d

    # Current streak: consecutive days ending at reference_date (or most recent date in set)
    if reference_date is None:
        reference_date = max(days)
    elif isinstance(reference_date, str):
        reference_date = date.fromisoformat(reference_date)

    cur = 0
    cursor = reference_date if reference_date in days else None
    while cursor in days:
        cur += 1
        cursor = cursor - timedelta(days=1)
    return (best, cur)


def get_projects(db_path=None):
    """Return list of projects with session counts for dropdown."""
    db_path = db_path or DB_PATH
    if not os.path.exists(db_path):
        return [{"id": "all", "name": "All Projects", "sessions": 0}]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return [{"id": "all", "name": "All Projects", "sessions": 0}]
    try:
        rows = conn.execute("""
            SELECT project_id, COUNT(*) as cnt
            FROM session
            GROUP BY project_id
            ORDER BY cnt DESC
        """).fetchall()
        projects = [{"id": "all", "name": "All Projects", "sessions": sum(r[1] for r in rows)}]
        for pid, cnt in rows:
            name = "Global" if pid == "global" else (pid[:8] + "…" if len(pid) > 8 else pid)
            projects.append({"id": pid, "name": name, "sessions": cnt})
        return projects
    finally:
        conn.close()


def build_record(db_path=None, now_ms=None, project_id=None):
    """Read the DB and return the full contract record. Never raises on DB problems."""
    db_path = db_path or DB_PATH
    if now_ms is None:
        now_ms = time.time() * 1000

    if not os.path.exists(db_path):
        print(f"tbrn.oc-plugin: DB not found at {db_path}", file=sys.stderr)
        return zero_state(now_ms)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"tbrn.oc-plugin: open failed: {e}", file=sys.stderr)
        return zero_state(now_ms)

    try:
        return _aggregate(conn, now_ms, project_id)
    except sqlite3.OperationalError as e:
        print(f"tbrn.oc-plugin: read failed (will retry next tick): {e}", file=sys.stderr)
        return zero_state(now_ms)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _aggregate(conn, now_ms, project_id=None):
    # Build WHERE clause for project filter
    where_clause = ""
    params = []
    if project_id and project_id != "all":
        where_clause = "WHERE project_id = ?"
        params = [project_id]

    rows = conn.execute(
        f"SELECT model, agent, parent_id, cost, tokens_input, tokens_output, "
        f"tokens_cache_read, tokens_cache_write, summary_additions, "
        f"summary_deletions, time_created, project_id FROM session {where_clause}",
        params
    ).fetchall()

    # agent name lookup for dispatch graph (parent_id -> parent's agent)
    agent_of = {}
    for sid, agent in conn.execute(f"SELECT id, agent FROM session {where_clause}", params):
        if sid and agent:
            agent_of[sid] = str(agent)

    # Fetch OpenCode stats for accurate high-level metrics (matches `opencode stats`)
    oc_stats = _get_opencode_stats()

    all_time = {"sessions": 0, "tokens": 0, "additions": 0, "deletions": 0, "cost": 0.0}
    by_day = {}
    by_model = {}
    by_agent = {}       # top-level sessions (parent_id IS NULL)
    by_subagent = {}    # dispatched subagent sessions (parent_id NOT NULL)
    by_provider = {}
    by_hour = [0] * 24
    dispatch = {}

    for (
        model, agent, parent_id, cost, ti, to, tcr, tcw, add, dele, tc, proj
    ) in rows:
        if tc is None:
            tc = now_ms
        tok = _tokens((ti, to, tcr, tcw))
        c = float(cost or 0.0)
        a = int(add or 0)
        d = int(dele or 0)

        all_time["sessions"] += 1
        all_time["tokens"] += tok
        all_time["additions"] += a
        all_time["deletions"] += d
        all_time["cost"] += c

        date_str = _local_date(tc)
        day = by_day.setdefault(date_str, {"date": date_str, "tokens": 0, "sessions": 0})
        day["tokens"] += tok
        day["sessions"] += 1

        provider, mid, _ = parse_model(model)
        mkey = f"{provider}/{mid}"
        m = by_model.setdefault(mkey, {"provider": provider, "id": mid, "name": mid if mid != "forked" else "Forked", "tokens": 0, "sessions": 0})
        m["tokens"] += tok
        m["sessions"] += 1

        p = by_provider.setdefault(provider, {"name": provider, "sessions": 0})
        p["sessions"] += 1

        if agent:
            bucket = by_subagent if parent_id else by_agent
            ag = bucket.setdefault(str(agent), {"name": str(agent), "sessions": 0})
            ag["sessions"] += 1

        try:
            by_hour[datetime.fromtimestamp(tc / 1000).hour] += 1
        except (ValueError, OverflowError, OSError):
            pass

        if parent_id and agent:
            parent_agent = agent_of.get(parent_id, "?")
            key = (parent_agent, str(agent))
            dispatch[key] = dispatch.get(key, 0) + 1

    daily = sorted(by_day.values(), key=lambda x: x["date"])

    # Determine reference date for current streak: most recent session date
    ref_date = daily[-1]["date"] if daily else None
    best_streak, cur_streak = _streaks([d["date"] for d in daily if d["sessions"] > 0], ref_date)
    active_days = len(daily)
    avg_tokens = int(round(all_time["tokens"] / active_days)) if active_days else 0

    models = sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True)[:5]
    agents = sorted(by_agent.values(), key=lambda x: x["sessions"], reverse=True)
    subagents = sorted(by_subagent.values(), key=lambda x: x["sessions"], reverse=True)
    providers = sorted(by_provider.values(), key=lambda x: x["sessions"], reverse=True)
    disp = sorted(
        ({"from": k[0], "to": k[1], "count": v} for k, v in dispatch.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    # Tools: filter by project if specified
    tools = _compute_tools(conn, where_clause, params)

    # Projects list for dropdown
    try:
        proj_rows = conn.execute(f"SELECT project_id, COUNT(*) as cnt FROM session {where_clause} GROUP BY project_id ORDER BY cnt DESC", params).fetchall()
    except sqlite3.Error:
        proj_rows = []
    total_sessions = sum(r[1] for r in proj_rows)
    projects = [{"id": "all", "name": "All Projects", "sessions": total_sessions}]
    for pid, cnt in proj_rows:
        name = "Global" if pid == "global" else (pid[:8] + "…" if len(pid) > 8 else pid)
        projects.append({"id": pid, "name": name, "sessions": cnt})

    all_time["cost"] = round(all_time["cost"], 4)

    return {
        "generatedAt": _iso(now_ms),
        "allTime": all_time,
        "stats": {
            "bestStreak": oc_stats.get("streak", best_streak) if oc_stats else best_streak,
            "currentStreak": oc_stats.get("streak", cur_streak) if oc_stats else cur_streak,
            "activeDays": oc_stats.get("activeDays", active_days) if oc_stats else active_days,
            "avgTokens": int(round(oc_stats.get("tokens", {}).get("input", 0) + oc_stats.get("tokens", {}).get("output", 0) + oc_stats.get("tokens", {}).get("reasoning", 0) + oc_stats.get("tokens", {}).get("cache", {}).get("read", 0) + oc_stats.get("tokens", {}).get("cache", {}).get("write", 0)) / max(1, oc_stats.get("activeDays", 1))) if oc_stats else avg_tokens,
            "sessions": oc_stats.get("sessions", all_time["sessions"]) if oc_stats else all_time["sessions"],
            "subagents": oc_stats.get("subagents", 0) if oc_stats else 0,
            "prompts": oc_stats.get("prompts", 0) if oc_stats else 0,
            "steps": oc_stats.get("steps", 0) if oc_stats else 0,
            "cost": oc_stats.get("cost", all_time["cost"]) if oc_stats else all_time["cost"],
            # OpenCode all-time totals for Overview ALL-TIME section
            "oc_sessions": oc_stats.get("sessions") if oc_stats else None,
            "oc_subagents": oc_stats.get("subagents") if oc_stats else None,
            "oc_tokens": oc_stats.get("tokens") if oc_stats else None,
            "oc_cost": oc_stats.get("cost") if oc_stats else None,
            "oc_prompts": oc_stats.get("prompts") if oc_stats else None,
            "oc_steps": oc_stats.get("steps") if oc_stats else None,
        },
        "daily": daily,
        "models": models,
        "agents": agents,
        "subagents": subagents,
        "tools": tools,
        "dispatch": disp,
        "hours": by_hour,
        "providers": providers,
        "projects": projects,
    }


def main():
    project_filter = sys.argv[1] if len(sys.argv) > 1 else "all"
    record = build_record(project_id=project_filter)
    json.dump(record, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
