"""Vienna: Stadt Wien Open Government Data + OpenStreetMap.

- Schanigärten, building bodies (Baukörpermodell) and public trees from
  Stadt Wien OGD (WFS), in EPSG:31256 (metres).
- Cafés / restaurants / bars and named streets from OpenStreetMap (Overpass).
"""

import json
import math
import re
import unicodedata
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import STRtree, points, Point, Polygon

NAME = "vienna"
CRS = "EPSG:31256"
ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "app" / "data"
TZ = "Europe/Vienna"
CENTER = (48.2085, 16.3731)

to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
from_wgs = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

CONIFERS = ("pinus", "picea", "abies", "taxus", "thuja", "cedrus", "pseudotsuga",
            "juniperus", "chamaecyparis", "larix", "sequoia", "metasequoia", "tsuga",
            "cupressus", "ilex", "magnolia grandiflora")


# --------------------------------------------------------------- download ---

WFS = "https://data.wien.gv.at/daten/geo"
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

STREETS_QUERY = """
[out:json][timeout:300];
area["name"="Wien"]["admin_level"="4"]->.wien;
way["highway"]["name"](area.wien);
out geom tags;
"""


def download_layer(name, type_name):
    from download import fetch_json
    out = RAW / f"{name}.geojson"
    features, start = [], 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": type_name, "srsName": "EPSG:31256", "outputFormat": "json",
            "count": PAGE, "startIndex": start, "sortBy": "OBJECTID" if name != "buildings" else "SE_SDO_ROWID",
        }
        page = fetch_json(WFS, params)
        features += page["features"]
        print(f"  {name}: {len(features)} / {page.get('numberMatched')}")
        if len(page["features"]) < PAGE:
            break
        start += PAGE
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def download(force):
    from download import download_osm
    for name, type_name in LAYERS.items():
        if force or not (RAW / f"{name}.geojson").exists():
            print(f"downloading {name}")
            download_layer(name, type_name)
    for name, query in (("osm_pois.json", OSM_QUERY), ("osm_streets.json", STREETS_QUERY)):
        if force or not (RAW / name).exists():
            print(f"downloading {name}")
            download_osm(RAW / name, query)


# ---------------------------------------------------------------- loading ---

def load_features(name):
    return json.loads((RAW / f"{name}.geojson").read_text())["features"]


def load_buildings():
    polys, top, ground = [], [], []
    for f in load_features("buildings"):
        p = f["properties"]
        if p["O_KOTE"] is None or p["LAYER"] == "Telefonzelle":
            continue
        g = f["geometry"]
        rings = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for r in rings:
            if len(r[0]) < 4:
                continue
            poly = Polygon(r[0], r[1:])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            polys.append(poly)
            top.append(p["O_KOTE"])
            ground.append(p["HOEHE_DGM"] if p["HOEHE_DGM"] is not None else p["T_KOTE"] or np.nan)
    return polys, np.array(top, float), np.array(ground, float)


# Baumkataster classes -> metres (class midpoints)
TREE_HEIGHT = {1: 3.0, 2: 8.0, 3: 13.0, 4: 18.0, 5: 23.0, 6: 28.0, 7: 33.0, 8: 38.0}
CROWN_DIAM = {1: 2.0, 2: 5.0, 3: 8.0, 4: 11.0, 5: 14.0, 6: 17.0, 7: 20.0, 8: 23.0}


def load_trees():
    xs, ys, h, d, ever = [], [], [], [], []
    for f in load_features("trees"):
        p = f["properties"]
        hh = TREE_HEIGHT.get(p["BAUMHOEHE"])
        dd = CROWN_DIAM.get(p["KRONENDURCHMESSER"])
        if not hh or not dd:
            continue
        x, y = f["geometry"]["coordinates"][:2]
        xs.append(x); ys.append(y); h.append(hh); d.append(dd)
        art = (p["GATTUNG_ART"] or "").lower()
        ever.append(art.startswith(CONIFERS))
    return np.array(xs), np.array(ys), np.array(h), np.array(d), np.array(ever)


def norm_street(s):
    s = unicodedata.normalize("NFKC", (s or "").lower()).replace("ß", "ss")
    s = re.sub(r"str\.?$", "strasse", s.strip())
    s = re.sub(r"[^a-zäöü0-9]", "", s)
    return s


def number_range(s):
    """'12-14' -> {12, 13, 14}; '7a' -> {7}."""
    nums = [int(n) for n in re.findall(r"\d+", str(s or ""))]
    if not nums:
        return set()
    if len(nums) >= 2 and re.search(r"\d\s*[-–]\s*\d", str(s)) and nums[1] > nums[0] and nums[1] - nums[0] < 20:
        return set(range(nums[0], nums[1] + 1))
    return set(nums)


def load_pois():
    pois = []
    for e in json.loads((RAW / "osm_pois.json").read_text())["elements"]:
        t = e.get("tags", {})
        if "name" not in t:
            continue
        lon, lat = (e["lon"], e["lat"]) if "lon" in e else (e["center"]["lon"], e["center"]["lat"])
        x, y = from_wgs.transform(lon, lat)
        pois.append({
            "osm": f"{e['type'][0]}{e['id']}", "name": t["name"], "amenity": t.get("amenity"),
            "x": x, "y": y, "street": norm_street(t.get("addr:street")),
            "nums": number_range(t.get("addr:housenumber")),
            "outdoor": t.get("outdoor_seating"), "cuisine": t.get("cuisine"),
            "hours": t.get("opening_hours"), "web": t.get("website") or t.get("contact:website"),
            "addr": " ".join(filter(None, [t.get("addr:street"), t.get("addr:housenumber")])),
        })
    return pois


def load_gardens():
    gardens = []
    for f in load_features("schanigarten"):
        p = f["properties"]
        x, y = f["geometry"]["coordinates"][:2]
        gardens.append({
            "x": x, "y": y, "area": p["FLAECHE"], "kind": p["NAME"].replace("Schanigarten ", ""),
            "street": p["STRASSE"], "street_n": norm_street(p["STRASSE"]), "onr": p["ONR"], "plz": p["PLZ"],
            "addr": f"{p['STRASSE']} {p['ONR']}",
        })
    return gardens


# --------------------------------------------------------------- matching ---

def match_names(gardens, pois):
    """Attach the most plausible OSM venue to each garden (or None)."""
    tree = STRtree(points([(p["x"], p["y"]) for p in pois]))
    for g in gardens:
        idx = tree.query(Point(g["x"], g["y"]).buffer(120))
        best, best_score = None, 0.0
        for i in idx:
            p = pois[i]
            dist = math.hypot(p["x"] - g["x"], p["y"] - g["y"])
            addr = p["street"] == g["street_n"] and g["onr"] in p["nums"]
            street = p["street"] == g["street_n"]
            if addr:
                score = 100 - dist * 0.3
            elif dist < 35:
                score = 60 - dist + (10 if street else 0) - (15 if p["street"] and not street else 0)
            else:
                continue
            if p["outdoor"] == "yes":
                score += 8
            elif p["outdoor"] == "no":
                score -= 20
            if p["amenity"] == "fast_food":
                score -= 5
            if score > best_score:
                best, best_score = p, score
        g["poi"] = best if best_score >= 25 else None


def place_key(g):
    """Gardens with the same key become one place in the app."""
    return ("osm", g["poi"]["osm"]) if g["poi"] else ("addr", g["street_n"], g["onr"])


# --------------------------------------------------------- garden shapes ---

DEPTH = {"Gehsteig": 2.0, "Parkspur": 2.0, "Gehsteig und Parkspur": 4.0,
         "Fußgänger/Begegnungszone": 4.0, "Begegnungszone": 4.0, "Privatgrund": 4.0,
         "Gehsteig/Grünfläche": 3.0}


def garden_length(g):
    area = max(g["area"] or 10.0, 2.0)
    depth = DEPTH.get(g["kind"], 2.5)
    return min(max(area / depth, 1.0), 40.0)


def facade_offset(g, d):
    """Distance of the garden's centre line from the façade, given the façade
    to street-centre distance d."""
    kind = g["kind"]
    if kind == "Parkspur":
        return min(4.5, max(1.5, d - 2.5))
    if kind == "Gehsteig und Parkspur":
        return min(3.0, max(1.2, d * 0.45))
    if kind in ("Fußgänger/Begegnungszone", "Begegnungszone", "Privatgrund"):
        return min(2.5, max(1.2, d * 0.4))
    if kind == "Gehsteig/Grünfläche":
        return min(2.0, max(1.0, d * 0.4))
    return min(1.2, max(0.6, d * 0.4))  # sidewalk
