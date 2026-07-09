#!/usr/bin/env python3
"""
Resolve the SCNY RMS well roster: read the workbook, spatially filter to the
SCNY region boundary, and assign each well to one of the 4 zones.

Reads:
  - ../Colusa_Yolo_RMS.xlsx        RMS wells for Colusa + Yolo counties
  - raw/scny_region.geojson        SCNY dissolved boundary (from build_boundaries.py)
  - raw/scny_zones.geojson         4 zones: CCWD, RD108, Dunnigan, Other

Writes:
  - data/wells_resolved.json       In-boundary wells with normalized schema + zone
  - data/wells_excluded.csv        Wells dropped (outside SCNY or missing coords)

Zone assignment: point-in-polygon against the 4 zones (which tile SCNY
exactly). A point that lands on a shared edge and matches no zone is
assigned to the nearest zone centroid, flagged with zone_by='nearest'.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
XLSX = (ROOT / ".." / "Colusa_Yolo_RMS.xlsx").resolve()
REGION = ROOT / "raw" / "scny_region.geojson"
ZONES = ROOT / "raw" / "scny_zones.geojson"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

WGS84 = "EPSG:4326"
ALBERS = "EPSG:3310"

# workbook column -> internal field
COLMAP = {
    "Well Key": "well_key",
    "Associated GSA/Alternative": "gsa",
    "Site Code": "site_code",
    "Local Well Name": "local_name",
    "State Well Number": "swn",
    "Monitoring Network Type": "network_type",
    "Reference Point Elevation (feet)": "rpe",
    "Ground Surface Elevation (feet)": "gse",
    "Well Use Type": "well_use",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Principal Aquifer": "principal_aquifer",
    "Well Completion Type": "well_completion_type",
    "Well Depth \n(feet bgs)": "well_depth",
    "County": "county",
    "Top Perforation": "top_perf",
    "Bottom Perforation": "bottom_perf",
    "Measurable Objective\n(feet)": "mo_ft",
    "Minimum Threshold\n(feet)": "mt_ft",
    "5-Year Interim Milestone\n(feet)": "im5_ft",
    "10-Year Interim Milestone\n(feet)": "im10_ft",
    "15-Year Interim Milestone\n(feet)": "im15_ft",
}


def _clean(v):
    if pd.isna(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return v  # keep numeric
    return v


def main() -> None:
    df = pd.read_excel(XLSX, sheet_name="Sheet1")
    df = df.rename(columns=COLMAP)
    n_total = len(df)

    # normalize a stable well_id
    df["well_id"] = df["swn"].where(df["swn"].notna(), df["local_name"])

    # missing coordinates
    bad_coord = df["latitude"].isna() | df["longitude"].isna()
    excluded = []
    for _, r in df[bad_coord].iterrows():
        excluded.append({"well_id": r["well_id"], "site_code": r.get("site_code"),
                         "reason": "missing lat/lon"})
    dfc = df[~bad_coord].copy()

    gdf = gpd.GeoDataFrame(
        dfc,
        geometry=[Point(xy) for xy in zip(dfc["longitude"], dfc["latitude"])],
        crs=WGS84,
    )

    region = gpd.read_file(REGION).to_crs(WGS84)
    region_geom = region.geometry.union_all()
    inside_mask = gdf.geometry.within(region_geom)

    for _, r in gdf[~inside_mask].iterrows():
        excluded.append({"well_id": r["well_id"], "site_code": r.get("site_code"),
                         "reason": "outside SCNY boundary"})

    ins = gdf[inside_mask].copy()

    # zone assignment
    zones = gpd.read_file(ZONES).to_crs(WGS84)
    zones_a = zones.to_crs(ALBERS)
    ins_a = ins.to_crs(ALBERS)

    def assign_zone(pt_a):
        hit = zones_a[zones_a.geometry.contains(pt_a)]
        if len(hit):
            return hit.iloc[0]["zone"], "within"
        # nearest zone by distance
        d = zones_a.geometry.distance(pt_a)
        return zones_a.loc[d.idxmin(), "zone"], "nearest"

    zone_vals, zone_by = [], []
    for pt in ins_a.geometry:
        z, how = assign_zone(pt)
        zone_vals.append(z)
        zone_by.append(how)
    ins["zone"] = zone_vals
    ins["zone_by"] = zone_by

    # build output records
    keep_fields = ["well_id", "swn", "local_name", "site_code", "gsa",
                   "network_type", "well_use", "principal_aquifer",
                   "well_completion_type", "county", "latitude", "longitude",
                   "gse", "rpe", "well_depth", "top_perf", "bottom_perf",
                   "mo_ft", "mt_ft", "im5_ft", "im10_ft", "im15_ft",
                   "zone", "zone_by"]
    records = []
    for _, r in ins.iterrows():
        rec = {f: _clean(r.get(f)) for f in keep_fields}
        rec["is_rms"] = True
        records.append(rec)

    (DATA / "wells_resolved.json").write_text(json.dumps(records, indent=2, default=str))
    if excluded:
        pd.DataFrame(excluded).to_csv(DATA / "wells_excluded.csv", index=False)

    # --- report ---
    print(f"Workbook wells:        {n_total}")
    print(f"  missing coords:      {int(bad_coord.sum())}")
    print(f"  outside SCNY:        {int((~inside_mask).sum())}")
    print(f"  INSIDE SCNY (kept):  {len(records)}")
    print(f"\nZone distribution (inside SCNY):")
    zc = ins["zone"].value_counts()
    for z in ["CCWD", "RD108", "Dunnigan", "Other"]:
        print(f"  {z:10s} {int(zc.get(z, 0)):>4}")
    near = ins[ins["zone_by"] == "nearest"]
    if len(near):
        print(f"  (edge-assigned by nearest: {len(near)})")
    print(f"\nNetwork types:")
    for k, v in ins["network_type"].value_counts().items():
        print(f"  {k}: {v}")
    n_missing_sc = int(ins["site_code"].isna().sum())
    print(f"\nMissing site_code among kept: {n_missing_sc}")
    dup = ins["site_code"].dropna()
    dups = dup[dup.duplicated(keep=False)]
    if len(dups):
        print(f"Duplicate site_codes: {sorted(dups.unique())}")
    print(f"\nWrote data/wells_resolved.json ({len(records)} wells)")
    if excluded:
        print(f"Wrote data/wells_excluded.csv ({len(excluded)} rows)")


if __name__ == "__main__":
    main()
