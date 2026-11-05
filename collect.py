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
  - A locked / missing / malformed / corrupt DB yields a valid ZERO-STATE
    record, never a crash or a partial object — the panel keeps its
    last-good record.
  - Diagnostics go to stderr; stdout carries ONLY the JSON.

Schema support (2026-11-04 — V2 table migration):
  OpenCode historically wrote `session` + `part`; V2.2.0+ additionally writes
  `session_v2` + `session_message` (same file, both may coexist). The legacy
  `session` table stopped receiving rows when V2 went live, which froze every
  breakdown at the switch date while the `opencode stats` overlay kept
  advancing — the two halves of one panel contradicted each other. This
  collector therefore:
  - prefers `session_v2` when it exists AND is non-empty (verified to be a
    true superset: it contains every legacy id plus the newer rows), falling
    back to legacy `session` on older installs;
  - reads tool calls from `session_message` (assistant messages, JSON
    `content[]` items with type=="tool", name field) when V2 is active,
    falling back to the legacy `part` scan (`data LIKE '%tool%'`, `tool`
    field) otherwise;
  - sums five token columns in V2 (legacy had no tokens_reasoning).

The tool scan is the expensive operation, so its result is cached in
~/.cache/tbrn.oc-plugin/tools.json keyed by max(rowid) of the source table
(O(1) probe; MAX(time_updated) would walk every payload page) plus a source
tag, recomputed only when that advances.
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

# Column pairs by schema family. Row values are read positionally through the
# idx map built from this list; tokens sum over the five V2 columns, four in
# legacy (tokens_reasoning absent there).
SESSION_COLUMNS_V2 = (
    "model", "agent", "parent_id", "cost", "tokens_input", "tokens_output",
    "tokens_reasoning", "tokens_cache_read", "tokens_cache_write",
    "summary_additions", "summary_deletions", "time_created", "time_updated",
    "project_id",
)
SESSION_COLUMNS_LEGACY = (
    "model", "agent", "parent_id", "cost", "tokens_input", "tokens_output",
    "tokens_cache_read", "tokens_cache_write",
    "summary_additions", "summary_deletions", "time_created", "project_id",
)


def zero_state(now_ms=None):
    """A fully-formed empty record. Every key the panel reads is present."""
    return {
        "generatedAt": _iso(now_ms),
        "allTime": {"sessions": 0, "tokens": 0, "additions": 0, "deletions": 0, "cost": 0.0},
        "stats": {
            "bestStreak": 0,
            "currentStreak": 0,
            "activeDays": 0,
            "avgTokens": 0,
            "sessions": 0,
            "subagents": 0,
            "prompts": 0,
            "steps": 0,
            "cost": 0.0,
            "oc_sessions": None,
            "oc_subagents": None,
            "oc_tokens": None,
            "oc_cost": None,
            "oc_prompts": None,
            "oc_steps": None,
        },
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


def _table_exists(conn, name):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


SCHEMAS = (
    # (source tag, table name, column list)
    ("v2", "session_v2", SESSION_COLUMNS_V2),
    ("legacy", "session", SESSION_COLUMNS_LEGACY),
)


def _detect_schema(conn):
    """Return (tag, table, columns) per DB shape.

    The `session_v2` family wins only when the table exists AND actually has
    rows: on an old install it may be absent entirely, and a present-but-empty
    one would otherwise zero out every breakdown while the legacy side still
    holds history.
    """
    for tag, table, cols in SCHEMAS:
        if not _table_exists(conn, table):
            continue
        try:
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            n = 0
        if n > 0:
            return (tag, table, cols)
    return (SCHEMAS[-1][0], SCHEMAS[-1][1], SCHEMAS[-1][2])


def _compute_tools(conn, tag):
    """Aggregate tool-call counts from the active tool-call source.

    V2 reads assistant messages from session_message (content[] items with
    type=="tool"; the tool name lives in the `name` field). Legacy reads the
    `part` table ({"tool": name} in data). The result is cached by max(rowid)
    of the source table plus the source tag — max(rowid) is an O(1) b-tree
    probe, unlike max(time_updated) which walks every payload page (measured
    73s on this DB) — and recomputed only when a new row lands.

    Project filtering is not wired into the tool scan today: Main.qml always
    passes "all". A filtered where clause would also poison the revision-keyed
    cache with per-project results, so the scan stays unfiltered and honest;
    revisit only if a project-aware tool listing is ever wired up.
    """
    if tag == "v2":
        rev_sql = "SELECT max(rowid) FROM session_message"
        like = "WHERE type='assistant' AND data LIKE '%\"tool\"%'"
        data_sql = f"SELECT data FROM session_message {like}"
        name_key = "name"
    else:
        rev_sql = "SELECT max(rowid) FROM part"
        like = "WHERE data LIKE '%\"tool\"%'"
        data_sql = f"SELECT data FROM part {like}"
        name_key = "tool"

    try:
        revision = conn.execute(rev_sql).fetchone()[0] or 0
    except sqlite3.Error:
        revision = None

    cached = _load_tools_cache()
    if cached is not None and revision is not None and _cache_matches(cached, tag, revision):
        return cached.get("tools", [])

    counts = {}
    try:
        rows = conn.execute(data_sql).fetchall()
    except sqlite3.Error as e:
        print(f"tbrn.oc-plugin: tool scan failed ({tag}): {e}", file=sys.stderr)
        if cached is not None:
            return cached.get("tools", [])
        return []

    for (data,) in rows:
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        items = obj.get("content") if tag == "v2" else [obj]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "tool":
                continue
            name = str(item.get(name_key) or "?")
            counts[name] = counts.get(name, 0) + 1

    tools = sorted(
        ({"name": k, "calls": v} for k, v in counts.items()),
        key=lambda x: x["calls"],
        reverse=True,
    )[:12]

    if revision is not None:
        _save_tools_cache({"src": tag, "revision": revision}, tools)
    return tools


def _cache_matches(cached, tag, revision):
    """Cache hit requires the same source schema AND revision."""
    got = cached.get("revision")
    if isinstance(got, dict):
        return got.get("src") == tag and got.get("revision") == revision
    return False


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
    except sqlite3.Error as e:
        # sqlite3.Error covers OperationalError AND DatabaseError (corrupt
        # file), plus LockError etc. The previous except caught only
        # OperationalError, so a corrupt DB crashed with a traceback and no
        # JSON on stdout.
        print(f"tbrn.oc-plugin: read failed (will retry next tick): {e}", file=sys.stderr)
        return zero_state(now_ms)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _aggregate(conn, now_ms, project_id=None):
    # Build WHERE clause for project filter
    filtered = bool(project_id and project_id != "all")
    params = [project_id] if filtered else []

    _, table, cols = _detect_schema(conn)
    select = ", ".join(cols)
    where = f"WHERE project_id = ?" if filtered else ""
    rows = conn.execute(f"SELECT {select} FROM {table} {where}", params).fetchall()

    # Positional lookup built from the actual column list, so a schema with
    # reordered/extra columns stays readable instead of hard-indexing.
    idx = {name: i for i, name in enumerate(cols)}
    tok_idx = []
    for name in ("tokens_input", "tokens_output", "tokens_reasoning",
                 "tokens_cache_read", "tokens_cache_write"):
        if name in idx:
            tok_idx.append(idx[name])
    i_model, i_agent, i_parent = idx["model"], idx["agent"], idx["parent_id"]
    i_cost = idx["cost"]
    i_add, i_del = idx["summary_additions"], idx["summary_deletions"]
    i_tc = idx["time_created"]
    i_proj = idx["project_id"]

    # agent name lookup for dispatch graph (parent_id -> parent's agent)
    agent_of = {}
    try:
        cur = conn.execute(f"SELECT id, agent FROM {table} {where}", params)
        for sid, agent in cur:
            if sid and agent:
                agent_of[sid] = str(agent)
    except sqlite3.Error as e:
        print(f"tbrn.oc-plugin: agent lookup failed: {e}", file=sys.stderr)

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

    for row in rows:
        model = row[i_model]
        agent = row[i_agent]
        parent_id = row[i_parent]
        cost = row[i_cost]
        tc = row[i_tc]
        add = row[i_add]
        dele = row[i_del]

        if tc is None or tc <= 0:
            # NULL / non-positive timestamps are unrecoverable; bucket like the
            # legacy None rule (now) so the session still counts, without
            # polluting 1970 with garbage rows.
            tc = now_ms
        tok = sum(row[i] or 0 for i in tok_idx)
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

    # Tools: filtered collection is the only supported mode today (project_id
    # is always "all" from Main.qml); _compute_tools scans the active source.
    tools = _compute_tools(conn, _detect_schema(conn)[0])

    # Projects list for dropdown
    try:
        proj_rows = conn.execute(
            f"SELECT project_id, COUNT(*) as cnt FROM {table} {where}"
            " GROUP BY project_id ORDER BY cnt DESC",
            params,
        ).fetchall()
    except sqlite3.Error as e:
        print(f"tbrn.oc-plugin: project rollup failed: {e}", file=sys.stderr)
        proj_rows = []
    total_sessions = sum(r[1] for r in proj_rows)
    projects = [{"id": "all", "name": "All Projects", "sessions": total_sessions}]
    for pid, cnt in proj_rows:
        name = "Global" if pid == "global" else (pid[:8] + "…" if len(pid) > 8 else pid)
        projects.append({"id": pid, "name": name, "sessions": cnt})

    all_time["cost"] = round(all_time["cost"], 4)

    # The opencode stats overlay exists to make ACTIVITY/ALL-TIME match the
    # real `opencode stats`. Overlaying it onto an EMPTY local DB would print
    # real numbers beside zero-token daily history (half-real, half-zero), so
    # when the queried table is empty we report genuine zeros instead.
    use_overlay = oc_stats is not None and all_time["sessions"] > 0

    return {
        "generatedAt": _iso(now_ms),
        "allTime": all_time,
        "stats": {
            "bestStreak": oc_stats.get("streak", best_streak) if use_overlay else best_streak,
            "currentStreak": oc_stats.get("streak", cur_streak) if use_overlay else cur_streak,
            "activeDays": oc_stats.get("activeDays", active_days) if use_overlay else active_days,
            "avgTokens": _oc_avg_tokens(oc_stats) if use_overlay else avg_tokens,
            "sessions": oc_stats.get("sessions", all_time["sessions"]) if use_overlay else all_time["sessions"],
            "subagents": oc_stats.get("subagents", 0) if use_overlay else 0,
            "prompts": oc_stats.get("prompts", 0) if use_overlay else 0,
            "steps": oc_stats.get("steps", 0) if use_overlay else 0,
            "cost": oc_stats.get("cost", all_time["cost"]) if use_overlay else all_time["cost"],
            # OpenCode all-time totals for Overview ALL-TIME section
            "oc_sessions": oc_stats.get("sessions") if use_overlay else None,
            "oc_subagents": oc_stats.get("subagents") if use_overlay else None,
            "oc_tokens": oc_stats.get("tokens") if use_overlay else None,
            "oc_cost": oc_stats.get("cost") if use_overlay else None,
            "oc_prompts": oc_stats.get("prompts") if use_overlay else None,
            "oc_steps": oc_stats.get("steps") if use_overlay else None,
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


def _oc_avg_tokens(oc_stats):
    """avg tokens per active day from the opencode stats payload, null-safe."""
    tokens = oc_stats.get("tokens") or {}
    read = (tokens.get("cache") or {}).get("read", 0) or 0
    write = (tokens.get("cache") or {}).get("write", 0) or 0
    total = (
        (tokens.get("input") or 0) + (tokens.get("output") or 0)
        + (tokens.get("reasoning") or 0) + read + write
    )
    return int(round(total / max(1, oc_stats.get("activeDays") or 1)))


def main():
    project_filter = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        record = build_record(project_id=project_filter)
    except Exception as e:  # absolute last resort: main must still emit JSON
        print(f"tbrn.oc-plugin: unexpected collector failure: {e}", file=sys.stderr)
        record = zero_state()
    json.dump(record, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
