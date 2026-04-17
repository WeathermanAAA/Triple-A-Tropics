# Triple-A-Tropics

Live at **[triple-a-tropics.com](https://triple-a-tropics.com/)** · run by **[@WeathermanAAA_](https://twitter.com/WeathermanAAA_)**

An auto-updating tropical cyclone dashboard. Currently shows Accumulated
Cyclone Energy (ACE) climatology for three basins: Atlantic, West Pacific,
East Pacific — each with an interactive chart and a sortable ranking table
of every season back to 1945.

---

## How it works

The site is a plain GitHub Pages static site with a Python script that
regenerates the chart data on a schedule. No server, no database — just
files in a repo, republished by GitHub every time the workflow commits.

```
User's browser
      ↑
GitHub Pages  ←  (re-serves HTML on commit)
      ↑
GitHub Actions  ←  (runs every 6 hours + on manual dispatch)
   │
   ├─ curl — pulls fresh IBTrACS CSVs from NCEI
   ├─ python generate_ace_plot.py --basin {wp,al,ep}
   │     (for each basin, parses CSV, fetches live ATCF b-decks,
   │      computes ACE, renders {basin}_ace.html and json)
   └─ git commit + push  →  triggers Pages rebuild
```

Everything is free: GitHub Actions minutes, Pages hosting, and the data
sources (NCEI IBTrACS + NHC/JTWC ATCF).

---

## Repository layout

```
Triple-A-Tropics/
├── .github/workflows/update-ace.yml   Cron + manual-dispatch workflow
├── .gitignore                         Skips the huge IBTrACS CSVs from commits
├── CNAME                              Custom domain (auto-managed by GH Pages)
│
├── index.html                         Homepage
├── styles.css                         Shared site styles (edit once, applies everywhere)
│
├── climatology/
│   ├── index.html                     Basin-selector hub (3 cards)
│   ├── atlantic/index.html            Atlantic ACE page (iframes /al_ace.html)
│   ├── west-pacific/index.html        WPac ACE page (iframes /wp_ace.html)
│   └── east-pacific/index.html        EPac ACE page (iframes /ep_ace.html)
│
├── generate_ace_plot.py               The generator — renders all basin charts
│
├── wp_ace.html + wp_ace_data.json     WPac chart (auto-regenerated)
├── al_ace.html + al_ace_data.json     Atlantic chart (auto-regenerated)
├── ep_ace.html + ep_ace_data.json     EPac chart (auto-regenerated)
│
├── generate_wp_ace_plot.py            Legacy — can be deleted (superseded)
└── DEPLOY.md                          Original step-by-step deploy guide
```

The `{basin}_ace.html` files are **self-contained** dark-mode SVG charts.
They're designed to be iframed into the basin subpages but also work
standalone. The `{basin}_ace_data.json` files are the underlying data if
you ever want to build more visualizations.

---

## Design decisions

**Why per-basin methodology?**
NHC (Atlantic, East Pacific) and JTWC (West Pacific) calculate ACE
differently:

| | NHC basins (AL, EP) | JTWC basin (WP) |
| --- | --- | --- |
| Subtropical storms (SS nature) | Counted | Excluded |
| Authoritative wind source | `USA_WIND` (1-min) | `USA_WIND`, fallback to WMO/Tokyo 10-min × ÷0.88 |
| Climo window | 1991–2020 | 1991–2020 |

This matches published operational numbers (Ryan Maue, CSU, Colorado
State, etc.) so our chart is directly comparable.

**Why the min/max envelope uses all seasons instead of 1991–2020?**
Otherwise legendary outlier seasons (Atlantic 1933, WPac 1997) burst above
the "max" envelope when selected — misleading. Percentile bands stay at
1991–2020 because that's the standard climatological reference window.

**Why iframe the chart into the subpage instead of embedding directly?**
Two reasons: (1) the chart can be regenerated and committed without
touching the outer page, and (2) the chart works standalone if you ever
want to embed it elsewhere (social card, another site, etc.).

**Why SVG and not Plotly/D3?**
Self-contained, zero JS dependencies, works in restrictive iframe sandboxes,
<200 KB per chart. Future me: don't add Plotly unless there's a killer
feature it enables.

---

## Adding a new basin

Add a dict entry under `BASINS` in `generate_ace_plot.py`:

```python
"si": {
    "short": "si",
    "name": "South Indian",
    "full_name": "South Indian Ocean",
    "ibtracs_file_code": "SI",          # ibtracs.SI.list.v04r01.csv
    "ibtracs_basin_col": ["SI"],        # BASIN column values to accept
    "atcf_prefix": "bsh",               # ATCF b-deck prefix
    "agency_name": "JTWC",              # warning agency for SH
    "agency_url": "https://www.metoc.navy.mil/jtwc/",
    "atcf_patterns": [
        "https://www.metoc.navy.mil/jtwc/products/atcf/btk/bsh{nn}{yy}.dat",
    ],
    "wind_preference": [("USA_WIND", 1.0), ("WMO_WIND", 1.0/0.88)],
    "ace_natures": {"TS"},              # JTWC style
    "atcf_dev_levels": {"TS", "TY", "STY", "HU"},
    "download_url": "https://www.ncei.noaa.gov/.../ibtracs.SI.list.v04r01.csv",
},
```

Then: add the curl + regenerate steps to `.github/workflows/update-ace.yml`,
and create `/climatology/south-indian/index.html` (mirror one of the
existing basin pages, swap the iframe `src` to `/si_ace.html`).

---

## Data sources

- **Historical + recent-past:** [IBTrACS v04r01](https://www.ncei.noaa.gov/products/international-best-track-archive) — NOAA NCEI's consolidated best-track archive. Updated periodically (not real-time).
- **Live current season (Atlantic, EPac):** [NHC ATCF archive](https://ftp.nhc.noaa.gov/atcf/btk/) — very reliable, open.
- **Live current season (West Pacific):** [JTWC ATCF](https://www.metoc.navy.mil/jtwc/) + mirrors — sometimes blocked from cloud runners.

---

## Running locally

```bash
# Install deps
pip install pandas numpy

# Download the IBTrACS CSV for the basin you want
curl -O https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv

# Generate
python generate_ace_plot.py --basin wp

# Outputs wp_ace.html and wp_ace_data.json in the current dir.
# Use --no-live to skip the live ATCF fetch (useful for offline testing).
```

---

## Roadmap

Things that are on the table but not built yet:

- **Storm tracks map (per basin)** — cyclonicwx-style geographic plot
  showing every current-season storm's position/intensity by DOY. Both
  a static PNG (matplotlib + cartopy) and an interactive version
  (Leaflet.js).
- **Seasonal outlook tracker** — ingest forecasts from CSU, NOAA,
  JTWC, etc. and overlay against observed activity as the season
  unfolds.
- **Active storms page** — live positions + forecast tracks for any
  systems currently being warned on.
- **Model data (GFS/ECMWF/HAFS):** spaghetti track plots, 500-mb
  heights, SSTs, shear fields. Needs xarray/cfgrib in the workflow.
- **Satellite imagery (GOES, Himawari):** imagery is huge (tens of MB
  per frame) so this will require splitting hosting — site stays on
  GitHub Pages, imagery moves to Cloudflare R2 or S3.

---

## Future-Claude context primer

If a future LLM assistant is helping extend this site: this site was
built iteratively with Claude (Cowork mode) over a single weekend. The
key architectural choices are documented above. Important gotchas we
hit along the way:

- IBTrACS BASIN codes: `NA` (North Atlantic), `EP` (East Pacific),
  `WP` (West Pacific). Always pass as a list to the filter — some
  files use alternative codes, and the script falls back to the
  whole file if the list matches zero rows.
- IBTrACS backfills `NATURE` column post-season QC. For `TRACK_TYPE
  == "PROVISIONAL"` rows (current season), accept `NATURE == "NR"`
  as tropical too, otherwise current-season ACE is zero until NCEI
  backfills.
- JTWC's `metoc.navy.mil` often blocks plain `python-urllib` User-
  Agent. Use a browser UA string. Even then, GitHub Actions runner
  IP ranges sometimes get 403'd. NHC's `ftp.nhc.noaa.gov/atcf/btk/`
  is always reachable.
- Current year: use `dt.date.today().year`, NOT `max(points.season)`.
  Otherwise pre-season basins relabel last year as "current."
- Climatology: percentile bands from 1991–2020 (comparable to NHC
  normals), min/max envelope from ALL seasons EXCLUDING the current
  incomplete one.

When extending:
- Generator is basin-agnostic: add a new `BASINS` entry + workflow
  step + subpage, no algorithm changes needed.
- Chart template is inlined in `HTML_TEMPLATE`; keep it self-contained
  (no CDN dependencies) so iframed charts remain portable.
