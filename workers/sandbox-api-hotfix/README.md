# tat-sandbox-api hotfix: native base64 on the checkpoint write path (2026-08-26)

The sandbox accounts API (`tat-sandbox-api`, Workers custom domain
`api.triple-a-tropics.com`) is built and deployed by Andrew from a source tree
that is not in this repo; only the built module is reachable from here
(`GET /accounts/{acct}/workers/scripts/tat-sandbox-api`). This directory
vendors the ONE change applied to that built module, as a unified diff, plus
the benchmark that justified it, so the change is auditable and re-applicable
to the next build of the real source.

## Why

Cost audit 2026-08-25/26: `PUT /api/checkpoint/<key>` and `POST /api/season`
were being terminated at exactly 50 ms CPU (`exceededCpu`) on 6-55% of
requests, before and after the Workers Paid upgrade. The hash was already
`crypto.subtle.digest` and the inflate already `DecompressionStream`; the
hand-written JS on the path was `decodeBase64`:

    Uint8Array.from(atob(value), (c) => c.charCodeAt(0))

one JS callback per byte of a ~1 MB checkpoint, plus a redundant
`new TextEncoder().encode(text)` of the whole request body just to measure it.

Measured with `bench.mjs` (node 24, the exact functions extracted from both
modules, synthetic checkpoints shaped for `validateCheckpointBundle`):

| gzip checkpoint | original write-path CPU | patched | of which decodeBase64 | residual inflate+JSON.parse |
| --- | ---: | ---: | ---: | ---: |
| 0.57 MB (2.1 MB JSON) | 132 ms | 61 ms | 76 -> 12 ms | 46 ms |
| 1.14 MB (4.2 MB JSON) | 210 ms | 132 ms | 113 -> 25 ms | ~100 ms |

Decoded bytes and re-encoded base64 are byte-identical to the original.
The residual is `JSON.parse` of the whole inflated checkpoint (native, but
proportional to size) inside `inflateCheckpoint` -> `validateCheckpointBundle`;
that is the validation design, not this patch's scope.

## What the patch does

- `decodeBase64`: `Uint8Array.fromBase64(value)` when the runtime has it, else
  `atob` + a tight index loop (no per-byte callback). The alphabet/length regex
  guard is unchanged, so accepted inputs are unchanged.
- `encodeBase64` (read path): `bytes.toBase64()` when available, else the
  original chunked `String.fromCharCode` + `btoa`.
- `requestJson`: byte length from `req.arrayBuffer()` instead of re-encoding
  the decoded text; same UTF-8 decode as `req.text()`.

## How it was deployed / how to redo or roll back

Deploy: `bash workers/sandbox-api-hotfix/deploy.sh` (fetches the current module, applies the patch, `wrangler deploy` with `sandbox.toml`, verifies). DEPLOYED 2026-08-26 20:18:50Z (version a0b94320) on Andrew's instruction, together with an explicit `[limits] cpu_ms = 30000` in `sandbox.toml` (the Worker was being terminated at exactly 50 ms on a Paid plan whose ceiling is 30 s; no `[limits]` block existed). Verified after deploy: five bindings intact incl. `CLERK_SECRET_KEY`, custom domain attached, `/api/health` 200, the version runtime reports `limits: {cpu_ms: 30000}`. First 180 s tail: 29/29 `ok`, 0 `exceededCpu`; `PUT /api/checkpoint` median 24.5 ms CPU (was 129) with a 153 ms request completing. Originally attempted with the Workers script API (multipart `metadata` + `worker.js`),
explicit bindings (`CLERK_AUTHORIZED_PARTY`, `CLERK_ISSUER`, `DEV_AUTH`, D1
`DB`) and `keep_bindings: ["secret_text"]` so `CLERK_SECRET_KEY` survives;
bindings verified via the settings endpoint after upload. Rollback: deploy the
previous version (6250f19f-5b8e-4be3-952a-8de1234e9d90, 2026-08-19) with
`POST .../workers/scripts/tat-sandbox-api/deployments`, or re-upload the
unpatched module the same way. To carry the change into Andrew's source:
apply the three hunks of `native-base64.patch` to `worker.mjs`.
