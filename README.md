# TBRN OC Plugin

OpenCode session statistics in a native Omarchy bar panel: one robot icon in the bar, one three-tab popup (Dashboard, Story, Overview) fed by a single JSON record from a read-only collector.

![Screenshot](assets/screenshot.png)

## Dashboard (tab 1)

- **ACTIVITY** — best/current streak, active days, sessions, subagents, prompts, steps (matches OpenCode's `opencode stats`)
- **TOKENS** with four ranges — 7D (day bars), 1M (week bars), 1Y (GitHub-style heatmap), ALL (heatmap)
- **TOKENS BY MODEL** — top 5, collapsible

## Story (tab 2)

- **PEAK HOURS** — 24-bar histogram, peak highlighted
- **AGENTS** — top-level sessions, collapsible
- **DISPATCH** — who dispatched to whom, with counts
- **SUBAGENTS** — dispatched sessions, collapsible

## Overview (tab 3)

- **ALL-TIME** — totals matching OpenCode's `/statistics` (tokens, sessions, subagents, prompts, steps, cost)
- **TOOLS** — most-used tools, collapsible
- **PROVIDERS** — collapsed by default

## Architecture

```
OpenCode (writes DB)                     Omarchy bar panel
┌────────────────────────────┐           ┌─────────────────────────┐
│ ~/.local/share/opencode/   │  read-    │ Panel.qml   (3-tab UI)  │
│   opencode.db              │<──only──  │   └ Main.qml (timer,    │
│   session_v2 / session      │   JSON   │      refresh coalescing)│
│   (legacy: session/part)   │  record   │        └ collect.py ────┤
│ opencode stats --json      │<──RPC────┤  (5-min stats cache,   │
└────────────────────────────┘           │   tools cache)          │
                                         └─────────────────────────┘
```

1. **Collector (`collect.py`)** — one JSON record on stdout. Reads the DB READ-ONLY (prefers OpenCode's V2 tables `session_v2`/`session_message`, falls back to legacy `session`/`part`), overlays `opencode stats --json` (cached 5 min) for the top-level numbers, and caches the expensive tool scan by source revision.
2. **Main.qml** — runs the collector on a timer (default 15 min, configurable 30 s–1 h) plus manual refresh; coalesces overlapping runs and keeps the last-good record on failure.
3. **Panel.qml** — renders the three-tab popup from the record.

## Requirements

- Omarchy with the Quickshell-based shell (`omarchy-shell`)
- OpenCode 2.x with the local stats DB at `~/.local/share/opencode/opencode.db`
- Python 3 (stdlib only); the collector shells out to `opencode stats --json`
- Tested against Omarchy shell (Nov 2026) and OpenCode 2.2.0+ schemas (legacy V1 `session`/`part` and V2 `session_v2`/`session_message`)

Schema handling: which table family is used is decided per connection. When `session_v2` exists with rows it wins (it is a superset in current OpenCode); a legacy-only install keeps working unchanged. An unknown future schema degrades to a zero-state record with a stderr diagnostic.

## Installation

Via the Omarchy plugin manager:

```bash
omarchy plugin add https://github.com/tbrnsb/tbrn-oc-plugin.git
omarchy plugin enable tbrn.oc-plugin
```

Manual:

```bash
git clone https://github.com/tbrnsb/tbrn-oc-plugin.git ~/.config/omarchy/plugins/tbrn.oc-plugin
omarchy-shell shell rescanPlugins
```

`omarchy-restart-shell` re-instantiates the bar slot after install or update (bar slots do not hot-replace).

## Configuration

`refreshIntervalSec` (default 900 s, minimum 30 s, maximum 3600 s) — how often the collector re-reads the DB. Adjust via Omarchy's plugin settings (`omarchy plugin set` or the plugin settings UI). Manual refresh: `R` in the panel, `omarchy-shell tbrn.oc-plugin refresh`, or the panel's refresh keybinding.

## Optional notifications

A separate companion plugin, `~/.config/opencode/plugins/agent-finished.js` (not part of this repo's install), emits a desktop notification when a subagent finishes:

```
"coder has finished in 18m 12s, consuming 2.0M"
```

It requires `notify-send` and a working notification daemon, and it must be installed into OpenCode's plugin directory separately. It is optional — the plugin works fully without it.

## Troubleshooting

- **No panel when clicking the robot** — `omarchy-shell tbrn.oc-plugin open` and check `journalctl --user -t omarchy-shell`. A QML error loads the plugin but leaves the panel blank.
- **Numbers look zero/empty** — `omarchy-shell tbrn.oc-plugin refresh`, then inspect stderr in the journal (`tbrn.oc-plugin:` lines). `opencode stats --json` failing (server busy) is non-fatal: the collector falls back to its own DB aggregation.
- **Stale data** — OpenCode flushed its legacy tables at the moment V2 activated; the collector auto-prefers `session_v2` in that case. If both look frozen, check that OpenCode is actually running (`opencode service status`).
- **DB locked / errors** — the collector opens the DB `mode=ro` and never writes; a busy server only slows reads.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

17 stdlib-only tests build temporary SQLite fixtures (V2, legacy, empty, corrupt, missing tables, malformed rows) and cover the JSON output contract, aggregation, dispatch, streaks, and failure handling. They never touch the real OpenCode DB or the user cache directory.

## Known limitations

- The project dropdown was removed from Overview; `collect.py` still accepts an optional project-id argument but the UI always passes "all".
- Tool scans are the heavy path; on a busy server the first cold run can take tens of seconds. Results are cached by source revision so warm refreshes are fast.
- Panel charts are sized for the Omarchy default; very small bar sizes may clip long labels.

## Uninstallation

```bash
omarchy plugin disable tbrn.oc-plugin
omarchy plugin remove tbrn.oc-plugin
```

Manual: delete `~/.config/omarchy/plugins/tbrn.oc-plugin/` and remove the widget from your bar config in `~/.config/omarchy/shell.json`, then `omarchy-restart-shell`. The caches under `~/.cache/tbrn.oc-plugin/` may be deleted too.

## License

MIT — see [LICENSE](LICENSE).
