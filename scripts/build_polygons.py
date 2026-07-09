#!/usr/bin/env python3
"""
Build the SCNY RMS polygon tessellations (Layer-1), two methods:

  single    One Voronoi tessellation across all 27 in-boundary RMS wells,
            clipped to the SCNY region boundary. One cell per well.

  four-zone Four INDEPENDENT tessellations, one per zone (CCWD, RD108,
            Dunnigan, Other), each clipped to its own zone boundary. Cells do
            not cross zone lines. A zone with a single well (Dunnigan) becomes
            one dissolved polygon = the whole zone boundary (is_aggregate).

Reads:
  - data/wells_resolved.json   (from build_wells.py — well_id, lat/lon, zone)
  - raw/scny_region.geojson
  - raw/scny_zones.geojson

Writes (Vina-compatible schema, consumed by build_dashboard.py):
  - js/polygons-data-single.js     const RMS_POLYGONS_SINGLE = [...]
  - js/polygons-data-four-zone.js  const RMS_POLYGONS_FOUR_ZONE = [...]

Polygon record fields:
  zone_label   unique polygon id  (= well_id; = zone name for an aggregate)
  rms_well_swn driving well_id     (null for an aggregate)
  rms_well_swns [well_ids]         (aggregate list)
  mgmt_area    zone (CCWD/RD108/Dunnigan/Other)
  is_aggregate bool
  area_acres   polygon area, EPSG:3310 equal-area
  rings        [[[lat, lng], ...]]  Leaflet convention
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from shapely import voronoi_polygons
from shapely.geometry import GeometryCollection, MultiPoint, Point, Polygon
from shapely.ops import transform
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WELLS = ROOT / "data" / "wells_resolved.json"
REGION = ROOT / "raw" / "scny_region.geojson"
ZONES = ROOT / "raw" / "scny_zones.geojson"
JS_DIR = ROOT / "js"
JS_DIR.mkdir(exist_ok=True)

WGS84 = "EPSG:4326"
ALBERS = "EPSG:3310"
ACRES_PER_M2 = 1.0 / 4046.8564224
ZONE_ORDER = ["CCWD", "RD108", "Dunnigan", "Other"]

_to_wgs = Transformer.from_crs(ALBERS, WGS84, always_xy=True).transform


# Rendering simplification (does NOT affect area_acres, which is computed from
# the full unsimplified Albers geometry). The no_rangeland region + district
# shapefiles are heavily fragmented; sub-threshold slivers are visual noise.
SIMPLIFY_TOL_M = 15.0
MIN_PART_ACRES = 2.0
MIN_HOLE_ACRES = 2.0


def _ring_coords(ring):
    return [[round(c[1], 6), round(c[0], 6)] for c in ring.coords]


def rings_latlng(geom_albers):
    """Reproject to WGS84 and return nested multipolygon rings for rendering.

    Structure: [ polygon, polygon, ... ] where each polygon is
    [ exterior_ring, hole_ring, ... ] and each ring is [[lat,lng], ...].
    This matches Leaflet's L.polygon(multipolygon-with-holes) nesting.

    Lightly simplified and sliver-filtered for display only.
    """
    geom = geom_albers.buffer(0).simplify(SIMPLIFY_TOL_M, preserve_topology=True)
    polys_out = []

    def add(poly):
        if poly.area * ACRES_PER_M2 < MIN_PART_ACRES:
            return
        w = transform(_to_wgs, poly)
        rings = [_ring_coords(w.exterior)]
        for interior, w_interior in zip(poly.interiors, w.interiors):
            if Polygon(interior).area * ACRES_PER_M2 >= MIN_HOLE_ACRES:
                rings.append(_ring_coords(w_interior))
        polys_out.append(rings)

    def walk(gg):
        t = gg.geom_type
        if t == "Polygon":
            add(gg)
        elif t in ("MultiPolygon", "GeometryCollection"):
            for part in gg.geoms:
                walk(part)

    walk(geom)
    return polys_out


def voronoi_cells(seed_pts, boundary):
    """Return {seed_index: clipped cell geometry} for seeds inside boundary.

    seed_pts: list of shapely Points (Albers). boundary: shapely (Albers).
    Uses ordered Voronoi so output cell i maps to input point i.
    """
    mp = MultiPoint(seed_pts)
    env = boundary.envelope.buffer(5000)  # pad so edge cells extend past boundary
    cells = voronoi_polygons(mp, extend_to=env, ordered=True)
    cells = list(cells.geoms) if isinstance(cells, GeometryCollection) else [cells]
    out = {}
    for i, cell in enumerate(cells):
        clipped = cell.buffer(0).intersection(boundary)
        if not clipped.is_empty and clipped.area > 0:
            out[i] = clipped
    return out


def build_method(method, wells, region_a, zones_a):
    """Return list of polygon records for the given method."""
    recs = []
    if method == "single":
        groups = [("__ALL__", region_a, wells)]
    else:
        groups = [(z, zones_a[z], [w for w in wells if w["zone"] == z])
                  for z in ZONE_ORDER]

    for gname, boundary, gw in groups:
        if not gw:
            continue
        if len(gw) == 1 and method == "four-zone":
            # single-well zone -> one dissolved aggregate polygon = zone boundary
            w = gw[0]
            recs.append({
                "zone_label": gname,
                "rms_well_swn": None,
                "rms_well_swns": [w["well_id"]],
                "mgmt_area": gname,
                "is_aggregate": True,
                "area_acres": round(boundary.area * ACRES_PER_M2, 1),
                "rings": rings_latlng(boundary),
            })
            continue

        seeds = [Point(w["_x"], w["_y"]) for w in gw]
        cells = voronoi_cells(seeds, boundary)
        for i, w in enumerate(gw):
            cell = cells.get(i)
            if cell is None:
                print(f"  ! {method}/{gname}: no cell for {w['well_id']}")
                continue
            recs.append({
                "zone_label": w["well_id"],
                "rms_well_swn": w["well_id"],
                "rms_well_swns": [w["well_id"]],
                "mgmt_area": w["zone"],
                "is_aggregate": False,
                "area_acres": round(cell.area * ACRES_PER_M2, 1),
                "rings": rings_latlng(cell),
            })
    return recs


def write_js(path, varname, recs, header):
    lines = [header, "", f"const {varname} = " + json.dumps(recs) + ";", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    wells = json.loads(WELLS.read_text())
    # project seeds to Albers once
    to_alb = Transformer.from_crs(WGS84, ALBERS, always_xy=True).transform
    for w in wells:
        w["_x"], w["_y"] = to_alb(w["longitude"], w["latitude"])

    region_a = gpd.read_file(REGION).to_crs(ALBERS).geometry.union_all().buffer(0)
    zdf = gpd.read_file(ZONES).to_crs(ALBERS)
    zones_a = {r["zone"]: r.geometry.buffer(0) for _, r in zdf.iterrows()}

    for method, var, fname in [
        ("single",    "RMS_POLYGONS_SINGLE",    "polygons-data-single.js"),
        ("four-zone", "RMS_POLYGONS_FOUR_ZONE", "polygons-data-four-zone.js"),
    ]:
        recs = build_method(method, wells, region_a, zones_a)
        hdr = (f"// Auto-generated by scripts/build_polygons.py - do not edit by hand.\n"
               f"// Method: {method}. {len(recs)} polygon entries from "
               f"{len(wells)} RMS wells.\n"
               f"// rings are arrays of [lat, lng] pairs (Leaflet convention).")
        write_js(JS_DIR / fname, var, recs, hdr)
        area = sum(r["area_acres"] for r in recs)
        n_agg = sum(1 for r in recs if r["is_aggregate"])
        print(f"{method:10s}: {len(recs):2d} polygons ({n_agg} aggregate), "
              f"total {area:,.0f} ac  -> js/{fname}")
        by_zone = {}
        for r in recs:
            by_zone[r["mgmt_area"]] = by_zone.get(r["mgmt_area"], 0) + 1
        print("            zones:", dict(by_zone))


if __name__ == "__main__":
    main()
