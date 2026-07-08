# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-08 ~22:40 UTC_

---

## MY QUEUE (Andrew's hands / decisions, ordered)

**① Box session (Hostinger, ~10 min) — lights the whole 27-product explorer (53 with full-disk).**
On the box, in the tsr repo dir (Docker only — no host pip, no cred paste):

```bash
git fetch origin && git checkout s2-sat-ingest && git pull
docker compose -p tat-s2 -f docker-compose.s2.yml build emit
docker compose -p tat-s2 -f docker-compose.s2.yml run --rm lifecycle --days 10
docker compose -p tat-s2 -f docker-compose.s2.yml run --rm emit \
    --suite conus --store r2 --prefix shadow --max-zoom 5
# optional continuous loop (Q7-tiered, 15-min default):
docker compose -p tat-s2 -f docker-compose.s2.yml --profile cron up -d emit-cron
```

Then: https://triple-a-tropics.com/satellite/explorer/ (picker top-left) and
…/explorer/compare.html. Credless check:
`curl https://cdn.triple-a-tropics.com/shadow/sat/goes19/conus/products.json`.
Claude polls the CDN and live-verifies once frames land. Full detail:
`RUNBOOK-S2.md` on tsr `s2-sat-ingest`.

**② Decision (art): HAFS env-color v0.12 on the live worker** — say go and
Claude repins `hafs-render-worker` hafs-render v0.11.0→v0.12.0 (Railway
auto-rebuilds; restyles the 9 env products to the palette look on TAT main
since 07-01).

**③ Decision (art, month-old): TCHP records hatching** — crops at
`https://cdn.triple-a-tropics.com/sst/records/review/<region>_tchp_anom.png`
(east-pacific / north-atlantic / western-atlantic / global-tropics). Say go
(Claude merges + disclosure + temp-workflow cleanup) or park.

**④ One-clicks, LOW:** close stale [PR #24](https://github.com/WeathermanAAA/Triple-A-Tropics/pull/24)
(ASCAT shipped via main 06-28; permission gate blocked Claude closing it);
optional $10 AWS budget alarm (console/root only).

## LANDED (with SHAs + artifacts)

- **Sat explorer Phase 3 — multi-product imagery suite** (2026-07-08):
  tsr `s2-sat-ingest` `a00fa8e` + review-fix `52a3306`; TAT main `d0b7c7c`
  (+ review-fix, see git log). 24 registry-driven products — true color
  (frozen GeoColor-lite pipeline verbatim), Sandwich, Air Mass, Dust, Fire
  Temperature, Day Cloud Phase, Nighttime Microphysics, all 16 ABI channels
  (+ Dvorak BD) — recipe engine + generated registry rows ("add a product =
  a config row"), suite emit off one pinned scan with shared band fetches,
  dockerized box service, viewer product picker + per-pane compare products +
  per-product colorbars (exact-norm tick placement). All 5 RGB scalings
  verified against the CIRA/RAMMB quick-guide PDFs; 4-finding adversarial
  review folded (tsr `52a3306` + TAT `da0a66e`).
  Artifact: https://claude.ai/code/artifact/22ac0c0b-3cfe-4ab5-aad8-53291ee55783
- **Suite extension** (2026-07-08, tsr `7902a98` + TAT, see git log): **Ash,
  Day Convection, Day Land Cloud/Natural Color** (primary-source verified —
  Day Convection green gamma 1 per the ABI guide, not EUMETSAT's 0.5 heritage)
  + **full-disk rows for every recipe** (goes19-fd-*, config-only; fd anchor
  path verified on a real CMIPF scan) + C13 BT rasters on the IR-window RGBs
  so the inspector works there. Registry now 27 CONUS + 26 FD tiled products;
  106 s2 tests green. Same box command lights everything: the cron form covers
  `--suite conus`; full disk = `--suite fd` (heavier, on-demand).
- **Phase 2d 90-frame WebM export** (recovered from dead session, `0906d09`) +
  **Q7 tiering plumbing** (tsr `ced02e1`: `--max-zoom`, lifecycle-TTL script).
- Earlier phases: 2a pyramid emitter `dfde476` ·
  [artifact](https://claude.ai/code/artifact/ec072fec-e15e-44d7-9fda-a4f6a073da0f);
  2b webmerc viewer `059ae48`/`3fe538c` ·
  [artifact](https://claude.ai/code/artifact/23740da2-ee95-4037-b1d7-94f18d4bb392);
  2c compare + BT inspector `9692eb4`/`5822abb` ·
  [artifact](https://claude.ai/code/artifact/5bc85d26-a907-4f65-bfd1-09ca896e4619).

## IN PROGRESS

- Nothing mid-flight. Next natural steps (will pick up autonomously): Day
  Snow-Fog RGB (verified numbers on file), fd sector in the viewer picker,
  live-verify + AGENT_STATUS update once the box emit (queue ①) runs.

## BLOCKERS

- None. Shadow explorer awaits the box emit (queue ①) to show real tiles —
  by design, not a defect. Live site unaffected throughout.

## HEALTH SNAPSHOT (2026-07-08 ~18 UTC sweep)

All pages 200 and fresh; feeds 4–10 min old; all 7 workflows green; Typhoon
BAVI (WP09, C4 125 kt) + invest 97W active; AL/EP quiet. Railway: nothing new
beyond the two verified palette repins (07-05/07-06).
