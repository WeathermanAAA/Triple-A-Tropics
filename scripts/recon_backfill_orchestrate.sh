#!/usr/bin/env bash
# Sequential 2011->now recon archive backfill driver.
#
# The update-recon workflow is serialized by its `concurrency` group, so
# backfill dispatches MUST be issued one at a time (waiting for each), never
# fanned out. This dispatches PER YEAR; if a year fails or times out it
# re-dispatches that year BY MONTH (recon season May-Nov). Idempotent: every
# chunk overwrites the same R2 keys + upserts the manifest, so any chunk can
# be re-run with no duplication. The live incremental cron keeps running and
# merges harmlessly between chunks.
#
# Usage: GH_TOKEN=<pat> scripts/recon_backfill_orchestrate.sh [START_YEAR END_YEAR]
set -uo pipefail
REPO="WeathermanAAA/Triple-A-Tropics"
WF="update-recon.yml"
LOG="${RECON_BACKFILL_LOG:-/tmp/recon_backfill.log}"
START="${1:-2011}"; END="${2:-2026}"
export GH_TOKEN="${GH_TOKEN:-$GH_PUSH_TOKEN}"

log(){ echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

latest_dispatch_id(){ gh run list --repo "$REPO" --workflow="$WF" \
    --event=workflow_dispatch -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null; }

# dispatch one chunk and block until its run finishes; echo the conclusion
run_chunk(){  # $1=year  $2=month(optional)
  local year="$1" month="${2:-}" extra="" before rid concl i
  [ -n "$month" ] && extra="-f backfill_month=$month"
  before="$(latest_dispatch_id)"
  gh workflow run "$WF" --repo "$REPO" -f "backfill_year=$year" $extra >/dev/null 2>&1
  # wait for a NEW workflow_dispatch run id to appear (ours)
  rid=""
  for i in $(seq 1 24); do
    sleep 5; rid="$(latest_dispatch_id)"
    [ -n "$rid" ] && [ "$rid" != "$before" ] && break
  done
  if [ -z "$rid" ] || [ "$rid" = "$before" ]; then echo "no-run"; return; fi
  gh run watch "$rid" --repo "$REPO" --interval 20 --exit-status >/dev/null 2>&1
  concl="$(gh run view "$rid" --repo "$REPO" --json conclusion -q '.conclusion' 2>/dev/null)"
  echo "${concl:-unknown}"
}

log "BACKFILL START years ${START}..${END}"
for y in $(seq "$START" "$END"); do
  log "year ${y}: dispatch (whole year)"
  c="$(run_chunk "$y")"
  if [ "$c" = "success" ]; then
    log "year ${y}: SUCCESS"
  else
    log "year ${y}: ${c} -> sub-chunking by month (May-Nov)"
    for m in 5 6 7 8 9 10 11; do
      mc="$(run_chunk "$y" "$m")"
      log "  ${y}-$(printf %02d "$m"): ${mc}"
      sleep 8
    done
  fi
  sleep 20   # let any queued incremental cron run interleave
done
log "BACKFILL DONE"
