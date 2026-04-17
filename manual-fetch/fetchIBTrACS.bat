@echo off
set URL=https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv
set OUTPUT=ibtracs.WP.list.v04r01.csv

curl.exe -fSL --retry 3 --retry-delay 10 -o "%OUTPUT%" "%URL%"

if errorlevel 1 (
    echo Download failed.
    exit /b %errorlevel%
)

echo Downloaded to %OUTPUT%