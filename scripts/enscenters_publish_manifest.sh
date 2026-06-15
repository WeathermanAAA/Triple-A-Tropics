#!/usr/bin/env bash
# Atomic, concurrency-safe publish of the SHARED Ensemble Cyclone Centers manifest.
#
# All three per-model workflows (update-enscenters / update-aifs-ens / update-gefs)
# call this ONE script so the read -> reconcile -> validate -> write -> prune dance
# is defined in a single place. They each write the SAME
# models/enscenters/manifest.json from a SEPARATE workflow, so the write must:
#   * never DROP another model's entry,
#   * never REGRESS a model's latest cycle,
#   * survive concurrent writes from the sibling workflows.
#
# How it does that:
#   1. Read the live manifest AUTHORITATIVELY from R2 (S3 API, not the flaky CDN),
#      classifying ok | absent | failed. On `failed` we SKIP publishing (exit 0):
#      better a missed cron (the next one self-heals) than a manifest written from a
#      bad read that clobbers siblings.
#   2. reconcile (scripts/enscenters_manifest_guard.py): merge THIS run's freshly
#      built manifest with live, per model -> union cycles, monotone latest, refuse
#      to write if any live model would be dropped or any latest would regress.
#   3. consistency gate: HEAD every cycle the reconciled manifest references; abort
#      before the swap+prune if any is missing (a partial sync must never make the
#      manifest point at a 404).
#   4. CAS: re-read live; if it changed since step 1 (a sibling published while we
#      worked) re-reconcile against the fresh copy. Shrinks the lost-update window
#      to the sub-second gap between the re-read and the cp. A residual loss still
#      self-heals: each model's own next run only advances its entry, never regresses.
#   5. cp the manifest, then prune cycles rolled out of retention -- FILTERED so a
#      prune key whose cycle is still referenced in the final manifest is never
#      deleted (manifest<->object invariant holds even if the built prune list and
#      the reconciled set disagree).
#
# Usage:  scripts/enscenters_publish_manifest.sh <active-slug>
# Env:    R2_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (R2 creds)
set -eo pipefail

SLUG="${1:?usage: enscenters_publish_manifest.sh <active-slug>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUCKET="triple-a-tropics-media"
PREFIX="models/enscenters"
KEY="$PREFIX/manifest.json"
SRC="./$PREFIX"
DST="s3://$BUCKET/$PREFIX"
GUARD="$SCRIPT_DIR/enscenters_manifest_guard.py"   # resolved next to this script (CWD-independent)

# Nothing produced (no complete cycle this run) -> leave live data alone.
if [ ! -f "$SRC/manifest.json" ]; then
  echo "[$SLUG] no manifest produced - cycle incomplete, nothing to publish."
  exit 0
fi

# 1. Per-cycle JSON first (no --delete; re-run re-uploads even at identical size).
aws s3 sync "$SRC/" "$DST/" --endpoint-url "$R2_ENDPOINT" \
  --cache-control "public, max-age=300" --content-type "application/json" \
  --exclude "*" --include "*/*.json" --only-show-errors

# read_live <outfile> -> best-effort fetch of the live manifest for cache-bust
# tokens + default_model. NOT the source of truth (R2 listing is). On absence or
# any error we just write {} and carry on; the listing still drives the manifest.
read_live() {
  local out="$1"
  if aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$BUCKET" \
        --key "$KEY" "$out" >/dev/null 2>&1; then
    return 0
  fi
  echo '{}' > "$out"
}

# list_r2 <centers_out> <tracks_out> -> writes {slug:[cycles]} for the centers JSON
# (the AUTHORITATIVE source of truth for the manifest) AND {slug:[cycles]} for the
# sibling .tracks.json files (so tracks_versions is derived from R2 reality too,
# race-proof). Prints ok|failed; on failure we SKIP the publish.
list_r2() {
  local out="$1" tout="$2" raw
  if ! raw=$(aws s3 ls "s3://$BUCKET/$PREFIX/" --recursive --endpoint-url "$R2_ENDPOINT" 2>&1); then
    printf '[%s] R2 listing error: %s\n' "$SLUG" "$raw" >&2
    echo failed; return 0
  fi
  if ! printf '%s\n' "$raw" | python3 -c '
import json, re, sys
present, tracks = {}, {}
for line in sys.stdin:
    parts = line.split()
    if not parts:
        continue
    key = parts[-1]
    mt = re.search(r"models/enscenters/([^/]+)/(\d{10})\.tracks\.json$", key)
    if mt:
        tracks.setdefault(mt.group(1), []).append(mt.group(2))
        continue
    m = re.search(r"models/enscenters/([^/]+)/(\d{10})\.json$", key)
    if m:
        present.setdefault(m.group(1), []).append(m.group(2))
json.dump(present, open(sys.argv[1], "w"), separators=(",", ":"))
json.dump(tracks, open(sys.argv[2], "w"), separators=(",", ":"))
' "$out" "$tout"; then
    echo failed; return 0
  fi
  echo ok; return 0
}

BUILT=/tmp/ens_built.json          # this run's freshly built manifest (immutable here)
LIVE_A=/tmp/ens_live_a.json
LIVE_B=/tmp/ens_live_b.json
R2_PRESENT=/tmp/ens_r2_present.json
R2_TRACKS=/tmp/ens_r2_tracks.json  # {slug:[cycles with a .tracks.json]} from the listing
FINAL=/tmp/ens_final.json
cp "$SRC/manifest.json" "$BUILT"

published=""
for attempt in 1 2 3 4 5; do
  # 2a. AUTHORITATIVE R2 listing = the truth source for every model's cycle set.
  if [ "$(list_r2 "$R2_PRESENT" "$R2_TRACKS")" = failed ]; then
    echo "[$SLUG] WARN: could not list R2 objects; SKIP publish this run (cron self-heals)."
    exit 0
  fi
  read_live "$LIVE_A"   # best-effort (cache-bust tokens + default_model)

  # 2b. reconcile: DERIVE each model's cycle set + latest from R2 reality (+ this
  #     run's freshly-built cycles). Aborts only on a suspected listing failure or
  #     a would-drop-a-model bug -> skip (prior stays live, self-heals next run).
  cp "$BUILT" "$FINAL"
  if ! python3 "$GUARD" "$FINAL" "$LIVE_A" "$R2_PRESENT" "$R2_TRACKS"; then
    echo "[$SLUG] WARN: guard refused the write (suspected bad listing / empty); SKIP (prior stays live)."
    exit 0
  fi

  # 3. consistency gate: the manifest must never point at a cycle JSON that is not
  #    on R2 (a 404 in the viewer, or worse a prune of good cycles behind a manifest
  #    that references a gone object). HEAD every referenced cycle, RETRYING to
  #    absorb a transient R2 hiccup. For a cycle still missing after retries:
  #      * if it is NOT a model's latest -> drop just that older cycle from the
  #        manifest and keep publishing (no regression; the dropdown loses one old
  #        run, the model's own next run re-plans it if still in window);
  #      * if it IS a model's latest -> do NOT regress it; SKIP this publish (green,
  #        prior stays live, loud log). This converts the old "one missing sibling
  #        object reds/wedges all three workflows" into graceful degradation.
  gate_rc=0
  python3 - "$R2_ENDPOINT" "$BUCKET" "$FINAL" <<'GATE' || gate_rc=$?
import json, subprocess, sys, time
endpoint, bucket, path = sys.argv[1], sys.argv[2], sys.argv[3]
man = json.load(open(path))

def present(key, tries=3):
    for i in range(tries):
        r = subprocess.run(["aws", "s3api", "head-object", "--endpoint-url", endpoint,
                            "--bucket", bucket, "--key", key], capture_output=True, text=True)
        if r.returncode == 0:
            return True
        if "NoSuchKey" in (r.stderr or "") or "NoSuchBucket" in (r.stderr or ""):
            return False              # genuinely absent: stop retrying
        if i < tries - 1:
            time.sleep(1.0 * (i + 1))  # transient: back off and retry
    return False

changed, checked, kept = False, 0, []
for model in man.get("models", []):
    slug = model.get("slug")
    cycles = list(model.get("cycles", []))
    latest = model.get("latest") or (max(cycles) if cycles else None)
    good, dropped = [], []
    for cyc in cycles:
        checked += 1
        if present("models/enscenters/%s/%s.json" % (slug, cyc)):
            good.append(cyc)
        else:
            if cyc == latest:
                sys.stderr.write("GATE SKIP: %s latest %s has no object on R2; "
                                 "refusing to regress, skipping publish.\n" % (slug, cyc))
                sys.exit(3)            # never drop a latest -> skip whole publish (green)
            dropped.append(cyc)
    if dropped:
        changed = True
        sys.stderr.write("gate: %s dropping older cycle(s) with no R2 object: %s\n" % (slug, dropped))
    cv = model.get("cycle_versions") or {}
    tv = model.get("tracks_versions") or {}
    model["cycles"], model["latest"] = good, (max(good) if good else None)
    model["cycle_versions"] = {c: cv[c] for c in good if c in cv}
    tkept = {c: tv[c] for c in good if c in tv}
    if tkept:
        model["tracks_versions"] = tkept
    elif "tracks_versions" in model:
        del model["tracks_versions"]
    kept.append(model)
man["models"] = kept
if changed:
    json.dump(man, open(path, "w"), separators=(",", ":"))
    print("gate: cleaned manifest (dropped missing older cycle object[s]); %d checked" % checked)
else:
    print("consistency OK: all %d referenced cycle(s) present on R2" % checked)
GATE
  if [ "$gate_rc" = 3 ]; then
    echo "[$SLUG] gate: a latest cycle object is missing on R2; SKIP publish (prior stays live, self-heals)."
    exit 0
  fi
  if [ "$gate_rc" != 0 ]; then
    echo "[$SLUG] ABORT: consistency gate error (rc=$gate_rc); prior stays live." >&2
    exit 1
  fi

  # 4. CAS: re-read live; if a sibling PUBLISHED under us (live changed), re-loop so
  #    we re-list R2 and re-derive against the sibling's newest cycles.
  read_live "$LIVE_B"
  if ! cmp -s "$LIVE_A" "$LIVE_B"; then
    echo "[$SLUG] live manifest changed under us (concurrent sibling publish); re-deriving (attempt $attempt)."
    sleep "$attempt"
    continue
  fi

  # 5a. write the reconciled manifest (short TTL; viewer also cache-busts)
  aws s3 cp "$FINAL" "$DST/manifest.json" --endpoint-url "$R2_ENDPOINT" \
    --cache-control "public, max-age=60" --content-type "application/json" --only-show-errors
  cp "$FINAL" "$SRC/manifest.json"   # local copy reflects what was published
  published=1
  break
done

if [ -z "$published" ]; then
  echo "[$SLUG] WARN: manifest did not converge after retries (heavy concurrent writes); SKIP (cron self-heals)."
  exit 0
fi

# 5b. prune cycles rolled out of retention, FILTERED so a still-referenced cycle is
#     never deleted (manifest<->object invariant). The built prune list is for THIS
#     model only; we drop any key the reconciled manifest still points at.
if [ -s "$SRC/prune_keys.txt" ]; then
  python3 - "$FINAL" "$SRC/prune_keys.txt" > /tmp/ens_prune_filtered.txt <<'FILTER'
import json, sys
final, prune_path = sys.argv[1], sys.argv[2]
man = json.load(open(final))
referenced = set()
for m in man.get("models", []):
    for c in m.get("cycles", []):
        referenced.add("%s/%s.json" % (m.get("slug"), c))
for line in open(prune_path):
    key = line.strip()
    if key and key not in referenced:
        print(key)
FILTER
  while IFS= read -r key || [ -n "$key" ]; do
    [ -n "$key" ] || continue
    echo "[$SLUG] prune $key"
    aws s3 rm "$DST/$key" --endpoint-url "$R2_ENDPOINT" --only-show-errors || true
  done < /tmp/ens_prune_filtered.txt
fi

echo "[$SLUG] Published -> https://cdn.triple-a-tropics.com/$KEY"
