#!/usr/bin/env bash
# Integration test for scripts/enscenters_publish_manifest.sh.
#
# Drives the REAL publish script (and the REAL reconcile guard) against a fake
# `aws` CLI backed by a local directory standing in for the R2 bucket. The
# manifest is DERIVED FROM the R2 object listing (the source of truth), so these
# tests prove: a model advances to its newest cycle ON R2 even when its own
# workflow didn't run, siblings are preserved, and a listing failure skips the
# publish (never clobbers).
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
  cat > "$BIN/aws" <<FAKE
#!/usr/bin/env bash
R2DIR="$R2DIR"
FAIL_READ="\${FAKE_AWS_FAIL_READ:-}"     # set to 1 to simulate R2 unreachable (read + list)
key_path() { echo "\$R2DIR/\$1"; }
uri_path() { echo "\$R2DIR/\${1#s3://triple-a-tropics-media/}"; }
sub="\$1 \$2"
shift 2 || true
case "\$sub" in
  "s3 sync")
    src="\$1"; dsturi="\$2"
    for f in "\$src"*/*.json; do
      [ -e "\$f" ] || continue
      rel="\${f#\$src}"; out="\$(uri_path "\$dsturi")/\$rel"
      mkdir -p "\$(dirname "\$out")"; cp "\$f" "\$out"
    done
    ;;
  "s3 ls")
    # aws s3 ls s3://bucket/models/enscenters/ --recursive -> "<date> <time> <size> <key>"
    if [ -n "\$FAIL_READ" ]; then echo "Could not connect to the endpoint URL" >&2; exit 255; fi
    ( cd "\$R2DIR" && find models/enscenters -name '*.json' 2>/dev/null \
        | while read -r k; do echo "2026-06-14 00:00:00 100 \$k"; done )
    ;;
  "s3api get-object")
    k=""; out=""
    while [ \$# -gt 0 ]; do case "\$1" in --key) k="\$2"; shift 2;; --bucket|--endpoint-url) shift 2;; *) out="\$1"; shift;; esac; done
    if [ -n "\$FAIL_READ" ]; then echo "Could not connect" >&2; exit 255; fi
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

mk_cycle() {  # <slug> <cycle> <dir>
  mkdir -p "$3/$1"; printf '{"model":"%s","init_cycle":"%s"}' "$1" "$2" > "$3/$1/$2.json"
}
seed_live() { cp "$1" "$R2DIR/models/enscenters/manifest.json"; }
check() { if [ "$2" = "$3" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: $1 (got '$2', want '$3')"; fi; }
latest_of() { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(next((e['latest'] for e in m['models'] if e['slug']==sys.argv[2]),''))" "$1" "$2"; }
slugs_of()  { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(','.join(sorted(e['slug'] for e in m['models'])))" "$1"; }
cycles_of() { python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print(','.join(next((e['cycles'] for e in m['models'] if e['slug']==sys.argv[2]),[])))" "$1" "$2"; }
run_publish() { ( cd "$WORK" && "$PUBLISH" "$1" >/tmp/pub.out 2>&1 ); echo $?; }

# ---- T1: gefs run derives the manifest from R2; siblings kept, gefs advanced ----
setup
mk_cycle gefs 2026061318 "$SRC"; mk_cycle gefs 2026061312 "$SRC"   # this run's build
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318","2026061312"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
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
check "T1 gefs advanced (synced build cycle)" "$(latest_of "$FINAL" gefs)" "2026061318"
check "T1 ecens preserved" "$(latest_of "$FINAL" ecens)" "2026061312"
teardown

# ---- T2: a NON-running model advances to its newest cycle ON R2 (THE prod bug) ----
# Stale live says ecens@Jun-12, but ecens/2026061312.json EXISTS on R2 (uploaded by
# an earlier ecens run that never re-published). A gefs run must re-point ecens at it.
setup
mk_cycle gefs 2026061318 "$SRC"
cat > "$SRC/manifest.json" <<'M'
{"schema_version":1,"default_model":"gefs","models":[{"slug":"gefs","label":"GEFS","cycles":["2026061318"],"latest":"2026061318","cycle_versions":{}}]}
M
printf '' > "$SRC/prune_keys.txt"
cat > "$WORK/live.json" <<'M'
{"schema_version":1,"default_model":"ecens","models":[
 {"slug":"ecens","label":"ECMWF ENS","cycles":["2026061206"],"latest":"2026061206","cycle_versions":{}}]}
M
seed_live "$WORK/live.json"
# R2 reality: ecens has a NEWER cycle object than the stale manifest references
mk_cycle ecens 2026061206 "$R2DIR/models/enscenters"; mk_cycle ecens 2026061212 "$R2DIR/models/enscenters"
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T2 exit" "$rc" "0"
check "T2 ecens re-pointed to newest-on-R2 (not stale Jun-12-06)" "$(latest_of "$FINAL" ecens)" "2026061212"
check "T2 gefs advanced" "$(latest_of "$FINAL" gefs)" "2026061318"
teardown

# ---- T3: R2 unreachable (listing fails) -> SKIP publish, never clobber ----
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
check "T3 live NOT clobbered" "$(slugs_of "$FINAL")" "ecens"
teardown

# ---- T4: a cycle object missing on R2 is naturally excluded by the listing ----
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
# Only ecens/2026061312 exists on R2; the older 2026061306 object is GONE
mk_cycle ecens 2026061312 "$R2DIR/models/enscenters"
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T4 exit" "$rc" "0"
check "T4 models" "$(slugs_of "$FINAL")" "ecens,gefs"
check "T4 ecens = only the present object" "$(cycles_of "$FINAL" ecens)" "2026061312"
check "T4 gefs advanced" "$(latest_of "$FINAL" gefs)" "2026061318"
teardown

# ---- T5: empty R2 listing while live has models -> suspected failure, SKIP ----
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
# Sabotage: remove the build's local cycle dir so the sync uploads NOTHING and the
# R2 listing is empty (no model objects), yet live has ecens -> guard must abort.
rm -rf "$SRC/gefs"
rc=$(run_publish gefs)
FINAL="$R2DIR/models/enscenters/manifest.json"
check "T5 exit (skip, green)" "$rc" "0"
check "T5 live unchanged (prior stays)" "$(slugs_of "$FINAL")" "ecens"
check "T5 ecens latest unchanged" "$(latest_of "$FINAL" ecens)" "2026061312"
teardown

echo "-----"
echo "publish-script integration: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
