"""
Data availability check for the Cottbuser Ostsee.

Checks BOTH sources in NASA's ASF archive (no login needed just to search):
  1. NISAR  GCOV + GUNW   (2025-2026, lake already full)
  2. Sentinel-1 GRD IW    (2019-2024, the filling years)

Setup:  pip install asf_search
Run:    python check_data.py
"""
from collections import Counter, defaultdict

import asf_search as asf

# Small box over the lake itself (not the whole city)
LAKE = "POLYGON((14.38 51.76, 14.47 51.76, 14.47 51.82, 14.38 51.82, 14.38 51.76))"


def show_tracks(results, label):
    by_track = defaultdict(list)
    for r in results:
        p = r.properties
        key = (p.get("pathNumber"), p.get("flightDirection"))
        by_track[key].append(p["startTime"][:10])
    print(f"\n{label}: {len(results)} scenes")
    print(f"  {'track':>5} {'dir':<11} {'n':>4}  first -> last")
    for (track, d), dates in sorted(by_track.items(), key=lambda kv: -len(kv[1])):
        dates.sort()
        print(f"  {str(track):>5} {str(d):<11} {len(dates):>4}  {dates[0]} -> {dates[-1]}")
    return by_track


def main():
    # ---------- NISAR ----------
    for product in ("GCOV", "GUNW"):
        res = asf.search(dataset="NISAR", processingLevel=product, intersectsWith=LAKE)
        show_tracks(res, f"NISAR {product}")

    # ---------- Sentinel-1 ----------
    s1 = asf.search(
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.GRD_HD,
        beamMode=asf.BEAMMODE.IW,
        intersectsWith=LAKE,
        start="2019-01-01T00:00:00Z",
        end="2025-03-31T23:59:59Z",
    )
    tracks = show_tracks(s1, "Sentinel-1 GRD IW (2019 - Mar 2025)")

    # Per-year / per-month coverage for the best track
    if tracks:
        (best_track, best_dir), dates = max(tracks.items(), key=lambda kv: len(kv[1]))
        months = Counter(d[:7] for d in dates)
        years = Counter(d[:4] for d in dates)
        print(f"\nBest Sentinel-1 track: {best_track} {best_dir}")
        print("  scenes per year: " + ", ".join(f"{y}: {n}" for y, n in sorted(years.items())))
        all_months = [f"{y}-{m:02d}" for y in range(2019, 2025) for m in range(1, 13)]
        missing = [m for m in all_months if m not in months]
        print(f"  months covered 2019-2024: {72 - len(missing)}/72"
              + (f"  (missing: {', '.join(missing)})" if missing else ""))

        # Key validation dates from the official LEAG reports
        print("\n  Scenes closest to official measurement dates:")
        for target, label in (("2023-09-05", "5.9 km2"), ("2024-02-16", "17.3 km2"),
                              ("2024-12-23", "18.8 km2 (full)")):
            nearest = min(dates, key=lambda d: abs(
                (int(d[:4]) * 372 + int(d[5:7]) * 31 + int(d[8:])) -
                (int(target[:4]) * 372 + int(target[5:7]) * 31 + int(target[8:]))))
            print(f"    {target} ({label}): nearest scene {nearest}")


if __name__ == "__main__":
    main()