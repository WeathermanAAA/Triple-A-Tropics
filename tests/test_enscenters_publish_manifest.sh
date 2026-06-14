#!/usr/bin/env bash
# Integration test for scripts/enscenters_publish_manifest.sh.
#
# Drives the REAL publish script (and the REAL reconcile guard) against a fake
# `aws` CLI backed by a local directory standing in for the R2 bucket. Proves the
# shell plumbing -- read-classify, reconcile, consistency gate, CAS, filtered
# prune -- not just the pure guard logic.
#
# Run:  bash tests/test_enscenters_publish_manifest.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PUBLISH="$REPO/scripts/enscenters_publish_manifest.sh"
PASS=0 FAIL=0

setup() {
  WORK="$(mktemp -d)"
  R2DIR="$WORK/r2"                       # stands in for s3://triple-a-tropics-media
  BIN="$WORK/bin"
  mkdir -p "$R2DIR/models/enscenters" "$BIN"
  # ---- fake aws: maps s3://triple-a-tropics-media/<p> and --key <k> to $R2DIR/<p> ----
  cat > "$BIN/aws" <<FAKE
#!/usr/bin/env bash
R2DIR="$R2DIR"
FAIL_READ="\${FAKE_AWS_FAIL_READ:-}"     # set to 1 to simulate a non-404 read failure
key_path() { echo "\$R2DIR/\$1"; }
uri_path() { echo "\$R2DIR/\${1#s3://triple-a-tropics-media/}"; }
sub="\$1 \$2"
shift 2 || true
case "\$sub" in
  "s3 sync")
    src="\$1"; dsturi="\$2"
    # mirror src/*/*.json into the bucket (matches --include '*/*.json')
    for f in "\$src"*/*.json; do
      [ -e "\$f" ] || continue
      rel="\${f#\$src}"; out="\$(uri_path "\$dsturi")/\$rel"
      mkdir -p "\$(dirname "\$out")"; cp "\$f" "\$out"
    done
    ;;
  "s3api get-object")
    k=""; out=""
    while [ \$# -gt 0 ]; do case "\$1" in --key) k="\$2"; shift 2;; --bucket|--endpoint-url) shift 2;; *) out="\$1"; shift;; esac; done
    if [ -n "\$FAIL_READ" ]; then echo "Connection timed out" >&2; exit 1; fi
    p="\$(key_path "\$k")"
    if [ -f "\$p" ]; then cp "\$p" "\$out"; echo '{"ETag":"x"}'; exit 0; fi
    echo "An error occurred (NoSuchKey) when calling the GetObject operation" >&2; exit 1
    ;;
  "s3api head-object")
    k=""
    while [ \$# -gt 0 ]; do case "\$1" in --key) k="\$2"; shift 2;; --bucket|--endpoint-url) shift 2;; *) shift;; esac; done
    [ -f "\$(key_path "\$k")" ] && exit 0 || { echo "An error occurred (NoSuchKey) when calling the HeadObject operation" >&2; exit 1; }
    ;;
  "s3 cp")
    f="\$1"; dsturi="\$2"; out="\$(uri_path "\$dsturi")"
    mkdir -p "\$(dirname "\$out")"; cp "\$f" "\$out"
    ;;
  "s3 rm")
    rm -f "\$(uri_path "\$1")"
    ;;
esac
exit 0
FAKE
  chmod +x "$BIN/aws"
  SRC="$WORK/models/enscenters"
  mkdir -p "$SRC"
  export R2_ENDPOINT="http://fake" PATH="$BIN:$PATH"
}

teardown() { rm -rf "$WORK"; unset FAKE_AWS_FAIL_READ; }

# write a per-cycle JSON for (slug, cycle) both into the build dir and (optionally) R2
mk_cycle() {  # <slug> <cycle> <dir>
  mkdir -p "$3/$1"; printf '{"model":"%s","init_cycle":"%s"}' "$1" "$2" > "$3/$1/$2.json"
}
seed_live() {  # write a live manifest + its cycle objects into R2
  cp "$1" "$R2DIR/models/enscenters/manifest.json"
}

check() { if [ "$2" = "$3" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: $1 (got '$2', want '$3')"; fi; }
latest_of() { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(next((e['latest'] for e in m['models'] if e['slug']==sys.argv[2]),''))" "$1" "$2"; }
slugs_of()  { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(','.join(sorted(e['slug'] for e in m['models'])))" "$1"; }

# Run the publish script from the build dir's parent so ./models/enscenters resolves.
run_publish() {  # <slug>
  ( cd "$WORK" && "$PUBLISH" "$1" >/tmp/pub.out 2>&1 ); echo $?
}

# ---- T1: gefs run with a THIN build preserves live siblings + advances gefs ----
setup
mk_cycle gefs 2026061318 "$SRC"; mk_cycle gefs 2026061312 "$SRC"
# build manifest (thin: only gefs) + cycle objects present locally for sync
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318","2026061312"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
# live R2 already has ecens + ecaie + an older gefs; their cycle objects exist on R2
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061312","2026061306"],"latest":"2026061312","cycle_versions":{}},
 {"slug":"ecaie","label":"AIFS-ENS","cycles":["2026061312"],"latest":"2026061312","cycle_versions":{}},
 {"slug":"gefs","label":"GEFS","cycles":["2026061312"],"latest":"2026061312","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
mk_cycle ecens 2026061312 "$R2DIR/models/enscenters"; mk_cycle ecens 2026061306 "$R2DIR/models/enscenters"
mk_cycle ecaie 2026061312 "$R2DIR/models/enscenters"; mk_cycle gefs 2026061312 "$R2DIR/models/enscenters"
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T1 exit" "$rc" "0"
check "T1 models" "$(slugs_of "$FINAL")" "ecaie,ecens,gefs"
check "T1 gefs latest advanced" "$(latest_of "$FINAL" gefs)" "2026061318"
check "T1 ecens preserved" "$(latest_of "$FINAL" ecens)" "2026061312"
teardown

# ---- T2: a REGRESSED ecens build must NOT regress ecens on R2 (monotone) ----
setup
mk_cycle ecens 2026061218 "$SRC"; mk_cycle ecens 2026061212 "$SRC"
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[{"slug":"ecens","label":"ECMWF ENS","cycles":["2026061218","2026061212"],"latest":"2026061218","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061312","2026061306"],"latest":"2026061312","cycle_versions":{}},
 {"slug":"ecaie","label":"AIFS-ENS","cycles":["2026061312"],"latest":"2026061312","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
mk_cycle ecens 2026061312 "$R2DIR/models/enscenters"; mk_cycle ecens 2026061306 "$R2DIR/models/enscenters"
mk_cycle ecaie 2026061312 "$R2DIR/models/enscenters"
rc=$(run_publish ecens)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T2 exit" "$rc" "0"
check "T2 ecens NOT regressed" "$(latest_of "$FINAL" ecens)" "2026061312"
check "T2 ecaie preserved" "$(latest_of "$FINAL" ecaie)" "2026061312"
teardown

# ---- T3: a non-404 live read failure SKIPS publish (exit 0) and does NOT clobber ----
setup
mk_cycle gefs 2026061318 "$SRC"
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061312"],"latest":"2026061312","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
mk_cycle ecens 2026061312 "$R2DIR/models/enscenters"
export FAKE_AWS_FAIL_READ=1
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T3 exit (skip, not fail)" "$rc" "0"
# live manifest untouched -> still ONLY ecens (the thin gefs build never clobbered it)
check "T3 live NOT clobbered" "$(slugs_of "$FINAL")" "ecens"
teardown

# ---- T4: a missing OLDER sibling cycle object is DROPPED, publish still proceeds ----
setup
mk_cycle gefs 2026061318 "$SRC"; mk_cycle gefs 2026061312 "$SRC"
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318","2026061312"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061312","2026061306"],"latest":"2026061312","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
# ecens latest object present, but the OLDER ecens/2026061306 object is GONE on R2
mk_cycle ecens 2026061312 "$R2DIR/models/enscenters"
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T4 exit" "$rc" "0"
check "T4 models" "$(slugs_of "$FINAL")" "ecens,gefs"
check "T4 ecens latest intact" "$(latest_of "$FINAL" ecens)" "2026061312"
check "T4 ecens missing-older dropped" "$(python3 -c "import json;m=json.load(open('$FINAL'));print(','.join(next(e['cycles'] for e in m['models'] if e['slug']=='ecens')))")" "2026061312"
check "T4 gefs advanced" "$(latest_of "$FINAL" gefs)" "2026061318"
teardown

# ---- T5: a missing LATEST object SKIPS publish (no regress, no clobber) ----
setup
mk_cycle gefs 2026061318 "$SRC"
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061312"],"latest":"2026061312","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
# ecens LATEST object is GONE on R2 -> gate must NOT regress it; skip publish (green)
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T5 exit (skip, green)" "$rc" "0"
# prior manifest stays live: gefs was NOT added, ecens NOT regressed/dropped
check "T5 live unchanged (prior stays)" "$(slugs_of "$FINAL")" "ecens"
check "T5 ecens latest unchanged" "$(latest_of "$FINAL" ecens)" "2026061312"
teardown

echo "-----"
echo "publish-script integration: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
