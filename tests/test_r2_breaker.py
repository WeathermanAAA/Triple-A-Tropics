"""Guards for the R2 circuit-breaker Worker (workers/r2-breaker.js) and its
wiring (toml, D1 schema, deploy script, GH liveness workflow, fleet card).

Layers, like tests/test_roadmap_board.py:
  - TestBreakerWorker: tests/r2_breaker_harness.cjs under node imports the
    Worker as an ES module with a fake D1 + stubbed fetch + controlled clock
    and prints one JSON object per scenario; each test here pins one rule of
    PHASE0_SPEC (partial-bucket exclusion, rate/pace math, verdicts, the
    2-tick trip, alert vs armed, /reset as the only way back, fail-open on
    analytics errors, heartbeat gaps, /status shape, route prefixes).
  - TestBreakerWiring: pure-python guards on the config and the other
    components of this deliverable (schema copies in lockstep, toml
    bindings/vars, no secrets in the repo, workflow thresholds, fleet card).
  - TestFleetCardDom: renders the fleet page's breaker card under jsdom with
    a stubbed fetch (skips cleanly when jsdom is missing).
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import tomllib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS = pathlib.Path(__file__).resolve().parent / "r2_breaker_harness.cjs"
WORKER = REPO / "workers" / "r2-breaker.js"
TOML = REPO / "workers" / "r2-breaker.toml"
SQL = REPO / "workers" / "r2-breaker.sql"
DEPLOY = REPO / "workers" / "deploy-breaker.sh"
WORKFLOW = REPO / ".github" / "workflows" / "breaker-liveness.yml"
FLEET = REPO / "fleet" / "index.html"
README = REPO / "workers" / "README.md"
NODE = shutil.which("node")

STATUS_KEYS = [
    "v", "mode", "writes_enabled", "tripped_at", "trip_reason", "episode",
    "would_have_tripped_count", "last_would_trip_at", "last_tick", "last_ok_tick",
    "heartbeat_age_s", "tick_interval_s", "rate_1h", "pace_15m", "warn_hourly",
    "trip_hourly", "verdict", "consecutive_over", "consecutive_errors", "last_error",
    "gaps_detected", "worker_ts",
]


def _jsdom_available() -> bool:
    if NODE is None:
        return False
    try:
        r = subprocess.run([NODE, "-e", "require('jsdom')"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


@unittest.skipIf(NODE is None, "node not on PATH")
class TestBreakerWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([NODE, str(HARNESS)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"breaker harness failed:\n{proc.stdout}\n{proc.stderr}"
        assert "ALL CHECKS PASSED" in proc.stderr, proc.stderr
        cls.out = json.loads(proc.stdout)
        for name, res in cls.out.items():
            assert "error" not in res, f"scenario {name}: {res['error']}"

    def test_partial_bucket_exclusion(self):
        s = self.out["partial_bucket"]
        self.assertEqual(s["dropped_partial"], 1)
        self.assertEqual(s["rate_1h"], 12000)     # poisoned partial bucket ignored
        self.assertEqual(s["pace_15m"], 12000)
        self.assertEqual(s["tick_rate"], 12000)
        self.assertEqual(s["boundary_rate"], 777777 + 11000)   # complete exactly at +5 min
        self.assertEqual(s["class_a"], 9)

    def test_rate_and_pace_math(self):
        s = self.out["rate_math"]
        self.assertEqual(s["rate_1h"], sum(i * 100 for i in range(1, 13)))
        self.assertEqual(s["pace_15m"], 4 * 600)
        self.assertEqual(s["few_rate"], 1000)      # missing buckets count as 0
        self.assertEqual(s["few_pace"], 2400)

    def test_verdicts(self):
        s = self.out["verdicts"]
        self.assertEqual(sorted(s["verdicts"]), ["ok", "over", "over", "warn"])
        self.assertEqual(s["issues_after_warn"], 0, "warn is ticks-only, no issue")
        self.assertEqual(s["over_consecutive"], 2)
        self.assertEqual(s["issues_total"], 1, "the 2nd consecutive over tick opens exactly one issue")

    def test_two_tick_trip_alert_mode(self):
        s = self.out["alert_episode"]
        self.assertEqual(s["writes_enabled"], True, "alert mode never sets writes_enabled=false")
        self.assertEqual(s["would_have_tripped_count"], 2)
        self.assertEqual(len(s["issues"]), 2, "exactly one issue per episode")
        for t in s["issues"]:
            self.assertTrue(t.startswith("[r2-breaker] WOULD HAVE TRIPPED: 180,000/h (pace 180,000/h) at 2026-08-25T"), t)
        self.assertEqual(s["comments"], 1)
        self.assertEqual(s["event_kinds"], ["would_trip", "episode_close", "would_trip", "episode_close"])

    def test_armed_mode_trips_and_stays_tripped(self):
        s = self.out["armed_trip"]
        self.assertTrue(s["trip_title"].startswith("[r2-breaker] TRIPPED — R2 writes STOPPED: 180,000/h"), s["trip_title"])
        self.assertEqual(s["tripped_at"], "2026-08-25T12:08:30.000Z")
        self.assertEqual(s["stayed_false_ticks"], 10)
        self.assertEqual(s["reset_status_no_key"], 403)
        self.assertEqual(s["reset_status_bad_key"], 403)
        self.assertEqual(s["reset_status"], 200)
        self.assertEqual(s["after_reset"], True)
        self.assertEqual(s["unset_admin_key"], 503)
        # reset mid-storm: the trailing hour is ignored, the storm re-trips on post-reset data
        self.assertEqual(s["held_after_reset"], [10, 15, 20])
        self.assertEqual(s["retrip_at_min"], 25)
        self.assertEqual(s["event_kinds"], ["arm", "trip", "episode_close", "disarm", "reset"])

    def test_manual_trip(self):
        s = self.out["manual_trip"]
        self.assertEqual(s["reason"], "manual")
        self.assertEqual(s["after_trip"], False)
        self.assertEqual(s["after_reset"], True)
        self.assertEqual(s["comments"], 1)

    def test_graphql_failure_fails_open(self):
        s = self.out["analytics_errors"]
        self.assertEqual(len(s["errors"]), 5)
        self.assertTrue(any("http 500" in e for e in s["errors"]), s["errors"])
        self.assertTrue(any("network" in e for e in s["errors"]), s["errors"])
        self.assertTrue(any("errors:" in e for e in s["errors"]), s["errors"])
        self.assertTrue(any("missing data" in e for e in s["errors"]), s["errors"])
        self.assertTrue(any("no complete buckets" in e for e in s["errors"]), s["errors"])
        self.assertEqual(s["issue_at"], 3)
        self.assertEqual(s["issues"], 1)
        self.assertEqual(s["recovered_comment"], 1)
        self.assertEqual(s["consecutive_over_after"], 2, "errors never reset consecutive_over")
        self.assertTrue(s["episode_after"])

    def test_empty_analytics_result_is_a_read_failure(self):
        """A 200 with zero rows must not read as a healthy 0/h (success-on-empty)."""
        s = self.out["empty_result"]
        self.assertTrue(s["streak_kept"])
        self.assertTrue(s["trip_after_recovery"], "over, empty, over reaches consecutive_over=2 and trips")
        self.assertTrue(s["alert_episode_opened"], "alternating empty reads never mask a storm")

    def test_arm_during_active_episode(self):
        s = self.out["arm_mid_episode"]
        self.assertTrue(s["immediate_trip"], "fresh sustained over: /arm stops writes at once")
        self.assertIn("TRIPPED NOW", s["arm_detail"])
        self.assertTrue(s["late_trip"], "stale reading: the next sustained over tick trips")
        self.assertIn("remain ENABLED", s["stale_detail"])
        self.assertIn("POST /trip", s["stale_detail"])
        self.assertTrue(s["calm_then_trip"])
        self.assertTrue(s["alert_untouched"], "alert mode never stops writes")
        self.assertEqual((s["issues_a"], s["issues_b"]), (1, 1), "one issue per episode holds")

    def test_reset_after_trip_holds(self):
        s = self.out["reset_after_trip"]
        self.assertFalse(s["retripped"])
        self.assertEqual(s["trip_issues"], 1, "no second TRIPPED issue after a reset")
        self.assertGreaterEqual(s["unfloored_rate_t35"], 150000, "the trailing hour alone would re-trip")
        self.assertEqual(s["trail"][0][1:], [0, 0, "ok"])
        self.assertTrue(all(row[3] == "ok" for row in s["trail"]), s["trail"])

    def test_boundary_tick_counts_just_closed_bucket(self):
        s = self.out["boundary_tick"]
        self.assertEqual(s["newest_at_boundary"], s["newest_at_offset"])
        self.assertEqual(s["dropped_partial"], 1)
        self.assertEqual(s["cron_minutes"], [2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57])

    def test_heartbeat_gap_detection(self):
        s = self.out["heartbeat_gap"]
        self.assertEqual(s["gaps_detected"], 2)
        self.assertEqual(s["titles"][0], "[r2-breaker] heartbeat gap: 21 min with no tick")
        self.assertTrue(s["titles"][1].startswith("[r2-breaker] heartbeat gap: 149 min"), s["titles"])

    def test_status_shape(self):
        s = self.out["status_shape"]
        self.assertEqual(s["keys"], STATUS_KEYS)
        self.assertEqual(s["heartbeat_age_s"], 420)
        fresh = s["fresh"]
        self.assertEqual(fresh["v"], 1)
        self.assertEqual(fresh["tick_interval_s"], 300)
        self.assertEqual((fresh["warn_hourly"], fresh["trip_hourly"]), (80000, 150000))
        self.assertEqual(fresh["episode"], {"active": False, "started_at": None,
                                            "peak_rate_1h": 0, "issue_url": None})

    def test_route_prefix_handling(self):
        c = self.out["routes"]["codes"]
        self.assertEqual(c["GET /status"], 200)
        self.assertEqual(c["GET /r2-breaker/status"], 200)
        self.assertEqual(c["GET /r2-breaker/status/"], 200)
        self.assertEqual(c["POST /status"], 405)
        self.assertEqual(c["GET /arm"], 405)
        self.assertEqual(c["POST /r2-breaker/arm"], 403)
        self.assertEqual(c["GET /nope"], 404)
        self.assertEqual(c["GET /r2-breaker/nope"], 404)
        self.assertEqual(c["OPTIONS /status"], 204)
        self.assertEqual(self.out["routes"]["ticks_n"], 4)
        self.assertEqual(self.out["routes"]["events_kind"], "arm")

    def test_persistence_and_alert_resilience(self):
        s = self.out["persistence"]
        self.assertGreaterEqual(s["ddl"], 4)
        self.assertNotIn("mode", s["steady_keys"])
        self.assertNotIn("writes_enabled", s["steady_keys"])
        self.assertEqual(s["no_token_calls"], 0)


class TestBreakerWiring(unittest.TestCase):
    """Config + companions stay coherent with the Worker and the spec."""

    def test_worker_is_a_plain_es_module(self):
        js = WORKER.read_text()
        self.assertNotRegex(js, r"^\s*import\s", "no imports: plain ES module, no bundler, no npm deps")
        self.assertNotIn("require(", js)
        self.assertIn("export default {", js)
        self.assertIn("async fetch(request, env)", js)
        self.assertIn("async scheduled(event, env, ctx)", js)

    def test_cron_is_offset_two_minutes_past_each_boundary(self):
        """Not */5: a bucket read on its own boundary is ~30% under-ingested."""
        t = tomllib.loads(TOML.read_text())
        crons = t["triggers"]["crons"]
        self.assertEqual(len(crons), 1)
        fields = crons[0].split()
        self.assertEqual(fields[1:], ["*", "*", "*", "*"])
        mins = [int(m) for m in fields[0].split(",")]
        self.assertEqual(mins, [2 + 5 * i for i in range(12)], crons)
        self.assertNotIn("*/5", crons[0])
        self.assertIn("2 min", TOML.read_text())     # the reason is recorded next to it

    @unittest.skipIf(NODE is None, "node not on PATH")
    def test_sql_file_matches_worker_schema(self):
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e",
             f"import('file://{WORKER}').then(m => console.log(JSON.stringify(m.SCHEMA)))"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        js_schema = json.loads(proc.stdout)
        body = "\n".join(l for l in SQL.read_text().splitlines() if not l.strip().startswith("--"))
        file_schema = [s.strip() for s in body.split(";") if s.strip()]
        self.assertEqual(file_schema, js_schema, "r2-breaker.sql and SCHEMA in r2-breaker.js drifted")
        for stmt in file_schema:
            self.assertIn("IF NOT EXISTS", stmt, "schema must stay idempotent")

    def test_toml_bindings_and_vars(self):
        t = TOML.read_text()
        self.assertIn('name = "r2-breaker"', t)
        self.assertIn('main = "r2-breaker.js"', t)
        self.assertIn('compatibility_date = "2026-06-01"', t)
        self.assertIn("workers_dev = true", t)
        self.assertIn('pattern = "triple-a-tropics.com/r2-breaker/*", zone_name = "triple-a-tropics.com"', t)
        self.assertIn("[triggers]", t)
        self.assertIn("[[d1_databases]]", t)
        self.assertIn('binding = "DB"', t)
        self.assertIn('database_name = "tat-breaker"', t)
        self.assertIn('database_id = "b071cd4b-ac24-4692-ab14-977ba051b99d"', t)
        for var in ('ACCOUNT_TAG = "33bb26c164250e1893f2ca61d293d44d"', 'BUCKET = "triple-a-tropics-media"',
                    'REPO = "WeathermanAAA/Triple-A-Tropics"', 'TRIP_HOURLY = "150000"',
                    'WARN_HOURLY = "80000"', 'DEFAULT_MODE = "alert"'):
            self.assertIn(var, t)
        # secrets never in the config
        for secret in ("CF_GRAPHQL_TOKEN =", "GITHUB_TOKEN =", "ADMIN_KEY ="):
            self.assertNotIn(secret, t)

    def test_deploy_script_wires_everything(self):
        d = DEPLOY.read_text()
        self.assertTrue(d.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", d)
        self.assertIn("CLOUDFLARE_API_TOKEN:?", d)
        self.assertIn("GH_PUSH_TOKEN:?", d)
        self.assertIn('D1_NAME="tat-breaker"', d)
        self.assertIn('wrangler d1 execute "$D1_NAME" --remote --file r2-breaker.sql -c r2-breaker.toml', d)
        self.assertIn("/d1/database/", d)     # REST fallback for the schema
        self.assertIn("wrangler deploy -c r2-breaker.toml", d)
        for secret in ("CF_GRAPHQL_TOKEN", "GITHUB_TOKEN", "ADMIN_KEY"):
            self.assertIn(f"wrangler secret put {secret} -c r2-breaker.toml", d)
        self.assertIn("BREAKER_ADMIN_KEY", d)
        self.assertIn("openssl rand -hex 16", d)
        self.assertIn("Analytics:Read", d)
        self.assertIn("seq 1 12", d)
        self.assertIn("/status", d)
        # the admin key banner precedes the smoke so a smoke failure cannot lose it
        self.assertLess(d.index("BREAKER ADMIN KEY:"), d.index('echo "== smoke:'))
        code = "\n".join(l for l in d.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("python3 -c", code, "inline python with bash quoting: use a quoted heredoc")

    def test_deploy_python_blocks_parse_and_smoke_runs(self):
        """Every quoted heredoc is valid Python; the smoke block runs on a sample document."""
        d = DEPLOY.read_text()
        blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", d, re.S)
        self.assertGreaterEqual(len(blocks), 2, "schema REST fallback + smoke")
        for b in blocks:
            ast.parse(b)
        smoke = [b for b in blocks if "STATUS_JSON" in b]
        self.assertEqual(len(smoke), 1)
        good = json.dumps({"v": 1, "mode": "alert", "writes_enabled": True, "last_tick": None,
                           "warn_hourly": 80000, "trip_hourly": 150000})
        r = subprocess.run(["python3", "-"], input=smoke[0], capture_output=True, text=True,
                           env={**os.environ, "STATUS_JSON": good}, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("v=1 mode=alert writes_enabled=True", r.stdout)
        r = subprocess.run(["python3", "-"], input=smoke[0], capture_output=True, text=True,
                           env={**os.environ, "STATUS_JSON": json.dumps({"v": 2})}, timeout=30)
        self.assertNotEqual(r.returncode, 0, "a non-v1 document fails the smoke")

    def test_liveness_workflow(self):
        w = WORKFLOW.read_text()
        self.assertIn("name: breaker-liveness", w)
        self.assertIn('cron: "11,41 * * * *"', w)
        self.assertIn("workflow_dispatch:", w)
        self.assertIn("contents: read", w)
        self.assertIn("group: breaker-liveness", w)
        self.assertIn("timeout-minutes: 5", w)
        self.assertIn("https://triple-a-tropics.com/r2-breaker/status", w)
        self.assertIn("for i in 1 2 3; do", w)
        self.assertIn("age > 1200", w)
        self.assertIn(">= 3", w)
        self.assertIn('d.get("v") != 1', w)
        self.assertIn("EXTERNAL leg", w)
        self.assertIn("load-shed", w)
        self.assertNotIn("secrets.", w, "read-only probe: no secrets")

    def test_fleet_card_wiring(self):
        html = FLEET.read_text()
        self.assertIn('<div id="fl-breaker" class="fl-brk"></div>', html)
        self.assertLess(html.index('id="fl-breaker"'), html.index('id="fl-grid"'), "card sits above the box grid")
        self.assertIn("https://triple-a-tropics.com/r2-breaker/status", html)
        self.assertIn("{ cache: 'no-store' }", html)
        self.assertIn("breaker unreachable", html)
        self.assertIn("var WARN_S = 600, DEAD_S = 1200;", html)
        self.assertIn("'breaker silent '", html)
        for label in ("ALERT-ONLY", "ARMED", "ENABLED", "STOPPED", "would have tripped", "pace, last 15 min"):
            self.assertIn(label, html)
        # the original page block is untouched: its markers and thresholds survive
        self.assertIn("var WARN_S = 180, DEAD_S = 600;", html)
        self.assertIn("loadRoster().then(load);", html)
        # house style in the NEW block: no em-dashes, no pulse rings
        block = html[html.index("R2 circuit breaker card"):]
        self.assertNotIn("—", block)
        self.assertNotIn("pulse", block)
        # request budget: poll at the tick cadence, never from a hidden tab
        self.assertIn("var POLL_MS = 300000;", block)
        self.assertIn("setInterval(poll, POLL_MS);", block)
        self.assertIn("if (document.hidden) return;", block)
        self.assertIn("document.addEventListener('visibilitychange', poll);", block)
        self.assertNotIn("60000", block)

    def test_readme_section(self):
        md = README.read_text()
        self.assertIn("## r2-breaker.js", md)
        self.assertIn("deploy-breaker.sh", md)
        self.assertIn("tests.test_r2_breaker", md)


@unittest.skipIf(NODE is None, "node not on PATH")
@unittest.skipUnless(_jsdom_available(), "jsdom not installed (npm install --no-save jsdom)")
class TestFleetCardDom(unittest.TestCase):
    """The breaker card renders each state from a stubbed /status fetch."""

    SCRIPT = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');   // node -e: args start at argv[1]
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const card = blocks.find(b => b.indexOf('R2 circuit breaker card') >= 0);
if (!card) throw new Error('breaker script block missing');
const base = { v: 1, mode: 'alert', writes_enabled: true, tripped_at: null, trip_reason: null,
  episode: { active: false, started_at: null, peak_rate_1h: 0, issue_url: null },
  would_have_tripped_count: 0, last_would_trip_at: null, last_tick: '2026-08-25T12:00:00.000Z',
  last_ok_tick: '2026-08-25T12:00:00.000Z', heartbeat_age_s: 120, tick_interval_s: 300,
  rate_1h: 29000, pace_15m: 31000, warn_hourly: 80000, trip_hourly: 150000, verdict: 'ok',
  consecutive_over: 0, consecutive_errors: 0, last_error: null, gaps_detected: 0,
  worker_ts: '2026-08-25T12:02:00.000Z' };
const cases = {
  ok: base,
  armed_stopped: Object.assign({}, base, { mode: 'armed', writes_enabled: false, trip_reason: 'manual',
    tripped_at: '2026-08-25T11:00:00.000Z', would_have_tripped_count: 2, rate_1h: 160000, verdict: 'over',
    episode: { active: true, started_at: '2026-08-25T11:00:00.000Z', peak_rate_1h: 200000,
      issue_url: 'https://github.com/WeathermanAAA/Triple-A-Tropics/issues/1' } }),
  silent: Object.assign({}, base, { heartbeat_age_s: 1500 }),
  stale: Object.assign({}, base, { heartbeat_age_s: 700 }),
  error: Object.assign({}, base, { verdict: 'error', consecutive_errors: 3, last_error: 'graphql http 500 <b>' }),
  unreachable: null,
  wrong_version: { v: 2 },
};
async function run(name, doc) {
  const dom = new JSDOM('<div id="fl-breaker" class="fl-brk"></div>', { runScripts: 'outside-only' });
  dom.window.fetch = () => doc === null
    ? Promise.reject(new Error('fetch failed'))
    : Promise.resolve({ ok: true, json: () => Promise.resolve(doc) });
  dom.window.setInterval = () => 0;
  dom.window.eval(card);
  await new Promise(r => setTimeout(r, 20));
  const box = dom.window.document.querySelector('#fl-breaker .fl-box');
  return { cls: box.className, label: box.querySelector('.fl-state').textContent,
    text: box.textContent.replace(/\s+/g, ' ') };
}
(async () => {
  const out = {};
  for (const [n, d] of Object.entries(cases)) out[n] = await run(n, d);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""

    def test_card_states(self):
        proc = subprocess.run([NODE, "-e", self.SCRIPT, str(FLEET)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["ok"]["cls"], "fl-box ok")
        self.assertEqual(out["ok"]["label"], "live")
        self.assertIn("ALERT-ONLY", out["ok"]["text"])
        self.assertIn("ENABLED", out["ok"]["text"])
        self.assertIn("29,000", out["ok"]["text"])
        self.assertEqual(out["armed_stopped"]["cls"], "fl-box dead")
        self.assertEqual(out["armed_stopped"]["label"], "writes stopped")
        self.assertIn("ARMED", out["armed_stopped"]["text"])
        self.assertIn("STOPPED", out["armed_stopped"]["text"])
        self.assertIn("Episode active", out["armed_stopped"]["text"])
        self.assertEqual(out["silent"]["cls"], "fl-box dead")
        self.assertTrue(out["silent"]["label"].startswith("breaker silent"), out["silent"]["label"])
        self.assertEqual(out["stale"]["cls"], "fl-box warn")
        self.assertEqual(out["error"]["cls"], "fl-box warn")
        self.assertEqual(out["error"]["label"], "analytics error")
        self.assertIn("graphql http 500 &lt;b&gt;", out["error"]["text"].replace("<b>", "&lt;b&gt;"))
        self.assertEqual(out["unreachable"]["cls"], "fl-box dead")
        self.assertEqual(out["unreachable"]["label"], "unreachable")
        self.assertIn("breaker unreachable", out["unreachable"]["text"])
        self.assertEqual(out["wrong_version"]["cls"], "fl-box dead", "a non-v1 document reads as a fault")


if __name__ == "__main__":
    unittest.main()
