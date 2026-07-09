#!/usr/bin/env python3
"""
Pull periodic GWL measurements for the in-boundary SCNY wells from the DWR
CKAN datastore (same source/resource as the Vina build).

Reads:
  - data/wells_resolved.json   (site_code per well; from build_wells.py)

Writes:
  - data/measurements.json     { site_code: [ {d, gwe, dtw, qa}, ... ] }
  - data/measurements_meta.json
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WELLS = ROOT / "data" / "wells_resolved.json"
OUT = ROOT / "data" / "measurements.json"
META = ROOT / "data" / "measurements_meta.json"

RESOURCE_ID = "bfa9f262-24a1-45bd-8dc8-138bc8107266"
CKAN_SEARCH = "https://data.cnra.ca.gov/api/3/action/datastore_search"
PAGE_SIZE = 32000


def fetch_for_site(sc: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        params = {
            "resource_id": RESOURCE_ID,
            "filters": json.dumps({"site_code": sc}),
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        url = CKAN_SEARCH + "?" + urllib.parse.urlencode(params)
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        j = r.json()
        if not j.get("success"):
            raise SystemExit(f"CKAN error for {sc}: {j}")
        recs = j["result"]["records"]
        rows.extend(recs)
        if len(recs) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main() -> None:
    wells = json.loads(WELLS.read_text())
    sites = sorted({w["site_code"] for w in wells if w.get("site_code")})
    print(f"Pulling measurements for {len(sites)} site_codes\n")

    by_site: dict[str, dict[str, dict]] = {}
    t0 = time.time()
    for i, sc in enumerate(sites, 1):
        recs = fetch_for_site(sc)
        for r in recs:
            d = (r.get("msmt_date") or "")[:10]
            if not d:
                continue
            try:
                gwe = float(r["gwe"]) if r.get("gwe") not in (None, "") else None
            except (TypeError, ValueError):
                gwe = None
            try:
                dtw = float(r["gse_gwe"]) if r.get("gse_gwe") not in (None, "") else None
            except (TypeError, ValueError):
                dtw = None
            qa = r.get("wlm_qa_desc") or ""
            by_site.setdefault(sc, {})[d] = {"d": d, "gwe": gwe, "dtw": dtw, "qa": qa}
        print(f"  [{i:>2}/{len(sites)}] {sc}  +{len(recs):>5} rows")

    out, n_records = {}, 0
    for sc, recs in by_site.items():
        srt = [recs[k] for k in sorted(recs)]
        out[sc] = srt
        n_records += len(srt)

    OUT.write_text(json.dumps(out))
    meta = {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_records": n_records,
        "n_wells": len(out),
        "source": "DWR CKAN datastore_search; resource " + RESOURCE_ID,
    }
    META.write_text(json.dumps(meta, indent=2))

    print(f"\nGot {n_records:,} records for {len(out)}/{len(sites)} wells "
          f"in {time.time()-t0:.1f}s")

    # per-well coverage, Good-QA spring focus
    id_by_sc = {w["site_code"]: (w.get("well_id"), w.get("zone")) for w in wells}
    print("\nCoverage (Good-QA record count, first->last year):")
    for sc in sites:
        recs = out.get(sc, [])
        good = [r for r in recs if r["qa"] == "Good" and r["gwe"] is not None]
        wid, zone = id_by_sc.get(sc, (sc, "?"))
        first = good[0]["d"][:4] if good else "—"
        last = good[-1]["d"][:4] if good else "—"
        flag = "  <-- NO Good GWE" if not good else ""
        print(f"  {str(wid):18} {zone:9} n_all={len(recs):>4} n_good={len(good):>4}"
              f"  {first}->{last}{flag}")


if __name__ == "__main__":
    main()
