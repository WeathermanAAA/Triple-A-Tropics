/**
 * cyclolab-router — Cloudflare Worker (VENDORED; the deployed copy must
 * match this file — see workers/README.md for deploy steps).
 *
 * Maps  triple-a-tropics.com/cyclolab/*  ->  R2 bucket objects under the
 * `cyclolab/` prefix, so the per-storm pages the intensity poller writes
 * to R2 serve as real documents (HTTP 200, text/html, real per-storm OG
 * tags) on the site's own host. CYCLOLAB_DESIGN.md §3.1.
 *
 * Path mapping:
 *   /cyclolab/                  -> cyclolab/index.html        (lab index)
 *   /cyclolab/{sid}/            -> cyclolab/{sid}/index.html
 *   /cyclolab/{sid}             -> 301 -> /cyclolab/{sid}/    (canonical)
 *   /cyclolab/adv/{sid}.json    -> cyclolab/adv/{sid}.json    (hydration)
 *   anything missing            -> branded 404 page (text/html, NOT the
 *                                  R2 text/plain default; no SPA rewrite
 *                                  by design - dead links must look dead)
 *
 * Bindings (wrangler.toml): r2_buckets: BUCKET -> the media bucket that
 * cdn.triple-a-tropics.com fronts. No secrets, GET/HEAD only.
 */

/** Resolve a request path to an R2 object key, or a redirect. Pure. */
export function resolve(pathname) {
  if (!pathname.startsWith("/cyclolab")) return { kind: "notfound" };
  let p = pathname.slice("/cyclolab".length); // "", "/", "/{sid}", ...
  if (p === "" || p === "/") return { kind: "key", key: "cyclolab/index.html" };
  if (p.endsWith("/")) return { kind: "key", key: "cyclolab" + p + "index.html" };
  // File-ish (has an extension in the last segment): serve as-is.
  const last = p.slice(p.lastIndexOf("/") + 1);
  if (last.includes(".")) return { kind: "key", key: "cyclolab" + p };
  // Extensionless directory form: canonicalize to trailing slash so
  // relative asset URLs inside the page resolve correctly.
  return { kind: "redirect", to: "/cyclolab" + p + "/" };
}

const NOT_FOUND_HTML = `<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CycloLab · not found</title>
<style>body{background:#0b0e13;color:#c8d4e7;font-family:"Metropolis","Helvetica Neue",Arial,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;text-align:center}
a{color:#5dd3ff}</style></head><body><div>
<h1>No lab here</h1>
<p>This storm page doesn't exist (or its season has been archived).</p>
<p><a href="/">Back to Triple-A-Tropics</a></p>
</div></body></html>`;

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("method not allowed", { status: 405 });
    }
    const url = new URL(request.url);
    const r = resolve(url.pathname);
    if (r.kind === "redirect") {
      return Response.redirect(url.origin + r.to + url.search, 301);
    }
    if (r.kind === "key") {
      const obj = await env.BUCKET.get(r.key);
      if (obj) {
        const headers = new Headers();
        // Serve the object's stored metadata verbatim (the poller PUTs
        // text/html + its own cache-control; JSON likewise).
        obj.writeHttpMetadata(headers);
        headers.set("etag", obj.httpEtag);
        if (!headers.has("cache-control")) {
          headers.set("cache-control", "public, max-age=30");
        }
        return new Response(request.method === "HEAD" ? null : obj.body,
                            { status: 200, headers });
      }
    }
    return new Response(request.method === "HEAD" ? null : NOT_FOUND_HTML, {
      status: 404,
      headers: { "content-type": "text/html; charset=utf-8",
                 "cache-control": "public, max-age=30" },
    });
  },
};
