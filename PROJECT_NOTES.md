# SCNY Storage Dashboard — Project Notes

A drought-conditioned groundwater storage dashboard for the **SCNY region**,
built with the same methodology as the Vina Subbasin dashboard
(`butte_github/2027-BC-Storage`), adapted to a **4-zone** framework.

## Zones (4)
CCWD, RD108, Dunnigan, and **Other** (SCNY residual = region minus the three
named districts). Analogous to Vina's three management areas, but four.

## Two-layer architecture (mirrors Vina)
- **Layer 1 — Network build** (Vina's `2027-BC-prop-network`): well roster +
  boundaries → Voronoi polygons (single basin-wide + per-zone), wells JS,
  measurements JS.
- **Layer 2 — Storage build** (Vina's `2027-BC-Storage`): polygons + SVSim
  Sy + measurements → ΔStorage, SVI year-type buckets, year-type-weighted
  normalization, 2042 project portfolio, single-file `index.html` with a
  **single ↔ 4-zone toggle**.

Ported into one self-contained SCNY repo (no separate prop-network repo) to
avoid maintaining two coupled repos for a new agency.

## Progress
- [x] Confirmed scope with user: full replica, 4 zones, SCNY-wide.
- [x] `scripts/build_boundaries.py` → `raw/scny_zones.geojson` (4 disjoint
      zones, clipped to SCNY, tiling exactly) + `raw/scny_region.geojson`.
- [x] Well roster: `scripts/build_wells.py` reads `../Colusa_Yolo_RMS.xlsx`
      (106 RMS wells, all "SGMA Representative"), spatially filters to SCNY
      → **27 wells inside** (79 excluded, in `data/wells_excluded.csv`),
      zone-assigned. `data/wells_resolved.json`.
      Zones: Other 13 / RD108 7 / CCWD 6 / **Dunnigan 1**.
- [x] Measurements: `scripts/fetch_measurements.py` pulls DWR CKAN periodic
      GWL for the 27 site_codes → `data/measurements.json` (24,391 records,
      all 27 wells; every well has ≥1 Good-QA March record).
- [x] Polygons: `scripts/build_polygons.py` builds BOTH methods with
      `shapely.voronoi_polygons` (ordered), clipped to region / zone
      boundaries, Dunnigan (1 well) as a dissolved aggregate. Output
      `js/polygons-data-single.js` + `js/polygons-data-four-zone.js`.
      Both: 27 polygons, geometrically valid, total area = 296,958 ac exactly.
      Rings emitted as nested multipolygon+holes (Leaflet convention);
      lightly simplified for display (15 m tol, drop <2 ac slivers/holes) —
      area_acres computed from FULL unsimplified Albers geometry.
- [x] Sy: `scripts/build_sy_svsim.py` (requests download to temp, nested-rings
      loader) → `data/polygon_sy_svsim_{single,four_zone}.csv`. All 27 polygons
      got a real SVSim Sy (0 fallbacks), range ~0.056–0.099.
- [x] JS wrappers: `scripts/build_js.py` → `js/wells-data.js` +
      `js/measurements-data.js`.
- [x] Ported `build_dashboard.py` + `build_html.py` (copied from Vina, then
      edited: local paths, METHODS=[single,four-zone], `flatten_rings()` shim
      for nested rings + fill-rule:evenodd, SCNY labels/titles, placeholder
      constants). **`index.html` builds and renders** — toggle works, 6 charts,
      2 interactive Leaflet maps (54 polys), headline numbers compute.
- [~] Constants: researched — Colusa Subbasin GSP gives storage 26–140 MAF over
      the WHOLE 723,823-ac subbasin (not the 297k-ac SCNY footprint); no
      published sub-region sustainable yield. So still PLACEHOLDER
      (SY=200k AFY, storage=10 MAF). Needs a project-specific basis
      (area-scale the subbasin figure to the SCNY footprint, or a GSP number).

## FIRST DRAFT RESULT (WY 1999–2025, no project portfolio yet)
| | single | four-zone |
|---|---:|---:|
| Basin net observed (AF) | −342,376 | −353,193 |
| Normalized cum 2025 (AF) | −336,518 | −321,098 |
| Observed avg loss rate (AF/yr) | 16,265 | 16,573 |
Preview: `python -m http.server` in the repo, open index.html (launch.json
"scny-storage" in butte_github/.claude). NOTE: screenshot tool stalls on
Leaflet CDN tiles — verified structurally instead; renders in a real browser.

## Remaining to go from draft → final
- Real agency constants (above).
- `data/project_portfolio.json` (per-well AF/yr) for the 2042 framing —
  currently empty, so recovery margins show as pure deficit.
- Write SCNY `README.md` (embedded as methodology section; currently absent).
- Optional: fix map section-letter labels (`zone[6:11]`), Dunnigan boundary
  2× area discrepancy, decide whether to render rangeland holes.

## Storage-basis note (needs a nod from user)
Storage is computed over the **`02_SCNY_Region_v3_no_rangeland` footprint =
296,958 ac** (rangeland excluded). The workbook's full SCNY attribute is
342,779 ac, but only the no_rangeland geometry exists as a shapefile.
Storage volumes scale with polygon area, so this footprint IS the storage
basis. If a full-area (incl. rangeland) basis is wanted, provide that boundary.

## Roster caveats to resolve with user
- Only 27 of 106 workbook wells fall inside SCNY. Nearest excluded well
  (`10N03E33B011M`) is just 108 m outside the boundary — confirm exclude.
- **Dunnigan zone = 1 well** (`12N01W05B001M`). Like Vina's Chico, this zone
  becomes a single dissolved polygon driven by one well.
- Several wells end their record early (March spans ending 2008–2022) — same
  late-baseline/gap issue Vina's normalization addresses.
- Agency constants still needed (sustainable yield, total storage, GSP cite).

## Zone geometry notes / caveats
- Named districts poke ~1,199 ac outside the SCNY `no_rangeland` boundary;
  `build_boundaries.py` clips them to SCNY so zones tile with zero gap.
- Areas after clip: CCWD 45,765 / RD108 58,714 / Dunnigan 10,421 /
  Other 182,058 / SCNY 296,958 acres.
- Dunnigan area: use the shapefile GEOMETRY area (11,060 ac raw / 10,421
  clipped) — confirmed correct by user. The `.dbf acres_lwa` (5,586) is the
  inaccurate one and is ignored (we never read it). RESOLVED.
- SCNY `.dbf lwa_acres` (342,779) > geometry area (296,958) because the
  `_no_rangeland` shapefile has rangeland removed. Using geometry area.

## Source of truth
Reference implementation:
`butte_github/2027-BC-Storage/` (README.md has the full methodology) and
`butte_github/2027-BC-prop-network/` (polygon + measurement builds).
