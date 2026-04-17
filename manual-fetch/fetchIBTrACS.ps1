$url = "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
$output = "ibtracs.WP.list.v04r01.csv"

curl.exe -fSL --retry 3 --retry-delay 10 -o $output $url

if ($LASTEXITCODE -ne 0) {
    Write-Error "Download failed."
    exit $LASTEXITCODE
}

Write-Host "Downloaded to $output"