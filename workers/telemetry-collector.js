/*
 * Telemetry collector — spec item #3's missing half (the server side).
 *
 * The models page counts per-product views/dwell in each visitor's browser
 * (models/telemetry.js, opt-out honoured). This worker receives small
 * anonymous batches and relays each as a GitHub repository_dispatch; the
 * telemetry-ingest workflow (which holds the R2 credentials this worker's
 * token cannot get) drops the payload under telemetry/inbox/{day}/ on R2,
 * and update-guidance compacts the inbox into telemetry/summary.json. No
 * accounts, no cookies, no IPs stored — product tallies and nothing else.
 *
 * WHY THE RELAY: the codespace's Cloudflare token can deploy workers but
 * cannot create/bind KV, D1, or R2 (verified 2026-08-03: auth error 10000
 * on all three), and the local AWS keys are not R2 keys. The dispatch hop
 * uses only credentials that already exist. When Andrew mints an R2-scoped
 * worker credential, the relay collapses to a direct R2 put.
 *
 * Headroom (stated, per the gate): worker requests — free tier 100k/day;
 * repository_dispatch — API limit ~5k/hr (client flush-throttle >=10 min/tab
 * keeps real volume orders below); Actions minutes — FREE, public repo;
 * R2 — one Class A write per batch against the 1M/month free tier (>=30k
 * batches/day before it costs $0.09/day). The binding ceiling today is
 * Actions-run NOISE, not money — revisit at ~500 batches/day.
 */
const MAX_BODY = 8192;
const MAX_ROWS = 64;
const OK_ORIGINS = ["https://triple-a-tropics.com", "https://www.triple-a-tropics.com"];
const SLUG = /^[a-z0-9_]{1,40}$/;

function cors(origin) {
  const o = OK_ORIGINS.includes(origin) ? origin : OK_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": o,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "86400",
  };
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(origin) });
    }
    if (req.method !== "POST") {
      return new Response("collector: POST only", { status: 405 });
    }
    // sendBeacon carries no custom headers; the Origin header is the gate.
    // (Absent Origin = non-browser client: reject.)
    if (!OK_ORIGINS.includes(origin)) {
      return new Response("origin", { status: 403, headers: cors(origin) });
    }
    let text = await req.text();
    if (text.length > MAX_BODY) {
      return new Response("too big", { status: 413, headers: cors(origin) });
    }
    let batch;
    try { batch = JSON.parse(text); } catch (e) {
      return new Response("json", { status: 400, headers: cors(origin) });
    }
    const rows = Array.isArray(batch && batch.rows) ? batch.rows.slice(0, MAX_ROWS) : [];
    const clean = [];
    for (const r of rows) {
      if (!r || !SLUG.test(String(r.p || ""))) continue;
      const v = Math.min(Math.max(0, r.v | 0), 500);
      const d = Math.min(Math.max(0, r.d | 0), 36000);
      if (v || d) clean.push({ p: r.p, v, d });
    }
    if (!clean.length) {
      return new Response("empty", { status: 204, headers: cors(origin) });
    }
    const gh = await fetch(
      `https://api.github.com/repos/${env.REPO}/dispatches`, {
        method: "POST",
        headers: {
          "authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "accept": "application/vnd.github+json",
          "user-agent": "tat-telemetry-collector",
        },
        body: JSON.stringify({
          event_type: "telemetry-batch",
          client_payload: { t: Date.now(), rows: clean },
        }),
      });
    if (gh.status !== 204) {
      return new Response("relay", { status: 502, headers: cors(origin) });
    }
    return new Response(null, { status: 204, headers: cors(origin) });
  },
};
