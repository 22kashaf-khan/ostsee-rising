"""
Turn the GeoTIFFs from nisar_fetch.py into web-ready images + data.json.

For every date it:
  1. puts HH and HV on one common 20 m UTM grid (so all dates line up)
  2. reduces radar speckle (median filter)
  3. detects water: HH below one Otsu threshold computed over ALL dates
  4. measures water area in km^2
  5. exports PNGs for the map + web/data/data.json for the app

Setup:  pip install numpy rasterio scipy pillow pyproj
Run:
  NISAR:      python process.py                     (HH/HV from data/raw)
  Sentinel-1: python process.py --raw data/raw_s1 --out web/data/s1 --co VV --cross VH
Output: <out>/<date>_co.png, _cross.png, _rgb.png, _water.png, data.json

Lake-only area: the lake outline is taken from the date with the most water
(the full lake), then grown by ~200 m. Water inside that outline counts as lake;
the Spree and small ponds outside it are ignored.
"""
import argparse
import warnings
import json
import os

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.ndimage import binary_dilation, binary_opening, label, median_filter

try:  # optional: official lake outline (OpenStreetMap) for a trained threshold
    from rasterio.features import rasterize
    from shapely.geometry import mapping, shape
    import warnings
    from shapely.ops import transform as shp_transform
    warnings.filterwarnings("ignore", category=DeprecationWarning)
except ImportError:
    shape = None

BBOX = (14.30, 51.72, 14.55, 51.85)  # same as nisar_fetch.py (W, S, E, N)
EPSG = 32633                         # UTM zone 33N (Cottbus)
RES = 20.0                           # metres, matches NISAR 20 MHz mode
LAKE_CENTER = (14.42, 51.79)       # lon, lat inside the Cottbuser Ostsee
# Box tightly around the lake: water is ~40-50% of pixels here, so Otsu finds the
# water/land split (over the whole region water is only ~7% and Otsu splits
# fields from forest instead).
THRESH_BOX = (14.38, 51.76, 14.47, 51.82)
# The lake outline is never allowed outside this slightly larger box.
LAKE_BOX = (14.36, 51.75, 14.50, 51.83)

# Display ranges in dB (tweak if images look too dark/bright)
HH_RANGE = (-25.0, 0.0)
HV_RANGE = (-32.0, -8.0)
RATIO_RANGE = (0.0, 15.0)


def common_grid():
    t = Transformer.from_crs(4326, EPSG, always_xy=True)
    w, s, e, n = BBOX
    xs, ys = t.transform([w, e, w, e], [s, s, n, n])
    x0, x1 = np.floor(min(xs) / RES) * RES, np.ceil(max(xs) / RES) * RES
    y0, y1 = np.floor(min(ys) / RES) * RES, np.ceil(max(ys) / RES) * RES
    width, height = int((x1 - x0) / RES), int((y1 - y0) / RES)
    return from_origin(x0, y1, RES, RES), width, height, (x0, y0, x1, y1)


def load(path, transform, width, height):
    dst = np.full((height, width), np.nan, dtype="float32")
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1), destination=dst,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=f"EPSG:{EPSG}",
            src_nodata=np.nan, dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    return dst


def despeckle(db, size=5):
    nan = ~np.isfinite(db)
    filled = np.where(nan, np.nanmedian(db), db)
    out = median_filter(filled, size=size)
    out[nan] = np.nan
    return out


def otsu(values, bins=256):
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


def stretch(db, lo, hi):
    return np.nan_to_num(np.clip((db - lo) / (hi - lo), 0, 1))


def save_gray(path, db, rng):
    v = (stretch(db, *rng) * 255).astype("uint8")
    alpha = np.where(np.isfinite(db), 255, 0).astype("uint8")
    Image.fromarray(np.dstack([v, v, v, alpha]), "RGBA").save(path, optimize=True)


def save_rgb(path, hh, hv):
    r = stretch(hh, *HH_RANGE)
    g = stretch(hv, *HV_RANGE)
    b = stretch(hh - hv, *RATIO_RANGE)
    rgb = (np.dstack([r, g, b]) * 255).astype("uint8")
    alpha = np.where(np.isfinite(hh) & np.isfinite(hv), 255, 0).astype("uint8")
    Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(path, optimize=True)


def save_water(path, mask):
    img = np.zeros(mask.shape + (4,), dtype="uint8")
    img[mask] = (40, 140, 255, 200)  # semi-transparent blue
    Image.fromarray(img, "RGBA").save(path, optimize=True)


def corners_lonlat(bounds):
    x0, y0, x1, y1 = bounds
    t = Transformer.from_crs(EPSG, 4326, always_xy=True)
    # MapLibre image source order: top-left, top-right, bottom-right, bottom-left
    pts = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
    return [list(map(lambda v: round(v, 6), t.transform(x, y))) for x, y in pts]


def load_lake(path):
    """Lake polygon (lon/lat GeoJSON) -> polygon in our UTM grid."""
    if shape is None or not path or not os.path.exists(path):
        return None
    with open(path) as f:
        gj = json.load(f)
    geom = gj["features"][0]["geometry"] if "features" in gj else gj.get("geometry", gj)
    to_utm = Transformer.from_crs(4326, EPSG, always_xy=True).transform
    return shp_transform(to_utm, shape(geom))


def poly_mask(poly, transform, shp):
    if poly.is_empty:
        return np.zeros(shp, dtype=bool)
    return rasterize([(mapping(poly), 1)], out_shape=shp, transform=transform, fill=0).astype(bool)


def trained_threshold(values_water, values_land):
    """Threshold that best separates water (darker) from land: max balanced accuracy."""
    w = values_water[np.isfinite(values_water)]
    l = values_land[np.isfinite(values_land)]
    best_t, best_acc = None, -1
    for t in np.arange(-40.0, 0.01, 0.1):
        acc = 0.5 * ((w < t).mean() + (l >= t).mean())
        if acc > best_acc:
            best_t, best_acc = float(t), float(acc)
    return best_t, best_acc


def trained_threshold_2d(w_a, w_b, l_a, l_b, step=0.25):
    """Best pair (ta, tb) for the rule: water = a < ta AND b < tb (max balanced accuracy)."""
    edges = np.arange(-40.0, 0.0 + step, step)
    def prep(a, b):
        ok = np.isfinite(a) & np.isfinite(b)
        return np.clip(a[ok], -39.99, -0.01), np.clip(b[ok], -39.99, -0.01)
    w_a, w_b = prep(w_a, w_b)
    l_a, l_b = prep(l_a, l_b)
    hw, _, _ = np.histogram2d(w_a, w_b, bins=[edges, edges])
    hl, _, _ = np.histogram2d(l_a, l_b, bins=[edges, edges])
    cw = hw.cumsum(0).cumsum(1) / max(len(w_a), 1)
    cl = hl.cumsum(0).cumsum(1) / max(len(l_a), 1)
    acc = 0.5 * (cw + (1 - cl))
    i, j = np.unravel_index(np.argmax(acc), acc.shape)
    return float(edges[i + 1]), float(edges[j + 1]), float(acc[i, j])


def box_mask(box, transform, shape):
    t = Transformer.from_crs(4326, EPSG, always_xy=True)
    w, s, e, n = box
    xs, ys = t.transform([w, e, w, e], [s, s, n, n])
    rows, cols = np.indices(shape)
    x = transform.c + (cols + 0.5) * transform.a
    y = transform.f + (rows + 0.5) * transform.e
    return (x >= min(xs)) & (x <= max(xs)) & (y >= min(ys)) & (y <= max(ys))


def lake_zone(water_masks, transform):
    """Outline of the full lake: biggest water body on the wettest date, grown ~200 m."""
    wettest = max(water_masks, key=lambda m: m.sum())
    labels, n = label(wettest)
    if n == 0:
        return np.ones_like(wettest, dtype=bool)
    x, y = Transformer.from_crs(4326, EPSG, always_xy=True).transform(*LAKE_CENTER)
    col, row = ~transform * (x, y)
    lab = labels[int(row), int(col)] if 0 <= int(row) < labels.shape[0] and 0 <= int(col) < labels.shape[1] else 0
    if lab == 0:  # centre not water: fall back to the biggest component
        lab = np.bincount(labels.ravel())[1:].argmax() + 1
    zone = binary_dilation(labels == lab, iterations=int(200 / RES))
    return zone & box_mask(LAKE_BOX, transform, zone.shape)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="web/data")
    ap.add_argument("--co", default="HH", help="co-pol layer name (HH for NISAR, VV for Sentinel-1)")
    ap.add_argument("--cross", default="HV", help="cross-pol layer name (HV / VH)")
    ap.add_argument("--lake", default="lake.geojson",
                    help="official lake outline (from get_lake.py); used to train the threshold")
    ap.add_argument("--flood-start", default=None,
                    help="YYYYMMDD flooding began; earlier dates teach the model what the dry pit "
                         "(sand) looks like, e.g. 20190412 for Sentinel-1")
    ap.add_argument("--band", choices=["auto", "co", "cross", "both"], default="auto",
                    help="which polarization detects water (auto = whichever separates best)")
    ap.add_argument("--train-from", default=None,
                    help="only train on dates >= YYYYMMDD (lake full), e.g. 20241201 for Sentinel-1")
    args = ap.parse_args()
    raw, out, co, cross = args.raw, args.out, args.co, args.cross

    os.makedirs(out, exist_ok=True)
    with open(os.path.join(raw, "manifest.json")) as f:
        manifest = sorted(json.load(f), key=lambda e: e["date"])
    seen, unique = set(), []
    for e in manifest:  # a date can appear twice when a pass spans two frames
        if e["date"] not in seen:
            seen.add(e["date"])
            unique.append(e)
    if len(unique) < len(manifest):
        print(f"Skipped {len(manifest) - len(unique)} duplicate date(s)")
    manifest = unique

    transform, width, height, bounds = common_grid()
    print(f"Grid: {width} x {height} px at {RES:.0f} m")

    stacks = []
    for e in manifest:
        if co not in e["layers"]:
            print(f"  {e['date']}: no {co}, skipped")
            continue
        a = despeckle(load(e["layers"][co]["path"], transform, width, height))
        b = None
        if cross in e["layers"]:
            b = despeckle(load(e["layers"][cross]["path"], transform, width, height))
        stacks.append((e, a, b))

    tbox = box_mask(THRESH_BOX, transform, (height, width))
    x, y = Transformer.from_crs(4326, EPSG, always_xy=True).transform(*LAKE_CENTER)
    ccol, crow = (int(v) for v in ~transform * (x, y))

    lake_poly = load_lake(args.lake)
    band = "co" if args.band == "auto" else args.band
    thr = {}  # band -> threshold(s)
    if lake_poly is not None:
        inside = poly_mask(lake_poly.buffer(-100), transform, (height, width))
        ring = (poly_mask(lake_poly.buffer(1500), transform, (height, width))
                & ~poly_mask(lake_poly.buffer(300), transform, (height, width)))
        train = [s for s in stacks if not args.train_from or s[0]["date"] >= args.train_from]
        dry = [s for s in stacks if args.flood_start and s[0]["date"] < args.flood_start]
        print(f"Training on {len(train)} full-lake date(s) with the official lake outline "
              f"({lake_poly.area / 1e6:.2f} km2)"
              + (f" + {len(dry)} dry-pit date(s) before {args.flood_start} as land" if dry else ""))

        def samples(idx):
            w = np.concatenate([s[idx][inside] for s in train])
            l = np.concatenate([s[idx][ring] for s in train] +
                               [s[idx][inside] for s in dry])
            return w, l

        results = {}
        for name, idx in (("co", 1), ("cross", 2)):
            if any(s[idx] is None for s in train + dry):
                continue
            w, l = samples(idx)
            t, acc = trained_threshold(w, l)
            results[name] = (acc, {name: t})
            label_ = co if name == "co" else cross
            print(f"  {label_:>4}: water median {np.nanmedian(w):6.1f} dB | land median "
                  f"{np.nanmedian(l):6.1f} dB | best threshold {t:6.1f} dB | accuracy {acc:.1%}")
        if "co" in results and "cross" in results:
            wa, la = samples(1)
            wb, lb = samples(2)
            ta, tb, acc = trained_threshold_2d(wa, wb, la, lb)
            results["both"] = (acc, {"co": ta, "cross": tb})
            print(f"  both: {co} < {ta:.1f} dB AND {cross} < {tb:.1f} dB | accuracy {acc:.1%}")
        if args.band == "auto":
            band = max(results, key=lambda k: results[k][0])
        thr = results[band][1]
        zone = poly_mask(lake_poly.buffer(300), transform, (height, width))
    else:
        print("No lake.geojson found -> falling back to Otsu (run get_lake.py for better results)")
        thr = {"co": otsu(np.concatenate([a[tbox] for _, a, _ in stacks]))}
        band = "co"
        zone = None

    names = {"co": co, "cross": cross}
    rule = " AND ".join(f"{names[k]} < {v:.1f} dB" for k, v in thr.items())
    print(f"Water detection: {rule}")
    threshold = thr.get("co", thr.get("cross"))
    idx = 1 if "co" in thr else 2
    used = rule

    def is_water(s):
        m = np.ones(s[1].shape, dtype=bool)
        for k, v in thr.items():
            arr = s[1] if k == "co" else s[2]
            m &= np.isfinite(arr) & (arr < v)
        return binary_opening(m, iterations=1)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        centre = np.nanmedian([np.nanmedian(s[idx][crow-3:crow+4, ccol-3:ccol+4]) for s in stacks])
    print(f"Median {names['co' if idx == 1 else 'cross']} at lake centre: {centre:.1f} dB")

    waters = [is_water(s) for s in stacks]
    if zone is None:
        zone = lake_zone(waters, transform)
    pixel_km2 = (RES * RES) / 1e6
    print(f"Lake outline area (full lake + 200 m margin): {zone.sum() * pixel_km2:.2f} km2")

    dates = []
    for (e, a, b), water in zip(stacks, waters):
        date = e["date"]
        valid = np.isfinite(a)
        lake = water & zone
        imgs = {"co": f"{date}_co.png", "water": f"{date}_water.png"}
        save_gray(os.path.join(out, imgs["co"]), a, HH_RANGE)
        save_water(os.path.join(out, imgs["water"]), lake)  # blue only inside the lake zone
        if b is not None:
            imgs["cross"] = f"{date}_cross.png"
            imgs["rgb"] = f"{date}_rgb.png"
            save_gray(os.path.join(out, imgs["cross"]), b, HV_RANGE)
            save_rgb(os.path.join(out, imgs["rgb"]), a, b)

        rec = {
            "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
            "lake_km2": round(float(lake.sum() * pixel_km2), 3),
            "all_water_km2": round(float(water.sum() * pixel_km2), 3),
            "lake_coverage": round(float(valid[zone].mean()), 3),
            "images": imgs,
        }
        if e.get("maturity"):
            rec["maturity"] = e["maturity"]
        if rec["lake_coverage"] < 0.98:
            print(f"  {date}: only {rec['lake_coverage']:.0%} of the lake visible -> left out")
            continue
        dates.append(rec)
        flag = " (BETA)" if e.get("maturity") == "BETA" else ""
        print(f"  {date}: lake {rec['lake_km2']:6.2f} km2   all water {rec['all_water_km2']:6.2f} km2"
              f"   lake visible {rec['lake_coverage']:.0%}{flag}")

    data = {
        "study_area": "Cottbuser Ostsee, Cottbus, Germany",
        "sensor": manifest[0].get("sensor", "NISAR L-band") if manifest else "",
        "source": manifest[0]["file"] if manifest else "",
        "polarizations": {"co": co, "cross": cross},
        "pixel_m": RES,
        "water_thresholds_db": {names[k]: round(v, 2) for k, v in thr.items()},
        "water_rule": rule,
        "corners": corners_lonlat(bounds),
        "dates": dates,
    }
    with open(os.path.join(out, "data.json"), "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nDone. {len(dates)} dates -> {out}/data.json")


if __name__ == "__main__":
    main()