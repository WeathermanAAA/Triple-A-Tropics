/* r2-breaker.js — R2 Class-A cost circuit breaker (cron every 5 min) + status API.
 *
 * WHY: the 2026-08-03 incident ran ~445,000 Class-A ops/h against a normal
 * ~29,000/h and nobody knew for a day. This Worker reads the account's R2
 * Class-A operation counts from the Cloudflare GraphQL analytics API every
 * 5 minutes, keeps a small state machine in D1, and raises the alarm via
 * GitHub issues (the house alert channel, label `breaker`). Its /status
 * document is mirrored to R2 (fleet/breaker.json) by the box heartbeat
 * and read by `tat_killswitch.py` inside every writer on the fleet.
 *
 * MODES (PHASE0_SPEC, binding):
 *   alert  (default, first week): PROVES itself alive, opens "WOULD HAVE
 *          TRIPPED" issues, NEVER sets writes_enabled=false.
 *   armed  : same, plus writes_enabled=false on a trip. Stays false until
 *          POST /reset. No auto-resume, ever.
 *
 * Arming DURING an active episode (the operator's natural reaction to a
 * "WOULD HAVE TRIPPED" issue) trips at once when the latest read is a fresh
 * sustained "over", else on the next over tick; the trip is a comment on
 * the episode's existing issue (one issue per episode holds).
 *
 * After POST /reset the trailing 60-min window still holds the pre-trip
 * storm for up to an hour, so only buckets that START at or after the
 * reset are counted (missing = 0): pace_15m recovers within 15 min, rate_1h
 * refills over the hour. Under-counting is the direction that never
 * re-trips on stale data; a storm that is STILL running re-trips as soon
 * as the post-reset buckets alone cross a threshold.
 *
 * FAIL-OPEN everywhere: an analytics read failure never changes
 * writes_enabled; an EMPTY analytics result is a read failure too (this
 * account never has a two-hour window with zero Class-A ops, so zero rows
 * means the dataset is lagging, not that load is zero); a missing
 * GITHUB_TOKEN never fails a tick; a missing ADMIN_KEY makes the admin
 * verbs 503 (never 200).
 *
 * Endpoints (zone route triple-a-tropics.com/r2-breaker/* AND the bare
 * workers.dev host — the /r2-breaker prefix is stripped when present):
 *   GET  /status            status JSON contract v1 (CORS *, no-store)
 *   GET  /ticks?n=48        recent ticks, newest first (max 288 = 24 h)
 *   GET  /events?n=50       recent events, newest first
 *   POST /arm | /disarm | /trip | /reset   header X-Admin-Key (secret
 *        ADMIN_KEY, constant-time compare) — 403 bad key, 503 unset
 *
 * Budget: Workers FREE plan (10 ms CPU/invocation, 100K req/day shared).
 * The cron is 288 invocations/day; boxes never poll this Worker directly
 * (they read the CDN mirror) — only the heartbeat mirror (2/min fleet-wide)
 * and humans/the liveness workflow hit /status.
 *
 * Secrets (deploy-breaker.sh — NEVER in the repo):
 *   CF_GRAPHQL_TOKEN   Account Analytics:Read (GraphQL)
 *   GITHUB_TOKEN       classic PAT, repo scope (issues RW)
 *   ADMIN_KEY          admin verbs
 * Vars (r2-breaker.toml): ACCOUNT_TAG, BUCKET, REPO, TRIP_HOURLY,
 *   WARN_HOURLY, DEFAULT_MODE.
 */

export const CONTRACT_VERSION = 1;
export const TICK_INTERVAL_S = 300;
export const TICK_MS = TICK_INTERVAL_S * 1000;
// /arm trips at once only on a reading younger than this (2 ticks): an
// hour-old "over" from a cron that stopped is not grounds to stop writes.
export const ARM_FRESH_MS = 2 * TICK_MS;
export const BUCKET_MS = 5 * 60 * 1000;
export const LOOKBACK_MS = 120 * 60 * 1000;
export const RATE_BUCKETS = 12;          // 12 x 5 min = 1 h
export const PACE_BUCKETS = 3;           // 3 x 5 min x 4 = hourly pace
export const TRIP_TICKS = 2;             // >= 10 min sustained
export const EPISODE_CALM_TICKS = 6;     // 30 min below warn closes an episode
export const ERROR_ISSUE_AT = 3;         // 15 min of analytics failures
export const GAP_MS = 20 * 60 * 1000;    // self-gap alarm
export const TICK_RETENTION_MS = 30 * 24 * 3600 * 1000;
export const MAX_TICKS = 288;
export const MAX_EVENTS = 500;

// Class A (billed $4.50/M) per the R2 pricing page; everything else is
// Class B or free. Success-only: rejected/throttled ops are not billed.
export const CLASS_A = [
  "ListObjects", "PutObject", "CopyObject", "CompleteMultipartUpload",
  "CreateMultipartUpload", "UploadPart", "UploadPartCopy",
  "ListMultipartUploads", "ListParts",
];

const GQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql";
const GH_DEFAULT = "https://api.github.com";
const UA = "tat-r2-breaker-worker";
const LABEL = "breaker";
const LABEL_COLOR = "b60205";
const ROUTE_PREFIX = "/r2-breaker";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, x-admin-key",
};

// Schema lives here AND in r2-breaker.sql (deploy-breaker.sh applies the
// file; the Worker self-heals a missing table from this copy). The test
// pins the two copies to each other.
export const SCHEMA = [
  "CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
  "CREATE TABLE IF NOT EXISTS ticks (ts TEXT PRIMARY KEY, rate_1h INTEGER, pace_15m INTEGER, verdict TEXT, mode TEXT, writes_enabled INTEGER, error TEXT, latency_ms INTEGER)",
  "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT, issue_url TEXT)",
  "CREATE INDEX IF NOT EXISTS events_ts ON events (ts)",
];

// ---------------------------------------------------------------- helpers --

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...CORS, ...extra,
    },
  });
}

function iso(ms) { return new Date(ms).toISOString(); }

function intVar(env, name, dflt) {
  const n = parseInt(env && env[name], 10);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

export function thresholds(env) {
  return {
    warn: intVar(env, "WARN_HOURLY", 80000),
    trip: intVar(env, "TRIP_HOURLY", 150000),
  };
}

// Constant-time string compare (no early exit on the first differing byte,
// no dependence on where the difference is). Length mismatch still returns
// false after the full loop.
export function safeEqual(a, b) {
  a = String(a == null ? "" : a);
  b = String(b == null ? "" : b);
  const n = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < n; i++) {
    diff |= (a.charCodeAt(i) | 0) ^ (b.charCodeAt(i) | 0);
  }
  return diff === 0;
}

// Zone route = /r2-breaker/<path>; workers.dev = /<path>. Both map to <path>.
export function routePath(pathname) {
  let p = pathname || "/";
  if (p === ROUTE_PREFIX || p.startsWith(ROUTE_PREFIX + "/")) {
    p = p.slice(ROUTE_PREFIX.length) || "/";
  }
  if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
  return p || "/";
}

// ------------------------------------------------------------------ state --

export function defaultState(env) {
  const mode = (env && env.DEFAULT_MODE === "armed") ? "armed" : "alert";
  return {
    mode,
    writes_enabled: true,
    tripped_at: null,
    trip_reason: null,
    episode: { active: false, started_at: null, peak_rate_1h: 0, issue_url: null },
    would_have_tripped_count: 0,
    last_would_trip_at: null,
    last_tick: null,
    last_ok_tick: null,
    rate_1h: null,
    pace_15m: null,
    verdict: "ok",
    consecutive_over: 0,
    consecutive_errors: 0,
    last_error: null,
    gaps_detected: 0,
    // internal bookkeeping (not in the /status contract)
    _calm_ticks: 0,            // consecutive below-warn ticks inside an episode
    _analytics_issue: null,    // open "cannot read R2 analytics" issue url
    _trip_issue: null,         // issue url of the last manual /trip
    _reset_at: null,           // ISO of the last /reset: buckets starting before it are ignored
  };
}

let schemaEnsured = false;

async function ensureSchema(env) {
  if (schemaEnsured) return;
  await env.DB.batch(SCHEMA.map((s) => env.DB.prepare(s)));
  schemaEnsured = true;
}

async function loadState(env) {
  const base = defaultState(env);
  let rows;
  try {
    rows = await env.DB.prepare("SELECT k, v FROM state").all();
  } catch (e) {
    if (!/no such table/i.test(String(e && e.message || e))) throw e;
    await ensureSchema(env);
    rows = await env.DB.prepare("SELECT k, v FROM state").all();
  }
  const snapshot = {};
  for (const r of (rows && rows.results) || []) {
    try {
      base[r.k] = JSON.parse(r.v);
      snapshot[r.k] = r.v;
    } catch { /* ignore a corrupt row; the default stands */ }
  }
  if (!base.episode || typeof base.episode !== "object") base.episode = defaultState(env).episode;
  // remember what D1 holds so saves can be diff-only (a concurrent admin POST
  // must not be clobbered by a tick that loaded state before it landed)
  Object.defineProperty(base, "__snapshot", { value: snapshot, enumerable: false });
  return base;
}

function stateStatements(env, state) {
  const snap = state.__snapshot || {};
  const stmts = [];
  for (const k of Object.keys(state)) {
    const v = JSON.stringify(state[k] === undefined ? null : state[k]);
    if (snap[k] === v) continue;
    stmts.push(env.DB.prepare(
      "INSERT INTO state (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
    ).bind(k, v));
  }
  return stmts;
}

async function saveState(env, state, extra = []) {
  const stmts = stateStatements(env, state).concat(extra);
  if (!stmts.length) return;
  await env.DB.batch(stmts);
}

function eventStatement(env, ts, kind, detail, issueUrl) {
  return env.DB.prepare(
    "INSERT INTO events (ts, kind, detail, issue_url) VALUES (?, ?, ?, ?)",
  ).bind(ts, kind, detail == null ? null : String(detail).slice(0, 2000), issueUrl || null);
}

// ----------------------------------------------------------------- github --

function ghBase(env) { return env.GH_BASE || GH_DEFAULT; }

function ghHeaders(env) {
  return {
    authorization: `Bearer ${env.GITHUB_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": UA,
    "content-type": "application/json",
  };
}

let labelEnsured = false;

async function ensureLabel(env, repo) {
  if (labelEnsured) return;
  try {
    const r = await fetch(`${ghBase(env)}/repos/${repo}/labels`, {
      method: "POST",
      headers: ghHeaders(env),
      body: JSON.stringify({ name: LABEL, color: LABEL_COLOR,
        description: "opened by the R2 circuit breaker Worker" }),
    });
    if (r.ok || r.status === 422) labelEnsured = true;   // 422 = exists
  } catch { /* non-fatal */ }
}

// Opens an issue; returns its html_url or null. Never throws: alerting
// must not fail a tick (the tick's own state is the primary record).
export async function ghIssue(env, title, body) {
  if (!env.GITHUB_TOKEN) {
    console.log(`[r2-breaker] GITHUB_TOKEN unset; not opening issue: ${title}`);
    return null;
  }
  const repo = env.REPO || "WeathermanAAA/Triple-A-Tropics";
  try {
    await ensureLabel(env, repo);
    const r = await fetch(`${ghBase(env)}/repos/${repo}/issues`, {
      method: "POST",
      headers: ghHeaders(env),
      body: JSON.stringify({ title, body, labels: [LABEL] }),
    });
    if (!r.ok) {
      console.log(`[r2-breaker] GitHub refused the issue (${r.status}): ${title}`);
      return null;
    }
    const issue = await r.json();
    return issue && issue.html_url ? issue.html_url : null;
  } catch (e) {
    console.log(`[r2-breaker] GitHub issue failed: ${e && e.message || e}`);
    return null;
  }
}

// Appends a comment to an issue identified by its html_url. Returns bool.
export async function ghComment(env, issueUrl, body) {
  if (!env.GITHUB_TOKEN || !issueUrl) return false;
  const m = /\/issues\/(\d+)\/?$/.exec(issueUrl);
  if (!m) return false;
  const repo = env.REPO || "WeathermanAAA/Triple-A-Tropics";
  try {
    const r = await fetch(`${ghBase(env)}/repos/${repo}/issues/${m[1]}/comments`, {
      method: "POST",
      headers: ghHeaders(env),
      body: JSON.stringify({ body }),
    });
    return r.ok;
  } catch (e) {
    console.log(`[r2-breaker] GitHub comment failed: ${e && e.message || e}`);
    return false;
  }
}

// -------------------------------------------------------------- analytics --

export function gqlQuery(env, nowMs) {
  const acct = env.ACCOUNT_TAG || "";
  const geq = iso(nowMs - LOOKBACK_MS);
  const lt = iso(nowMs);
  const types = JSON.stringify(CLASS_A);
  return `{ viewer { accounts(filter:{accountTag:"${acct}"}) { ` +
    `r2OperationsAdaptiveGroups(limit:100, filter:{datetime_geq:"${geq}", ` +
    `datetime_lt:"${lt}", actionStatus:"success", actionType_in:${types}}, ` +
    `orderBy:[datetimeFiveMinutes_ASC]) { sum { requests } ` +
    `dimensions { datetimeFiveMinutes } } } } }`;
}

// rows: [{sum:{requests}, dimensions:{datetimeFiveMinutes}}] as returned by
// GraphQL. Merges rows sharing a bucket start, drops the partial newest
// bucket (start + 5 min > now), sums the newest complete buckets. Missing
// buckets count as 0 (never invent load). sinceMs (the last /reset) is a
// floor: complete buckets that START before it are dropped as well, so a
// reset is judged on post-reset load only (see the header). `complete` is
// the raw count BEFORE the floor: zero means the dataset returned nothing.
export function summarizeBuckets(rows, nowMs, sinceMs = 0) {
  const byStart = new Map();
  for (const r of rows || []) {
    const d = r && r.dimensions && r.dimensions.datetimeFiveMinutes;
    const t = Date.parse(d);
    if (!Number.isFinite(t)) continue;
    const n = Number(r.sum && r.sum.requests) || 0;
    byStart.set(t, (byStart.get(t) || 0) + n);
  }
  const complete = [...byStart.keys()]
    .filter((t) => t + BUCKET_MS <= nowMs)
    .sort((a, b) => b - a);
  const counted = sinceMs > 0 ? complete.filter((t) => t >= sinceMs) : complete;
  const newest = counted.slice(0, RATE_BUCKETS);
  const rate_1h = newest.reduce((s, t) => s + byStart.get(t), 0);
  const pace_15m = 4 * counted.slice(0, PACE_BUCKETS).reduce((s, t) => s + byStart.get(t), 0);
  return {
    rate_1h,
    pace_15m,
    buckets: newest.length,
    complete: complete.length,
    dropped_partial: byStart.size - complete.length,
    dropped_before_reset: complete.length - counted.length,
    newest_start: newest.length ? iso(newest[0]) : null,
  };
}

export async function readClassA(env, nowMs, sinceMs = 0) {
  if (!env.CF_GRAPHQL_TOKEN) throw new Error("CF_GRAPHQL_TOKEN unset");
  let r;
  try {
    r = await fetch(GQL_ENDPOINT, {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.CF_GRAPHQL_TOKEN}`,
        "content-type": "application/json",
        "user-agent": UA,
      },
      body: JSON.stringify({ query: gqlQuery(env, nowMs) }),
    });
  } catch (e) {
    throw new Error(`graphql network: ${e && e.message || e}`);
  }
  if (!r.ok) throw new Error(`graphql http ${r.status}`);
  let doc;
  try { doc = await r.json(); } catch { throw new Error("graphql: bad JSON"); }
  if (doc && Array.isArray(doc.errors) && doc.errors.length) {
    const msg = doc.errors.map((e) => e && e.message).filter(Boolean).join("; ");
    throw new Error(`graphql errors: ${msg.slice(0, 300) || "unspecified"}`);
  }
  const accounts = doc && doc.data && doc.data.viewer && doc.data.viewer.accounts;
  const rows = Array.isArray(accounts) && accounts[0] && accounts[0].r2OperationsAdaptiveGroups;
  if (!Array.isArray(rows)) throw new Error("graphql: missing data");
  const s = summarizeBuckets(rows, nowMs, sinceMs);
  // Success-on-empty is not success: a 200 with no complete bucket in a
  // two-hour window is the analytics pipeline lagging (this account never
  // idles), and reporting it as 0/h would reset consecutive_over and
  // advance last_ok_tick in the middle of a storm. Fail open instead.
  if (s.complete === 0) {
    throw new Error(`graphql: no complete buckets in the last ${LOOKBACK_MS / 60000} min (empty result)`);
  }
  return s;
}

export function verdictFor(rate_1h, pace_15m, th) {
  if (rate_1h >= th.trip || pace_15m >= 2 * th.trip) return "over";
  if (rate_1h >= th.warn) return "warn";
  return "ok";
}

// ------------------------------------------------------------------- tick --

function fmtNum(n) { return n == null ? "n/a" : Number(n).toLocaleString("en-US"); }

function issueFooter(env, th) {
  return [
    "",
    "---",
    `Thresholds: warn ${fmtNum(th.warn)}/h, trip ${fmtNum(th.trip)}/h ` +
    `(fast path: 15-min pace >= ${fmtNum(2 * th.trip)}/h), ` +
    `sustained for ${TRIP_TICKS} consecutive ${TICK_INTERVAL_S / 60}-min ticks.`,
    `Bucket: ${env.BUCKET || "(account-wide)"}. Status: ` +
    "https://triple-a-tropics.com/r2-breaker/status ; fleet page: " +
    "https://triple-a-tropics.com/fleet/",
    "Operator: `scripts/fleet.sh breaker status|arm|disarm|trip|reset` " +
    "(tsr). Manual re-arm only: POST /reset is the ONLY path back to writing.",
  ].join("\n");
}

// One breaker tick. nowMs is injectable for tests; production passes
// Date.now(). Returns the persisted state (contract fields + internals).
export async function tick(env, nowMs = Date.now()) {
  const th = thresholds(env);
  const state = await loadState(env);
  const nowIso = iso(nowMs);
  const extra = [];

  // 4. heartbeat + self-gap (the previous tick's stamp is the reference)
  const prevTick = state.last_tick ? Date.parse(state.last_tick) : NaN;
  if (Number.isFinite(prevTick) && nowMs - prevTick > GAP_MS) {
    const mins = Math.round((nowMs - prevTick) / 60000);
    state.gaps_detected += 1;
    const url = await ghIssue(env,
      `[r2-breaker] heartbeat gap: ${mins} min with no tick`,
      [
        `The breaker cron did not run between ${state.last_tick} and ${nowIso} ` +
        `(${mins} min; expected every ${TICK_INTERVAL_S / 60} min).`,
        "",
        "During the gap R2 writes were unmonitored. If this recurs, check the " +
        "Cloudflare Workers dashboard (cron trigger, free-plan request cap) and " +
        "the breaker-liveness GitHub workflow, which is the external leg of " +
        "this alarm.",
        `Gaps detected so far: ${state.gaps_detected}.`,
        issueFooter(env, th),
      ].join("\n"));
    extra.push(eventStatement(env, nowIso, "gap", `${mins} min without a tick`, url));
  }
  state.last_tick = nowIso;

  // 1. analytics read (post-reset floor: only buckets starting at/after the
  // last /reset count; the floor expires once it leaves the query window)
  let sinceMs = state._reset_at ? Date.parse(state._reset_at) : NaN;
  if (!Number.isFinite(sinceMs) || nowMs - sinceMs > LOOKBACK_MS) {
    sinceMs = 0;
    state._reset_at = null;
  }
  const t0 = Date.now();
  let reading = null;
  let err = null;
  try {
    reading = await readClassA(env, nowMs, sinceMs);
  } catch (e) {
    err = String(e && e.message || e).slice(0, 500);
  }
  const latency = Math.max(0, Date.now() - t0);

  if (err) {
    // 5. fail-open: no writes_enabled change, no consecutive_over reset
    state.consecutive_errors += 1;
    state.last_error = err;
    state.verdict = "error";
    state.rate_1h = null;
    state.pace_15m = null;
    if (state.consecutive_errors === ERROR_ISSUE_AT) {
      const url = await ghIssue(env,
        "[r2-breaker] cannot read R2 analytics (15 min)",
        [
          `${ERROR_ISSUE_AT} consecutive ticks failed to read r2OperationsAdaptiveGroups.`,
          `Last error: ${err}`,
          "",
          "The breaker is FAIL-OPEN: writes_enabled is unchanged " +
          `(currently ${state.writes_enabled}) and R2 writes are unmonitored ` +
          "until the read recovers. Check the CF_GRAPHQL_TOKEN secret " +
          "(Account Analytics:Read) and the GraphQL API status.",
          issueFooter(env, th),
        ].join("\n"));
      state._analytics_issue = url;
      extra.push(eventStatement(env, nowIso, "analytics_error", err, url));
    }
  } else {
    if (state.consecutive_errors >= ERROR_ISSUE_AT) {
      const note = `Analytics read recovered at ${nowIso} after ` +
        `${state.consecutive_errors} failed ticks (last error: ${state.last_error}).`;
      if (state._analytics_issue) await ghComment(env, state._analytics_issue, note);
      extra.push(eventStatement(env, nowIso, "analytics_recovered", note, state._analytics_issue));
      state._analytics_issue = null;
    }
    state.consecutive_errors = 0;
    state.last_error = null;
    state.last_ok_tick = nowIso;
    state.rate_1h = reading.rate_1h;
    state.pace_15m = reading.pace_15m;

    // 2. verdict
    const verdict = verdictFor(reading.rate_1h, reading.pace_15m, th);
    state.verdict = verdict;
    state.consecutive_over = verdict === "over" ? state.consecutive_over + 1 : 0;

    // 3. trip rule -> episode open (one issue per episode)
    const armed = state.mode === "armed";
    const numbers = `${fmtNum(reading.rate_1h)}/h (pace ${fmtNum(reading.pace_15m)}/h) at ${nowIso}`;
    const reason = `rate ${fmtNum(reading.rate_1h)}/h, pace ${fmtNum(reading.pace_15m)}/h ` +
      `over ${state.consecutive_over} ticks`;
    if (state.consecutive_over >= TRIP_TICKS && !state.episode.active) {
      state.would_have_tripped_count += 1;
      state.last_would_trip_at = nowIso;
      state.episode = {
        active: true, started_at: nowIso, peak_rate_1h: reading.rate_1h, issue_url: null,
      };
      state._calm_ticks = 0;
      if (armed) {
        state.writes_enabled = false;
        state.tripped_at = nowIso;
        state.trip_reason = reason;
      }
      const url = await ghIssue(env,
        armed
          ? `[r2-breaker] TRIPPED — R2 writes STOPPED: ${numbers}`
          : `[r2-breaker] WOULD HAVE TRIPPED: ${numbers}`,
        [
          `R2 Class-A operations: rate_1h ${fmtNum(reading.rate_1h)}/h, ` +
          `pace_15m ${fmtNum(reading.pace_15m)}/h, over threshold for ` +
          `${state.consecutive_over} consecutive ticks (>= ${TRIP_TICKS * TICK_INTERVAL_S / 60} min).`,
          `Normal is ~29,000/h; the 2026-08-03 incident ran ~445,000/h.`,
          "",
          armed
            ? "Mode ARMED: writes_enabled is now FALSE. Every guarded writer on " +
              "box1 and box2 (emit lanes, s1 ingest, meso/floater/intensity/" +
              "guidance pollers, overlays pollers) drops its R2 puts within " +
              "~2 min of the heartbeat mirror picking this up. Serving, prune " +
              "(deletes) and the fleet heartbeat continue. Nothing resumes " +
              "until an operator POSTs /reset."
            : "Mode ALERT-ONLY: nothing was stopped. Had the breaker been " +
              "armed it would have set writes_enabled=false and stopped R2 " +
              "writes on both boxes (emit lanes, s1 ingest, meso/floater/" +
              "intensity/guidance pollers, overlays pollers) until /reset.",
          `Would-have-tripped count: ${state.would_have_tripped_count}.`,
          "This issue receives a comment when the episode ends (30 min below warn).",
          issueFooter(env, th),
        ].join("\n"));
      state.episode.issue_url = url;
      extra.push(eventStatement(env, nowIso, armed ? "trip" : "would_trip", reason, url));
    } else if (state.episode.active) {
      if (reading.rate_1h > state.episode.peak_rate_1h) state.episode.peak_rate_1h = reading.rate_1h;
      // Late trip: the breaker was armed while this episode was already
      // open (alert-mode episodes never stop writes). writes_enabled=true
      // inside an armed active episode arises ONLY from that sequence, so a
      // sustained over reading stops writes now, commenting on the existing
      // issue rather than opening a second one.
      if (armed && state.writes_enabled && verdict === "over" && state.consecutive_over >= TRIP_TICKS) {
        state.writes_enabled = false;
        state.tripped_at = nowIso;
        state.trip_reason = `${reason} (armed during active episode)`;
        const note = `TRIPPED at ${nowIso} (armed during this episode): R2 writes STOPPED. ` +
          `rate_1h ${fmtNum(reading.rate_1h)}/h, pace_15m ${fmtNum(reading.pace_15m)}/h, ` +
          `over for ${state.consecutive_over} consecutive ticks. Every guarded writer on ` +
          "box1 and box2 drops its R2 puts within ~2 min of the heartbeat mirror; " +
          "nothing resumes until an operator POSTs /reset.";
        if (state.episode.issue_url) await ghComment(env, state.episode.issue_url, note);
        extra.push(eventStatement(env, nowIso, "trip", state.trip_reason, state.episode.issue_url));
      }
      if (verdict === "ok") {
        state._calm_ticks += 1;
        if (state._calm_ticks >= EPISODE_CALM_TICKS) {
          const started = Date.parse(state.episode.started_at);
          const mins = Number.isFinite(started) ? Math.round((nowMs - started) / 60000) : null;
          const note = `Episode ended at ${nowIso}: peak rate_1h ` +
            `${fmtNum(state.episode.peak_rate_1h)}/h, duration ` +
            `${mins == null ? "n/a" : mins + " min"}, ` +
            `${EPISODE_CALM_TICKS} consecutive ticks below warn.` +
            (state.writes_enabled ? "" :
              " writes_enabled is STILL FALSE: POST /reset when the cause is understood.");
          if (state.episode.issue_url) await ghComment(env, state.episode.issue_url, note);
          extra.push(eventStatement(env, nowIso, "episode_close", note, state.episode.issue_url));
          state.episode = { ...state.episode, active: false };
          state._calm_ticks = 0;
        }
      } else {
        state._calm_ticks = 0;
      }
    }
  }

  // 6. persist: state diff + tick row + retention
  extra.push(env.DB.prepare(
    "INSERT OR REPLACE INTO ticks (ts, rate_1h, pace_15m, verdict, mode, writes_enabled, error, latency_ms) " +
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  ).bind(nowIso, state.rate_1h, state.pace_15m, state.verdict, state.mode,
    state.writes_enabled ? 1 : 0, err, latency));
  extra.push(env.DB.prepare("DELETE FROM ticks WHERE ts < ?").bind(iso(nowMs - TICK_RETENTION_MS)));
  await saveState(env, state, extra);
  return state;
}

// ----------------------------------------------------------------- status --

export function statusDoc(state, env, nowMs = Date.now()) {
  const th = thresholds(env);
  const ep = state.episode || {};
  const lt = state.last_tick ? Date.parse(state.last_tick) : NaN;
  // key order = the contract's order in PHASE0_SPEC (humans read this raw)
  return {
    v: CONTRACT_VERSION,
    mode: state.mode,
    writes_enabled: state.writes_enabled,
    tripped_at: state.tripped_at,
    trip_reason: state.trip_reason,
    episode: {
      active: !!ep.active,
      started_at: ep.started_at || null,
      peak_rate_1h: ep.peak_rate_1h | 0,
      issue_url: ep.issue_url || null,
    },
    would_have_tripped_count: state.would_have_tripped_count,
    last_would_trip_at: state.last_would_trip_at,
    last_tick: state.last_tick,
    last_ok_tick: state.last_ok_tick,
    heartbeat_age_s: Number.isFinite(lt) ? Math.max(0, Math.floor((nowMs - lt) / 1000)) : null,
    tick_interval_s: TICK_INTERVAL_S,
    rate_1h: state.rate_1h,
    pace_15m: state.pace_15m,
    warn_hourly: th.warn,
    trip_hourly: th.trip,
    verdict: state.verdict,
    consecutive_over: state.consecutive_over,
    consecutive_errors: state.consecutive_errors,
    last_error: state.last_error,
    gaps_detected: state.gaps_detected,
    worker_ts: iso(nowMs),
  };
}

// ------------------------------------------------------------------ admin --

async function adminVerb(request, env, verb) {
  if (!env.ADMIN_KEY) return json({ error: "ADMIN_KEY not configured" }, 503);
  const key = request.headers.get("x-admin-key") || "";
  if (!safeEqual(key, env.ADMIN_KEY)) return json({ error: "forbidden" }, 403);

  const nowMs = Date.now();
  const nowIso = iso(nowMs);
  const th = thresholds(env);
  const state = await loadState(env);
  let detail;
  let target = null;          // issue to comment on, if any
  let kind = verb;
  const events = [];          // extra event rows (an /arm that trips now)

  if (verb === "arm") {
    detail = state.mode === "armed" ? "already armed" : "mode alert -> armed";
    state.mode = "armed";
    target = state.episode.active ? state.episode.issue_url : null;
    if (state.episode.active && state.writes_enabled) {
      // The operator is arming IN RESPONSE to a live episode. Stop writes
      // now when the latest reading is a fresh, sustained over (consecutive
      // _over >= TRIP_TICKS implies the last successful read was "over");
      // otherwise say loudly that writes are still running and what stops
      // them. Never trip on a stale or errored reading (fail-open).
      const okAge = state.last_ok_tick ? nowMs - Date.parse(state.last_ok_tick) : NaN;
      const fresh = Number.isFinite(okAge) && okAge <= ARM_FRESH_MS;
      if (fresh && state.verdict === "over" && state.consecutive_over >= TRIP_TICKS) {
        state.writes_enabled = false;
        state.tripped_at = nowIso;
        state.trip_reason = `armed during active episode: rate ${fmtNum(state.rate_1h)}/h, ` +
          `pace ${fmtNum(state.pace_15m)}/h over ${state.consecutive_over} ticks`;
        detail += `; episode active since ${state.episode.started_at}: TRIPPED NOW, ` +
          "R2 writes STOPPED (POST /reset re-enables)";
        events.push(eventStatement(env, nowIso, "trip", state.trip_reason, target));
      } else {
        detail += `; episode active since ${state.episode.started_at} but R2 writes remain ` +
          `ENABLED (last reading ${state.verdict}${fresh ? "" : ", stale"}): the next ` +
          "sustained over tick stops them, or POST /trip to stop them now";
      }
    }
  } else if (verb === "disarm") {
    detail = state.mode === "alert" ? "already alert-only" : "mode armed -> alert";
    state.mode = "alert";
    target = state.episode.active ? state.episode.issue_url : null;
    // note: does NOT re-enable writes; /reset is the only path back
  } else if (verb === "trip") {
    kind = "manual_trip";
    detail = state.writes_enabled ? "writes stopped by admin" : "already stopped; reason refreshed";
    state.writes_enabled = false;
    state.trip_reason = "manual";
    state.tripped_at = nowIso;
    target = state.episode.active ? state.episode.issue_url : null;
  } else if (verb === "reset") {
    const wasEnabled = state.writes_enabled;
    const ep = state.episode;
    detail = (wasEnabled ? "writes were already enabled" : "writes re-enabled") +
      (ep.active ? `; episode closed (peak ${fmtNum(ep.peak_rate_1h)}/h)` : "");
    state.writes_enabled = true;
    state.tripped_at = null;
    state.trip_reason = null;
    target = ep.active ? ep.issue_url : (state._trip_issue || null);
    if (ep.active) state.episode = { ...ep, active: false };
    state._calm_ticks = 0;
    state._trip_issue = null;
    // A reset is a fresh start: the over streak ends here and, from the
    // next tick, only buckets that start at/after this moment are counted
    // (the trailing hour still holds the storm the trip stopped, and would
    // otherwise re-trip an armed breaker within 5 min of every reset).
    state.consecutive_over = 0;
    state._reset_at = nowIso;
    detail += "; counting only R2 ops after this moment for the next hour";
  } else {
    return json({ error: "not found" }, 404);
  }

  const body = [
    `Admin action **${verb}** at ${nowIso}: ${detail}.`,
    `State now: mode=${state.mode}, writes_enabled=${state.writes_enabled}` +
    (state.trip_reason ? `, trip_reason=${state.trip_reason}` : "") +
    `, rate_1h=${fmtNum(state.rate_1h)}, verdict=${state.verdict}.`,
  ].join("\n");
  let url = null;
  if (target) {
    url = (await ghComment(env, target, body)) ? target : null;
  } else {
    const title = {
      arm: `[r2-breaker] ARMED by admin at ${nowIso}`,
      disarm: `[r2-breaker] DISARMED (alert-only) by admin at ${nowIso}`,
      trip: `[r2-breaker] TRIPPED manually — R2 writes STOPPED at ${nowIso}`,
      reset: `[r2-breaker] RESET: R2 writes re-enabled at ${nowIso}`,
    }[verb];
    url = await ghIssue(env, title, body + issueFooter(env, th));
    if (verb === "trip" && url) state._trip_issue = url;
  }
  await saveState(env, state, [eventStatement(env, nowIso, kind, detail, url)].concat(events));
  return json({ ok: true, action: verb, detail, issue_url: url, status: statusDoc(state, env, nowMs) });
}

// ------------------------------------------------------------------- http --

function clampN(url, dflt, max) {
  const n = parseInt(url.searchParams.get("n"), 10);
  if (!Number.isFinite(n) || n < 1) return dflt;
  return Math.min(n, max);
}

async function handleFetch(request, env) {
  const url = new URL(request.url);
  const path = routePath(url.pathname);
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  if (path === "/status") {
    if (method !== "GET" && method !== "HEAD") return json({ error: "method not allowed" }, 405);
    const state = await loadState(env);
    return json(statusDoc(state, env, Date.now()));
  }
  if (path === "/ticks") {
    if (method !== "GET") return json({ error: "method not allowed" }, 405);
    const n = clampN(url, 48, MAX_TICKS);
    const r = await env.DB.prepare(
      "SELECT ts, rate_1h, pace_15m, verdict, mode, writes_enabled, error, latency_ms " +
      "FROM ticks ORDER BY ts DESC LIMIT ?",
    ).bind(n).all();
    const ticks = ((r && r.results) || []).map((t) => ({ ...t, writes_enabled: !!t.writes_enabled }));
    return json({ v: CONTRACT_VERSION, n: ticks.length, ticks });
  }
  if (path === "/events") {
    if (method !== "GET") return json({ error: "method not allowed" }, 405);
    const n = clampN(url, 50, MAX_EVENTS);
    const r = await env.DB.prepare(
      "SELECT id, ts, kind, detail, issue_url FROM events ORDER BY id DESC LIMIT ?",
    ).bind(n).all();
    const events = (r && r.results) || [];
    return json({ v: CONTRACT_VERSION, n: events.length, events });
  }
  if (path === "/arm" || path === "/disarm" || path === "/trip" || path === "/reset") {
    if (method !== "POST") return json({ error: "method not allowed" }, 405);
    return adminVerb(request, env, path.slice(1));
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    try {
      return await handleFetch(request, env);
    } catch (e) {
      console.log(`[r2-breaker] fetch failed: ${e && e.stack || e}`);
      return json({ error: "internal", detail: String(e && e.message || e).slice(0, 300) }, 500);
    }
  },
  async scheduled(event, env, ctx) {
    // Date.now(), not event.scheduledTime: a delayed cron must judge bucket
    // completeness against the real clock or it mis-drops the newest bucket.
    const run = tick(env, Date.now()).catch((e) => {
      console.log(`[r2-breaker] tick failed: ${e && e.stack || e}`);
    });
    if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(run);
    await run;
  },
};
