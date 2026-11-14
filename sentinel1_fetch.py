"""
Sentinel-1 radar images of the Cottbuser Ostsee, 2019-2025, processed by ASF HyP3.

    python sentinel1_fetch.py list                # which scenes and tracks exist near each date
    python sentinel1_fetch.py submit --track 175  # order RTC processing (5 credits per scene)
    python sentinel1_fetch.py download            # wait for the jobs, save GeoTIFFs to data/s1/

Login: the same Earthdata account as for NISAR (read from ~/.netrc, otherwise asks).
"""
import argparse
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import asf_search as asf
import hyp3_sdk
from hyp3_sdk.exceptions import AuthenticationError

# Generous box around the lake. The lake outline itself is applied later in process.py.
LAKE_BBOX = "POLYGON((14.37 51.77, 14.50 51.77, 14.50 51.84, 14.37 51.84, 14.37 51.77))"
JOB_NAME = "ostsee-rising"
OUT_DIR = Path("data/s1")
WINDOW_DAYS = 6          # Sentinel-1 repeats every 6-12 days, so +-6 usually finds one
CREDITS_PER_SCENE = 5    # 30 m RTC

# One spring and one autumn date per year, no deep winter (ice), plus the validation dates.
TARGET_DATES = [
    ("2019-04-20", "flooding just started (12.04.2019)"),
    ("2019-10-01", ""),
    ("2020-05-01", ""),
    ("2020-10-01", ""),
    ("2021-05-01", ""),
    ("2021-10-01", ""),
    ("2022-05-01", ""),
    ("2022-10-01", ""),
    ("2023-05-01", ""),
    ("2023-09-05", "VALIDATION: official 5.9 km2"),
    ("2024-05-15", ""),
    ("2024-10-01", ""),
    ("2025-02-20", "VALIDATION: official 18.5 km2 (winter, check for ice)"),
    ("2025-06-15", "full lake, summer"),
]


def scene_day(product):
    return date.fromisoformat(product.properties["startTime"][:10])


def find_scenes(target, track=None, window=WINDOW_DAYS):
    """Sentinel-1 GRD scenes over the lake near `target`, closest date first."""
    day = date.fromisoformat(target)
    results = asf.search(
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.GRD_HD,
        beamMode=asf.BEAMMODE.IW,
        intersectsWith=LAKE_BBOX,
        relativeOrbit=track,
        start=(day - timedelta(days=window)).isoformat(),
        end=(day + timedelta(days=window + 1)).isoformat(),
    )
    return sorted(results, key=lambda p: abs(scene_day(p) - day))


def connect():
    try:
        return hyp3_sdk.HyP3()
    except AuthenticationError:
        return hyp3_sdk.HyP3(prompt="password")


def cmd_list(args):
    tracks_per_date = Counter()
    for target, note in TARGET_DATES:
        scenes = find_scenes(target)
        print(f"\n{target}  {note}")
        for p in scenes:
            props = p.properties
            direction = props["flightDirection"][:4].lower()
            print(f"   {scene_day(p)}  track {props['pathNumber']:>3} {direction}  {props['sceneName']}")
        tracks_per_date.update({p.properties["pathNumber"] for p in scenes})

    print("\nHow many target dates each track covers (pick one that covers all):")
    for track, n in tracks_per_date.most_common():
        print(f"   track {track:>3}: {n}/{len(TARGET_DATES)}")


def cmd_submit(args):
    hyp3 = connect()
    already = {job.job_parameters["granules"][0] for job in hyp3.find_jobs(name=JOB_NAME)}

    todo = []
    for target, _ in TARGET_DATES:
        # Same track for every date, so the viewing angle is identical across the time series.
        scenes = find_scenes(target, args.track) or find_scenes(target, args.track, 2 * WINDOW_DAYS)
        if not scenes:
            print(f"{target}: nothing on track {args.track}, skipped")
            continue
        granule = scenes[0].properties["sceneName"]
        status = "already ordered" if granule in already else "new"
        print(f"{target}: {scene_day(scenes[0])}  {granule}  ({status})")
        if granule not in already:
            todo.append(granule)

    if not todo:
        print("\nNothing new to order.")
        return
    print(f"\n{len(todo)} new jobs, {CREDITS_PER_SCENE * len(todo)} credits. "
          f"Credits left this month: {hyp3.check_credits()}")
    if input("Submit? [y/N] ").strip().lower() != "y":
        return
    for granule in todo:
        hyp3.submit_rtc_job(granule, name=JOB_NAME, resolution=30, radiometry="gamma0", scale="power")
    print("Submitted. `download` can be run now; it waits until the jobs are done.")


def cmd_download(args):
    hyp3 = connect()
    jobs = hyp3.find_jobs(name=JOB_NAME)
    if not len(jobs):
        print("No jobs found. Run `submit` first.")
        return
    print(f"{len(jobs)} jobs. Waiting for all to finish (checks every minute)...")
    jobs = hyp3.watch(jobs)

    done = jobs.filter_jobs(succeeded=True, pending=False, running=False, failed=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for zip_path in done.download_files(OUT_DIR):
        with zipfile.ZipFile(zip_path) as z:
            # Keep only the backscatter layers (VV, VH); skip the rest of the package.
            for name in z.namelist():
                if name.endswith(("_VV.tif", "_VH.tif")):
                    z.extract(name, OUT_DIR)
        zip_path.unlink()

    failed = len(jobs) - len(done)
    print(f"Saved {len(done)} scenes to {OUT_DIR}/" + (f"  ({failed} failed)" if failed else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(run=cmd_list)
    submit = sub.add_parser("submit")
    submit.add_argument("--track", type=int, required=True, help="relative orbit, chosen from `list`")
    submit.set_defaults(run=cmd_submit)
    sub.add_parser("download").set_defaults(run=cmd_download)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
