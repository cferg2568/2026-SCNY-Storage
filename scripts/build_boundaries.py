#!/usr/bin/env python3
"""
Build the SCNY boundary layer for the storage dashboard (Layer-1 groundwork).

Reads (from ../Shapefiles/, never modifies):
  - 02_SCNY_Region_v3_no_rangeland.shp   Overall SCNY region (rangeland removed)
  - CCWD.shp                             Colusa County Water District
  - RD108.shp                            Reclamation District 108
  - Dunnigan.shp                         Dunnigan Water District

Produces the 4-zone tessellation-clip layer used by build_polygons.py:
  - raw/scny_region.geojson   Single dissolved SCNY boundary (basin-wide clip)
  - raw/scny_zones.geojson    4 zones: CCWD, RD108, Dunnigan, Other (residual)

Zone construction:
  * Named districts (CCWD, RD108, Dunnigan) are CLIPPED to the SCNY region so
    the ~1,199 ac that pokes outside the region is dropped and the 4 zones tile
    SCNY exactly with no overlap.
  * "Other" = SCNY  minus  (CCWD union RD108 union Dunnigan).
  * If the named districts overlap each other, precedence is assigned in the
    order listed in ZONE_SOURCES (earlier wins) so the zones stay disjoint.

All geometry math is done in EPSG:3310 (NAD83 California Albers, equal-area
meters); output is written back in EPSG:4326 (WGS84 lat/lon) to match the
Vina raw/*.geojson convention.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHP_DIR = (ROOT / ".." / "Shapefiles").resolve()
RAW_OUT = ROOT / "raw"
RAW_OUT.mkdir(parents=True, exist_ok=True)

WGS84 = "EPSG:4326"
ALBERS = "EPSG:3310"
ACRES_PER_M2 = 1.0 / 4046.8564224

SCNY_SHP = SHP_DIR / "02_SCNY_Region_v3_no_rangeland.shp"

# Order matters: earlier zones win where named districts overlap each other.
ZONE_SOURCES = [
    ("CCWD",     "CCWD.shp",     "Colusa County WD"),
    ("RD108",    "RD108.shp",    "Reclamation District 108"),
    ("Dunnigan", "Dunnigan.shp", "Dunnigan WD"),
]
RESIDUAL_ZONE = ("Other", "Other (SCNY residual)")


def _dissolved_albers(shp: Path):
    """Load a shapefile, reproject to Albers, return one dissolved geometry."""
    g = gpd.read_file(shp).to_crs(ALBERS)
    return unary_union(g.geometry.values)


def main() -> None:
    scny = _dissolved_albers(SCNY_SHP)
    scny = scny.buffer(0)  # heal any self-intersections before boolean ops

    # Clip each named district to SCNY, keeping zones mutually disjoint.
    consumed = None  # union of zones already assigned (earlier precedence)
    zone_geoms: dict[str, object] = {}
    for key, fname, _label in ZONE_SOURCES:
        geom = _dissolved_albers(SHP_DIR / fname).buffer(0).intersection(scny)
        if consumed is not None:
            geom = geom.difference(consumed)
        zone_geoms[key] = geom
        consumed = geom if consumed is None else unary_union([consumed, geom])

    # Residual "Other" zone = SCNY minus everything named.
    zone_geoms[RESIDUAL_ZONE[0]] = scny.difference(consumed)

    # --- write the 4-zone layer -----------------------------------------
    labels = {k: lbl for k, _f, lbl in ZONE_SOURCES}
    labels[RESIDUAL_ZONE[0]] = RESIDUAL_ZONE[1]
    order = [k for k, _f, _l in ZONE_SOURCES] + [RESIDUAL_ZONE[0]]

    zones_gdf = gpd.GeoDataFrame(
        {
            "zone": order,
            "zone_label": [labels[k] for k in order],
            "acres": [zone_geoms[k].area * ACRES_PER_M2 for k in order],
        },
        geometry=[zone_geoms[k] for k in order],
        crs=ALBERS,
    ).to_crs(WGS84)
    zones_path = RAW_OUT / "scny_zones.geojson"
    zones_path.write_text(zones_gdf.to_json())

    # --- write the dissolved region ------------------------------------
    region_gdf = gpd.GeoDataFrame(
        {"name": ["SCNY"], "acres": [scny.area * ACRES_PER_M2]},
        geometry=[scny],
        crs=ALBERS,
    ).to_crs(WGS84)
    region_path = RAW_OUT / "scny_region.geojson"
    region_path.write_text(region_gdf.to_json())

    # --- report ---------------------------------------------------------
    print(f"SCNY region: {scny.area * ACRES_PER_M2:,.1f} acres")
    print("Zones (disjoint, clipped to SCNY):")
    total = 0.0
    for k in order:
        a = zone_geoms[k].area * ACRES_PER_M2
        total += a
        print(f"  {k:10s} {a:12,.1f} ac   ({labels[k]})")
    print(f"  {'TOTAL':10s} {total:12,.1f} ac   "
          f"(residual gap vs SCNY: {scny.area * ACRES_PER_M2 - total:,.1f} ac)")
    print(f"\nWrote {zones_path.relative_to(ROOT)}")
    print(f"Wrote {region_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
