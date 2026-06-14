#!/usr/bin/env bash
# Publish _mp4_build/sst/ to the `mp4-artifacts` orphan branch, OVERLAY-style.
#
# The SST animation is now published by TWO shards: the OISST shard (Job 1) and
# the CRW shard (Job 2) of update-sst.yml. Each runs the animator with only its
# product family, so _mp4_build/sst/ holds just that family's MP4s + a merged
# manifest.json (generate_sst_animations._write_manifest folds in the other
# family's clips from the live manifest).
#
# This script therefore OVERLAYS (copies onto the existing branch sst/ dir, no
# wipe) so a shard never deletes the OTHER shard's MP4s. The branch is purpose-
# built for force-push rewrites; source code lives on main. Used by both jobs.
#
# Requires: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKSPACE in the environment.
set -eo pipefail

SRC="${GITHUB_WORKSPACE}/_mp4_build/sst"
if [ ! -d "$SRC" ] || [ -z "$(ls -A "$SRC" 2>/dev/null)" ]; then
  echo "No SST build output at $SRC — nothing to push."
  exit 0
fi

ARTIFACTS_DIR=/tmp/mp4-artifacts
rm -rf "$ARTIFACTS_DIR"
mkdir -p "$ARTIFACTS_DIR"
cd "$ARTIFACTS_DIR"

git init -q
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git remote add origin \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

# Pull the existing branch if present; else bootstrap it as an orphan. Fetching
# the latest is what makes the overlay safe across the two sequential jobs:
# Job 1 overlays OISST, Job 2 fetches that and overlays CRW on top.
if git fetch --depth=1 origin mp4-artifacts 2>/dev/null; then
  git checkout -q -b mp4-artifacts FETCH_HEAD
else
  echo "mp4-artifacts branch does not exist yet — creating it"
  git checkout -q --orphan mp4-artifacts
  git rm -rfq . 2>/dev/null || true
fi

# OVERLAY: add/replace this shard's MP4s + the merged manifest, KEEPING the
# other shard's MP4s already on the branch. (The previous wipe-and-replace
# would have deleted the other family's MP4s on a single-family run.)
mkdir -p sst
cp -r "$SRC/." sst/

# Refresh README only if missing — manual edits survive.
if [ ! -f README.md ]; then
  cp "${GITHUB_WORKSPACE}/_mp4_build/README.md" README.md
fi

git add sst README.md
if git diff --cached --quiet; then
  echo "No SST artifact changes to push."
  exit 0
fi
git commit -q -m "chore: refresh SST MP4 artifacts ($(date -u +'%Y-%m-%d %H:%M UTC'))"
# Force-push is fine — this branch is a CDN publish surface, rewritten each run.
git push --force origin mp4-artifacts
