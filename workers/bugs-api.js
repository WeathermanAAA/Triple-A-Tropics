/* bugs-api.js — GitHub-issues-backed tester bug board API.
 *
 * Serves triple-a-tropics.com/bugs-api/* for the nav-hidden /bugs/ page.
 * Testers submit ANONYMOUSLY (no GitHub account): the Worker holds a
 * server-side GitHub PAT (secret GITHUB_TOKEN, Issues RW on the repo) and
 * files their reports as issues labeled "tester-report" — so closing an
 * issue ("fixes #N" in a commit) crosses the report off the board.
 *
 * Endpoints:
 *   POST  /bugs-api/issues            {tester?, area, severity, title,
 *                                      detail, website(honeypot)}
 *   GET   /bugs-api/issues            -> [{number,title,area,severity,
 *                                      tester,created,state,closed_at,
 *                                      html_url}] newest first
 *   PATCH /bugs-api/issues/{number}   {state: open|closed}, requires
 *                                      x-admin-key header
 *
 * Anti-spam (low-stakes by design): honeypot field plus a per-IP/day +
 * global/day rate limit that uses GitHub itself as the counter — each
 * issue body carries an invisible HMAC(ip) tag, and POST counts the last
 * 24 h of tester-report issues. No passcode, no KV/DO needed.
 *
 * Secrets (wrangler secret put …, see deploy-bugs.sh — NEVER in the repo):
 *   GITHUB_TOKEN     durable classic PAT with repo scope (issues RW)
 *   ADMIN_KEY        admin key for PATCH (and the ratekey HMAC salt)
 */

// GH_BASE env override exists ONLY so local tests can point the worker at a
// mock GitHub (wrangler dev --local); production leaves it unset.
const GH_DEFAULT = "https://api.github.com";
const UA = "tat-bugs-board-worker";
const LABEL = "tester-report";

const AREAS = ["Satellite Explorer", "CycloLab", "Models", "Climatology",
  "SST", "Recon", "Subseasonal", "Other"];
const SEVERITIES = ["blocker", "major", "minor", "nit"];

const PER_IP_PER_DAY = 5;
const GLOBAL_PER_DAY = 30;

const LABEL_COLORS = {
  [LABEL]: "5b6f8f",
  "sev:blocker": "b60205",
  "sev:major": "d93f0b",
  "sev:minor": "fbca04",
  "sev:nit": "c5def5",
};
const AREA_COLOR = "1d4e89";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, x-admin-key",
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}

function ghBase(env) {
  return env.GH_BASE || GH_DEFAULT;
}

function ghHeaders(env) {
  return {
    authorization: `token ${env.GITHUB_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": UA,
    "content-type": "application/json",
  };
}

async function hmacTag(ip, env) {
  // privacy-preserving per-IP tag: HMAC keyed on ADMIN_KEY, 12 hex chars
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(env.ADMIN_KEY || "no-key"),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign(
    "HMAC", key, new TextEncoder().encode(ip || "unknown"));
  return [...new Uint8Array(sig)].slice(0, 6)
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

const ensuredLabels = new Set(); // per-isolate cache; 422s are harmless anyway

async function ensureLabel(env, repo, name, color, description) {
  if (ensuredLabels.has(name)) return;
  const r = await fetch(`${ghBase(env)}/repos/${repo}/labels`, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify({ name, color, description }),
  });
  // 201 created | 422 already exists — both fine; anything else non-fatal
  if (r.ok || r.status === 422) ensuredLabels.add(name);
}

async function recentReports(env, repo) {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const r = await fetch(
    `${ghBase(env)}/repos/${repo}/issues?labels=${LABEL}&state=all&per_page=100` +
    `&since=${since}&sort=created&direction=desc`,
    { headers: ghHeaders(env) });
  if (!r.ok) return null;
  const items = await r.json();
  const cutoff = Date.now() - 24 * 3600 * 1000;
  return items.filter((i) => !i.pull_request &&
    new Date(i.created_at).getTime() >= cutoff);
}

function fieldFromLabels(labels, prefix) {
  const hit = (labels || []).find((l) => (l.name || "").startsWith(prefix));
  return hit ? hit.name.slice(prefix.length) : "";
}

function testerFromBody(body) {
  const m = /\*\*Submitted by:\*\* (.+)/.exec(body || "");
  return m ? m[1].trim() : "anonymous";
}

async function handlePost(request, env, repo) {
  let p;
  try { p = await request.json(); } catch { return json({ error: "bad JSON" }, 400); }

  // honeypot: pretend success so bots don't adapt; file nothing
  if ((p.website || "").trim() !== "") return json({ ok: true });

  const title = (p.title || "").trim();
  const detail = (p.detail || "").trim();
  const area = AREAS.includes(p.area) ? p.area : null;
  const severity = SEVERITIES.includes(p.severity) ? p.severity : null;
  if (title.length < 4 || title.length > 140) {
    return json({ error: "title must be 4-140 characters" }, 400);
  }
  if (detail.length < 10 || detail.length > 6000) {
    return json({ error: "description must be 10-6000 characters" }, 400);
  }
  if (!area) return json({ error: "pick an area" }, 400);
  if (!severity) return json({ error: "pick a severity" }, 400);
  const tester = (p.tester || "").trim().slice(0, 60) || "anonymous";

  // rate limit: GitHub itself is the counter (24 h window)
  const ip = request.headers.get("cf-connecting-ip") || "";
  const tag = await hmacTag(ip, env);
  const recent = await recentReports(env, repo);
  if (recent === null) return json({ error: "GitHub unreachable, try again" }, 502);
  if (recent.length >= GLOBAL_PER_DAY) {
    return json({ error: "the board hit its daily report cap, try tomorrow" }, 429);
  }
  const mine = recent.filter((i) => (i.body || "").includes(`ratekey:${tag}`));
  if (mine.length >= PER_IP_PER_DAY) {
    return json({ error: "daily per-tester limit reached, try tomorrow" }, 429);
  }

  const labels = [LABEL, `sev:${severity}`, `area:${area}`];
  await ensureLabel(env, repo, LABEL, LABEL_COLORS[LABEL], "filed from /bugs/");
  await ensureLabel(env, repo, `sev:${severity}`,
    LABEL_COLORS[`sev:${severity}`] || "cccccc", "tester-reported severity");
  await ensureLabel(env, repo, `area:${area}`, AREA_COLOR, "tester-reported area");

  const now = new Date().toISOString().replace("T", " ").slice(0, 16);
  const body = [
    `**Area:** ${area}`,
    `**Severity:** ${severity}`,
    `**Submitted by:** ${tester}`,
    "",
    detail,
    "",
    "---",
    `_Filed via the tester bug board (/bugs/) at ${now} UTC._`,
    `<!-- ratekey:${tag} -->`,
  ].join("\n");

  const r = await fetch(`${ghBase(env)}/repos/${repo}/issues`, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify({ title, body, labels }),
  });
  if (!r.ok) {
    return json({ error: `GitHub refused the issue (${r.status})` }, 502);
  }
  const issue = await r.json();
  return json({ ok: true, number: issue.number, html_url: issue.html_url });
}

async function handleGet(env, repo) {
  const r = await fetch(
    `${ghBase(env)}/repos/${repo}/issues?labels=${LABEL}&state=all&per_page=100` +
    `&sort=created&direction=desc`,
    { headers: ghHeaders(env) });
  if (!r.ok) return json({ error: `GitHub read failed (${r.status})` }, 502);
  const items = await r.json();
  const shaped = items.filter((i) => !i.pull_request).map((i) => ({
    number: i.number,
    title: i.title,
    area: fieldFromLabels(i.labels, "area:"),
    severity: fieldFromLabels(i.labels, "sev:"),
    tester: testerFromBody(i.body),
    created: i.created_at,
    state: i.state,
    closed_at: i.closed_at,
    html_url: i.html_url,
  }));
  return json(shaped, 200, { "cache-control": "public, max-age=60" });
}

async function handlePatch(request, env, repo, number) {
  if (!env.ADMIN_KEY ||
      (request.headers.get("x-admin-key") || "") !== env.ADMIN_KEY) {
    return json({ error: "bad admin key" }, 401);
  }
  let p;
  try { p = await request.json(); } catch { return json({ error: "bad JSON" }, 400); }
  const state = p.state === "closed" ? "closed" : p.state === "open" ? "open" : null;
  if (!state) return json({ error: "state must be open|closed" }, 400);

  // only tester-report issues are reachable through this API
  const cur = await fetch(`${ghBase(env)}/repos/${repo}/issues/${number}`,
    { headers: ghHeaders(env) });
  if (!cur.ok) return json({ error: `no such issue (${cur.status})` }, 404);
  const issue = await cur.json();
  if (!(issue.labels || []).some((l) => l.name === LABEL)) {
    return json({ error: "not a tester report" }, 403);
  }

  const r = await fetch(`${ghBase(env)}/repos/${repo}/issues/${number}`, {
    method: "PATCH",
    headers: ghHeaders(env),
    body: JSON.stringify(state === "closed"
      ? { state, state_reason: "completed" } : { state }),
  });
  if (!r.ok) return json({ error: `GitHub refused the change (${r.status})` }, 502);
  const updated = await r.json();
  return json({ ok: true, number: updated.number, state: updated.state });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const repo = env.REPO || "WeathermanAAA/Triple-A-Tropics";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (!env.GITHUB_TOKEN) {
      return json({ error: "board backend not configured yet" }, 503);
    }

    const m = /^\/bugs-api\/issues(?:\/(\d+))?$/.exec(url.pathname);
    if (!m) return json({ error: "not found" }, 404);

    if (request.method === "GET" && !m[1]) return handleGet(env, repo);
    if (request.method === "POST" && !m[1]) return handlePost(request, env, repo);
    if (request.method === "PATCH" && m[1]) {
      return handlePatch(request, env, repo, m[1]);
    }
    return json({ error: "method not allowed" }, 405);
  },
};
