// Node harness for the R2 circuit-breaker Worker (workers/r2-breaker.js).
//
//   node tests/r2_breaker_harness.cjs        # run every scenario, print JSON
//
// Imports the Worker as an ES module (dynamic import), stubs globalThis.fetch
// (GraphQL analytics + GitHub) and binds a fake D1 (prepare().bind().run()/
// first()/all() + batch()). Time is controlled by overriding Date.now, so
// the partial-bucket rule, the 20-min self-gap and heartbeat_age_s are all
// deterministic. Driven by tests/test_r2_breaker.py; also asserts on its own
// so a bare `node` run fails loudly.
//
// Output: one JSON object {"<scenario>": {...}} on stdout; exit 1 on any
// assertion failure (the failing scenario is named on stderr).
"use strict";

const assert = require("assert");
const path = require("path");
const { pathToFileURL } = require("url");

const ROOT = path.resolve(__dirname, "..");

// The Worker logs with console.log (Workers runtime); under node that is
// stdout, which must stay pure JSON for the python driver. Capture instead.
const LOGS = [];
console.log = (...a) => { LOGS.push(a.map(String).join(" ")); };
const WORKER = path.join(ROOT, "workers", "r2-breaker.js");

const MIN = 60 * 1000;
const T0 = Date.parse("2026-08-25T12:03:30Z");   // NOT on a 5-min boundary
const ADMIN = "test-admin-key-0123456789abcdef";
const ISSUES = "https://github.com/WeathermanAAA/Triple-A-Tropics/issues/";
const CRON = "2,7,12,17,22,27,32,37,42,47,52,57 * * * *";   // r2-breaker.toml (2 min past each boundary)
const isoAt = (ms) => new Date(ms).toISOString();

// ------------------------------------------------------------- fake D1 ---

class Stmt {
  constructor(db, sql, args) { this.db = db; this.sql = sql; this.args = args || []; }
  bind(...args) { return new Stmt(this.db, this.sql, args); }
  async run() { const r = this.db._exec(this.sql, this.args); return { success: true, meta: r.meta || {} }; }
  async all() { const r = this.db._exec(this.sql, this.args); return { success: true, results: r.rows || [], meta: r.meta || {} }; }
  async first() { const r = this.db._exec(this.sql, this.args); return (r.rows && r.rows[0]) || null; }
}

class FakeD1 {
  constructor(opts = {}) {
    this.state = new Map();
    this.ticks = new Map();
    this.events = [];
    this.nextEventId = 1;
    this.log = [];
    this.tablesMissing = !!opts.tablesMissing;
    this.ddl = 0;
  }
  prepare(sql) { return new Stmt(this, sql, []); }
  async batch(stmts) { return Promise.all(stmts.map((s) => s.run())); }
  _exec(sql, args) {
    this.log.push({ sql, args });
    if (/^CREATE (TABLE|INDEX) IF NOT EXISTS/.test(sql)) { this.ddl++; this.tablesMissing = false; return {}; }
    if (this.tablesMissing) throw new Error("D1_ERROR: no such table: state");
    if (sql === "SELECT k, v FROM state") {
      return { rows: [...this.state.entries()].map(([k, v]) => ({ k, v })) };
    }
    if (sql.startsWith("INSERT INTO state (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE")) {
      this.state.set(args[0], args[1]); return {};
    }
    if (sql.startsWith("INSERT OR REPLACE INTO ticks")) {
      const [ts, rate_1h, pace_15m, verdict, mode, writes_enabled, error, latency_ms] = args;
      this.ticks.set(ts, { ts, rate_1h, pace_15m, verdict, mode, writes_enabled, error, latency_ms });
      return {};
    }
    if (sql === "DELETE FROM ticks WHERE ts < ?") {
      for (const ts of [...this.ticks.keys()]) if (ts < args[0]) this.ticks.delete(ts);
      return {};
    }
    if (sql.startsWith("INSERT INTO events (ts, kind, detail, issue_url)")) {
      const [ts, kind, detail, issue_url] = args;
      this.events.push({ id: this.nextEventId++, ts, kind, detail, issue_url });
      return {};
    }
    if (/^SELECT .* FROM ticks ORDER BY ts DESC LIMIT \?$/.test(sql)) {
      const rows = [...this.ticks.values()].sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, args[0]);
      return { rows };
    }
    if (/^SELECT .* FROM events ORDER BY id DESC LIMIT \?$/.test(sql)) {
      return { rows: this.events.slice().reverse().slice(0, args[0]) };
    }
    throw new Error(`fake D1: unhandled SQL: ${sql}`);
  }
  stateObj() {
    const o = {};
    for (const [k, v] of this.state.entries()) o[k] = JSON.parse(v);
    return o;
  }
}

// ---------------------------------------------------------- fetch stub ---

// world.gql: {rows} | {status} | {throw} | {errors} | {nodata}
// world.gh.calls: every GitHub call {method, path, body}
function installFetch(world) {
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    const method = (init.method || "GET").toUpperCase();
    if (u.startsWith("https://api.cloudflare.com/client/v4/graphql")) {
      world.gqlCalls.push(JSON.parse(init.body));
      const g = world.gql;
      if (g.throw) throw new TypeError("fetch failed: network");
      if (g.status) return new Response("upstream error", { status: g.status });
      if (g.errors) return Response.json({ data: null, errors: [{ message: "bad token" }] });
      if (g.nodata) return Response.json({ data: { viewer: { accounts: [] } } });
      return Response.json({ data: { viewer: { accounts: [{ r2OperationsAdaptiveGroups: g.rows || [] }] } } });
    }
    if (u.startsWith("https://api.github.com/")) {
      const p = u.slice("https://api.github.com".length);
      world.gh.calls.push({ method, path: p, body: init.body ? JSON.parse(init.body) : null,
        auth: (init.headers || {}).authorization, ua: (init.headers || {})["user-agent"] });
      if (/\/labels$/.test(p)) return Response.json({ name: "breaker" }, { status: 201 });
      if (/\/issues$/.test(p) && method === "POST") {
        const n = world.gh.next++;
        return Response.json({ number: n, html_url: ISSUES + n }, { status: 201 });
      }
      if (/\/issues\/\d+\/comments$/.test(p) && method === "POST") {
        return Response.json({ id: 1 }, { status: 201 });
      }
      return new Response("nope", { status: 404 });
    }
    throw new Error(`fetch stub: unexpected URL ${u}`);
  };
}

function newWorld(overrides = {}) {
  const world = {
    now: T0,
    gql: { rows: [] },
    gqlCalls: [],
    gh: { calls: [], next: 100 },
    db: new FakeD1(overrides.db || {}),
  };
  world.env = {
    DB: world.db,
    ACCOUNT_TAG: "acct-test",
    BUCKET: "triple-a-tropics-media",
    REPO: "WeathermanAAA/Triple-A-Tropics",
    TRIP_HOURLY: "150000",
    WARN_HOURLY: "80000",
    DEFAULT_MODE: "alert",
    CF_GRAPHQL_TOKEN: "gql-token",
    GITHUB_TOKEN: "gh-token",
    ADMIN_KEY: ADMIN,
    ...(overrides.env || {}),
  };
  installFetch(world);
  Date.now = () => world.now;
  return world;
}

function issuesOpened(world) { return world.gh.calls.filter((c) => /\/issues$/.test(c.path) && c.method === "POST"); }
function commentsPosted(world) { return world.gh.calls.filter((c) => /\/comments$/.test(c.path)); }

// Aligned 5-min buckets covering the last 120 min INCLUDING the partial
// newest one (its start is floor(now / 5 min), which is < 5 min before
// now). perBucket(startMs, indexFromNewest) -> requests.
function buckets(nowMs, perBucket) {
  const newestStart = Math.floor(nowMs / (5 * MIN)) * 5 * MIN;
  const rows = [];
  for (let i = 0; i < 24; i++) {
    const start = newestStart - i * 5 * MIN;
    rows.push({ sum: { requests: perBucket(start, i) }, dimensions: { datetimeFiveMinutes: new Date(start).toISOString() } });
  }
  return rows.reverse();   // ASC, like the real orderBy
}

const flat = (n) => (start, i) => (i === 0 ? 9999999 : n);   // partial bucket poisoned

async function req(mod, world, method, p, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const r = await mod.default.fetch(new Request("https://example.test" + p, { method, headers }), world.env);
  let body = null;
  const text = await r.text();
  try { body = JSON.parse(text); } catch { body = text; }
  return { status: r.status, headers: r.headers, body };
}

async function tickAt(mod, world, minutesFromT0, rowsOrGql) {
  world.now = T0 + minutesFromT0 * MIN;
  if (rowsOrGql !== undefined) world.gql = Array.isArray(rowsOrGql) ? { rows: rowsOrGql } : rowsOrGql;
  await mod.default.scheduled({ scheduledTime: world.now, cron: CRON }, world.env,
    { waitUntil() {} });
  return world.db.stateObj();
}

// ----------------------------------------------------------- scenarios ---

const out = {};
const scenarios = {};

scenarios.partial_bucket = async (mod) => {
  const nowMs = T0;
  const rows = buckets(nowMs, (start, i) => (i === 0 ? 777777 : 1000));
  const s = mod.summarizeBuckets(rows, nowMs);
  // 24 rows, 1 partial dropped, 12 newest complete summed
  assert.strictEqual(s.dropped_partial, 1);
  assert.strictEqual(s.buckets, 12);
  assert.strictEqual(s.rate_1h, 12000);
  assert.strictEqual(s.pace_15m, 12000);
  // exactly at the boundary (start + 5 min == now) the bucket is complete
  const boundary = Math.floor(nowMs / (5 * MIN)) * 5 * MIN + 5 * MIN;
  const s2 = mod.summarizeBuckets(rows, boundary);
  assert.strictEqual(s2.dropped_partial, 0);
  assert.strictEqual(s2.rate_1h, 777777 + 11 * 1000);
  // via a real tick as well
  const world = newWorld();
  const st = await tickAt(mod, world, 0, rows);
  assert.strictEqual(st.rate_1h, 12000);
  assert.strictEqual(st.pace_15m, 12000);
  assert.strictEqual(st.verdict, "ok");
  // the query window is [now-120min, now)
  const q = world.gqlCalls[0].query;
  assert.ok(q.includes(`datetime_geq:"${new Date(nowMs - 120 * MIN).toISOString()}"`), q);
  assert.ok(q.includes(`datetime_lt:"${new Date(nowMs).toISOString()}"`), q);
  assert.ok(q.includes('actionStatus:"success"'), q);
  for (const t of mod.CLASS_A) assert.ok(q.includes(`"${t}"`), t);
  assert.ok(q.includes("datetimeFiveMinutes_ASC"), q);
  return { dropped_partial: s.dropped_partial, rate_1h: s.rate_1h, pace_15m: s.pace_15m,
    boundary_rate: s2.rate_1h, tick_rate: st.rate_1h, class_a: mod.CLASS_A.length };
};

scenarios.rate_math = async (mod) => {
  const nowMs = T0;
  // distinct values: complete bucket i (1..23) carries i*100
  const rows = buckets(nowMs, (start, i) => (i === 0 ? 5 : i * 100));
  const s = mod.summarizeBuckets(rows, nowMs);
  const expectRate = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].reduce((a, i) => a + i * 100, 0);
  assert.strictEqual(s.rate_1h, expectRate);
  assert.strictEqual(s.pace_15m, 4 * (100 + 200 + 300));
  // fewer than 12 complete buckets -> missing count as 0
  const few = rows.slice(-5);   // partial + 4 complete (400,300,200,100)
  const sf = mod.summarizeBuckets(few, nowMs);
  assert.strictEqual(sf.buckets, 4);
  assert.strictEqual(sf.rate_1h, 1000);
  assert.strictEqual(sf.pace_15m, 4 * 600);
  // duplicate rows for one bucket merge; garbage rows ignored
  const dup = mod.summarizeBuckets(rows.concat(rows[0], { sum: null, dimensions: {} }), nowMs);
  assert.strictEqual(dup.rate_1h, expectRate + 0);   // rows[0] is the oldest (i=23), outside the 12
  const empty = mod.summarizeBuckets([], nowMs);
  assert.deepStrictEqual([empty.rate_1h, empty.pace_15m, empty.buckets, empty.complete], [0, 0, 0, 0]);
  assert.strictEqual(s.complete, 23, "raw complete count is reported before any floor");
  // reset floor: buckets that start before sinceMs are dropped (missing = 0)
  const floorAt = Math.floor(nowMs / (5 * MIN)) * 5 * MIN - 2 * 5 * MIN + 1;   // just after bucket i=2 starts
  const floored = mod.summarizeBuckets(rows, nowMs, floorAt);
  assert.strictEqual(floored.buckets, 1, "only bucket i=1 starts at/after the floor and is complete");
  assert.strictEqual(floored.rate_1h, 100);
  assert.strictEqual(floored.pace_15m, 400);
  assert.strictEqual(floored.complete, 23);
  assert.strictEqual(floored.dropped_before_reset, 22);
  return { rate_1h: s.rate_1h, pace_15m: s.pace_15m, few_rate: sf.rate_1h, few_pace: sf.pace_15m };
};

scenarios.verdicts = async (mod) => {
  const th = mod.thresholds({ WARN_HOURLY: "80000", TRIP_HOURLY: "150000" });
  assert.deepStrictEqual(th, { warn: 80000, trip: 150000 });
  assert.deepStrictEqual(mod.thresholds({}), { warn: 80000, trip: 150000 });
  const v = (r, p) => mod.verdictFor(r, p, th);
  assert.strictEqual(v(29000, 30000), "ok");
  assert.strictEqual(v(79999, 79999), "ok");
  assert.strictEqual(v(80000, 80000), "warn");
  assert.strictEqual(v(149999, 100000), "warn");
  assert.strictEqual(v(150000, 100000), "over");
  assert.strictEqual(v(100000, 299999), "warn");
  assert.strictEqual(v(100000, 300000), "over");   // fast path: pace >= 2x trip
  // via ticks: each verdict shows up in state + tick row
  const world = newWorld();
  const okS = await tickAt(mod, world, 0, buckets(T0, flat(2500)));
  assert.strictEqual(okS.verdict, "ok");
  const warnS = await tickAt(mod, world, 5, buckets(T0 + 5 * MIN, flat(7000)));
  assert.strictEqual(warnS.verdict, "warn");
  assert.strictEqual(warnS.rate_1h, 84000);
  const issuesAfterWarn = issuesOpened(world).length;
  assert.strictEqual(issuesAfterWarn, 0, "warn is recorded in ticks only");
  const overS = await tickAt(mod, world, 10, buckets(T0 + 10 * MIN, flat(13000)));
  assert.strictEqual(overS.verdict, "over");
  assert.strictEqual(overS.consecutive_over, 1);
  // pace fast path: the 3 newest complete buckets carry 26,000 each
  const fast = buckets(T0 + 15 * MIN, (start, i) => (i === 0 ? 1 : i <= 3 ? 26000 : 1000));
  const fastS = await tickAt(mod, world, 15, fast);
  assert.strictEqual(fastS.pace_15m, 312000);
  assert.ok(fastS.rate_1h < 150000);
  assert.strictEqual(fastS.verdict, "over");
  assert.strictEqual(fastS.consecutive_over, 2);
  const ticks = [...world.db.ticks.values()].map((t) => t.verdict).sort();
  return { verdicts: ticks, issues_after_warn: issuesAfterWarn, over_consecutive: fastS.consecutive_over,
    issues_total: issuesOpened(world).length };
};

scenarios.alert_episode = async (mod) => {
  const world = newWorld();
  const over = (m) => buckets(T0 + m * MIN, flat(15000));   // 180K/h
  const calm = (m) => buckets(T0 + m * MIN, flat(2500));    // 30K/h
  let st = await tickAt(mod, world, 0, over(0));
  assert.strictEqual(st.consecutive_over, 1);
  assert.strictEqual(st.episode.active, false, "one over tick must not open an episode");
  assert.strictEqual(issuesOpened(world).length, 0);
  st = await tickAt(mod, world, 5, over(5));
  assert.strictEqual(st.consecutive_over, 2);
  assert.strictEqual(st.episode.active, true, "2 consecutive over ticks open the episode");
  assert.strictEqual(st.would_have_tripped_count, 1);
  assert.strictEqual(st.last_would_trip_at, new Date(T0 + 5 * MIN).toISOString());
  assert.strictEqual(st.writes_enabled, true, "alert mode never stops writes");
  assert.strictEqual(st.tripped_at, null);
  const iss = issuesOpened(world);
  assert.strictEqual(iss.length, 1);
  assert.ok(iss[0].body.title.startsWith("[r2-breaker] WOULD HAVE TRIPPED: 180,000/h"), iss[0].body.title);
  assert.deepStrictEqual(iss[0].body.labels, ["breaker"]);
  assert.strictEqual(iss[0].ua, "tat-r2-breaker-worker");
  assert.strictEqual(iss[0].auth, "Bearer gh-token");
  assert.strictEqual(st.episode.issue_url, ISSUES + "100");
  // stays over for 3 more ticks: still ONE issue, peak tracked
  st = await tickAt(mod, world, 10, buckets(T0 + 10 * MIN, flat(20000)));
  st = await tickAt(mod, world, 15, over(15));
  st = await tickAt(mod, world, 20, over(20));
  assert.strictEqual(issuesOpened(world).length, 1, "exactly one issue per episode");
  assert.strictEqual(st.episode.peak_rate_1h, 240000);
  assert.strictEqual(st.writes_enabled, true);
  // 5 calm ticks: episode still open; the 6th closes it with a comment
  for (let k = 1; k <= 5; k++) st = await tickAt(mod, world, 20 + 5 * k, calm(20 + 5 * k));
  assert.strictEqual(st.episode.active, true);
  assert.strictEqual(commentsPosted(world).length, 0);
  st = await tickAt(mod, world, 50, calm(50));
  assert.strictEqual(st.episode.active, false, "6 ticks below warn close the episode");
  const cm = commentsPosted(world);
  assert.strictEqual(cm.length, 1);
  assert.ok(cm[0].path.endsWith("/issues/100/comments"), cm[0].path);
  assert.ok(/peak rate_1h 240,000\/h/.test(cm[0].body.body), cm[0].body.body);
  assert.strictEqual(st.would_have_tripped_count, 1);
  // a warn tick inside a NEW calm streak resets the calm counter
  st = await tickAt(mod, world, 55, over(55));
  st = await tickAt(mod, world, 60, over(60));
  assert.strictEqual(st.episode.active, true, "a fresh episode opens a fresh issue");
  assert.strictEqual(issuesOpened(world).length, 2);
  assert.strictEqual(st.would_have_tripped_count, 2);
  for (let k = 1; k <= 3; k++) st = await tickAt(mod, world, 60 + 5 * k, calm(60 + 5 * k));
  st = await tickAt(mod, world, 80, buckets(T0 + 80 * MIN, flat(7000)));   // warn: not calm
  for (let k = 1; k <= 5; k++) st = await tickAt(mod, world, 80 + 5 * k, calm(80 + 5 * k));
  assert.strictEqual(st.episode.active, true, "warn resets the 6-tick calm streak");
  st = await tickAt(mod, world, 110, calm(110));
  assert.strictEqual(st.episode.active, false);
  assert.strictEqual(st.writes_enabled, true, "alert mode: writes never touched");
  const kinds = world.db.events.map((e) => e.kind);
  return { issues: issuesOpened(world).map((c) => c.body.title), comments: cm.length,
    would_have_tripped_count: st.would_have_tripped_count, writes_enabled: st.writes_enabled,
    peak: 240000, event_kinds: kinds };
};

scenarios.armed_trip = async (mod) => {
  const world = newWorld();
  const over = (m) => buckets(T0 + m * MIN, flat(15000));
  const calm = (m) => buckets(T0 + m * MIN, flat(2500));
  // arm needs the key
  let r = await req(mod, world, "POST", "/arm");
  assert.strictEqual(r.status, 403);
  r = await req(mod, world, "POST", "/arm", { headers: { "x-admin-key": "wrong" } });
  assert.strictEqual(r.status, 403);
  r = await req(mod, world, "POST", "/r2-breaker/arm", { headers: { "X-Admin-Key": ADMIN } });
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  assert.strictEqual(r.body.status.mode, "armed");
  const armIssues = issuesOpened(world).length;   // the arm audit issue
  let st = await tickAt(mod, world, 0, over(0));
  assert.strictEqual(st.writes_enabled, true, "first over tick: not yet");
  st = await tickAt(mod, world, 5, over(5));
  assert.strictEqual(st.writes_enabled, false, "armed + 2 over ticks = trip");
  assert.strictEqual(st.tripped_at, new Date(T0 + 5 * MIN).toISOString());
  assert.ok(/rate 180,000\/h/.test(st.trip_reason), st.trip_reason);
  assert.strictEqual(st.episode.active, true);
  const trip = issuesOpened(world).slice(armIssues);
  assert.strictEqual(trip.length, 1);
  assert.ok(trip[0].body.title.startsWith("[r2-breaker] TRIPPED — R2 writes STOPPED: 180,000/h"), trip[0].body.title);
  const tripTick = [...world.db.ticks.values()].find((t) => t.ts === st.tripped_at);
  assert.strictEqual(tripTick.writes_enabled, 0);
  // stays false across many ok ticks, including after the episode closes
  for (let k = 1; k <= 10; k++) st = await tickAt(mod, world, 5 + 5 * k, calm(5 + 5 * k));
  assert.strictEqual(st.episode.active, false);
  assert.strictEqual(st.writes_enabled, false, "no auto-resume, ever");
  assert.strictEqual(st.verdict, "ok");
  const closeNote = commentsPosted(world).find((c) => /Episode ended/.test(c.body.body));
  assert.ok(closeNote && /STILL FALSE/.test(closeNote.body.body), "close comment says writes still stopped");
  // disarm does NOT re-enable writes
  r = await req(mod, world, "POST", "/disarm", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.status.mode, "alert");
  assert.strictEqual(r.body.status.writes_enabled, false);
  st = await tickAt(mod, world, 60, calm(60));
  assert.strictEqual(st.writes_enabled, false);
  // reset: 403 without key, 200 with -> the only way back
  r = await req(mod, world, "POST", "/reset");
  assert.strictEqual(r.status, 403);
  assert.strictEqual(world.db.stateObj().writes_enabled, false);
  r = await req(mod, world, "POST", "/reset", { headers: { "x-admin-key": ADMIN + "x" } });
  assert.strictEqual(r.status, 403);
  r = await req(mod, world, "POST", "/reset", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  st = world.db.stateObj();
  assert.strictEqual(st.writes_enabled, true);
  assert.strictEqual(st.tripped_at, null);
  assert.strictEqual(st.trip_reason, null);
  assert.strictEqual(st.episode.active, false);
  st = await tickAt(mod, world, 65, calm(65));
  assert.strictEqual(st.writes_enabled, true);
  // ADMIN_KEY unset -> 503 even with a header
  const w2 = newWorld({ env: { ADMIN_KEY: "" } });
  r = await req(mod, w2, "POST", "/trip", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 503);
  // reset mid-storm (cause NOT fixed): the trailing hour is ignored after a
  // reset, so writes stay enabled while the POST-reset buckets are under
  // threshold, and the storm re-trips once those buckets alone cross one
  // (40K/bucket = 480K/h: pace path at T+20 (1), rate 120K + pace at T+25 (2))
  const w3 = newWorld();
  await req(mod, w3, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  const storm = (m) => buckets(T0 + m * MIN, flat(40000));
  await tickAt(mod, w3, 0, storm(0));
  let s3 = await tickAt(mod, w3, 5, storm(5));
  assert.strictEqual(s3.writes_enabled, false);
  const rr = await req(mod, w3, "POST", "/reset", { headers: { "x-admin-key": ADMIN } });   // at T+5
  assert.strictEqual(rr.status, 200);
  assert.strictEqual(rr.body.status.consecutive_over, 0, "reset ends the over streak");
  assert.ok(/after this moment/.test(rr.body.detail), rr.body.detail);
  assert.strictEqual(w3.db.stateObj().writes_enabled, true);
  const held = [];
  let retripAt = null;
  for (const m of [10, 15, 20, 25]) {
    s3 = await tickAt(mod, w3, m, storm(m));
    if (s3.writes_enabled) held.push(m); else if (retripAt == null) retripAt = m;
  }
  assert.deepStrictEqual(held, [10, 15, 20], "post-reset buckets alone decide");
  assert.strictEqual(retripAt, 25, "a storm that keeps running re-trips on post-reset data");
  assert.strictEqual(issuesOpened(w3).filter((c) => /TRIPPED/.test(c.body.title)).length, 2, "fresh episode, fresh issue");
  const kinds = world.db.events.map((e) => e.kind);
  return { trip_title: trip[0].body.title, tripped_at: new Date(T0 + 5 * MIN).toISOString(),
    stayed_false_ticks: 10, reset_status_no_key: 403, reset_status_bad_key: 403,
    reset_status: 200, after_reset: st.writes_enabled, unset_admin_key: 503, event_kinds: kinds,
    held_after_reset: held, retrip_at_min: retripAt };
};

scenarios.manual_trip = async (mod) => {
  const world = newWorld();
  const calm = (m) => buckets(T0 + m * MIN, flat(2500));
  await tickAt(mod, world, 0, calm(0));
  let r = await req(mod, world, "POST", "/trip", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  let st = world.db.stateObj();
  assert.strictEqual(st.writes_enabled, false);
  assert.strictEqual(st.trip_reason, "manual");
  assert.strictEqual(st.mode, "alert", "manual trip works in alert mode too");
  assert.ok(issuesOpened(world).some((c) => /TRIPPED manually/.test(c.body.title)));
  st = await tickAt(mod, world, 5, calm(5));
  st = await tickAt(mod, world, 10, calm(10));
  assert.strictEqual(st.writes_enabled, false, "ok ticks never re-enable");
  // reset comments on the manual-trip issue
  r = await req(mod, world, "POST", "/reset", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(world.db.stateObj().writes_enabled, true);
  const cm = commentsPosted(world);
  assert.strictEqual(cm.length, 1);
  assert.ok(cm[0].path.endsWith("/issues/100/comments"));
  return { after_trip: false, reason: "manual", after_reset: true, comments: cm.length };
};

scenarios.analytics_errors = async (mod) => {
  const world = newWorld();
  const over = (m) => buckets(T0 + m * MIN, flat(15000));
  let st = await tickAt(mod, world, 0, over(0));
  assert.strictEqual(st.consecutive_over, 1);
  const kinds = [];
  const failures = [{ status: 500 }, { throw: true }, { errors: true }, { nodata: true }, { rows: [] }];
  for (let i = 0; i < failures.length; i++) {
    st = await tickAt(mod, world, 5 * (i + 1), failures[i]);
    assert.strictEqual(st.verdict, "error", JSON.stringify(failures[i]));
    assert.strictEqual(st.consecutive_errors, i + 1);
    assert.strictEqual(st.consecutive_over, 1, "errors never reset consecutive_over");
    assert.strictEqual(st.writes_enabled, true);
    assert.strictEqual(st.rate_1h, null);
    assert.ok(st.last_error, "last_error recorded");
    assert.strictEqual(st.last_tick, new Date(T0 + 5 * (i + 1) * MIN).toISOString());
    assert.strictEqual(st.last_ok_tick, new Date(T0).toISOString(), "last_ok_tick frozen on error");
    kinds.push(st.last_error);
  }
  const iss = issuesOpened(world);
  assert.strictEqual(iss.length, 1, "issue exactly at the 3rd consecutive error (not 1, 2 or 4)");
  assert.strictEqual(iss[0].body.title, "[r2-breaker] cannot read R2 analytics (15 min)");
  const errEvent = world.db.events.find((e) => e.kind === "analytics_error");
  assert.ok(errEvent && errEvent.ts === new Date(T0 + 15 * MIN).toISOString());
  assert.ok(/no complete buckets/.test(st.last_error), "empty result is a read failure: " + st.last_error);
  // recovery: comment on the issue, counters reset, consecutive_over continues -> trips the episode
  st = await tickAt(mod, world, 30, over(30));
  assert.strictEqual(st.verdict, "over");
  assert.strictEqual(st.consecutive_errors, 0);
  assert.strictEqual(st.last_error, null);
  assert.strictEqual(st.consecutive_over, 2);
  assert.strictEqual(st.episode.active, true);
  const cm = commentsPosted(world);
  assert.strictEqual(cm.length, 1);
  assert.ok(/recovered/.test(cm[0].body.body));
  assert.ok(world.db.events.some((e) => e.kind === "analytics_recovered"));
  // armed + error storm: writes_enabled untouched even with consecutive_over >= 2
  const w2 = newWorld();
  await req(mod, w2, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  await tickAt(mod, w2, 0, over(0));
  for (let i = 1; i <= 5; i++) {
    const s2 = await tickAt(mod, w2, 5 * i, { status: 502 });
    assert.strictEqual(s2.writes_enabled, true, "fail-open: analytics failure never trips");
  }
  // unset token is an error too (never a trip)
  const w3 = newWorld({ env: { CF_GRAPHQL_TOKEN: "" } });
  const s3 = await tickAt(mod, w3, 0, over(0));
  assert.strictEqual(s3.verdict, "error");
  assert.ok(/CF_GRAPHQL_TOKEN/.test(s3.last_error));
  return { errors: kinds, issue_at: 3, issues: iss.length, recovered_comment: cm.length,
    consecutive_over_after: st.consecutive_over, episode_after: st.episode.active };
};

scenarios.heartbeat_gap = async (mod) => {
  const world = newWorld();
  const calm = (m) => buckets(T0 + m * MIN, flat(2500));
  let st = await tickAt(mod, world, 0, calm(0));
  assert.strictEqual(st.gaps_detected, 0, "first tick ever is not a gap");
  st = await tickAt(mod, world, 5, calm(5));
  st = await tickAt(mod, world, 25, calm(25));      // exactly 20 min: not a gap
  assert.strictEqual(st.gaps_detected, 0);
  st = await tickAt(mod, world, 46, calm(46));      // 21 min: gap
  assert.strictEqual(st.gaps_detected, 1);
  const iss = issuesOpened(world);
  assert.strictEqual(iss.length, 1);
  assert.strictEqual(iss[0].body.title, "[r2-breaker] heartbeat gap: 21 min with no tick");
  assert.strictEqual(st.last_tick, new Date(T0 + 46 * MIN).toISOString());
  st = await tickAt(mod, world, 51, calm(51));      // back to normal: no new gap
  assert.strictEqual(st.gaps_detected, 1);
  assert.strictEqual(issuesOpened(world).length, 1, "one issue per gap");
  st = await tickAt(mod, world, 200, { status: 500 });   // gap + analytics error in one tick
  assert.strictEqual(st.gaps_detected, 2);
  assert.strictEqual(st.verdict, "error");
  assert.strictEqual(issuesOpened(world).length, 2);
  const gapEvents = world.db.events.filter((e) => e.kind === "gap");
  assert.strictEqual(gapEvents.length, 2);
  assert.strictEqual(gapEvents[0].issue_url, ISSUES + "100");
  return { gaps_detected: st.gaps_detected, titles: issuesOpened(world).map((c) => c.body.title) };
};

const STATUS_TYPES = {
  v: "number", mode: "string", writes_enabled: "boolean", tripped_at: "null|string",
  trip_reason: "null|string", episode: "object", would_have_tripped_count: "number",
  last_would_trip_at: "null|string", last_tick: "null|string", last_ok_tick: "null|string",
  heartbeat_age_s: "null|number", tick_interval_s: "number", rate_1h: "null|number",
  pace_15m: "null|number", warn_hourly: "number", trip_hourly: "number", verdict: "string",
  consecutive_over: "number", consecutive_errors: "number", last_error: "null|string",
  gaps_detected: "number", worker_ts: "string",
};

function checkShape(doc) {
  for (const [k, t] of Object.entries(STATUS_TYPES)) {
    assert.ok(k in doc, `status missing key ${k}`);
    const v = doc[k];
    const actual = v === null ? "null" : typeof v;
    assert.ok(t.split("|").includes(actual), `status.${k}: ${actual} not in ${t}`);
  }
  const extra = Object.keys(doc).filter((k) => !(k in STATUS_TYPES));
  assert.deepStrictEqual(extra, [], "no undocumented keys in /status");
  for (const k of ["active", "started_at", "peak_rate_1h", "issue_url"]) assert.ok(k in doc.episode, k);
  assert.ok(["alert", "armed"].includes(doc.mode));
  assert.ok(["ok", "warn", "over", "error"].includes(doc.verdict));
}

scenarios.status_shape = async (mod) => {
  const world = newWorld();
  // before any tick: everything null/zero, still the full shape, from an EMPTY db
  let r = await req(mod, world, "GET", "/status");
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.headers.get("cache-control"), "no-store");
  assert.strictEqual(r.headers.get("access-control-allow-origin"), "*");
  checkShape(r.body);
  const fresh = r.body;
  assert.strictEqual(fresh.v, 1);
  assert.strictEqual(fresh.mode, "alert");
  assert.strictEqual(fresh.writes_enabled, true);
  assert.strictEqual(fresh.heartbeat_age_s, null);
  assert.strictEqual(fresh.last_tick, null);
  assert.strictEqual(fresh.tick_interval_s, 300);
  assert.strictEqual(fresh.warn_hourly, 80000);
  assert.strictEqual(fresh.trip_hourly, 150000);
  assert.strictEqual(fresh.worker_ts, new Date(T0).toISOString());
  // after a tick + 7 min: heartbeat_age_s is 420, no internal keys leak
  await tickAt(mod, world, 0, buckets(T0, flat(2500)));
  world.now = T0 + 7 * MIN;
  r = await req(mod, world, "GET", "/r2-breaker/status");
  checkShape(r.body);
  assert.strictEqual(r.body.heartbeat_age_s, 420);
  assert.strictEqual(r.body.rate_1h, 30000);
  assert.strictEqual(r.body.last_ok_tick, new Date(T0).toISOString());
  assert.ok(!("_calm_ticks" in r.body) && !("__snapshot" in r.body));
  // DEFAULT_MODE=armed honoured on an empty database
  const w2 = newWorld({ env: { DEFAULT_MODE: "armed" } });
  const r2 = await req(mod, w2, "GET", "/status");
  assert.strictEqual(r2.body.mode, "armed");
  return { keys: Object.keys(fresh), heartbeat_age_s: r.body.heartbeat_age_s, fresh };
};

scenarios.routes = async (mod) => {
  const rp = mod.routePath;
  assert.strictEqual(rp("/r2-breaker/status"), "/status");
  assert.strictEqual(rp("/r2-breaker/status/"), "/status");
  assert.strictEqual(rp("/status"), "/status");
  assert.strictEqual(rp("/r2-breaker"), "/");
  assert.strictEqual(rp("/r2-breaker/"), "/");
  assert.strictEqual(rp("/r2-breakerx/status"), "/r2-breakerx/status");
  assert.strictEqual(rp("/"), "/");
  assert.strictEqual(rp("/r2-breaker/ticks"), "/ticks");
  const world = newWorld();
  for (let i = 0; i < 4; i++) await tickAt(mod, world, 5 * i, buckets(T0 + 5 * i * MIN, flat(2500)));
  const codes = {};
  const probe = async (m, p, h) => { const r = await req(mod, world, m, p, { headers: h }); codes[`${m} ${p}`] = r.status; return r; };
  await probe("GET", "/status");
  await probe("GET", "/r2-breaker/status");
  await probe("GET", "/r2-breaker/status/");
  await probe("HEAD", "/status");
  await probe("POST", "/status");
  await probe("GET", "/arm");
  await probe("GET", "/reset");
  await probe("POST", "/r2-breaker/arm");
  await probe("GET", "/nope");
  await probe("GET", "/r2-breaker/nope");
  await probe("GET", "/");
  await probe("GET", "/r2-breaker");
  await probe("OPTIONS", "/status");
  await probe("OPTIONS", "/r2-breaker/reset");
  assert.strictEqual(codes["GET /status"], 200);
  assert.strictEqual(codes["GET /r2-breaker/status"], 200);
  assert.strictEqual(codes["GET /r2-breaker/status/"], 200);
  assert.strictEqual(codes["HEAD /status"], 200);
  assert.strictEqual(codes["POST /status"], 405);
  assert.strictEqual(codes["GET /arm"], 405);
  assert.strictEqual(codes["GET /reset"], 405);
  assert.strictEqual(codes["POST /r2-breaker/arm"], 403);
  assert.strictEqual(codes["GET /nope"], 404);
  assert.strictEqual(codes["GET /r2-breaker/nope"], 404);
  assert.strictEqual(codes["GET /"], 404);
  assert.strictEqual(codes["GET /r2-breaker"], 404);
  assert.strictEqual(codes["OPTIONS /status"], 204);
  assert.strictEqual(codes["OPTIONS /r2-breaker/reset"], 204);
  const nf = await req(mod, world, "GET", "/nope");
  assert.deepStrictEqual(nf.body, { error: "not found" });
  // ticks + events
  let r = await req(mod, world, "GET", "/r2-breaker/ticks?n=2");
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.n, 2);
  assert.strictEqual(r.body.ticks[0].ts, new Date(T0 + 15 * MIN).toISOString(), "newest first");
  assert.strictEqual(r.body.ticks[0].writes_enabled, true);
  r = await req(mod, world, "GET", "/ticks");
  assert.strictEqual(r.body.n, 4);
  r = await req(mod, world, "GET", "/ticks?n=99999");
  assert.strictEqual(r.body.n, 4);
  assert.strictEqual(world.db.log[world.db.log.length - 1].args[0], 288, "n clamps to 288");
  r = await req(mod, world, "GET", "/ticks?n=-3");
  assert.strictEqual(world.db.log[world.db.log.length - 1].args[0], 48, "bad n -> default 48");
  await req(mod, world, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  r = await req(mod, world, "GET", "/events?n=10");
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.events[0].kind, "arm");
  return { codes, ticks_n: 4, events_kind: r.body.events[0].kind };
};

scenarios.persistence = async (mod) => {
  // schema self-heal on a database without tables
  const world = newWorld({ db: { tablesMissing: true } });
  const st = await tickAt(mod, world, 0, buckets(T0, flat(2500)));
  assert.ok(world.db.ddl >= mod.SCHEMA.length, "schema applied when the table is missing");
  assert.strictEqual(st.verdict, "ok");
  // ticks older than 30 days are pruned
  world.db.ticks.set("2026-06-01T00:00:00.000Z", { ts: "2026-06-01T00:00:00.000Z" });
  await tickAt(mod, world, 5, buckets(T0 + 5 * MIN, flat(2500)));
  assert.ok(!world.db.ticks.has("2026-06-01T00:00:00.000Z"), "old tick pruned");
  assert.strictEqual(world.db.ticks.size, 2);
  // diff-only state writes: a steady tick does not rewrite mode/writes_enabled
  const before = world.db.log.length;
  await tickAt(mod, world, 10, buckets(T0 + 10 * MIN, flat(2500)));
  const writes = world.db.log.slice(before).filter((l) => l.sql.startsWith("INSERT INTO state"));
  const keys = writes.map((l) => l.args[0]);
  assert.ok(keys.includes("last_tick") && keys.includes("last_ok_tick"), keys);
  assert.ok(!keys.includes("mode") && !keys.includes("writes_enabled"), `steady tick rewrote ${keys}`);
  // state survives a fresh module load (only D1 carries it)
  const mod2 = await import(pathToFileURL(WORKER).href + "?reload=" + Date.now());
  const r = await req(mod2, world, "GET", "/status");
  assert.strictEqual(r.body.last_tick, new Date(T0 + 10 * MIN).toISOString());
  // GITHUB_TOKEN unset: alerting skipped, tick still succeeds
  const w2 = newWorld({ env: { GITHUB_TOKEN: "" } });
  await tickAt(mod, w2, 0, buckets(T0, flat(15000)));
  const s2 = await tickAt(mod, w2, 5, buckets(T0 + 5 * MIN, flat(15000)));
  assert.strictEqual(s2.episode.active, true);
  assert.strictEqual(s2.episode.issue_url, null);
  assert.strictEqual(w2.gh.calls.length, 0);
  assert.ok(LOGS.some((l) => /GITHUB_TOKEN unset; not opening issue/.test(l)), LOGS.slice(-3));
  // GitHub 500 on issue create: tick still completes, issue_url null
  const w3 = newWorld();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (u, i) => (String(u).includes("api.github.com") ? new Response("x", { status: 500 }) : realFetch(u, i));
  await tickAt(mod, w3, 0, buckets(T0, flat(15000)));
  const s3 = await tickAt(mod, w3, 5, buckets(T0 + 5 * MIN, flat(15000)));
  assert.strictEqual(s3.episode.active, true);
  assert.strictEqual(s3.episode.issue_url, null);
  globalThis.fetch = realFetch;
  return { ddl: world.db.ddl, steady_keys: keys, no_token_calls: w2.gh.calls.length };
};

// Finding: a 200 with zero rows used to be a healthy 0/h tick (reset the
// over streak, advanced last_ok_tick). It is a read failure: fail open.
scenarios.empty_result = async (mod) => {
  const over = (m) => buckets(T0 + m * MIN, flat(15000));   // 180K/h
  const world = newWorld();
  await req(mod, world, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  let st = await tickAt(mod, world, 0, over(0));
  assert.strictEqual(st.consecutive_over, 1);
  st = await tickAt(mod, world, 5, { rows: [] });
  assert.strictEqual(st.verdict, "error");
  assert.ok(/no complete buckets/.test(st.last_error), st.last_error);
  assert.strictEqual(st.consecutive_errors, 1);
  assert.strictEqual(st.consecutive_over, 1, "empty read keeps the over streak");
  assert.strictEqual(st.last_ok_tick, isoAt(T0), "empty read is not an ok tick");
  assert.strictEqual(st.rate_1h, null);
  assert.strictEqual(st.writes_enabled, true);
  // only the partial newest bucket present -> still nothing complete -> error
  st = await tickAt(mod, world, 10, buckets(T0 + 10 * MIN, flat(15000)).slice(-1));
  assert.strictEqual(st.verdict, "error");
  assert.strictEqual(st.consecutive_errors, 2);
  assert.strictEqual(st.consecutive_over, 1);
  // read recovers, still over: streak reaches 2 -> armed trip
  st = await tickAt(mod, world, 15, over(15));
  assert.strictEqual(st.consecutive_errors, 0);
  assert.strictEqual(st.consecutive_over, 2);
  assert.strictEqual(st.writes_enabled, false);
  // alert mode, storm alternating with empty reads: the episode still opens
  const w2 = newWorld();
  let s2;
  for (let i = 0; i < 4; i++) s2 = await tickAt(mod, w2, 5 * i, i % 2 ? { rows: [] } : over(5 * i));
  assert.strictEqual(s2.verdict, "error");
  s2 = await tickAt(mod, w2, 20, over(20));
  assert.strictEqual(s2.consecutive_over, 3);
  assert.strictEqual(s2.episode.active, true, "alternating empty reads never mask a storm");
  assert.strictEqual(issuesOpened(w2).length, 1);
  return { error: undefined, empty_error: st.last_error === null ? null : st.last_error,
    streak_kept: true, trip_after_recovery: st.writes_enabled === false,
    alert_episode_opened: s2.episode.active };
};

// Finding: arming during an active (alert-opened) episode never stopped
// writes for that storm. Now: fresh sustained over -> trip at once from
// /arm; stale/calm reading -> loud detail, trip on the next sustained over
// tick. Either way a comment on the episode issue, never a second issue.
scenarios.arm_mid_episode = async (mod) => {
  const storm = (m) => buckets(T0 + m * MIN, flat(40000));   // 480K/h
  const calm = (m) => buckets(T0 + m * MIN, flat(2500));
  // (a) fresh reading: /arm trips now
  const world = newWorld();
  await tickAt(mod, world, 0, storm(0));
  let st = await tickAt(mod, world, 5, storm(5));
  assert.strictEqual(st.episode.active, true);
  assert.strictEqual(st.writes_enabled, true);
  assert.strictEqual(issuesOpened(world).length, 1);
  world.now = T0 + 7 * MIN;   // operator reads the issue two minutes later
  let r = await req(mod, world, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.status.mode, "armed");
  assert.strictEqual(r.body.status.writes_enabled, false, "arming into a live sustained over stops writes now");
  assert.ok(/TRIPPED NOW/.test(r.body.detail), r.body.detail);
  assert.strictEqual(r.body.issue_url, ISSUES + "100", "commented on the episode issue");
  assert.strictEqual(issuesOpened(world).length, 1, "no second issue");
  st = world.db.stateObj();
  assert.strictEqual(st.tripped_at, isoAt(T0 + 7 * MIN));
  assert.ok(/armed during active episode/.test(st.trip_reason), st.trip_reason);
  assert.deepStrictEqual(world.db.events.map((e) => e.kind), ["would_trip", "arm", "trip"]);
  for (const m of [10, 15, 20]) st = await tickAt(mod, world, m, storm(m));
  assert.strictEqual(st.writes_enabled, false);
  assert.strictEqual(issuesOpened(world).length, 1);
  assert.strictEqual(world.db.events.filter((e) => e.kind === "trip").length, 1, "one trip, no repeats");
  const armDetail = r.body.detail;
  // (b) stale reading (two failed reads, last ok 12 min ago): /arm must NOT
  // trip on it, says so, and the next sustained over tick trips in-episode
  const w2 = newWorld();
  await tickAt(mod, w2, 0, storm(0));
  await tickAt(mod, w2, 5, storm(5));
  await tickAt(mod, w2, 10, { status: 500 });
  await tickAt(mod, w2, 15, { status: 502 });
  w2.now = T0 + 17 * MIN;
  r = await req(mod, w2, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.status.writes_enabled, true, "stale reading: never trip on it");
  assert.ok(/remain ENABLED/.test(r.body.detail) && /stale/.test(r.body.detail), r.body.detail);
  assert.ok(/POST \/trip/.test(r.body.detail), r.body.detail);
  let s2 = await tickAt(mod, w2, 20, storm(20));
  assert.strictEqual(s2.verdict, "over");
  assert.strictEqual(s2.writes_enabled, false, "next sustained over tick inside the episode trips");
  assert.ok(/armed during active episode/.test(s2.trip_reason), s2.trip_reason);
  assert.strictEqual(issuesOpened(w2).length, 1, "the trip is a comment, not a new issue");
  const tc = commentsPosted(w2).find((c) => /TRIPPED at/.test(c.body.body));
  assert.ok(tc && tc.path.endsWith("/issues/100/comments"), "trip comment lands on the episode issue");
  assert.ok(w2.db.events.some((e) => e.kind === "trip" && e.issue_url === ISSUES + "100"));
  const staleDetail = r.body.detail;
  // (c) episode active but calming (last verdict ok): /arm says writes stay
  // enabled; a returning storm trips on its 2nd over tick, in-episode
  const w3 = newWorld();
  await tickAt(mod, w3, 0, storm(0));
  await tickAt(mod, w3, 5, storm(5));
  await tickAt(mod, w3, 10, calm(10));
  w3.now = T0 + 12 * MIN;
  r = await req(mod, w3, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.body.status.writes_enabled, true);
  assert.ok(/remain ENABLED \(last reading ok\)/.test(r.body.detail), r.body.detail);
  let s3 = await tickAt(mod, w3, 15, storm(15));
  assert.strictEqual(s3.writes_enabled, true, "one over tick is not sustained");
  s3 = await tickAt(mod, w3, 20, storm(20));
  assert.strictEqual(s3.writes_enabled, false);
  assert.strictEqual(s3.episode.active, true);
  assert.strictEqual(issuesOpened(w3).length, 1);
  // (d) alert mode never late-trips; an already-tripped armed episode never double-trips
  const w4 = newWorld();
  await tickAt(mod, w4, 0, storm(0));
  let s4 = await tickAt(mod, w4, 5, storm(5));
  for (const m of [10, 15, 20]) s4 = await tickAt(mod, w4, m, storm(m));
  assert.strictEqual(s4.writes_enabled, true, "alert mode: never");
  assert.ok(!w4.db.events.some((e) => e.kind === "trip"));
  return { arm_detail: armDetail, stale_detail: staleDetail, immediate_trip: st.writes_enabled === false,
    late_trip: s2.writes_enabled === false, calm_then_trip: s3.writes_enabled === false,
    alert_untouched: s4.writes_enabled, issues_a: issuesOpened(world).length, issues_b: issuesOpened(w2).length };
};

// Finding: after an armed trip the trailing hour keeps rate_1h over for up
// to 55 min, so /reset was undone on the next tick. Now only buckets that
// start at/after the reset count, for the hour the floor is in the window.
scenarios.reset_after_trip = async (mod) => {
  const world = newWorld();
  await req(mod, world, "POST", "/arm", { headers: { "x-admin-key": ADMIN } });
  const tripAt = T0 + 5 * MIN;
  // 40K/bucket storm until the trip; the kill switch then drops box writes to 10/bucket
  const rows = (m) => buckets(T0 + m * MIN, (start) => (start >= tripAt ? 10 : 40000));
  await tickAt(mod, world, 0, rows(0));
  let st = await tickAt(mod, world, 5, rows(5));
  assert.strictEqual(st.writes_enabled, false);
  assert.strictEqual(st.tripped_at, isoAt(tripAt));
  for (const m of [10, 15, 20, 25]) st = await tickAt(mod, world, m, rows(m));
  assert.strictEqual(st.verdict, "over", "the trailing hour still holds the storm 20 min after the trip");
  assert.ok(st.pace_15m < 1000, "pace already reflects the stopped writes");
  // T+30: cause understood, operator resets
  world.now = T0 + 30 * MIN;
  const r = await req(mod, world, "POST", "/reset", { headers: { "x-admin-key": ADMIN } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.status.writes_enabled, true);
  assert.strictEqual(r.body.status.consecutive_over, 0);
  assert.strictEqual(r.body.status.episode.active, false);
  assert.strictEqual(r.body.issue_url, ISSUES + "101", "reset comments on the trip issue");
  assert.strictEqual(world.db.stateObj()._reset_at, isoAt(T0 + 30 * MIN));
  // every tick for the next 90 min: still enabled, verdict ok, no second TRIPPED issue
  const trail = [];
  for (let m = 35; m <= 120; m += 5) {
    st = await tickAt(mod, world, m, rows(m));
    trail.push([m, st.rate_1h, st.pace_15m, st.verdict]);
    assert.strictEqual(st.writes_enabled, true, `re-tripped at T+${m}: ${JSON.stringify(trail)}`);
    assert.strictEqual(st.verdict, "ok", `verdict ${st.verdict} at T+${m}`);
    assert.strictEqual(st.consecutive_errors, 0, "a floored read with raw buckets is not an empty read");
  }
  assert.strictEqual(issuesOpened(world).filter((c) => /TRIPPED/.test(c.body.title)).length, 1);
  assert.deepStrictEqual(trail[0].slice(1), [0, 0, "ok"], "first post-reset tick: no counted bucket yet");
  assert.deepStrictEqual(trail[3].slice(1), [30, 120, "ok"], "T+50: three post-reset buckets of 10 (pace 4 x 30)");
  assert.ok(world.db.stateObj()._reset_at, "floor still set while inside the 2 h window");
  st = await tickAt(mod, world, 155, rows(155));
  assert.strictEqual(st._reset_at, null, "floor expires once it leaves the query window");
  assert.strictEqual(st.rate_1h, 120, "full trailing hour again (12 x 10)");
  // the old bug, for the record: the same trail WITHOUT the floor is over until T+60
  const unfloored = mod.summarizeBuckets(rows(35), T0 + 35 * MIN);
  assert.ok(unfloored.rate_1h >= 150000, `unfloored rate at T+35 = ${unfloored.rate_1h}`);
  return { trail, unfloored_rate_t35: unfloored.rate_1h, retripped: false,
    trip_issues: issuesOpened(world).filter((c) => /TRIPPED/.test(c.body.title)).length };
};

// Finding: on a */5 cron the tick lands ~2 s after a boundary, when the
// just-closed bucket is only ~70% ingested. The cron is now 2 min past each
// boundary; the completeness rule is unchanged, so both instants count the
// just-closed bucket (it is fully ingested only at the offset one).
scenarios.boundary_tick = async (mod) => {
  const boundary = Math.floor(T0 / (5 * MIN)) * 5 * MIN + 5 * MIN;   // 12:05:00
  const rows = buckets(boundary + 2000, (start, i) => (i === 0 ? 111 : i === 1 ? 222 : 1000));
  const atBoundary = mod.summarizeBuckets(rows, boundary + 2000);
  const atOffset = mod.summarizeBuckets(rows, boundary + 2 * MIN + 2000);
  for (const s of [atBoundary, atOffset]) {
    assert.strictEqual(s.dropped_partial, 1, "the bucket that just opened is partial");
    assert.strictEqual(s.newest_start, isoAt(boundary - 5 * MIN), "the just-closed bucket is the newest counted");
    assert.strictEqual(s.pace_15m, 4 * (222 + 1000 + 1000));
  }
  // the cron minutes are all 2 past a 5-min boundary, one per 5 min
  const mins = CRON.split(" ")[0].split(",").map(Number);
  assert.strictEqual(mins.length, 12);
  assert.ok(mins.every((m, i) => m === 2 + 5 * i), CRON);
  // a tick at the offset instant through the Worker sees the same numbers
  const world = newWorld();
  world.now = boundary + 2 * MIN + 2000;
  world.gql = { rows };
  await mod.default.scheduled({ scheduledTime: world.now, cron: CRON }, world.env, { waitUntil() {} });
  const st = world.db.stateObj();
  assert.strictEqual(st.pace_15m, atOffset.pace_15m);
  return { newest_at_boundary: atBoundary.newest_start, newest_at_offset: atOffset.newest_start,
    dropped_partial: atOffset.dropped_partial, cron_minutes: mins };
};

scenarios.safe_equal = async (mod) => {
  assert.strictEqual(mod.safeEqual("abc", "abc"), true);
  assert.strictEqual(mod.safeEqual("abc", "abd"), false);
  assert.strictEqual(mod.safeEqual("abc", "abcd"), false);
  assert.strictEqual(mod.safeEqual("", ""), true);
  assert.strictEqual(mod.safeEqual("a", ""), false);
  assert.strictEqual(mod.safeEqual(null, ""), true);
  assert.strictEqual(mod.safeEqual(undefined, "x"), false);
  return { ok: true };
};

// ----------------------------------------------------------------- main ---

(async () => {
  const mod = await import(pathToFileURL(WORKER).href);
  assert.strictEqual(typeof mod.default.fetch, "function");
  assert.strictEqual(typeof mod.default.scheduled, "function");
  const realNow = Date.now;
  let failed = false;
  for (const [name, fn] of Object.entries(scenarios)) {
    try {
      out[name] = await fn(mod);
    } catch (e) {
      failed = true;
      out[name] = { error: String(e && e.message || e) };
      console.error(`SCENARIO FAILED: ${name}\n${e && e.stack || e}`);
    }
  }
  Date.now = realNow;
  process.stdout.write(JSON.stringify(out) + "\n");
  if (failed) process.exit(1);
  console.error("ALL CHECKS PASSED");
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
