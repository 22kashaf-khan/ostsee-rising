"""
Fake frames in the DATA_CONTRACT format, so the web app can be built before the real data exists.
Areas follow the official curve roughly; the images are synthetic. Delete web/data/ when real data arrives.

    python tools/make_demo_data.py
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "web" / "data"
BOUNDS = [[51.77, 14.37], [51.84, 14.50]]
WIDTH, HEIGHT = 300, 260

# Box is ~8.95 km wide and ~7.78 km tall, so one pixel is ~0.000893 km2
PIXEL_KM2 = (8.951 / WIDTH) * (7.784 / HEIGHT)

DEMO_FRAMES = [  # date, sensor, rough lake area in km2
    ("2019-04-20", "Sentinel-1", 0.4), ("2019-10-01", "Sentinel-1", 1.1),
    ("2020-05-01", "Sentinel-1", 1.9), ("2020-10-01", "Sentinel-1", 2.4),
    ("2021-05-01", "Sentinel-1", 3.3), ("2021-10-01", "Sentinel-1", 3.9),
    ("2022-05-01", "Sentinel-1", 4.6), ("2022-10-01", "Sentinel-1", 5.2),
    ("2023-05-01", "Sentinel-1", 5.7), ("2023-09-05", "Sentinel-1", 5.9),
    ("2024-05-15", "Sentinel-1", 16.8), ("2024-10-01", "Sentinel-1", 17.6),
    ("2025-02-20", "Sentinel-1", 18.4), ("2025-06-15", "Sentinel-1", 18.5),
    ("2026-06-20", "NISAR", 18.5), ("2026-08-14", "NISAR", 18.4), ("2026-10-01", "NISAR", 18.5),
]

rng = np.random.default_rng(7)


def smooth_noise(radius):
    noise = Image.fromarray((rng.random((HEIGHT, WIDTH)) * 255).astype("uint8"))
    blurred = np.asarray(noise.filter(ImageFilter.GaussianBlur(radius)), float)
    return (blurred - blurred.mean()) / blurred.std()


def terrain():
    """Pit floor: a wobbly bowl with three deep spots (the separate sub-basins)."""
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
    cx, cy = WIDTH * 0.52, HEIGHT * 0.5
    bowl = ((x - cx) / 105) ** 2 + ((y - cy) / 78) ** 2 + 0.25 * smooth_noise(12)
    for bx, by in [(0.35, 0.35), (0.62, 0.62), (0.7, 0.3)]:
        bowl -= 0.9 * np.exp(-(((x - WIDTH * bx) / 18) ** 2 + ((y - HEIGHT * by) / 18) ** 2))
    return bowl


def speckle(look_db, spread_db):
    """Radar images are grainy: same surface, different brightness per pixel."""
    return look_db + spread_db * rng.standard_normal((HEIGHT, WIDTH))


def gray_png(db, lo, hi, path):
    scaled = np.clip((db - lo) / (hi - lo), 0, 1) * 255
    Image.fromarray(scaled.astype("uint8"), "L").save(path)


def water_png(mask, path):
    rgba = np.zeros((HEIGHT, WIDTH, 4), "uint8")
    rgba[mask] = (40, 120, 255, 200)
    Image.fromarray(rgba, "RGBA").save(path)


def main():
    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    floor = terrain()
    vegetation = smooth_noise(6) > 0.4            # patches of trees around the pit
    frames = []

    for date, sensor, area in DEMO_FRAMES:
        level = np.quantile(floor, area / PIXEL_KM2 / floor.size)
        water = floor < level
        co_pol, cross_pol = ("VV", "VH") if sensor == "Sentinel-1" else ("HH", "HV")

        co = np.where(water, speckle(-22, 2), speckle(-9, 2.5) + 2 * vegetation)
        cross = np.where(water, speckle(-29, 2), speckle(-18, 2.5) + 5 * vegetation)

        tag = f"{date}_{'s1' if sensor == 'Sentinel-1' else 'nisar'}"
        layers = {co_pol: f"frames/{tag}_{co_pol.lower()}.png",
                  cross_pol: f"frames/{tag}_{cross_pol.lower()}.png",
                  "water": f"frames/{tag}_water.png"}
        gray_png(co, -25, 0, OUT / layers[co_pol])
        gray_png(cross, -30, -5, OUT / layers[cross_pol])
        water_png(water, OUT / layers["water"])

        frames.append({"date": date, "sensor": sensor,
                       "area_km2": round(water.sum() * PIXEL_KM2, 2), "layers": layers})

    manifest = {
        "demo": True,
        "bounds": BOUNDS,
        "official": [
            {"date": "2023-09-05", "area_km2": 5.9, "source": "LEAG, 07.09.2023"},
            {"date": "2025-02-20", "area_km2": 18.5, "source": "LEAG, 11.06.2025"},
        ],
        "frames": frames,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"{len(frames)} demo frames written to {OUT}")


if __name__ == "__main__":
    main()
