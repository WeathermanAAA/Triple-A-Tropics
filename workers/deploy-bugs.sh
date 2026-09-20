#!/usr/bin/env bash
# Deploy the bug-board Worker without rotating secrets or posting a test report.
# Prerequisite: wrangler login; GITHUB_TOKEN and ADMIN_KEY already configured.
set -euo pipefail
cd "$(dirname "$0")"
npx wrangler d1 execute BUG_RATE_DB --remote --file bugs-rate-limit.sql -c bugs-api.toml
npx wrangler deploy -c bugs-api.toml
curl --fail --silent --show-error https://triple-a-tropics.com/bugs-api/issues \
  | python3 -c 'import sys,json; print(f"Board readable: {len(json.load(sys.stdin))} reports")'
