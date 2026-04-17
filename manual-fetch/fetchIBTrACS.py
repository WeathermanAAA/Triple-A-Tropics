import sys
import time
import urllib.request
import urllib.error

URL = "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
OUTPUT = "ibtracs.WP.list.v04r01.csv"
RETRIES = 3
DELAY = 10

for attempt in range(1, RETRIES + 1):
    try:
        with urllib.request.urlopen(URL) as response:
            if response.status != 200:
                raise urllib.error.HTTPError(
                    URL, response.status, f"HTTP {response.status}", response.headers, None
                )

            with open(OUTPUT, "wb") as f:
                f.write(response.read())

        print(f"Downloaded to {OUTPUT}")
        sys.exit(0)

    except Exception as e:
        print(f"Attempt {attempt} failed: {e}", file=sys.stderr)
        if attempt < RETRIES:
            time.sleep(DELAY)
        else:
            print("Download failed.", file=sys.stderr)
            sys.exit(1)
