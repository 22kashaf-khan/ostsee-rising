# Ostsee Rising ✦

A coal mine learns to swim: the Cottbuser Ostsee filling up (2019–2025), seen by Sentinel-1 and NASA-ISRO NISAR radar.
NASA Space Apps Challenge 2026 · "Dancing with the SARs"

## Run the web app

```bash
cd web
python -m http.server 8000
```
Open http://localhost:8000. Opening `index.html` directly won't work, because browsers block loading the data files from disk.

## Features

| Feature | Where | How it works |
|---|---|---|
| Story mode | Story tab | 6 chapters drive the map as you scroll (map pinned on top on phones) |
| Time slider + play | Top card | Steps through every satellite pass; chart ring follows |
| Water / plant view | Explore → Radar glasses | Co-pol (VV/HH) vs cross-pol (VH/HV) images |
| Radar ↔ photo | Explore → Radar glasses | Fades the radar to show the Esri satellite photo underneath |
| Compare: swipe, fade, spy lens | Explore → Compare | Same three modes as NASA Worldview |
| Water history map | Explore, Story chapter 4 | Computed in the browser: each pixel coloured by the first year it was water **and** still water on the next pass |
| Click-anywhere time series | Click the map | 3×3 pixel window, averaged in linear power, shown in dB for both views + "first under water" date |
| Shareable links | Explore → Share | The view (date, view, compare, map position) is stored after the `#` in the URL |
| Timelapse GIF | Explore → Share | Every pass drawn on a canvas and encoded in the browser with gifenc |

## Folders

| Path | What |
|---|---|
| `web/` | The website (no build step: plain HTML, CSS, JS) |
| `web/DATA_CONTRACT.md` | **Read this, data team.** Exact files the website expects |
| `web/data/` | `manifest.json` + PNG frames (currently DEMO data) |
| `web/lib/` | Leaflet (map), Chart.js (charts), gifenc (GIF export): stored locally so the demo works on bad Wi-Fi |
| `tools/make_demo_data.py` | Makes fake frames in the contract format |
| `sentinel1_fetch.py` | Orders and downloads the Sentinel-1 images (data team) |

## Swapping in real data

1. Delete `web/data/`.
2. Put the real `manifest.json` and `frames/` there, following `DATA_CONTRACT.md`.
3. Reload the page. No code changes needed. The "demo data" badge disappears automatically.

## Editing the story

The chapters are plain HTML in `web/index.html` (search for `class="chapter"`).
Each chapter's `data-*` attributes set the map:

| Attribute | Example | Effect |
|---|---|---|
| `data-date` | `first`, `last`, `2023-09-05` | Shows the satellite pass closest to that date |
| `data-play` | `true` | Animates from the first pass up to `data-date` |
| `data-pol` | `co` / `cross` | Water view (VV/HH) or plant view (VH/HV) |
| `data-compare` | `swipe` / `fade` / `spy` | Turns compare on in that mode |
| `data-before` | `first` | The "before" date for compare |
| `data-opacity` | `0.35` | Fades the radar to show the photo underneath |

Team stars and name: in `web/index.html`, search for `class="stars"`.

## Deploy (free)

Push the repo to GitHub → Settings → Pages → deploy from branch, folder `/web` (or move `web/` contents to `/docs`).
