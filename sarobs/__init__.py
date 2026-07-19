"""sarobs - storm-tasked SAR ocean-surface wind product.

Discovers per-storm Level-2 SAR wind files from the NOAA/NESDIS/STAR/SOCD
tropical SAR listing (page-parse; the directory autoindex is closed), renders
each pass in house style on STAR/SOCD's published wind-speed color scale, and
publishes a per-storm indexed archive to R2 under ``sar/``:

  sar/manifest.json        storm index (newest activity first)
  sar/{slug}/index.json    per-storm pass index (newest first)
  sar/{slug}/{stem}.png    rendered pass
  sar/{slug}/{stem}_th.jpg pass thumbnail

Never-miss poller contract: the live R2 manifest/indexes are the watermark
(no local state), only unseen passes are fetched/rendered (idempotent
backfill), uploads are pass-files-first / index / manifest-last, and every
per-pass step is fault-isolated so one bad file never kills a tick.
"""
