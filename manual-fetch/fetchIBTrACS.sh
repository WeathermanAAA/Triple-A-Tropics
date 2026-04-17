#!/usr/bin/env bash
set -euo pipefail

url="https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
output="ibtracs.WP.list.v04r01.csv"

curl -fSL --retry 3 --retry-delay 10 -o "$output" "$url"

echo "Downloaded to $output"