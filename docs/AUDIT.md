# Audit — tbrn.oc-plugin (2026-10-30)

Evidence-backed review of the repository before any changes. Each confirmed
item was reproduced with a command; see "Evidence" per finding.

## Baseline

- `collect.py` 471 lines, `Panel.qml` 1069, `Main.qml` 89, `manifest.json` 35, `README.md` 70.
- Python 3.14.7. `qmllint` at `/usr/lib/qt6/bin/qmllint`.
- Clean git tree at 71d6dfe. Deployed copy under
  `~/.config/omarchy/plugins/tbrn.oc-plugin/` is byte-identical to the repo.
- Warm-cache collector run: 0.65s. Cold (part-table scan): 21s.
- No LICENSE, no .gitignore, no tests, no CI.

## 1. Confirmed bugs

### B1 — Corrupt DB crashes the collector (no JSON on stdout)
`build_record` catches only `sqlite3.OperationalError` (collect.py:308). A
corrupt/non-SQLite file raises `sqlite3.DatabaseError`, which is the *parent*
of `OperationalError`, so it is not caught. `main()` then propagates the
exception → traceback on stderr, **nothing on stdout** → the panel receives no
record and shows no error. This directly contradicts the stability contract in
the module docstring ("a malformed DB yields a valid ZERO-STATE record, never a
crash").
Evidence: `sqlite3.OperationalError` is not a subclass of `sqlite3.DatabaseError`;
feeding a text file as the DB raises `DatabaseError`.

### B2 — 1M week bars render with zero width (NaN)
`peakWeek()` (Panel.qml:162-167) does `Math.max(peak, weeks[i])` where
`weeks[i]` is an object `{tokens,label,formatted}`, not a number. The result is
`NaN`, so every WeekRow `ratio` is `NaN` and the bar `width` is `NaN` → no bar
drawn in the 1M TOKENS view.
Evidence: replicating the function in node returns `peakWeek() = NaN` and
`barWidth=NaN` for all four weeks.

### B3 — ACTIVITY row overflows; 3 of 7 tiles are clipped
The ACTIVITY `Row` (Panel.qml:346-387) has 7 tiles, each `width:
parent.width / 4` = 175% of the available width. Only the first 4 tiles
(BEST STREAK, ACTIVE DAYS, SESSIONS, SUBAGENTS) are visible; PROMPTS, STEPS and
CURRENT are clipped off the right edge.
Evidence: live screenshot of the open panel shows the row cut off after
SUBAGENTS; 7 × 0.25 = 1.75 width fraction.

### B4 — Empty DB leaks real `opencode stats` numbers
With a session table present but 0 rows, `build_record` still applies the
`opencode stats --json` overlay, so `stats.activeDays` reports the real value
(30) while `allTime.sessions` is 0 — an inconsistent, misleading zero-state.
Evidence: empty fixture DB → `sessions: 0` but `stats.activeDays: 30`.

## 2. Missing / fragile error handling

- **F1** — `_aggregate`'s session query, the `agent_of` query and the projects
  query all assume every column exists. Schema drift (a future OpenCode
  version) raises `OperationalError`; after B1 is fixed this degrades to
  zero_state, which is acceptable but currently untested.
- **F2** — Line 542 (Panel.qml ALL-TIME) reads `oc_tokens.cache.read` without
  the `?.` guard that line 114 uses. It works today (real payload always has
  `cache`) but is inconsistent and would throw if the shape changed.
- **F3** — `main()` has no top-level guard: any unexpected exception loses the
  JSON contract entirely (see B1).
- **F4** — `_local_date(-5000)` buckets an invalid/negative `time_created` into
  1970-01-01, polluting `daily` and `activeDays`. `None` is handled (falls back
  to now) but non-positive values are not.

## 3. Compatibility risks

- **C1** — Hard dependency on exact `session` columns: `model, agent, parent_id,
  cost, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,
  summary_additions, summary_deletions, time_created, project_id`; and on the
  `part` table (`data`, `time_updated`, `session_id`) for tools. No schema
  detection or version documentation.
- **C2** — `opencode stats --json` output shape is assumed stable (`tokens.cache`,
  `streak`, `activeDays`, …). Only tested against the current install.
- **C3** — Tools cache is keyed only on `max(part.time_updated)`, not on the
  project filter, so a filtered run could reuse the wrong cached tools. Latent
  today because `projectFilter` is always "all".

## 4. Missing tests

None exist. No collector tests, no QML checks, no fixtures, no test command.

## 5. Documentation gaps

- README claims a working **project filter** (Features bullet) but the Overview
  dropdown was removed and `projectFilter` is hard-coded to "all" in Main.qml —
  the feature is not reachable.
- README quotes hard-coded statistics (585 sessions, 259 subagents, $14.94) that
  will drift; the live values are 778 / 262 / $14.91.
- No architecture/data-flow diagram, no troubleshooting, no testing section, no
  known-limitations, no uninstall, no compatibility matrix.
- No LICENSE file despite `license: MIT` in the manifest.

## 6. UI / usability problems

- **U1** = B3 (ACTIVITY clipping).
- **U2** = B2 (1M bars invisible).
- No stale-data or error indicator: when a refresh fails the panel silently
  keeps old data with no visual cue (the "updated Ns ago" hero hint is the only
  signal).
- Loading vs empty vs error are not distinguished: an empty DB and a failed
  collector both render the same "No OpenCode sessions yet" text.

## 7. Working correctly — must be preserved

- Collector emits a single valid JSON object with a stable 12-key contract.
- Read-only DB access via `file:...?mode=ro`.
- Graceful handling of missing DB (zero_state), NULL/malformed `model`
  ("Forked"), missing `part` table (tools=[]), NULL tokens (→0).
- Streak computation, daily/model/agent/subagent/provider/hour aggregation.
- 5-minute `opencode stats` cache and tools cache (0.65s warm run).
- Main.qml refresh coalescing (no overlapping runs) and last-good-record
  retention on parse failure.
- Three-tab layout, GitHub heatmap, dispatch graph, collapsible rows.
- Bar robot icon always renders.
