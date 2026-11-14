"""
Export the processed NISAR + Sentinel-1 results into the website format (web/DATA_CONTRACT.md).

Run it in the folder where you ran process.py. It reads:
    data/raw/ + web/data/data.json          (NISAR, from `python process.py`)
    data/raw_s1/ + web/data/s1/data.json    (Sentinel-1, from `python process.py --raw data/raw_s1 ...`)
    lake.geojson                            (from get_lake.py)
and writes the website's manifest.json + frames/*.png:

    python export_web.py --out ~/Desktop/ostsee-rising/web/data

What it does for every date:
  * radar layers: same speckle filter as process.py, reprojected to plain lat/lon (EPSG:4326),
    cropped to BOUNDS, one fixed dB stretch per polarization (no flicker), grayscale PNG
  * water layer: the exact water rule process.py used (thresholds read from its data.json),
    only inside the lake outline + 300 m, as a blue RGBA PNG
  * area_km2: the lake area process.py measured (computed in metres, not from the PNG)
"""
import argparse
import glob
import json
import os

import numpy as np
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.ndimage import binary_opening

import process as P  # same folder: reuse the exact grid, filter and lake outline

# Lake (OSM: lat 51.750-51.810, lon 14.380-14.468) plus ~1 km margin. [[south, west], [north, east]]
BOUNDS = [[51.74, 14.36], [51.82, 14.49]]
PIXEL_M = 25  # output pixel size, roughly
STRETCH = {"VV": [-25, 0], "VH": [-30, -5], "HH": [-25, 0], "HV": [-30, -5]}
WATER_RGBA = (40, 120, 255, 200)

# Official measurements (LEAG, the mine operator) - shown as diamonds on the chart
OFFICIAL = [
    {"date": "2023-09-05", "area_km2": 5.9,
     "source": "LEAG report to Cottbus city council, 07.09.2023 (water level +57.9 m)"},
    {"date": "2024-02-16", "area_km2": 17.3,
     "source": "LEAG report to the Brandenburg lignite committee, 14.03.2024"},
    {"date": "2024-12-23", "area_km2": 18.8,
     "source": "LEAG: target level +62.5 m reached 23.12.2024; target water area 18.8 km2"},
]

SENSORS = [
    {"name": "Sentinel-1", "tag": "s1", "raw": "data/raw_s1", "result": "web/data/s1/data.json",
     "co": "VV", "cross": "VH"},
    {"name": "NISAR", "tag": "nisar", "raw": "data/raw", "result": "web/data/data.json",
     "co": "HH", "cross": "HV"},
]


def web_grid():
    (south, west), (north, east) = BOUNDS
    lat_mid = np.radians((south + north) / 2)
    width = int(round((east - west) * 111320 * np.cos(lat_mid) / PIXEL_M))
    height = int(round((north - south) * 110540 / PIXEL_M))
    transform = from_origin(west, north, (east - west) / width, (north - south) / height)
    return transform, width, height


def to_web(arr, utm_transform, web_transform, w, h, nearest=False):
    dst = np.full((h, w), np.nan if not nearest else 0, dtype="float32" if not nearest else "uint8")
    reproject(
        source=arr if not nearest else arr.astype("uint8"), destination=dst,
        src_transform=utm_transform, src_crs=f"EPSG:{P.EPSG}",
        dst_transform=web_transform, dst_crs="EPSG:4326",
        src_nodata=np.nan if not nearest else None, dst_nodata=np.nan if not nearest else None,
        resampling=Resampling.nearest if nearest else Resampling.bilinear,
    )
    return dst


def save_gray(path, db, lo, hi):
    v = np.nan_to_num(np.clip((db - lo) / (hi - lo), 0, 1)) * 255
    Image.fromarray(v.astype("uint8"), "L").save(path, optimize=True)


def save_water(path, mask):
    rgba = np.zeros(mask.shape + (4,), dtype="uint8")
    rgba[mask.astype(bool)] = WATER_RGBA
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def thresholds(result, co, cross):
    """Water rule from process.py's data.json, e.g. {"VV": -14.0, "VH": -26.0}."""
    if "water_thresholds_db" in result:
        return result["water_thresholds_db"]
    # older process.py version: one threshold on one band
    band = result.get("water_band", co)
    band = band if band in (co, cross) else co
    return {band: result["water_threshold_db"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="the website's data folder, e.g. ../ostsee-rising/web/data")
    ap.add_argument("--lake", default="lake.geojson")
    args = ap.parse_args()

    out = os.path.expanduser(args.out)
    frames_dir = os.path.join(out, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    old = glob.glob(os.path.join(frames_dir, "*.png"))
    for f in old:
        os.remove(f)
    if old:
        print(f"Removed {len(old)} old PNGs from {frames_dir}")

    utm_transform, uw, uh, _ = P.common_grid()
    web_transform, ww, wh = web_grid()
    lake = P.load_lake(args.lake)
    if lake is None:
        raise SystemExit("lake.geojson not found - run get_lake.py first")
    zone = P.poly_mask(lake.buffer(300), utm_transform, (uh, uw))
    print(f"Web grid: {ww} x {wh} px (~{PIXEL_M} m), bounds {BOUNDS}")

    frames = []
    for s in SENSORS:
        if not os.path.exists(s["result"]):
            print(f"{s['name']}: {s['result']} not found, skipped")
            continue
        with open(s["result"]) as f:
            result = json.load(f)
        with open(os.path.join(s["raw"], "manifest.json")) as f:
            raw = {e["date"]: e for e in json.load(f)}  # duplicates collapse to one entry
        thr = thresholds(result, s["co"], s["cross"])
        print(f"{s['name']}: {len(result['dates'])} dates, water rule "
              + " AND ".join(f"{k} < {v} dB" for k, v in thr.items()))

        for rec in result["dates"]:
            date = rec["date"]
            e = raw[date.replace("-", "")]
            utm = {}
            for pol in (s["co"], s["cross"]):
                if pol in e["layers"]:
                    utm[pol] = P.despeckle(P.load(e["layers"][pol]["path"], utm_transform, uw, uh))

            water = np.ones((uh, uw), dtype=bool)
            for pol, t in thr.items():
                water &= np.isfinite(utm[pol]) & (utm[pol] < t)
            water = binary_opening(water, iterations=1) & zone

            layers = {}
            for pol, arr in utm.items():
                name = f"frames/{date}_{s['tag']}_{pol.lower()}.png"
                save_gray(os.path.join(out, name), to_web(arr, utm_transform, web_transform, ww, wh),
                          *STRETCH[pol])
                layers[pol] = name
            name = f"frames/{date}_{s['tag']}_water.png"
            save_water(os.path.join(out, name),
                       to_web(water, utm_transform, web_transform, ww, wh, nearest=True))
            layers["water"] = name

            frame = {"date": date, "sensor": s["name"], "area_km2": rec["lake_km2"], "layers": layers}
            if rec.get("maturity") == "BETA":
                frame["maturity"] = "BETA"  # pre-calibration NISAR data
            frames.append(frame)

    frames.sort(key=lambda f: f["date"])
    manifest = {"bounds": BOUNDS, "stretch": STRETCH, "official": OFFICIAL, "frames": frames}
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(frames_dir, "*.png"))) / 1e6
    print(f"\nDone: {len(frames)} frames, {size:.1f} MB of PNGs -> {out}/manifest.json")


if __name__ == "__main__":
    main()