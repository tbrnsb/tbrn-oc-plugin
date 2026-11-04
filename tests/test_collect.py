"""tests/test_collect.py — collector contract + reliability tests.

Run with (stdlib only, no third-party deps):

    python3 -m unittest discover -s tests -v

Every test builds temporary SQLite fixtures; none touch the real
~/.local/share/opencode/opencode.db. `collect.DB_PATH` is left alone except
where the test explicitly redirects it, and the module-level caches are
pointed at a tempdir via _reset_env() so tests never read/write the user's
~/.cache/tbrn.oc-plugin either.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import collect  # noqa: E402

V2_SESSION_DDL = """
CREATE TABLE session_v2 (
  id TEXT, project_id TEXT, workspace_id TEXT, parent_id TEXT,
  fork_session_id TEXT, slug TEXT, directory TEXT, path TEXT, title TEXT,
  version TEXT, share_url TEXT, summary_additions INT, summary_deletions INT,
  summary_files INT, summary_diffs TEXT, metadata TEXT, cost REAL,
  tokens_input INT, tokens_output INT, tokens_reasoning INT,
  tokens_cache_read INT, tokens_cache_write INT, revert INT, permission TEXT,
  agent TEXT, model TEXT, time_created INT, time_updated INT,
  time_compacting INT, time_archived INT, time_suspended INT,
  resume_attempts INT, time_idle INT, time_viewed TEXT, idle_outcome TEXT,
  time_busy INT)
"""
V2_MESSAGE_DDL = """
CREATE TABLE session_message (
  id TEXT, session_id TEXT, type TEXT, seq INT,
  time_created INT, time_updated INT, data TEXT)
"""
LEGACY_SESSION_DDL = """
CREATE TABLE session (
  id TEXT, model TEXT, agent TEXT, parent_id TEXT, cost REAL,
  tokens_input INT, tokens_output INT, tokens_cache_read INT,
  tokens_cache_write INT, summary_additions INT, summary_deletions INT,
  time_created INT, project_id TEXT)
"""
LEGACY_PART_DDL = """
CREATE TABLE part (session_id TEXT, data TEXT, time_created INT, time_updated INT)
"""


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        collect.CACHE_DIR = self._tmp.name
        collect.TOOLS_CACHE = os.path.join(self._tmp.name, "tools.json")
        collect.OPENCODE_STATS_CACHE = os.path.join(self._tmp.name, "stats.json")
        # Tests must never shell out to `opencode stats --json` (a live
        # server may make that wait up to 30 s). Most fixtures exercise the
        # overlay-off path anyway; the empty-DB test additionally proves the
        # overlay stays off with no stats source.
        self._stats_stub = mock.patch.object(
            collect, "_get_opencode_stats", return_value=None)
        self._stats_stub.start()

    def tearDown(self):
        self._stats_stub.stop()
        self._tmp.cleanup()

    # ---- fixture builders -------------------------------------------------
    def v2_db(self, sessions=(), messages=(), with_part=True):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.execute(V2_SESSION_DDL)
        c.execute(V2_MESSAGE_DDL)
        if with_part:
            c.execute(LEGACY_PART_DDL)  # real DBs still carry it, but empty
        for row in sessions:
            c.execute(
                "INSERT INTO session_v2 VALUES (%s)" % ",".join("?" * 36), row)
        for mid, sid, typ, data in messages:
            c.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (mid, sid, typ, 0, 0, 0, data))
        c.commit()
        c.close()
        return db

    def legacy_db(self, sessions=(), parts=()):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.execute(LEGACY_SESSION_DDL)
        c.execute(LEGACY_PART_DDL)
        for row in sessions:
            c.execute(
                "INSERT INTO session VALUES (%s)" % ",".join("?" * 13), row)
        for sid, data in parts:
            c.execute(
                "INSERT INTO part (session_id, data) VALUES (?,?)", (sid, data))
        c.commit()
        c.close()
        return db

    def v2_row(self, **kw):
        base = dict(
            id="s1", project_id="p1", workspace_id="w1", parent_id=None,
            fork_session_id=None, slug="slug", directory="/d", path="/p",
            title="t", version="1", share_url=None,
            summary_additions=5, summary_deletions=2, summary_files=1,
            summary_diffs=None, metadata=None, cost=1.0,
            tokens_input=100, tokens_output=50, tokens_reasoning=20,
            tokens_cache_read=10, tokens_cache_write=5,
            revert=0, permission=None, agent="build",
            model='{"id":"qwen-x","providerID":"prov"}',
            time_created=1793300000000, time_updated=1793300001000,
            time_compacting=None, time_archived=None, time_suspended=None,
            resume_attempts=0, time_idle=None, time_viewed=None,
            idle_outcome=None, time_busy=None)
        base.update(kw)
        return tuple(base[k] for k in (
            "id", "project_id", "workspace_id", "parent_id", "fork_session_id",
            "slug", "directory", "path", "title", "version", "share_url",
            "summary_additions", "summary_deletions", "summary_files",
            "summary_diffs", "metadata", "cost", "tokens_input",
            "tokens_output", "tokens_reasoning", "tokens_cache_read",
            "tokens_cache_write", "revert", "permission", "agent", "model",
            "time_created", "time_updated", "time_compacting", "time_archived",
            "time_suspended", "resume_attempts", "time_idle", "time_viewed",
            "idle_outcome", "time_busy"))

    def legacy_row(self, **kw):
        base = dict(
            id="l1", model='{"id":"m1","providerID":"prov"}', agent="docs",
            parent_id=None, cost=0.5, tokens_input=11, tokens_output=2,
            tokens_cache_read=3, tokens_cache_write=1,
            summary_additions=4, summary_deletions=1,
            time_created=1793300000000, project_id="proj")
        base.update(kw)
        return tuple(base[k] for k in (
            "id", "model", "agent", "parent_id", "cost", "tokens_input",
            "tokens_output", "tokens_cache_read", "tokens_cache_write",
            "summary_additions", "summary_deletions", "time_created",
            "project_id"))


class TestV2Path(CollectorTestCase):
    def test_v2_fixture_counts_tokens_including_reasoning(self):
        db = self.v2_db(sessions=[self.v2_row()])
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["tokens"], 185)  # 100+50+20+10+5
        self.assertEqual(r["allTime"]["sessions"], 1)
        self.assertEqual(r["allTime"]["cost"], 1.0)

    def test_v2_tools_read_message_content_name_field(self):
        data = json.dumps({"agent": "build", "content": [
            {"type": "tool", "name": "edit"},
            {"type": "tool", "name": "edit"},
            {"type": "tool", "name": "shell"},
            {"type": "text", "text": "done"},
        ]})
        db = self.v2_db(
            sessions=[self.v2_row()],
            messages=[("m1", "s1", "assistant", data)])
        r = collect.build_record(db_path=db)
        tools = {t["name"]: t["calls"] for t in r["tools"]}
        self.assertEqual(tools, {"edit": 2, "shell": 1})

    def test_empty_v2_with_legacy_history_falls_back_to_legacy(self):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.execute(V2_SESSION_DDL)
        c.execute(V2_MESSAGE_DDL)
        c.execute(LEGACY_SESSION_DDL)
        c.execute(LEGACY_PART_DDL)
        c.execute("INSERT INTO session VALUES (%s)" % ",".join("?" * 13),
                  self.legacy_row())
        c.commit()
        c.close()
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 1)
        self.assertEqual(r["allTime"]["tokens"], 17)
        self.assertEqual(r["agents"], [{"name": "docs", "sessions": 1}])

    def test_dispatch_uses_parent_agent_name(self):
        sessions = [
            self.v2_row(id="parent", agent="build"),
            self.v2_row(id="child1", agent="coder", parent_id="parent"),
            self.v2_row(id="child2", agent="tester", parent_id="parent"),
        ]
        db = self.v2_db(sessions=sessions)
        r = collect.build_record(db_path=db)
        disp = {(d["from"], d["to"]): d["count"] for d in r["dispatch"]}
        self.assertEqual(disp, {("build", "coder"): 1, ("build", "tester"): 1})

    def test_agent_split_top_level_vs_children(self):
        sessions = [
            self.v2_row(id="top", agent="build"),
            self.v2_row(id="sub", agent="coder", parent_id="top"),
        ]
        db = self.v2_db(sessions=sessions)
        r = collect.build_record(db_path=db)
        self.assertEqual([(a["name"], a["sessions"]) for a in r["agents"]],
                         [("build", 1)])
        self.assertEqual([(a["name"], a["sessions"]) for a in r["subagents"]],
                         [("coder", 1)])


class TestStatsOverlay(CollectorTestCase):
    """The opencode-stats overlay contract: non-empty local data gets the
    official numbers, an empty local table gets genuine zeros."""

    def setUp(self):
        super().setUp()
        self.oc = {
            "streak": 22, "activeDays": 30, "sessions": 618, "subagents": 259,
            "prompts": 4321, "steps": 8642, "cost": 14.94,
            "tokens": {"input": 10, "output": 20, "reasoning": 5,
                       "cache": {"read": 30, "write": 0}},
        }

    def _with_stats(self, sessions, stats=None, patch=True):
        db = self.v2_db(sessions=sessions)
        if not patch:
            return collect.build_record(db_path=db)
        return_value = self.oc if stats is None else stats
        with mock.patch.object(collect, "_get_opencode_stats",
                               return_value=return_value):
            return collect.build_record(db_path=db)

    def test_overlay_applies_when_local_table_has_sessions(self):
        s = self._with_stats([self.v2_row(id="x")])
        self.assertEqual(s["stats"]["sessions"], 618)
        self.assertEqual(s["stats"]["subagents"], 259)
        self.assertEqual(s["stats"]["bestStreak"], 22)
        self.assertEqual(s["stats"]["activeDays"], 30)
        # avgTokens: (10+20+5+30+0) / 30 active days = 65/30 -> 2
        self.assertEqual(s["stats"]["avgTokens"], 2)
        self.assertEqual(s["stats"]["oc_sessions"], 618)
        self.assertEqual(s["stats"]["oc_cost"], 14.94)

    def test_overlay_coerces_missing_tokens_keys(self):
        self.oc["tokens"] = {}  # cache totally absent
        s = self._with_stats([self.v2_row(id="x")])
        self.assertEqual(s["stats"]["avgTokens"], 0)  # 0 / 30 days

    def test_uses_local_values_when_stats_unavailable(self):
        # patch=False keeps the base setUp stub (stats source unavailable)
        s = self._with_stats([self.v2_row(id="x")], patch=False)
        self.assertEqual(s["stats"]["sessions"], 1)
        self.assertEqual(s["stats"]["activeDays"], 1)
        self.assertIsNone(s["stats"]["oc_sessions"])

    def test_empty_local_table_reports_genuine_zeros_even_with_stats(self):
        # Empty v2 table even WITH stats present -> use_overlay stays False
        db = self.v2_db()
        with mock.patch.object(collect, "_get_opencode_stats",
                               return_value=self.oc):
            r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 0)
        self.assertEqual(r["stats"]["activeDays"], 0)
        self.assertEqual(r["stats"]["sessions"], 0)
        self.assertIsNone(r["stats"]["oc_sessions"])


class TestLegacyPath(CollectorTestCase):
    def test_legacy_fixture(self):
        data = '{"type":"tool","tool":"edit","callID":"c1","state":{"input":{"filePath":"/a/b"}}}'
        db = self.legacy_db(sessions=[self.legacy_row()], parts=[("l1", data)])
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["tokens"], 17)
        self.assertEqual(r["tools"], [{"name": "edit", "calls": 1}])


class TestFailureHandling(CollectorTestCase):
    def test_missing_db_yields_zero_state(self):
        r = collect.build_record(db_path="/nonexistent/db.sqlite")
        self.assertEqual(r["allTime"]["sessions"], 0)
        self.assertEqual(r["stats"]["activeDays"], 0)
        self.assertEqual(len(r["hours"]), 24)

    def test_corrupt_db_yields_zero_state_not_crash(self):
        db = tempfile.mktemp(suffix=".db")
        with open(db, "w") as f:
            f.write("this is not a database")
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 0)
        self.assertEqual(r["stats"]["activeDays"], 0)  # genuine zeros, no overlay
        os.unlink(db)

    def test_empty_db_reports_genuine_zeros_no_overlay_leak(self):
        db = self.v2_db()
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 0)
        self.assertEqual(r["stats"]["activeDays"], 0)
        self.assertIsNone(r["stats"]["oc_sessions"])

    def test_missing_tables_yield_zero_state(self):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.commit()
        c.close()
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 0)

    def test_missing_part_table_degrades_to_empty_tools(self):
        db = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(db)
        c.execute(LEGACY_SESSION_DDL)
        c.execute("INSERT INTO session VALUES (%s)" % ",".join("?" * 13),
                  self.legacy_row())
        c.commit()
        c.close()
        r = collect.build_record(db_path=db)
        self.assertEqual(r["tools"], [])
        self.assertEqual(r["allTime"]["sessions"], 1)

    def test_null_and_partial_rows_do_not_crash(self):
        sessions = [
            self.v2_row(id="a"),
            self.v2_row(id="b", model=None, agent=None, parent_id="missing",
                        cost=None, tokens_input=None, tokens_output=None,
                        tokens_reasoning=None, tokens_cache_read=None,
                        tokens_cache_write=None, summary_additions=None,
                        summary_deletions=None, time_created=0),
            self.v2_row(id="c", model="not-json{{{", time_created=-5),
        ]
        db = self.v2_db(sessions=sessions)
        r = collect.build_record(db_path=db)
        self.assertEqual(r["allTime"]["sessions"], 3)
        forked = [m for m in r["models"] if m["name"] == "Forked"]
        self.assertEqual(forked[0]["sessions"], 2)  # NULL + malformed model
        # zero/negative timestamps bucket to "now", never to 1970
        for day in r["daily"]:
            self.assertTrue(day["date"].startswith("20"))


class TestAggregation(CollectorTestCase):
    def test_daily_buckets_and_streaks_match_dates(self):
        base = 1793200000000
        day_ms = 86400000
        sessions = [
            self.v2_row(id="d1", time_created=base),                    # day 0
            self.v2_row(id="d2", time_created=base + day_ms),           # day 1
            self.v2_row(id="d3", time_created=base + 2 * day_ms),       # day 2 (streak 3)
        ]
        db = self.v2_db(sessions=sessions)
        r = collect.build_record(db_path=db)
        self.assertEqual([d["sessions"] for d in r["daily"]], [1, 1, 1])
        self.assertGreaterEqual(r["stats"]["bestStreak"], 3)

    def test_hours_histogram_always_length_24(self):
        db = self.v2_db(sessions=[self.v2_row()])
        r = collect.build_record(db_path=db)
        self.assertEqual(len(r["hours"]), 24)
        self.assertEqual(sum(r["hours"]), 1)

    def test_provider_and_model_sums(self):
        sessions = [
            self.v2_row(id="a",
                        model='{"id":"m1","providerID":"prov"}'),
            self.v2_row(id="b",
                        model='{"id":"m2","providerID":"prov"}'),
        ]
        db = self.v2_db(sessions=sessions)
        r = collect.build_record(db_path=db)
        providers = {p["name"]: p["sessions"] for p in r["providers"]}
        self.assertEqual(providers, {"prov": 2})
        models = {m["id"]: m["sessions"] for m in r["models"]}
        self.assertEqual(set(models.values()), {1})


class TestOutputContract(CollectorTestCase):
    def test_main_emits_valid_json_even_on_corrupt_db(self):
        db = tempfile.mktemp(suffix=".db")
        with open(db, "w") as f:
            f.write("junk")
        out = StringIO()
        with mock.patch("collect.DB_PATH", db), \
             mock.patch("sys.argv", ["collect.py"]), \
             mock.patch("sys.stdout", out):
            collect.main()
        r = json.loads(out.getvalue())
        self.assertEqual(sorted(r.keys()), sorted(
            ["generatedAt", "allTime", "stats", "daily", "models", "agents",
             "subagents", "tools", "dispatch", "hours", "providers",
             "projects"]))
        os.unlink(db)

    def test_generated_at_is_utc_iso(self):
        db = self.v2_db(sessions=[self.v2_row()])
        r = collect.build_record(db_path=db)
        self.assertIn("+00:00", r["generatedAt"])
        os.unlink(db)


if __name__ == "__main__":
    unittest.main()
