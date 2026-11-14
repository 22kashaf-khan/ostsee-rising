# What the web app needs from the data pipeline

Everything goes in `web/data/`. The app reads only these files.

## 1. `web/data/manifest.json`

```json
{
  "bounds": [[51.77, 14.37], [51.84, 14.50]],
  "stretch": { "VV": [-25, 0], "VH": [-30, -5], "HH": [-25, 0], "HV": [-30, -5] },
  "official": [
    { "date": "2023-09-05", "area_km2": 5.9,  "source": "LEAG, 07.09.2023" },
    { "date": "2025-02-20", "area_km2": 18.5, "source": "LEAG, 11.06.2025" }
  ],
  "frames": [
    {
      "date": "2023-09-05",
      "sensor": "Sentinel-1",
      "area_km2": 5.8,
      "layers": {
        "VV":    "frames/2023-09-05_s1_vv.png",
        "VH":    "frames/2023-09-05_s1_vh.png",
        "water": "frames/2023-09-05_s1_water.png"
      }
    },
    {
      "date": "2026-08-14",
      "sensor": "NISAR",
      "area_km2": 18.4,
      "layers": {
        "HH":    "frames/2026-08-14_nisar_hh.png",
        "HV":    "frames/2026-08-14_nisar_hv.png",
        "water": "frames/2026-08-14_nisar_water.png"
      }
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `bounds` | `[[south, west], [north, east]]` in lat/lon. **Every PNG must cover exactly this box.** |
| `stretch` | Optional. The dB range each radar PNG was scaled to (grey 0 = first number, 255 = second). The click-anywhere chart uses it to turn grey back into dB. Defaults shown. |
| `official` | Official LEAG measurements, drawn as reference points on the chart |
| `frames` | One entry per satellite date, **sorted by date** |
| `area_km2` | Lake area our pipeline measured (water pixels inside the lake outline) |
| `layers` | Any subset of `VV`, `VH`, `HH`, `HV`, `water`. Missing layers are simply hidden. |

## 2. PNG rules

- **Same box, same size:** crop every image to `bounds` and reproject it to **EPSG:4326** (plain lat/lon) so all frames line up.
- **Radar layers (VV/VH/HH/HV):** grayscale, in dB, with **one fixed stretch per polarization for all dates**, e.g. VV −25…0 dB, VH −30…−5 dB. If every image is stretched on its own, the slider flickers and fakes change.
- **Water layer:** RGBA. Water = `(40, 120, 255, 200)`, everything else fully transparent.
- Size: 30 m pixels give about 300 × 260 px. Keep PNGs under 500 KB each.

## Quick export snippet (rasterio)

```python
import numpy as np
from PIL import Image

def to_png(db, lo, hi, path):
    scaled = np.clip((db - lo) / (hi - lo), 0, 1) * 255
    Image.fromarray(np.nan_to_num(scaled).astype("uint8"), "L").save(path)

def water_png(mask, path):
    rgba = np.zeros((*mask.shape, 4), "uint8")
    rgba[mask] = (40, 120, 255, 200)
    Image.fromarray(rgba, "RGBA").save(path)
```
