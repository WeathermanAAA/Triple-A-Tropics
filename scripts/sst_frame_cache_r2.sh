#!/usr/bin/env bash
# R2-backed frame cache for the SST animator.
#
# WHY THIS EXISTS (consolidation fix 2): the rolling 90-day frame cache is
# ~9.5 GB on disk (8 product families x 18 regions x ~104 days of PNGs at
# FRAME_DPI=150). The old design saved it to the GitHub Actions cache TWICE
# per run -- a `-oisst` entry (Job 1) and a `-full` entry (Job 2) -- and the
# restore-keys fallthrough meant the `-oisst` entry already carried the prior
# run's CRW frames, so BOTH entries were ~9.5 GB full-set copies. Two ~9.5 GB
# caches against GitHub's 10 GB/repo cache cap evicted each other every run,
# forcing a ~6 h COLD re-render that re-stamped the OISST final/prelim seam
# (render-once never got to hold). R2 has no 10 GB cap, so one durable copy
# lives there and render-once finally holds.
#
#   sst_frame_cache_r2.sh restore   # R2 -> ./_frame_cache  (cold-render on miss)
#   sst_frame_cache_r2.sh save      # ./_frame_cache -> R2  (atomic temp-key swap)
#
# Env (mapped from the R2_* workflow secrets, same as upload_to_r2.sh):
#   AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY  R2_ENDPOINT
#
# Single streamed tar object (no per-file S3 API overhead, no intermediate
# 9.5 GB file on the runner disk). NOT a public asset: the `_buildcache/`
# prefix is unlinked and never referenced by any manifest. R2 egress is free,
# so the only cost is trivial storage. Bump the `_vN` suffix to force a
# one-time cold re-render of the whole window (the R2 equivalent of the old
# `-vN-` GitHub cache-key sledgehammer).
set -uo pipefail

BUCKET="triple-a-tropics-media"
KEY="_buildcache/sst_frame_cache_v3.tar"
DIR="_frame_cache"

: "${R2_ENDPOINT:?R2_ENDPOINT must be set}"
EP=(--endpoint-url "$R2_ENDPOINT")

case "${1:-}" in
  restore)
    # Always start clean: a PARTIAL extract (broken download mid-stream)
    # would leave holes the render fills from FINAL data -> the very seam
    # we are trying to let age out. pipefail makes the `if` false if the
    # download OR the untar fails, so any failure -> clean cold render.
    rm -rf "$DIR"
    if aws s3 cp "s3://${BUCKET}/${KEY}" - "${EP[@]}" 2>/dev/null | tar -xf - ; then
      echo "frame cache restored from R2 (${KEY}, $(du -sh "$DIR" 2>/dev/null | cut -f1 || echo '?'))"
    else
      rm -rf "$DIR"
      echo "no usable R2 frame cache -> cold render this run"
    fi
    ;;
  save)
    if [ ! -d "$DIR" ] || [ -z "$(ls -A "$DIR" 2>/dev/null)" ]; then
      echo "no ${DIR} to save -- skipping"
      exit 0
    fi
    # Stream tar -> a TEMP key, then server-side copy to the live key only
    # after the full stream uploaded. pipefail fails the `if` on a truncated
    # tar, so a broken save NEVER replaces the good live object (a corrupt
    # live tar would force a needless cold render next run).
    TMP="${KEY}.$$.inprogress"
    if tar -cf - "$DIR" | aws s3 cp - "s3://${BUCKET}/${TMP}" "${EP[@]}" ; then
      aws s3 cp "s3://${BUCKET}/${TMP}" "s3://${BUCKET}/${KEY}" "${EP[@]}"
      aws s3 rm "s3://${BUCKET}/${TMP}" "${EP[@]}" >/dev/null 2>&1 || true
      echo "frame cache saved to R2 (${KEY})"
    else
      aws s3 rm "s3://${BUCKET}/${TMP}" "${EP[@]}" >/dev/null 2>&1 || true
      echo "WARNING: frame cache save failed (tar/upload) -> previous R2 cache kept" >&2
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 restore|save" >&2
    exit 2
    ;;
esac
