"""
Sentinel-1 (2019 - Mar 2025) for the Cottbuser Ostsee, via NASA ASF HyP3.

HyP3 does the radar processing (terrain correction) on NASA's servers for free.
We only download the finished GeoTIFFs and keep a small clip around the lake.

Selection rule (automatic, no hand-picking):
  * track 146 ASCENDING only (same viewing geometry every time)
  * the FIRST scene of EVERY month, Jan 2019 - Mar 2025 (all seasons)
  * plus the scenes nearest the official LEAG measurements (for validation)

Setup:  pip install asf_search hyp3_sdk rasterio numpy
Steps:
  python s1_order.py submit     # order ~78 RTC jobs (asks for Earthdata login)
  python s1_order.py status     # check progress (jobs take ~10-60 min)
  python s1_order.py download   # download finished jobs, clip, convert to dB
                                # (safe to run repeatedly while jobs finish)
Output: data/raw_s1/<YYYYMMDD>_VV.tif, _VH.tif, manifest.json
Then:   python process.py --raw data/raw_s1 --out web/data/s1 --co VV --cross VH
"""
import argparse
import glob
import json
import os
import shutil
import time
import zipfile

import asf_search as asf
import hyp3_sdk as sdk
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

BBOX = (14.30, 51.72, 14.55, 51.85)  # same box as the NISAR scripts
LAKE = "POLYGON((14.38 51.76, 14.47 51.76, 14.47 51.82, 14.38 51.82, 14.38 51.76))"
TRACK, DIRECTION = 146, "ASCENDING"
VALIDATION_DATES = ["2023-09-10", "2024-02-13", "2024-12-21"]  # nearest to LEAG reports
JOB_NAME = "ostsee-s1"
RAW, TMP = "data/raw_s1", "data/tmp_s1"


def scene_date(name):
    return name.split("_")[4][:8]  # S1A_IW_GRDH_1SDV_20190104T...


def pick_scenes():
    res = asf.search(
        platform=asf.PLATFORM.SENTINEL1, processingLevel=asf.PRODUCT_TYPE.GRD_HD,
        beamMode=asf.BEAMMODE.IW, intersectsWith=LAKE,
        relativeOrbit=TRACK, flightDirection=DIRECTION,
        start="2019-01-01T00:00:00Z", end="2025-03-31T23:59:59Z",
    )
    scenes = sorted(res, key=lambda r: r.properties["startTime"])
    picked, months = {}, set()
    for r in scenes:
        d = r.properties["startTime"][:10]
        if d[:7] not in months:              # first scene of each month
            months.add(d[:7])
            picked[d] = r.properties["sceneName"]
        if d in VALIDATION_DATES and d not in picked:
            picked[d] = r.properties["sceneName"]
    return [picked[d] for d in sorted(picked)]


def submit():
    names = pick_scenes()
    hyp3 = sdk.HyP3(prompt="password")
    print(f"{len(names)} scenes selected; credits: {hyp3.check_credits()}")
    done = {j.job_parameters["granules"][0] for j in hyp3.find_jobs(name=JOB_NAME)}
    todo = [n for n in names if n not in done]
    print(f"{len(done)} already ordered, submitting {len(todo)}")
    for n in todo:
        hyp3.submit_rtc_job(
            n, name=JOB_NAME, resolution=30, radiometry="gamma0",
            scale="power", speckle_filter=False, dem_matching=False,
        )
    print("Submitted. Run `python s1_order.py status` in ~15 minutes.")


def status():
    jobs = sdk.HyP3(prompt="password").find_jobs(name=JOB_NAME)
    counts = {}
    for j in jobs:
        counts[j.status_code] = counts.get(j.status_code, 0) + 1
    print(f"{len(jobs)} jobs: {counts}")


def clip_to_db(src_path, out_path):
    with rasterio.open(src_path) as src:
        b = transform_bounds("EPSG:4326", src.crs, *BBOX, densify_pts=21)
        win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=0).astype("float32")
        transform, crs = src.window_transform(win), src.crs
    arr[~np.isfinite(arr) | (arr <= 0)] = np.nan
    db = 10.0 * np.log10(arr)
    with rasterio.open(out_path, "w", driver="GTiff", height=db.shape[0],
                       width=db.shape[1], count=1, dtype="float32", crs=crs,
                       transform=transform, nodata=np.nan, compress="deflate") as dst:
        dst.write(db, 1)
    return float(np.isfinite(db).mean())


def download_one(job, date, tries=3):
    """Download + clip one job; retry if the connection drops."""
    for attempt in range(1, tries + 1):
        shutil.rmtree(TMP, ignore_errors=True)  # never mix files from a failed attempt
        os.makedirs(TMP)
        try:
            for z in job.download_files(TMP):
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(TMP)
                os.remove(z)
            for pol in ("VV", "VH"):
                hits = glob.glob(os.path.join(TMP, "**", f"*_{pol}.tif"), recursive=True)
                if hits:
                    valid = clip_to_db(hits[0], os.path.join(RAW, f"{date}_{pol}.tif"))
                    print(f"  {date} {pol}: valid pixels {valid:.0%}")
            return True
        except Exception as exc:
            print(f"  {date}: attempt {attempt}/{tries} failed ({type(exc).__name__}), retrying...")
            for pol in ("VV", "VH"):  # remove half-written outputs
                f = os.path.join(RAW, f"{date}_{pol}.tif")
                if os.path.exists(f):
                    os.remove(f)
            time.sleep(10)
        finally:
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"  {date}: SKIPPED after {tries} attempts - just run download again later")
    return False


def download():
    os.makedirs(RAW, exist_ok=True)
    jobs = sdk.HyP3(prompt="password").find_jobs(name=JOB_NAME)
    finished = [j for j in jobs if j.succeeded()]
    print(f"{len(finished)}/{len(jobs)} jobs finished")
    failed = 0
    for job in finished:
        date = scene_date(job.job_parameters["granules"][0])
        if os.path.exists(os.path.join(RAW, f"{date}_VH.tif")):
            continue  # already done
        if not download_one(job, date):
            failed += 1
    if failed:
        print(f"\n{failed} date(s) still missing - run `python s1_order.py download` again.")

    manifest = []
    for vv in sorted(glob.glob(os.path.join(RAW, "*_VV.tif"))):
        date = os.path.basename(vv)[:8]
        layers = {"VV": {"path": vv}}
        vh = vv.replace("_VV.tif", "_VH.tif")
        if os.path.exists(vh):
            layers["VH"] = {"path": vh}
        manifest.append({"date": date, "file": f"Sentinel-1 track {TRACK} {DIRECTION}",
                         "sensor": "Sentinel-1 C-band", "layers": layers})
    with open(os.path.join(RAW, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest: {len(manifest)} dates in {RAW}/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["submit", "status", "download"])
    {"submit": submit, "status": status, "download": download}[ap.parse_args().step]()