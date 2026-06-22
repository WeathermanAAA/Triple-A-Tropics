#!/usr/bin/env bash
# Sequential 2011->now recon archive backfill driver.
#
# The update-recon workflow is serialized by its `concurrency` group (so the
# manifest read-merge-write - the growing union - never races), therefore
# backfill dispatches MUST be issued ONE AT A TIME, waiting for each. We
# dispatch PER MONTH over the recon season (May-Nov): a whole-year sweep is
# slow and can hit the per-PIL file cap (silent truncation) on a busy season,
# whereas a month is always well under the cap + the job timeout. Idempotent:
# each chunk overwrites the same R2 keys + upserts the manifest, so any chunk
# re-runs with no duplication; a COMPLETED.log lets a re-launch skip done work.
# The live incremental cron keeps running and merges harmlessly between chunks.
#
# Usage: GH_TOKEN=<pat> scripts/recon_backfill_orchestrate.sh [START_YEAR END_YEAR]
set -uo pipefail
REPO="WeathermanAAA/Triple-A-Tropics"
WF="update-recon.yml"
LOG="${RECON_BACKFILL_LOG:-/tmp/recon_backfill.log}"
STATE="${RECON_BACKFILL_STATE:-/tmp/recon_backfill_done.log}"
MONTHS="${RECON_BACKFILL_MONTHS:-5 6 7 8 9 10 11}"
START="${1:-2011}"; END="${2:-2026}"
export GH_TOKEN="${GH_TOKEN:-$GH_PUSH_TOKEN}"
touch "$STATE"

log(){ echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
latest_id(){ gh run list --repo "$REPO" --workflow="$WF" \
    --event=workflow_dispatch -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null; }

run_chunk(){  # $1=year $2=month -> echoes conclusion
  local year="$1" month="$2" before rid i
  before="$(latest_id)"
  gh workflow run "$WF" --repo "$REPO" \
    -f "backfill_year=$year" -f "backfill_month=$month" >/dev/null 2>&1
  rid=""
  for i in $(seq 1 24); do
    sleep 5; rid="$(latest_id)"
    [ -n "$rid" ] && [ "$rid" != "$before" ] && break
  done
  [ -z "$rid" ] || [ "$rid" = "$before" ] && { echo "no-run"; return; }
  gh run watch "$rid" --repo "$REPO" --interval 20 --exit-status >/dev/null 2>&1
  gh run view "$rid" --repo "$REPO" --json conclusion -q '.conclusion' 2>/dev/null
}

log "BACKFILL START years ${START}..${END} months [${MONTHS}]"
for y in $(seq "$START" "$END"); do
  for m in $MONTHS; do
    key="${y}-$(printf %02d "$m")"
    if grep -qx "$key" "$STATE" 2>/dev/null; then log "  $key: already done (skip)"; continue; fi
    c="$(run_chunk "$y" "$m")"
    log "  $key: ${c:-unknown}"
    if [ "$c" = "success" ]; then echo "$key" >> "$STATE"
    else
      # one retry, then leave for a manual re-dispatch (logged, not marked done)
      sleep 15; c2="$(run_chunk "$y" "$m")"; log "  $key: retry -> ${c2:-unknown}"
      [ "$c2" = "success" ] && echo "$key" >> "$STATE" || log "  $key: NEEDS RE-DISPATCH"
    fi
    sleep 12   # stagger between chunks; let any queued incremental cron run
  done
  log "year ${y}: season chunks dispatched"
done
log "BACKFILL DONE"
