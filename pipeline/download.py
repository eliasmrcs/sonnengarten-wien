"""Download the raw open data needed for the shadow model.

- Schanigärten, building bodies (Baukörpermodell) and public trees from
  Stadt Wien Open Government Data (WFS), in EPSG:31256 (metres).
- Cafés / restaurants / bars from OpenStreetMap (Overpass API).

Files land in data/raw/. Existing files are skipped unless --force is given.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
WFS = "https://data.wien.gv.at/daten/geo"
OVERPASS = "https://overpass-api.de/api/interpreter"
PAGE = 50000

LAYERS = {
    "schanigarten": "ogdwien:SCHANIGARTENOGD",
    "buildings": "ogdwien:FMZKBKMOGD",
    "trees": "ogdwien:BAUMKATOGD",
}

OSM_QUERY = """
[out:json][timeout:180];
area["name"="Wien"]["admin_level"="4"]->.wien;
(
  nwr["amenity"~"^(restaurant|cafe|bar|pub|biergarten|ice_cream|fast_food)$"](area.wien);
);
out center tags;
"""


def fetch(url, data=None, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "sonnengarten-wien/0.1"})
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - retry any network error
            print(f"  retry {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def download_layer(name, type_name):
    out = RAW / f"{name}.geojson"
    features, start = [], 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": type_name, "srsName": "EPSG:31256", "outputFormat": "json",
            "count": PAGE, "startIndex": start, "sortBy": "OBJECTID" if name != "buildings" else "SE_SDO_ROWID",
        }
        page = json.loads(fetch(WFS + "?" + urllib.parse.urlencode(params)))
        features += page["features"]
        print(f"  {name}: {len(features)} / {page.get('numberMatched')}")
        if len(page["features"]) < PAGE:
            break
        start += PAGE
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


STREETS_QUERY = """
[out:json][timeout:300];
area["name"="Wien"]["admin_level"="4"]->.wien;
way["highway"]["name"](area.wien);
out geom tags;
"""


def download_osm(name, query):
    out = RAW / name
    body = urllib.parse.urlencode({"data": query}).encode()
    out.write_bytes(fetch(OVERPASS, data=body))
    print(f"  {name}: {len(json.loads(out.read_text())['elements'])} elements")


def main():
    force = "--force" in sys.argv
    RAW.mkdir(parents=True, exist_ok=True)
    for name, type_name in LAYERS.items():
        if force or not (RAW / f"{name}.geojson").exists():
            print(f"downloading {name}")
            download_layer(name, type_name)
    for name, query in (("osm_pois.json", OSM_QUERY), ("osm_streets.json", STREETS_QUERY)):
        if force or not (RAW / name).exists():
            print(f"downloading {name}")
            download_osm(name, query)


if __name__ == "__main__":
    main()
