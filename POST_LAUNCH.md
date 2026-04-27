# Post-launch follow-ups

Tracking deferred work that should be revisited once a missing data source
or external dependency is in place. Each item should name what triggers
the work (a data source becoming available, an upstream API change, etc.)
so it can be picked up at the right moment instead of carried as an open
todo forever.

## Invest X-marker glow intensity scales with formation probability

`generate_tracks_plot.py::render_tracks_svg` currently renders every
invest's current-position X with the same uniform red glow (filter
`#invest-red-glow` defined in `SVG_DEFS`).

The intent — per the original spec — is for the glow intensity (or
hue/saturation) to scale with the NHC/JTWC formation probability:

- **NHC Tropical Weather Outlook (TWO):** 2-day and 7-day percentages
  (Low / Medium / High → 0–30% / 40–60% / 70–100%) per AL/EP basin.
- **JTWC ABPW10:** Low / Medium / High formation likelihood for each
  WP/IO disturbance.

The knackwx ATCF v2 endpoint we use today does not include this field,
so we'd need a separate data source:

- NHC TWO: `https://www.nhc.noaa.gov/gtwo.php` (HTML) or
  `https://www.nhc.noaa.gov/xml/TWOAT.xml` / `TWOEP.xml` (RSS/XML).
- JTWC ABPW: `https://www.metoc.navy.mil/jtwc/products/abpw10.txt`
  (text bulletin; same `metoc.navy.mil` 403-on-Actions issue noted in
  CLAUDE.md — would need to flow through the Cloudflare Worker proxy
  alongside the existing ATCF chain).

Implementation sketch when a source is wired up:

1. Plumb `formation_probability` (Low/Med/High or 2-day %) onto each
   invest in `fetch_live_invests` (or a new fetcher) and into the
   storm dict.
2. Either parameterize the glow filter (per-storm `stdDeviation` /
   `flood-opacity` — needs unique filter ids since SVG filter primitives
   can't read attributes from the referencing element) or switch from a
   single shared filter to a small bank of three filters
   (`#invest-red-glow-low/med/high`) and pick the right url() at render
   time. The latter is cheaper.
3. Optionally tint hue (e.g. yellow → orange → red as probability
   climbs) instead of pure intensity scaling.

Trigger: pick this up when adding a TWO/ABPW fetcher for any reason
(e.g. an outlook page on the site itself).
