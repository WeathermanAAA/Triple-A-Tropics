#!/usr/bin/env bash
# Upload a file (or sync a directory) to the triple-a-tropics-media
# R2 bucket via the S3 API.
#
# Usage:
#   upload_to_r2.sh <local_file> <r2_key>           # single file
#   upload_to_r2.sh <local_dir>  <r2_prefix>        # recursive sync
#
# Env (mapped from R2_* secrets in the workflow env):
#   AWS_ACCESS_KEY_ID
#   AWS_SECRET_ACCESS_KEY
#   R2_ENDPOINT
#
# Cache-Control: max-age=300 (5 min). The 6h ACE workflow is the
# fastest-churning consumer; 5 min keeps stale-window short without
# burning a network round-trip per page load.
set -euo pipefail

LOCAL="${1:?usage: upload_to_r2.sh <local_path> <r2_key_or_prefix>}"
TARGET="${2:?usage: upload_to_r2.sh <local_path> <r2_key_or_prefix>}"

: "${R2_ENDPOINT:?R2_ENDPOINT must be set}"

BUCKET="triple-a-tropics-media"
CACHE_CONTROL="public, max-age=300"

if [ -d "$LOCAL" ]; then
  # Directory → recursive sync. Lets `aws` batch and parallelize,
  # and skips re-uploading unchanged objects on warm runs.
  aws s3 sync "$LOCAL" "s3://${BUCKET}/${TARGET%/}/" \
    --endpoint-url "$R2_ENDPOINT" \
    --cache-control "$CACHE_CONTROL" \
    --size-only \
    --only-show-errors
elif [ -f "$LOCAL" ]; then
  CONTENT_TYPE="$(file --mime-type -b "$LOCAL")"
  aws s3 cp "$LOCAL" "s3://${BUCKET}/${TARGET}" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type "$CONTENT_TYPE" \
    --cache-control "$CACHE_CONTROL" \
    --only-show-errors
else
  echo "upload_to_r2: not a file or directory: $LOCAL" >&2
  exit 1
fi
