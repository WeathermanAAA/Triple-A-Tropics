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
 * WHY THE RELAY: the codespace's Cloudflare token (tat-codespace-deploy,
 * id 3b5eb373...) deploys workers AND manages zone routes, but cannot
 * list/create/bind KV, D1, or R2 (re-verified 2026-08-03 against the list
 * endpoints: code 10000 on all three, exact errors in AGENT_STATUS), and
 * the local AWS keys are real AWS, not R2. The dispatch hop uses only
 * credentials that already exist. If the EXISTING "Triple-a-Weather" token
 * (Account.D1 + more, all accounts) is ever added to the Codespace, the
 * relay collapses to a direct put - no new token needed.
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
    // The shipped client (models/telemetry.js flush()) sends
    // {page, day, products: {slug: {views, dwell_s}}}; accept that shape
    // directly and normalise to rows.
    const prods = (batch && batch.products && typeof batch.products === "object")
      ? batch.products : {};
    const clean = [];
    for (const k of Object.keys(prods).slice(0, MAX_ROWS)) {
      if (!SLUG.test(k)) continue;
      const b = prods[k] || {};
      const v = Math.min(Math.max(0, b.views | 0), 500);
      const d = Math.min(Math.max(0, b.dwell_s | 0), 36000);
      if (v || d) clean.push({ p: k, v, d });
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
