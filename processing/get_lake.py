"""
Download the official outline of the Cottbuser Ostsee from OpenStreetMap
and save it as lake.geojson (used by process.py to train the water threshold).

Setup:  pip install requests shapely
Run:    python get_lake.py
Data © OpenStreetMap contributors (ODbL) - credit this on the website.
"""
import json
import time

import requests
from shapely.geometry import LineString, mapping
from shapely.ops import polygonize, unary_union

BBOX = (51.72, 14.30, 51.85, 14.55)  # south, west, north, east
QUERY = f"""
[out:json][timeout:90];
(
  way["name"~"Cottbuser Ostsee"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  relation["name"~"Cottbuser Ostsee"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out geom;
"""
SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Overpass rejects anonymous clients (HTTP 406) - identify ourselves politely
HEADERS = {
    "User-Agent": "OstseeRising/1.0 (NASA Space Apps 2026 student hackathon, BTU Cottbus)",
    "Accept": "application/json",
}


def fetch():
    for attempt in range(2):
        for url in SERVERS:
            try:
                r = requests.post(url, data={"data": QUERY}, headers=HEADERS, timeout=120)
                r.raise_for_status()
                print(f"  got data from {url}")
                return r.json()["elements"]
            except Exception as exc:
                print(f"  {url} failed: {exc}")
        if attempt == 0:
            print("  retrying in 20 s ...")
            time.sleep(20)
    raise SystemExit("Could not reach OpenStreetMap. See the manual option in the chat.")


def main():
    elements = fetch()
    lines = []
    for el in elements:
        if el["type"] == "way" and "geometry" in el:
            lines.append(LineString([(p["lon"], p["lat"]) for p in el["geometry"]]))
        for m in el.get("members", []):
            if m.get("role") == "outer" and "geometry" in m:
                lines.append(LineString([(p["lon"], p["lat"]) for p in m["geometry"]]))
    polys = list(polygonize(unary_union(lines))) if lines else []
    if not polys:
        raise SystemExit("No lake polygon found in OpenStreetMap response.")
    lake = max(polys, key=lambda p: p.area)  # the main lake
    with open("lake.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "properties": {"name": "Cottbuser Ostsee", "source": "© OpenStreetMap contributors"},
            "geometry": mapping(lake)}]}, f)
    w, s, e, n = lake.bounds
    print(f"Saved lake.geojson  (bounds lon {w:.3f}-{e:.3f}, lat {s:.3f}-{n:.3f}, "
          f"{len(polys)} polygon(s) found, kept the largest)")


if __name__ == "__main__":
    main()