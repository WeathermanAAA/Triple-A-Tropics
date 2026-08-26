// Stage-by-stage CPU benchmark of the exact helper functions from both modules,
// on a synthetic checkpoint bundle shaped like validateCheckpointBundle expects.
import fs from "node:fs"; import zlib from "node:zlib"; import vm from "node:vm";
const MAX_CHECKPOINT_JSON_BYTES = 12e6, MAX_REQUEST_BYTES = 225e4;
function extract(src, names) {
  const out = {};
  for (const n of names) {
    const re = new RegExp(`(?:async )?function ${n}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}\\n`);
    const m = src.match(re); if (!m) throw new Error("no fn " + n); out[n] = m[0];
  }
  return out;
}
function load(file) {
  const src = fs.readFileSync(file, "utf8");
  const fns = extract(src, ["decodeBase64", "encodeBase64", "blobBytes", "sha256Hex", "inflateCheckpoint", "validateCheckpointBundle", "checkpointKey", "requestJson"]);
  const code = `${Object.values(fns).join("\n")}\nreturn {decodeBase64, encodeBase64, sha256Hex, inflateCheckpoint, validateCheckpointBundle, requestJson};`;
  const ctx = { MAX_CHECKPOINT_JSON_BYTES, MAX_REQUEST_BYTES, HttpError: class extends Error {}, atob, btoa, TextEncoder, TextDecoder, Uint8Array, Blob, DecompressionStream, crypto, JSON, Error, Number, ArrayBuffer, Array, String, Math };
  return new Function(...Object.keys(ctx), code)(...Object.values(ctx));
}
// synthetic bundle: grow until gzip reaches the target (real checkpoints: 0.5-1.2 MB gzip)
function bundle(targetGzMB) {
  const seasons = []; let i = 0;
  const b = { v: 1, save: { format: 3, value: { checkpoint: { origin: "account", lineage: "lineage_0123456789" }, settingsModifiedTick: 12, world: [] } }, seasons };
  const names = ["Alberto","Beryl","Chris","Debby","Ernesto","Francine","Gordon","Helene"];
  let gz = 0;
  while (gz < targetGzMB * 1e6) {
    for (let k = 0; k < 25; k++) seasons.push({ format: 3, season: i++, value: { year: 2000 + (i % 30), storms: Array.from({ length: 24 }, (_, s) => ({ name: names[s % 8] + (i % 7), peak: 40 + ((s * 7 + i) % 120), track: Array.from({ length: 40 }, (_, t) => [Math.round(1000 + ((i * 13 + s * 7 + t * 3) % 2000)) / 10, Math.round(500 + ((i * 11 + s * 5 + t * 2) % 1500)) / 10, 25 + ((t * 5 + s) % 100)]) })) } });
    gz = zlib.gzipSync(Buffer.from(JSON.stringify(b)), { level: 6 }).length;
  }
  return b;
}
async function cpu(fn) { const t0 = process.cpuUsage(); const t1 = process.hrtime.bigint(); const r = await fn(); const c = process.cpuUsage(t0); return { ms: Math.round((c.user + c.system) / 1000), wall: Number(process.hrtime.bigint() - t1) / 1e6, r }; }
const orig = load("worker.orig.js"), pat = load("worker.patched.js");
for (const mb of [0.5, 1.1]) {
  const json = JSON.stringify(bundle(mb));
  const gz = zlib.gzipSync(Buffer.from(json), { level: 6 });
  const b64 = Buffer.from(gz).toString("base64");
  const body = JSON.stringify({ baseRevision: 1, baseGeneration: 1, saveName: "x", payloadSize: gz.length, payloadSha256: "", payloadB64: b64, meta: {} });
  console.log(`\n== synthetic checkpoint: json ${(json.length/1e6).toFixed(1)} MB -> gzip ${(gz.length/1e6).toFixed(2)} MB -> b64 ${(b64.length/1e6).toFixed(2)} MB; request body ${(body.length/1e6).toFixed(2)} MB`);
  for (const [name, m] of [["ORIGINAL", orig], ["PATCHED", pat]]) {
    const req = { headers: { get: () => String(Buffer.byteLength(body)) }, text: async () => body, arrayBuffer: async () => Buffer.from(body).buffer.slice(Buffer.from(body).byteOffset, Buffer.from(body).byteOffset + Buffer.byteLength(body)) };
    const st = {};
    st.requestJson = await cpu(() => m.requestJson(req));
    const parsed = st.requestJson.r;
    st.decodeBase64 = await cpu(async () => m.decodeBase64(parsed.payloadB64));
    const bytes = st.decodeBase64.r;
    st.sha256Hex = await cpu(() => m.sha256Hex(bytes));
    st.inflate_parse = await cpu(() => m.inflateCheckpoint(bytes));
    st.validate = await cpu(async () => m.validateCheckpointBundle(st.inflate_parse.r));
    st.encodeBase64_GET = await cpu(async () => m.encodeBase64(bytes));
    const same = Buffer.compare(Buffer.from(bytes), gz) === 0 && st.encodeBase64_GET.r === b64;
    const total = ["requestJson","decodeBase64","sha256Hex","inflate_parse","validate"].reduce((a,k)=>a+st[k].ms,0);
    console.log(`  ${name.padEnd(9)} PUT path CPU ms: requestJson ${st.requestJson.ms}  decodeBase64 ${st.decodeBase64.ms}  sha256 ${st.sha256Hex.ms}  inflate+JSON.parse ${st.inflate_parse.ms}  validate ${st.validate.ms}  | total ${total} ms   (GET encodeBase64 ${st.encodeBase64_GET.ms} ms)  bytes/b64 identical: ${same}`);
  }
}
