"""Paris (intra-muros, the 20 arrondissements).

- Terraces: Ville de Paris "Terrasses et étalages : Autorisations" (ODbL), points
  with length and width of each authorised device.
- Trees: Ville de Paris "Les arbres" (ODbL), height in m and circumference in cm.
- Buildings: IGN BD TOPO v3 `batiment` (Licence Ouverte 2.0) via the Géoplateforme
  WFS, in Lambert 93 (EPSG:2154), for Paris plus 300 m around it (92/93/94).
- Arrondissements (boundary, postcodes): IGN BD TOPO `arrondissement_municipal`.
- Venue names / hours / website / cuisine and named streets: OpenStreetMap.
"""

import json
from pathlib import Path

from pyproj import Transformer

NAME = "paris"
CRS = "EPSG:2154"
ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "raw" / "paris"
OUT = ROOT / "app" / "data" / "paris"
TZ = "Europe/Paris"
CENTER = (48.8566, 2.3522)
POINT_ON_GARDEN = True            # terrace points sit on the terrace, not on the address
CHUNK = 1000.0                    # horizons split into 1 km map chunks (Lambert 93 grid)
MAX_POINTS = 10                   # sample points per place (big brasseries have up to 30)

to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
from_wgs = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)


# --------------------------------------------------------------- download ---

OPENDATA = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
WFS = "https://data.geopf.fr/wfs/ows"
WFS_PAGE = 5000                   # server maximum
BUILDING_TILE = 1000.0            # metres; a Paris km² has up to ~5k buildings
MARGIN = 320.0                    # MAX_DIST + a little: shadow casters outside Paris

BUILDING_FIELDS = ("cleabs,nature,construction_legere,etat_de_l_objet,nombre_d_etages,hauteur,"
                   "altitude_minimale_sol,altitude_minimale_toit,altitude_maximale_toit,geometrie")
TREE_FIELDS = ("idbase,domanialite,genre,espece,libellefrancais,hauteurenm,circonferenceencm,"
               "stadedeveloppement,geo_point_2d")

OSM_QUERY = """
[out:json][timeout:180];
area["ref:INSEE"="75056"]["boundary"="administrative"]->.paris;
(
  nwr["amenity"~"^(restaurant|cafe|bar|pub|biergarten|ice_cream|fast_food)$"](area.paris);
);
out center tags;
"""

STREETS_QUERY = """
[out:json][timeout:300];
area["ref:INSEE"="75056"]["boundary"="administrative"]->.paris;
way["highway"]["name"](area.paris);
out geom tags;
"""


def wfs_page(type_name, start, **extra):
    from download import fetch_json
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name,
              "srsName": CRS, "outputFormat": "application/json", "count": WFS_PAGE,
              "startIndex": start, **extra}
    return fetch_json(WFS, params)


def download_arrondissements():
    page = wfs_page("BDTOPO_V3:arrondissement_municipal", 0,
                    cql_filter="code_insee_de_la_commune_de_rattach='75056'")
    (RAW / "arrondissements.geojson").write_text(json.dumps(page))
    print(f"  arrondissements: {len(page['features'])}")


def download_buildings():
    """All BD TOPO buildings in 1 km tiles that touch Paris + MARGIN."""
    from shapely import box, unary_union
    from shapely.geometry import shape
    arr = json.loads((RAW / "arrondissements.geojson").read_text())["features"]
    area = unary_union([shape(f["geometry"]) for f in arr]).buffer(MARGIN)
    x0, y0, x1, y1 = area.bounds
    t = BUILDING_TILE
    tiles = [(x, y) for x in range(int(x0 // t), int(x1 // t) + 1)
             for y in range(int(y0 // t), int(y1 // t) + 1)
             if box(x * t, y * t, (x + 1) * t, (y + 1) * t).intersects(area)]
    features, seen = [], set()
    for k, (tx, ty) in enumerate(tiles):
        bbox = f"{tx * t},{ty * t},{(tx + 1) * t},{(ty + 1) * t},{CRS}"
        start = 0
        while True:
            page = wfs_page("BDTOPO_V3:batiment", start, bbox=bbox, propertyName=BUILDING_FIELDS,
                            sortBy="cleabs")
            for f in page["features"]:
                cid = f["properties"]["cleabs"]
                if cid not in seen:  # buildings crossing a tile edge come twice
                    seen.add(cid)
                    features.append(f)
            if len(page["features"]) < WFS_PAGE:
                break
            start += WFS_PAGE
        if k % 20 == 0:
            print(f"  buildings: tile {k + 1}/{len(tiles)}, {len(features)} so far")
    (RAW / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"  buildings: {len(features)}")


def download_opendata(dataset, out, select=None):
    from download import fetch
    url = f"{OPENDATA}/{dataset}/exports/json" + (f"?select={select}" if select else "")
    (RAW / out).write_bytes(fetch(url))
    print(f"  {out}: {len(json.loads((RAW / out).read_text()))} records")


def download(force):
    from download import download_osm
    steps = [
        ("arrondissements.geojson", download_arrondissements),
        ("terraces.json", lambda: download_opendata("terrasses-autorisations", "terraces.json")),
        ("trees.json", lambda: download_opendata("les-arbres", "trees.json", TREE_FIELDS)),
        ("osm_pois.json", lambda: download_osm(RAW / "osm_pois.json", OSM_QUERY)),
        ("osm_streets.json", lambda: download_osm(RAW / "osm_streets.json", STREETS_QUERY)),
        ("buildings.geojson", download_buildings),
    ]
    for name, step in steps:
        if force or not (RAW / name).exists():
            print(f"downloading {name}")
            step()


# ---------------------------------------------------------------- loading ---

import math  # noqa: E402
import re  # noqa: E402
import unicodedata  # noqa: E402
from collections import Counter  # noqa: E402
from difflib import SequenceMatcher  # noqa: E402

import numpy as np  # noqa: E402
from shapely import STRtree, points, Point, Polygon  # noqa: E402
from shapely.geometry import shape  # noqa: E402

CONIFERS = ("pinus", "picea", "abies", "taxus", "thuja", "cedrus", "pseudotsuga",
            "juniperus", "chamaecyparis", "larix", "sequoia", "metasequoia", "tsuga",
            "cupressus", "ilex", "magnolia grandiflora")
STATS = Counter()                 # numbers for docs/paris-report.md


def load_arrondissements():
    """[(postcode, polygon)] in Lambert 93."""
    feats = json.loads((RAW / "arrondissements.geojson").read_text())["features"]
    return [(f["properties"]["code_postal"], shape(f["geometry"])) for f in feats]


def load_buildings():
    """BD TOPO buildings -> polygons, roof elevation, ground elevation (metres above sea).

    Roof = altitude_maximale_toit (ridge) when present, else altitude_minimale_toit
    (gutter), else ground + hauteur; missing heights are filled from the number of
    floors, then from the median height of buildings within 50 m.
    """
    polys, top, ground, height, src = [], [], [], [], []
    for f in json.loads((RAW / "buildings.geojson").read_text())["features"]:
        p = f["properties"]
        if p.get("etat_de_l_objet") not in (None, "En service"):
            STATS["buildings_not_in_service"] += 1
            continue
        if p.get("construction_legere") and (p.get("hauteur") or 0) < 5:
            STATS["buildings_light_skipped"] += 1   # kiosks, sheds, terrace enclosures
            continue
        sol = p.get("altitude_minimale_sol")
        sol = sol if sol is not None and sol > -100 else np.nan
        h = p.get("hauteur")
        roof, how = None, None
        if p.get("altitude_maximale_toit") is not None and p["altitude_maximale_toit"] > -100:
            roof, how = p["altitude_maximale_toit"], "ridge"
        elif p.get("altitude_minimale_toit") is not None and p["altitude_minimale_toit"] > -100:
            roof, how = p["altitude_minimale_toit"], "gutter"
        elif h and not np.isnan(sol):
            roof, how = sol + h, "sol+hauteur"
        elif p.get("nombre_d_etages") and not np.isnan(sol):
            h = p["nombre_d_etages"] * 3.0 + 1.0
            roof, how = sol + h, "floors"
        g = f["geometry"]
        rings = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for r in rings:
            if len(r[0]) < 4:
                continue
            poly = Polygon([c[:2] for c in r[0]], [[c[:2] for c in hole] for hole in r[1:]])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            polys.append(poly)
            top.append(np.nan if roof is None else roof)
            ground.append(sol)
            height.append(np.nan if roof is None or np.isnan(sol) else roof - sol)
            src.append(how or "missing")
    top, ground, height = np.array(top, float), np.array(ground, float), np.array(height, float)
    STATS.update({f"buildings_height_{k}": v for k, v in Counter(src).items()})
    STATS["buildings_total"] = len(polys)

    # fill missing: ground from nearest building with ground, height from neighbours
    tree = STRtree(polys)
    miss = np.where(np.isnan(top))[0]
    for i in miss:
        c = polys[i].centroid
        near = [j for j in tree.query(c.buffer(50)) if not np.isnan(height[j]) and j != i]
        hn = float(np.median(height[near])) if near else 10.0
        if np.isnan(ground[i]):
            gn = [j for j in near if not np.isnan(ground[j])]
            ground[i] = float(np.median(ground[gn])) if gn else np.nanmedian(ground)
        top[i] = ground[i] + hn
    STATS["buildings_height_filled_from_neighbours"] = len(miss)
    return polys, top, ground


def crown_diameter(h, circ):
    """No crown size in the data: species-agnostic rule (see docs/paris-report.md)."""
    return np.clip(0.25 * h + 0.02 * circ, 2.0, 18.0)


def load_trees():
    T = json.loads((RAW / "trees.json").read_text())
    h = np.array([t["hauteurenm"] or 0 for t in T], float)
    c = np.array([t["circonferenceencm"] or 0 for t in T], float)
    ok = (h > 0) & (h <= 45)
    # height from circumference where height is missing: median height per 20 cm class
    fit = {}
    cls = (c // 20).astype(int)
    for k in np.unique(cls[ok & (c > 0)]):
        sel = ok & (cls == k)
        if sel.sum() >= 30:
            fit[k] = float(np.median(h[sel]))
    derived = ~ok & (c > 0) & (c < 600) & np.isin(cls, list(fit))
    h = np.where(derived, [fit.get(k, 0.0) for k in cls], np.where(ok, h, 0.0))
    use = h > 0
    STATS.update(trees_total=len(T), trees_height_given=int(ok.sum()), trees_height_derived=int(derived.sum()),
                 trees_skipped=int((~use).sum()))
    lon = np.array([t["geo_point_2d"]["lon"] for t in T])
    lat = np.array([t["geo_point_2d"]["lat"] for t in T])
    x, y = from_wgs.transform(lon, lat)
    art = [f"{t['genre'] or ''} {t['espece'] or ''}".strip().lower() for t in T]
    ever = np.array([a.startswith(CONIFERS) for a in art])
    d = crown_diameter(h, c)
    return x[use], y[use], h[use], d[use], ever[use]


def strip_accents(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


STREET_ABBR = {"bd": "boulevard", "bv": "boulevard", "bld": "boulevard", "av": "avenue", "pl": "place",
               "r": "rue", "fg": "faubourg", "st": "saint", "ste": "sainte", "pass": "passage",
               "imp": "impasse", "sq": "square", "chee": "chaussee"}


def norm_street(s):
    s = strip_accents((s or "").lower())
    words = re.findall(r"[a-z0-9]+", s)
    return "".join(STREET_ABBR.get(w, w) for w in words)


def norm_name(s):
    s = strip_accents((s or "").lower())
    s = re.sub(r"\b(le|la|les|l|au|aux|du|de|des|d|chez|cafe|bar|restaurant|brasserie|bistrot|bistro)\b", " ", s)
    return "".join(re.findall(r"[a-z0-9]+", s))


ADDR = re.compile(r"^\s*(\d+)\s*(?:[-/]\s*\d+\s*)?(BIS|TER|QUATER|[A-Z](?=\s))?\s*(.*)$", re.I)


def split_address(a):
    """'23 BIS BOULEVARD DIDEROT' -> (23, 'BOULEVARD DIDEROT')."""
    m = ADDR.match(a or "")
    if not m:
        return None, (a or "").strip()
    return int(m.group(1)), m.group(3).strip()


def number_range(s):
    """'12-14' -> {12, 13, 14}; '7bis' -> {7}."""
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
            "osm": f"{e['type'][0]}{e['id']}", "name": t["name"], "name_n": norm_name(t["name"]),
            "amenity": t.get("amenity"), "x": x, "y": y, "street": norm_street(t.get("addr:street")),
            "nums": number_range(t.get("addr:housenumber")),
            "outdoor": t.get("outdoor_seating"), "cuisine": t.get("cuisine"),
            "hours": t.get("opening_hours"), "web": t.get("website") or t.get("contact:website"),
        })
    return pois


# Typologie -> kind code (translated in the app). None = not seating (étalage etc.).
def kind_of(typ):
    t = strip_accents((typ or "").upper()).replace("-", " ")
    if "ETALAGE" in t or t in ("COMMERCE ACCESSOIRE", "ECRAN PARALLELE A LA FACADE") or not t:
        return None
    contre, summer = t.startswith("CONTRE"), "ESTIVALE" in t
    if contre and summer:
        return "contre-estivale"
    if contre:
        return "contre"
    if summer:
        return "estivale"
    if "FERMEE" in t:
        return "fermee"
    if "OUVERTE" in t:
        return "ouverte"
    return None


# where on the street the authorised device stands (for the strip model)
def surface_of(typ):
    t = strip_accents((typ or "").upper())
    if "STATIONNEMENT" in t:
        return "parking"
    if "PIETONNE" in t or "PLACES ET TERRE" in t:
        return "pedestrian"
    return "sidewalk"


NAME_TYPES = (("glac", "ice_cream"), ("pizz", "restaurant"), ("restau", "restaurant"), ("brasserie", "restaurant"),
              ("bistro", "restaurant"), ("cafe", "cafe"), ("coffee", "cafe"), ("bar", "bar"), ("pub", "pub"))


def guess_type(name):
    n = strip_accents((name or "").lower())
    for key, typ in NAME_TYPES:
        if re.search(rf"\b{key}", n):
            return typ
    return None


def tidy_name(s):
    """Dataset names are upper case: 'CAFE DE FLORE' -> 'Cafe de Flore'."""
    small = {"de", "du", "des", "la", "le", "les", "et", "a", "au", "aux", "en", "d", "l", "sur"}
    words = re.split(r"(\s+|-|')", (s or "").strip().lower())
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w in small) else w[:1].upper() + w[1:])
    return "".join(out) or None


def load_gardens():
    arr = load_arrondissements()
    atree = STRtree([p for _, p in arr])
    gardens, kinds = [], Counter()
    for t in json.loads((RAW / "terraces.json").read_text()):
        kind = kind_of(t["typologie"])
        kinds[t["typologie"]] += 1
        if kind is None:
            STATS["terraces_not_seating"] += 1
            continue
        lon, lat = t["geo_point_2d"]["lon"], t["geo_point_2d"]["lat"]
        x, y = from_wgs.transform(lon, lat)
        hit = [i for i in atree.query(Point(x, y)) if arr[i][1].contains(Point(x, y))]
        if not hit:
            STATS["terraces_outside_paris"] += 1
            continue
        plz = arr[hit[0]][0]
        if plz == "75016" and t["arrondissement"] == "75116":
            plz = "75116"  # the 16th has two postcodes; keep the city's own
        if t["arrondissement"] != plz:
            STATS["terraces_arrondissement_field_differs"] += 1
        onr, street = split_address(t["adresse"])
        length = t["longueur"] or 0.0
        depth = t["largeur"] or 0.0
        if not 0.3 <= depth <= 15 or not 0.5 <= length <= 60:
            STATS["terraces_size_defaulted"] += 1
            length = length if 0.5 <= length <= 60 else 5.0
            depth = depth if 0.3 <= depth <= 15 else 1.5
        gardens.append({
            "x": x, "y": y, "area": round(length * depth, 1), "length": length, "depth": depth,
            "kind": kind, "surface": surface_of(t["typologie"]), "typ": t["typologie"],
            "street": street, "street_n": norm_street(street), "onr": onr, "plz": int(plz),
            "addr": tidy_name(f"{onr} {street}" if onr else street),
            "name": tidy_name(t["nom_enseigne"] or t["nom_societe"]),
            "name_n": norm_name(t["nom_enseigne"] or t["nom_societe"]), "siret": t["siret"],
        })
    STATS["terraces_downloaded"] = sum(kinds.values())
    STATS["terraces_used"] = len(gardens)
    STATS["terraces_by_typologie"] = dict(kinds.most_common())
    STATS["terraces_by_kind"] = dict(Counter(g["kind"] for g in gardens))
    return gardens


# --------------------------------------------------------------- matching ---

def match_names(gardens, pois):
    """Attach the OSM venue with the most similar name (for hours, web, cuisine, type).

    Unlike Vienna, every terrace already has the business name, so a match needs
    the name to agree; address and distance only break ties.
    """
    tree = STRtree(points([(p["x"], p["y"]) for p in pois]))
    for g in gardens:
        best, best_score = None, 0.0
        for i in tree.query(Point(g["x"], g["y"]).buffer(80)):
            p = pois[i]
            a, b = g["name_n"], p["name_n"]
            if not a or not b:
                continue
            sim = SequenceMatcher(None, a, b).ratio()
            if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
                sim = max(sim, 0.9)
            if sim < 0.75:
                continue
            dist = math.hypot(p["x"] - g["x"], p["y"] - g["y"])
            score = sim * 100 - dist * 0.3
            if p["street"] == g["street_n"] and g["onr"] in p["nums"]:
                score += 10
            if score > best_score:
                best, best_score = p, score
        g["poi"] = best
        if g["poi"] is None:
            g["type"] = guess_type(g["name"])
    STATS["terraces_with_osm_match"] = sum(1 for g in gardens if g["poi"])


def place_key(g):
    """One place per business (SIRET): its terrace, contre-terrasse and summer
    terrace become one entry with several groups of sample points."""
    if g["siret"]:
        return ("siret", g["siret"])
    return ("name", g["name_n"], g["street_n"], g["onr"])


# --------------------------------------------------------- garden shapes ---

def garden_length(g):
    return min(max(g["length"], 1.0), 40.0)


def facade_offset(g, d):
    """Distance of the terrace's centre line from the façade, given the façade
    to street-centre distance d. Terraces stand against the shop front;
    contre-terrasses stand at the kerb side of the pavement (or on the parking
    lane), with the walkway between them and the façade."""
    depth = g["depth"]
    sidewalk = min(max(0.4 * d, 1.5), 9.0)       # estimated pavement width
    if g["surface"] == "parking":
        return min(max(sidewalk + depth / 2, 1.5), max(d - 1.0, 1.5))
    if g["kind"].startswith("contre"):
        return min(max(sidewalk - depth / 2, depth / 2 + 1.4), max(d - 1.0, 1.5))
    return min(max(depth / 2, 0.5), max(d - 1.0, 0.5))
