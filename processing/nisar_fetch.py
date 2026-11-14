"""
NISAR GCOV fetcher for the Cottbuser Ostsee study area.

Streams each GCOV file from NASA Earthdata and reads ONLY the pixels inside our
bounding box (no multi-GB downloads), then saves small GeoTIFFs in dB:

    data/raw/<YYYYMMDD>_HH.tif
    data/raw/<YYYYMMDD>_HV.tif
    data/raw/manifest.json

Setup (once):
    pip install earthaccess h5py pyproj rasterio numpy

Usage:
    python nisar_fetch.py --list
        -> shows every track/frame over Cottbus and its dates (pick one!)

    python nisar_fetch.py --track 109 --dir D --frame 062 --limit 1
        -> test on one file first (check the GeoTIFF looks right)

    python nisar_fetch.py --track 109 --dir D --frame 062
        -> fetch all dates for that track

You will be asked for your Earthdata username/password on first run
(or put them in ~/.netrc to skip the prompt).
"""
import argparse
import json
import os
import re
from collections import defaultdict

import earthaccess
import h5py
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

# Cottbus + Cottbuser Ostsee, with margin.  (West, South, East, North)
BBOX = (14.30, 51.72, 14.55, 51.85)
SHORT_NAME = "NISAR_L2_GCOV_PROVISIONAL_V1"
GRID = "/science/LSAR/GCOV/grids/frequencyA"
OUT_DIR = "data/raw"

# NISAR_L2_PR_GCOV_<cycle>_<track>_<A|D>_<frame>_<mode>_<pol>_<x>_<start>...
NAME_RE = re.compile(
    r"GCOV_(?P<cycle>\d{3})_(?P<track>\d{3})_(?P<dir>[AD])_(?P<frame>\d{3})_"
    r"(?P<mode>\d{4})_(?P<pol>\w{4})_\w_(?P<date>\d{8})T(?P<time>\d{6})"
)


def granule_name(g):
    return g["umm"]["GranuleUR"]


def parse(name):
    m = NAME_RE.search(name)
    if not m:
        raise ValueError(f"Unrecognised file name: {name}")
    return m.groupdict()


COLLECTIONS = {
    "PROVISIONAL": SHORT_NAME,               # calibrated, Jun 2026 onwards
    "BETA": "NISAR_L2_GCOV_BETA_V1",          # pre-calibration, Oct 2025 - early 2026
}


def search():
    out = []
    for maturity, short_name in COLLECTIONS.items():
        for g in earthaccess.search_data(short_name=short_name, bounding_box=BBOX):
            g["_maturity"] = maturity
            out.append(g)
    return out


def list_tracks(granules):
    groups = defaultdict(list)
    for g in granules:
        p = parse(granule_name(g))
        tag = "*" if g.get("_maturity") == "BETA" else ""
        groups[(p["track"], p["dir"], p["frame"])].append(p["date"] + tag)
    print(f"{len(granules)} GCOV files over the study area\n")
    print(f"{'track':>5} {'dir':>3} {'frame':>5} {'n':>3}  dates")
    for (track, d, frame), dates in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        dates = sorted(set(dates))
        print(f"{track:>5} {d:>3} {frame:>5} {len(dates):>3}  {', '.join(dates)}")
    print("\n* = BETA (pre-calibration). Pick the track with the most dates "
          "whose footprint fully covers the lake.")


def read_epsg(grid):
    proj = grid["projection"]
    for key in ("epsg_code", "epsg"):
        if key in proj.attrs:
            return int(proj.attrs[key])
    return int(proj[()])


def bbox_window(grid):
    """Row/col slices of the native grid that cover BBOX."""
    epsg = read_epsg(grid)
    to_native = Transformer.from_crs(4326, epsg, always_xy=True)
    w, s, e, n = BBOX
    xs, ys = to_native.transform([w, e, w, e], [s, s, n, n])
    x = grid["xCoordinates"][:]
    y = grid["yCoordinates"][:]

    cols = np.where((x >= min(xs)) & (x <= max(xs)))[0]
    rows = np.where((y >= min(ys)) & (y <= max(ys)))[0]
    if cols.size == 0 or rows.size == 0:
        return None
    c0, c1 = cols.min(), cols.max() + 1
    r0, r1 = rows.min(), rows.max() + 1

    dx = float(x[1] - x[0])
    dy = float(y[1] - y[0])
    y_flip = dy > 0  # we want north-up (y decreasing down the rows)
    top = (y[r1 - 1] if y_flip else y[r0]) + abs(dy) / 2
    transform = from_origin(x[c0] - dx / 2, top, dx, abs(dy))
    return (slice(r0, r1), slice(c0, c1)), transform, epsg, y_flip


def to_db(power):
    power = power.astype("float32")
    power[~np.isfinite(power) | (power <= 0)] = np.nan
    return 10.0 * np.log10(power)


def save_tif(path, arr, transform, epsg):
    with rasterio.open(
        path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype="float32", crs=f"EPSG:{epsg}", transform=transform,
        nodata=np.nan, compress="deflate",
    ) as dst:
        dst.write(arr, 1)


def fetch(granules, track, direction, frame, limit=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    def wanted(p):
        return (p["track"], p["dir"]) == (track, direction) and (
            frame is None or p["frame"] == frame)

    picked = [g for g in granules if wanted(parse(granule_name(g)))]
    picked.sort(key=lambda g: parse(granule_name(g))["date"])
    if limit:
        picked = picked[:limit]
    if not picked:
        print("No files for that track/dir/frame. Run --list first.")
        return

    print(f"Fetching {len(picked)} file(s) for track {track} {direction} frame {frame}")
    manifest = []
    best = {}  # date -> valid fraction already saved (some dates have 2 frames)
    for g in picked:
        name = granule_name(g)
        date = parse(name)["date"]
        if best.get(date, 0) >= 0.95:
            continue
        try:
            fobj = earthaccess.open([g])[0]  # one file at a time: a bad file can't stop the run
            with h5py.File(fobj, "r") as h:
                grid = h[GRID]
                win = bbox_window(grid)
                if win is None:
                    print(f"  {date}: study area not in this frame, skipped")
                    continue
                (rs, cs), transform, epsg, y_flip = win
                entry = {"date": date, "file": name, "epsg": epsg,
                         "sensor": "NISAR L-band", "maturity": g.get("_maturity"),
                         "layers": {}}
                arrays = {}
                for pol, ds in (("HH", "HHHH"), ("HV", "HVHV")):
                    if ds in grid:
                        arr = to_db(grid[ds][rs, cs])
                        arrays[pol] = arr[::-1] if y_flip else arr
                if "HH" not in arrays:
                    print(f"  {date}: no HH layer, skipped")
                    continue
                valid_hh = float(np.isfinite(arrays["HH"]).mean())
                if valid_hh <= best.get(date, -1):
                    print(f"  {date}: second frame covers less ({valid_hh:.0%}), kept first")
                    continue
                for pol, arr in arrays.items():
                    out = os.path.join(OUT_DIR, f"{date}_{pol}.tif")
                    save_tif(out, arr, transform, epsg)
                    entry["layers"][pol] = {
                        "path": out, "valid_fraction": round(float(np.isfinite(arr).mean()), 3)}
                best[date] = valid_hh
                manifest = [e for e in manifest if e["date"] != date] + [entry]
                summary = ", ".join(
                    "%s %.0f%%" % (k, v["valid_fraction"] * 100) for k, v in entry["layers"].items())
                tag = " (BETA)" if entry["maturity"] == "BETA" else ""
                print(f"  {date}: saved, valid pixels {summary}{tag}")
        except Exception as exc:  # keep going if one file fails
            print(f"  {date}: FAILED ({exc})")

    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nDone. {len(manifest)} date(s) written to {OUT_DIR}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list tracks and dates")
    ap.add_argument("--track", help="relative orbit, e.g. 109")
    ap.add_argument("--dir", choices=["A", "D"], help="A=ascending, D=descending")
    ap.add_argument("--frame", help="frame number, e.g. 062")
    ap.add_argument("--limit", type=int, help="only fetch the first N dates (testing)")
    args = ap.parse_args()

    earthaccess.login()
    granules = search()

    if args.list or not args.track:
        list_tracks(granules)
    else:
        frame = args.frame.zfill(3) if args.frame else None
        fetch(granules, args.track.zfill(3), args.dir, frame, args.limit)


if __name__ == "__main__":
    main()