/**
 * explorer-gate — Cloudflare Worker (VENDORED; the deployed copy must match
 * this file — see workers/README.md for deploy steps).
 *
 * LINK-ONLY preview gate for the Satellite Explorer (per Andrew 2026-07-08):
 * `triple-a-tropics.com/satellite/explorer*` serves ONLY to requests carrying
 * the secret preview token — either `?k=<token>` (which sets a scoped cookie
 * and redirects to the clean URL) or that cookie. Everything else gets the
 * site's REAL 404 (status + body fetched from the origin's own missing-page
 * response), so to the un-tokened world the path looks nonexistent.
 *
 * The path stays /satellite/explorer/ (the eventual public URL); un-gating
 * for launch = delete this Worker's route (or the Worker). Nothing else to
 * change — the pages themselves are already deployed on Pages.
 *
 * Bindings: PREVIEW_TOKEN (wrangler secret — NOT in this public repo).
 * FAIL-CLOSED: if the secret is unset/empty, every request 404s.
 * Note: the shadow tile/data URLs on cdn.triple-a-tropics.com are NOT gated
 * (unlinked R2 keys); this gates the PAGES.
 */

const COOKIE = "tat_explorer_preview";
const COOKIE_PATH = "/satellite/explorer";
const MAX_AGE = 60 * 60 * 24 * 90; // 90 days; re-arm anytime with ?k=

/**
 * Pure gate decision. Returns one of:
 *   {kind:"grant", location}  — correct ?k=, set cookie + redirect clean
 *   {kind:"pass"}             — valid cookie, serve the page
 *   {kind:"deny"}             — everything else (serve the real 404)
 */
export function decide(url, cookieHeader, token) {
  if (!token) return { kind: "deny" }; // fail closed on missing secret
  const k = url.searchParams.get("k");
  if (k !== null) {
    if (k === token) {
      const clean = new URL(url);
      clean.searchParams.delete("k");
      return { kind: "grant", location: clean.toString() };
    }
    return { kind: "deny" }; // wrong token: identical 404, no oracle
  }
  const ok = (cookieHeader || "")
    .split(/;\s*/)
    .some((c) => c === COOKIE + "=" + token);
  return ok ? { kind: "pass" } : { kind: "deny" };
}

/** The origin's own 404 (GitHub Pages), so the gate is indistinguishable
 *  from a missing page. Cached briefly at the edge. */
async function realNotFound(origin) {
  const miss = await fetch(origin + "/__tat_no_such_page__", {
    headers: { Accept: "text/html" },
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  return new Response(miss.body, {
    status: 404,
    headers: {
      "Content-Type": miss.headers.get("Content-Type") || "text/html; charset=utf-8",
      "X-Robots-Tag": "noindex, nofollow",
      "Cache-Control": "no-store",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("method not allowed", { status: 405 });
    }
    const d = decide(url, request.headers.get("Cookie"), env.PREVIEW_TOKEN);
    if (d.kind === "grant") {
      return new Response(null, {
        status: 302,
        headers: {
          Location: d.location,
          "Set-Cookie":
            COOKIE + "=" + env.PREVIEW_TOKEN +
            "; Path=" + COOKIE_PATH +
            "; Max-Age=" + MAX_AGE +
            "; Secure; HttpOnly; SameSite=Lax",
          "Cache-Control": "no-store",
        },
      });
    }
    if (d.kind === "deny") return realNotFound(url.origin);
    // pass: serve the page from the origin (GitHub Pages), belt-and-braces
    // noindex on top of the in-page meta.
    const resp = await fetch(request);
    const h = new Headers(resp.headers);
    h.set("X-Robots-Tag", "noindex, nofollow");
    h.set("Cache-Control", "no-store"); // gated preview: never edge-cache
    return new Response(resp.body, { status: resp.status, headers: h });
  },
};
