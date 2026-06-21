# Ensemble Cyclone Centers — per-system clustering (source of truth)

How the `/models/` Ensemble Cyclone Centers product groups ~30–50 ensemble member
cyclone centers into **per-system clusters** (one cluster = one storm), and why the
clustering is **b-deck–anchored**, not purely spatial.

Code: `generate_enscenters.py` → `enscenters/tracking.py` (clustering keystone) +
`enscenters/anchors.py` (the b-deck moving anchors). This doc is the prose; the code
comments are the detail.

---

## The pipeline (where clustering sits)

Per cycle, per model:

1. **Detect** closed-low centers per member/step from the MSLP field (self-detected
   models) or parse them from a native genesis tracker (GEFS) — `enscenters/detect.py`,
   `enscenters/warmcore.py`, `enscenters/tracks.py`.
2. **Warm-core filter** the self-detected centers (300–500 hPa thickness-max +
   Hart-B) so only tropical/warm-core lows survive — `enscenters/warmcore.py`.
   **This is DETECTION and is untouched by anything below.**
3. **Stage A — link** each member's surviving per-step centers into per-member tracks
   (`link_tracks`).
4. **Stage B — cluster** the member tracks into per-system clusters (this doc).
5. **Stage C — derive** per-cluster products: robust ensemble-mean track, intensity
   plume, position-covariance envelope.

Clustering (Stage B) is the only thing this doc covers. It operates on already-detected,
already-warm-core-filtered, already-linked member tracks — it is **association, not
detection**.

---

## Why spatial-only clustering fails

The original Stage B was **purely spatial**: union-find genesis seeds + HDBSCAN on a
track-to-track distance (mean great-circle separation over overlapping leads) +
a scale-adaptive merge/refine pass. It has **no notion of system identity or absolute
time**, which produces two real failure modes:

- **OVER-SPLIT** — one real system whose member centers **fan or recurve** along-track
  past the same-system scale (`SAME_SYSTEM_DEG = 6°`) breaks into **two** clusters. The
  ensemble is forecasting *one* storm with a spread/bifurcation of *future tracks*, but
  density sees two blobs and reports two systems.
- **UNDER-MERGE** — two genuinely separate systems that happen to sit **within ~6°**
  get unioned into a single seed and merged into **one** cluster. Two storms, reported
  as one.

Both come from the same root: density has only *positions*, not *which official system
a center belongs to*, and no moving frame of reference to follow a system forward.

---

## The fix: b-deck moving anchors (Stage B0)

Before the density method runs, **anchor every KNOWN system** on its official
position and **associate** member tracks to it.

### 1. Seed from the live designated + invest feed

Anchors come from the **same feed the home map uses**:
`https://cdn.triple-a-tropics.com/global_storms.geojson` — the FeatureCollection
`ace_core.build_global_geojson` publishes and `/global_tracks.html` renders. It already
carries **every active designated storm, PTC, and invest** (NHC AL/EP/CP + JTWC
WP/IO/SH via the poller's knackwx path) with a recent track. We reuse it instead of
re-fetching b-decks / `CurrentStorms.json` / knackwx ourselves.

`enscenters/anchors.py::anchors_from_geojson` turns each `active_marker` feature into
one `Anchor`; its **persistence motion** (heading + speed) comes from that storm's last
two `observation` fixes.

### 2. Forward-progress into a moving anchor track

`Anchor.position_at(step_h)` advances the latest fix along its heading at its speed via
the **exact great-circle destination formula** (`gc_destination`) — dateline-safe, valid
over multi-thousand-km projections (a flat-earth offset is not). The latest fix may be
newer/older than the model init; `age1_h` references the projection so each forecast
lead lands on the right position. So every system has a **projected anchor position at
every forecast hour** — a window that tracks the system forward.

### 3. Associate by nearest moving anchor

`enscenters/tracking.py::_associate_to_anchors`: a member track is **matched** to an
anchor when ≥ `ASSOC_MIN_FRAC` (0.35) of its fixes that overlap the anchor's leads fall
inside a **widening gate** `ASSOC_GATE0_DEG + ASSOC_GATE_GROW_DEG_PER_H · lead`
(5° + 0.04°/h ⇒ ~9.8° at 120 h — a cone that grows with anchor-projection uncertainty).
Among the anchors it matches, it joins the **nearest** (smallest mean separation).

This is what kills both bugs:

- **No over-split** — one system has **one** anchor. Even a member that recurves onto a
  divergent branch still rides the shared early/trunk portion, clearing `MIN_FRAC`, so
  the whole fan collapses onto the single anchor → **one cluster**.
- **No under-merge** — two systems have **two** anchors. Each member joins its *nearest*
  anchor, so the gate can be generous without merging neighbours → **two clusters**.
  (Nearest-anchor — not gate width — decides *which* system a track joins.)

### 4. Genesis still works (nothing is lost)

Tracks near **no** anchor (`best is None`) become **leftover** and are clustered by the
**unchanged density method** (`_density_cluster`) — so brand-new invests / genesis the
b-deck hasn't designated yet still form clusters. Anchored systems simply take priority.
An anchored bucket with fewer than `ASSOC_MIN_MEMBERS` (2) distinct members is *returned
to the leftover pool*, never deleted — an anchor can only re-group tracks, never drop
them. Each anchored cluster carries an additive `"anchor": {sid, name, is_invest}` label
(the current viewer ignores unknown keys).

---

## Isolation & safety

- **Detection untouched.** Warm-core filtering, center detection, and Stage A linkage
  are unchanged. This layer only *associates* already-detected centers.
- **Derived products untouched.** Mean track / vmax plume / envelope are computed
  per-cluster exactly as before.
- **No-anchor path is byte-identical.** `cluster_tracks(..., anchors=None)` ==
  `cluster_with_anchors(..., None)` == the original spatial clustering. Proven by
  `tests/test_enscenters_anchors.py::TestRegression`.
- **Never blocks the publish.** The live feed is fetched stdlib-only (the cron has no
  `requests`) and **any** failure — unreachable feed, malformed JSON, no active systems,
  or a **stale cycle** (an old `--tracks-only` backfill older than
  `ANCHOR_MAX_CYCLE_AGE_H = 30 h`, which must not borrow *today's* systems) — returns
  `[]` and reverts to density-only. Gated by `ENSCENTERS_ANCHORS` (set `0`/`false` to
  disable).

---

## Before / after (the two report failure cases)

Reproduced as hermetic fixtures in `tests/test_enscenters_anchors.py` — the **baseline**
asserts the bug, the **anchored** run asserts the fix:

| Case | Fixture | Density-only (before) | B-deck anchored (after) |
| --- | --- | --- | --- |
| **Over-split** (one recurving/fanning WPAC-style system) | `_bifurcating_system` (shared trunk → ±10° symmetric fork by F120) | **2 clusters** ❌ | **1 cluster** ✅ |
| **Under-merge** (two systems ~5° apart, parallel motion) | `_two_close_systems(sep_deg=5)` | **1 cluster** ❌ | **2 clusters** ✅ |
| Normal single system | `_line_member` | 1 | 1 (unchanged) |
| Genesis far from anchors | anchored + far W-Pacific low | n/a | 2 (1 anchored + 1 density, genesis not lost) |

`test_density_alone_over_splits` / `test_anchor_collapses_to_one` and
`test_density_alone_under_merges` / `test_two_anchors_split_into_two` are the
before/after assertions.

---

## Knobs (all in `enscenters/tracking.py` / `enscenters/anchors.py`)

| Constant | Default | Meaning |
| --- | --- | --- |
| `ASSOC_GATE0_DEG` | 5.0 | match radius at lead 0 (== genesis radius) |
| `ASSOC_GATE_GROW_DEG_PER_H` | 0.04 | gate growth per forecast hour (anchor-error cone) |
| `ASSOC_MIN_FRAC` | 0.35 | min fraction of a track's overlapping fixes inside the gate |
| `ASSOC_MIN_MEMBERS` | 2 | min distinct members for an anchored cluster (else → leftover) |
| `ANCHOR_MAX_CYCLE_AGE_H` | 30 | max cycle age to apply live anchors (older → density-only) |
| `SAME_SYSTEM_DEG` / `CLUSTER_MERGE_EPS_DEG` / `GENESIS_RADIUS_DEG` | 6 / 2.5 / 5 | the unchanged density-method scales |

**Edge note:** an *extreme* bifurcation (members fork wider than the long-lead gate cone)
may still surface the divergent branch as a separate **low-confidence genesis** cluster
rather than merging it — honest behaviour (the ensemble really is showing two plausible
tracks), not a silent merge. Widen `ASSOC_GATE_GROW_DEG_PER_H` if a specific basin needs
a fatter cone.
