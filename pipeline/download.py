"""Download the raw open data needed for the shadow model.

    .venv/bin/python pipeline/download.py [--city vienna|paris] [--force]

The city module (pipeline/cities/<city>.py) knows its sources; this file holds
the shared HTTP helpers. Files land in the city's raw folder (Vienna: data/raw/,
Paris: data/raw/paris/). Existing files are skipped unless --force is given.
"""

import argparse
import json
import time
import urllib.parse
import urllib.request

import cities

OVERPASS = "https://overpass-api.de/api/interpreter"


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


def fetch_json(url, params):
    return json.loads(fetch(url + "?" + urllib.parse.urlencode(params)))


def download_osm(out, query):
    body = urllib.parse.urlencode({"data": query}).encode()
    out.write_bytes(fetch(OVERPASS, data=body))
    print(f"  {out.name}: {len(json.loads(out.read_text())['elements'])} elements")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="vienna", choices=cities.CITIES)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    C = cities.load(args.city)
    C.RAW.mkdir(parents=True, exist_ok=True)
    C.download(args.force)


if __name__ == "__main__":
    main()
