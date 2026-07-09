#!/usr/bin/env python3
"""
Compute polygon-by-polygon specific yield from DWR SVSim Texture Data for the
SCNY RMS network polygons (both single and four-zone methods).

Method (Sacramento Valley Simulation Model TM-1B):
    Coarse-grained sediments -> Sy = 0.15
    Fine-grained sediments   -> Sy = 0.05
    Polygon Sy = (%coarse * 0.15) + (%fine * 0.05)
where %coarse = total coarse thickness over all valid boreholes in the polygon
/ total valid thickness, within the 0-500 ft below-ground analysis window.
Boreholes need >=200 ft of classified lithology to count.

Inputs:
  - <tmp>/svsim_scny/svsim_texture_data.csv   DWR CKAN 544623e2-... (~9 MB, auto-download)
  - js/polygons-data-single.js
  - js/polygons-data-four-zone.js

Outputs:
  - data/polygon_sy_svsim_single.csv
  - data/polygon_sy_svsim_four_zone.csv
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from pathlib import Path

import requests
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / "data"
JS_DIR = ROOT / "js"

POLY_VAR_BY_METHOD = {
    "single":    ("RMS_POLYGONS_SINGLE",    JS_DIR / "polygons-data-single.js"),
    "four-zone": ("RMS_POLYGONS_FOUR_ZONE", JS_DIR / "polygons-data-four-zone.js"),
}

SVSIM_DIR = Path(tempfile.gettempdir()) / "svsim_scny"
SVSIM_CSV = SVSIM_DIR / "svsim_texture_data.csv"
SVSIM_URL = ("https://data.cnra.ca.gov/dataset/"
             "5f06b5e9-39c2-411a-a39c-cc0e76e6a35f/resource/"
             "544623e2-0cd5-4c5b-827f-affa4abf4e16/download/"
             "svsim_texture_data.csv")

DEPTH_TOP_FT = 0
DEPTH_BASE_FT = 500
MIN_VALID_THICKNESS_FT = 200
SY_COARSE = 0.15
SY_FINE = 0.05

US_SURVEY_FT_TO_M = 0.3048006096
TRANSFORMER = Transformer.from_crs("EPSG:26910", "EPSG:4326", always_xy=True)

USCS_COARSE_PREFIX = ("G", "S")
USCS_FINE_PREFIX = ("M", "C", "O", "P")
USCS_FINE_LITERAL = {"TPSL", "HP", "TOPSOIL", "TUFF", "TUFF/ASH",
                     "VOLCANIC FRAGS", "ASH", "VLCU"}
USCS_SKIP_LITERAL = {"FILL", "ROCK", "XLN", "XLN/FRCT", "UNKNOWN", "NR", "N/A"}
COARSE_TERMS = ("sand", "gravel", "cobble", "boulder", "pebble", "grit",
                "sandstone")
FINE_TERMS = ("clay", "silt", "mud", "muck", "peat", "loam", "adobe",
              "hardpan", "hard pan", "topsoil", "top soil", "shale",
              "ash", "lava", "tuff")
SKIP_TERMS = ("rock", "unknown", "decomposed")


def _last_match(text, terms):
    best = -1
    for term in terms:
        i = text.rfind(term)
        if i > best:
            best = i
    return best


def classify_lith(uscs, lith_desc):
    u = (uscs or "").strip()
    uu = u.upper()
    if uu in USCS_SKIP_LITERAL:
        u = ""
    elif uu in USCS_FINE_LITERAL:
        return "fine"
    elif u:
        for ch in uu:
            if ch in USCS_COARSE_PREFIX:
                return "coarse"
            if ch in USCS_FINE_PREFIX:
                return "fine"
            if ch.isalpha():
                break
    d = (lith_desc or "").strip().lower()
    if not d:
        return None
    if any(t in d for t in SKIP_TERMS):
        return None
    coarse_at = _last_match(d, COARSE_TERMS)
    fine_at = _last_match(d, FINE_TERMS)
    if coarse_at < 0 and fine_at < 0:
        return None
    if fine_at < 0:
        return "coarse"
    if coarse_at < 0:
        return "fine"
    return "coarse" if coarse_at > fine_at else "fine"


def aggregate_borehole(layers):
    total = coarse = 0.0
    for L in layers:
        top = max(L["top"], DEPTH_TOP_FT)
        base = min(L["base"], DEPTH_BASE_FT)
        if base <= top or L["classification"] is None:
            continue
        thick = base - top
        total += thick
        if L["classification"] == "coarse":
            coarse += thick
    return total, coarse


def load_polygons_from_js(path, var_name):
    text = path.read_text()
    m = re.search(rf"const\s+{var_name}\s*=\s*(\[.*?\]);\s*$", text,
                  re.DOTALL | re.MULTILINE)
    return json.loads(m.group(1))


def polygons_to_shapely(polygons):
    """{zone_label: shapely (lon/lat) geometry}, honoring nested multipolygon
    + holes rings ([ [ext, hole...], [ext2, ...] ] with [lat,lng] points)."""
    out = {}
    for p in polygons:
        parts = []
        for poly in p["rings"]:
            ext = [(lng, lat) for lat, lng in poly[0]]
            holes = [[(lng, lat) for lat, lng in h] for h in poly[1:]]
            parts.append(Polygon(ext, holes))
        out[p["zone_label"]] = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return out


def ensure_svsim_csv():
    if SVSIM_CSV.exists() and SVSIM_CSV.stat().st_size > 1_000_000:
        return
    SVSIM_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading SVSim Texture Data -> {SVSIM_CSV} (~9 MB)...")
    with requests.get(SVSIM_URL, stream=True, timeout=300) as r:
        r.raise_for_status()
        with SVSIM_CSV.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def build_borehole_records():
    boreholes = {}
    with SVSIM_CSV.open(encoding="latin-1") as f:
        for row in csv.DictReader(f):
            name = row["SVSIM_NAME"]
            try:
                x_ft, y_ft = float(row["X"]), float(row["Y"])
                top, base = float(row["TOP_BGS"]), float(row["BASE_BGS"])
            except (ValueError, TypeError):
                continue
            if base <= top:
                continue
            cls = classify_lith(row.get("USCS", ""), row.get("LITH_DESC", ""))
            if name not in boreholes:
                lon, lat = TRANSFORMER.transform(x_ft * US_SURVEY_FT_TO_M,
                                                 y_ft * US_SURVEY_FT_TO_M)
                boreholes[name] = {"name": name, "lat": lat, "lon": lon, "layers": []}
            boreholes[name]["layers"].append(
                {"top": top, "base": base, "classification": cls})
    return list(boreholes.values())


def assign_boreholes(boreholes, polys_shapely):
    by_zone = {z: [] for z in polys_shapely}
    for bh in boreholes:
        pt = Point(bh["lon"], bh["lat"])
        for zone, geom in polys_shapely.items():
            if geom.contains(pt):
                by_zone[zone].append(bh)
                break
    return by_zone


def compute_sy(by_zone):
    rows = []
    for zone, bhs in by_zone.items():
        total = coarse = 0.0
        n_valid = 0
        for bh in bhs:
            t, c = aggregate_borehole(bh["layers"])
            if t < MIN_VALID_THICKNESS_FT:
                continue
            n_valid += 1
            total += t
            coarse += c
        pct = coarse / total if total > 0 else None
        sy = pct * SY_COARSE + (1 - pct) * SY_FINE if pct is not None else None
        rows.append({"zone_label": zone, "n_bh": len(bhs), "n_valid": n_valid,
                     "total_thick_ft": round(total), "coarse_thick_ft": round(coarse),
                     "pct_coarse": round(pct, 4) if pct is not None else None,
                     "sy": round(sy, 4) if sy is not None else None})
    return rows


def run_method(method, boreholes):
    var_name, poly_js = POLY_VAR_BY_METHOD[method]
    print(f"\n=== {method} ({poly_js.name}) ===")
    polys = polygons_to_shapely(load_polygons_from_js(poly_js, var_name))
    rows = compute_sy(assign_boreholes(boreholes, polys))

    print(f"{'Polygon':<18} {'n_bh':>5} {'>=200':>6} {'%coarse':>8} {'Sy':>8}")
    print("-" * 50)
    for r in rows:
        sy_s = f"{r['sy']:.4f}" if r["sy"] is not None else "n/a"
        pc_s = f"{r['pct_coarse']*100:.1f}%" if r["pct_coarse"] is not None else "n/a"
        print(f"{r['zone_label']:<18} {r['n_bh']:>5} {r['n_valid']:>6} {pc_s:>8} {sy_s:>8}")

    suffix = method.replace("-", "_")
    out_csv = DATA_DIR / f"polygon_sy_svsim_{suffix}.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone_label", "n_boreholes_in_polygon",
                    "n_boreholes_with_>=200ft_valid", "total_thick_ft",
                    "coarse_thick_ft", "pct_coarse", "sy"])
        for r in rows:
            w.writerow([r["zone_label"], r["n_bh"], r["n_valid"],
                        r["total_thick_ft"], r["coarse_thick_ft"],
                        f"{r['pct_coarse']:.4f}" if r["pct_coarse"] is not None else "",
                        f"{r['sy']:.4f}" if r["sy"] is not None else ""])
    n_fb = sum(1 for r in rows if r["sy"] is None)
    print(f"Wrote {out_csv.name}  ({n_fb} polygons w/o SVSim coverage -> basin-mean fallback in dashboard)")


def main():
    methods = ["single", "four-zone"]
    if len(sys.argv) > 1 and sys.argv[1] in POLY_VAR_BY_METHOD:
        methods = [sys.argv[1]]
    ensure_svsim_csv()
    print(f"Loading SVSim boreholes from {SVSIM_CSV}...")
    boreholes = build_borehole_records()
    print(f"  {len(boreholes):,} boreholes total in SVSim")
    for m in methods:
        run_method(m, boreholes)


if __name__ == "__main__":
    main()
