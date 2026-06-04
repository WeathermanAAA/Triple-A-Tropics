// Parity harness for the per-basin live overlay (LIVE_BASIN_JS).
//
// The Python tests render the overlay script for a basin (with all
// __LIVE_*__ / __ICON_*__ tokens applied), write it to a temp .cjs file,
// and invoke this harness:
//
//   node js_harness.cjs <overlay.cjs> <input.json>
//
// input.json: {storms: [...], year: int, header: {...}, vocab: {...},
//              fmt1_values: [floats], fmt2_values: [floats]}
//
// Prints a JSON object of every built fragment so the Python side can
// assert byte-identical output against its own renderers
// (render_tracks_svg / render_active_icons / render_cards_html /
// render_panel_title_html / render_stats_html).
"use strict";

const fs = require("fs");
const path = require("path");

const overlay = require(path.resolve(process.argv[2]));
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const storms = input.storms || [];

const out = {
  tracks: overlay.buildTracksSvg(storms),
  active: overlay.buildActiveSvg(storms),
  cards: overlay.buildCardsHtml(storms),
  panel_title: overlay.buildPanelTitle(storms, input.year),
  stats: overlay.buildStatsHtml(input.header, input.vocab),
  marker_types: storms.map(overlay.markerType),
  fmt1: (input.fmt1_values || []).map(function (v) { return overlay.pyFixed(v, 1); }),
  fmt2: (input.fmt2_values || []).map(function (v) { return overlay.pyFixed(v, 2); }),
};
process.stdout.write(JSON.stringify(out));
