"""Turn the raw open data into the app's data files.

For every Schanigarten we
  1. attach a name from OpenStreetMap (address match, else nearest venue),
  2. model the garden as a strip in front of the nearest façade and put a few
     sample points on it,
  3. compute, for every sample point, the skyline in each compass direction:
     the elevation angle up to which buildings block the sky, and the band of
     elevations covered by tree crowns.

The app then only needs the current sun position to decide sun / shade.

Outputs (app/data/):
  places.json   one entry per venue, with its sample-point range
  horizons.bin  per sample point, AZ_BINS x 3 bytes (building, tree low, tree high)
                angles stored as round(deg * 2), tree bytes 0/0 = no tree
"""

import json
import math
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely import STRtree, points, Point, Polygon, LineString, MultiLineString

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "app" / "data"

AZ_MIN, AZ_MAX = 40, 320          # sun never rises north of ~50° in Vienna
AZ_BINS = AZ_MAX - AZ_MIN + 1     # 1° bins
SUB_RAYS = 3                      # rays per bin (bin max over -0.33°, 0, +0.33°)
RES = 0.5                         # raster resolution, metres
MAX_DIST = 300.0                  # how far obstacles are considered, metres
TILE = 400.0                      # processing tile size, metres
EYE = 1.2                         # height above ground of a seated guest
CONIFERS = ("pinus", "picea", "abies", "taxus", "thuja", "cedrus", "pseudotsuga",
            "juniperus", "chamaecyparis", "larix", "sequoia", "metasequoia", "tsuga",
            "cupressus", "ilex", "magnolia grandiflora")

to_wgs = Transformer.from_crs("EPSG:31256", "EPSG:4326", always_xy=True)
from_wgs = Transformer.from_crs("EPSG:4326", "EPSG:31256", always_xy=True)


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


def first_number(s):
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m else None


def number_range(s):
    """'12-14' -> {12, 13, 14}; '7a' -> {7}."""
    nums = [int(n) for n in re.findall(r"\d+", str(s or ""))]
    if not nums:
        return set()
    if len(nums) >= 2 and re.search(r"\d\s*[-–]\s*\d", str(s)) and nums[1] > nums[0] and nums[1] - nums[0] < 20:
        return set(range(nums[0], nums[1] + 1))
    return set(nums)


def load_osm():
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


# --------------------------------------------------------- garden shapes ---

DEPTH = {"Gehsteig": 2.0, "Parkspur": 2.0, "Gehsteig und Parkspur": 4.0,
         "Fußgänger/Begegnungszone": 4.0, "Begegnungszone": 4.0, "Privatgrund": 4.0,
         "Gehsteig/Grünfläche": 3.0}


def load_streets():
    """OSM street centrelines grouped by normalised name."""
    by_name = defaultdict(list)
    for e in json.loads((RAW / "osm_streets.json").read_text())["elements"]:
        geom = e.get("geometry")
        if not geom or len(geom) < 2:
            continue
        xy = [from_wgs.transform(c["lon"], c["lat"]) for c in geom]
        by_name[norm_street(e["tags"]["name"])].append(LineString(xy))
    global STREET_LINES, STREET_TREE
    STREET_LINES = [l for v in by_name.values() for l in v]
    STREET_TREE = STRtree(STREET_LINES)
    return {k: (MultiLineString(v) if len(v) > 1 else v[0]) for k, v in by_name.items()}


def facade_offset(kind, d):
    """Distance of the garden's centre line from the façade, given the façade
    to street-centre distance d."""
    if kind == "Parkspur":
        return min(4.5, max(1.5, d - 2.5))
    if kind == "Gehsteig und Parkspur":
        return min(3.0, max(1.2, d * 0.45))
    if kind in ("Fußgänger/Begegnungszone", "Begegnungszone", "Privatgrund"):
        return min(2.5, max(1.2, d * 0.4))
    if kind == "Gehsteig/Grünfläche":
        return min(2.0, max(1.0, d * 0.4))
    return min(1.2, max(0.6, d * 0.4))  # sidewalk


def _inside(p, bpolys, btree):
    q = Point(p)
    return any(bpolys[i].contains(q) for i in btree.query(q))


def garden_samples(g, bpolys, btree, streets):
    """Sample points for one garden.

    The city publishes the *address point* (almost always inside the building),
    plus the street the garden is on. So: find the nearest point on that street,
    walk from there towards the address point until the first façade, and put the
    garden as a strip in front of that façade, parallel to the street.
    """
    pr = np.array([g["x"], g["y"]])
    pt = Point(pr)
    street = streets.get(g["street_n"])
    if street is None or street.distance(pt) > 150:
        # name not found (spelling differs): fall back to the nearest street
        g["placement"] = "no-street"
        street = None
        k = STREET_TREE.query_nearest(pt, max_distance=60)
        if len(k):
            street = STREET_LINES[k[0]]

    area = max(g["area"] or 10.0, 2.0)
    depth = DEPTH.get(g["kind"], 2.5)
    length = min(max(area / depth, 1.0), 40.0)

    if street is not None:
        line = street
        if line.geom_type == "MultiLineString":
            line = min(line.geoms, key=lambda l: l.distance(pt))
        s = line.project(pt)
        ps = np.array(line.interpolate(s).coords[0])
        a = np.array(line.interpolate(max(s - 2, 0)).coords[0])
        b = np.array(line.interpolate(min(s + 2, line.length)).coords[0])
        tangent = (b - a) / (np.linalg.norm(b - a) or 1.0)
        to_addr = pr - ps
        dist = np.linalg.norm(to_addr)
        if dist < 0.5:
            g["placement"] = "on-street"
            normal = np.array([-tangent[1], tangent[0]])
            center = pr
        else:
            u = to_addr / dist
            seg = LineString([ps, pr])
            first = dist
            for i in btree.query(seg):
                hit = seg.intersection(bpolys[i])
                if not hit.is_empty:
                    first = min(first, Point(ps).distance(hit))
            if first < 0.3:  # street centre line runs under a building (arcade)
                first = dist
            facade = ps + u * first
            normal = -u
            center = facade + normal * facade_offset(g["kind"], first)
            g["placement"] = ("fallback-" if g.get("placement") == "no-street" else "") + \
                ("facade" if first < dist else "open")
            if first >= dist:  # address point is in the open: trust it
                center = pr
    else:
        tangent, normal, center = np.array([1.0, 0.0]), np.array([0.0, 1.0]), pr

    n = int(min(max(math.ceil(length / 5.0), 1), 5))
    offsets = [0.0] if n == 1 else np.linspace(-length / 2 + 1, length / 2 - 1, n)
    out = []
    for o in offsets:
        p = center + tangent * o
        # step away from the façade until outside any building (bends, corners)
        k = 0
        while _inside(p, bpolys, btree) and k < 24:
            p = p + normal * 0.25
            k += 1
        if k < 24:
            out.append(p)
    if not out:
        out = [center]

    ground = np.nan
    near = btree.query(pt.buffer(40))
    gi = [i for i in near if not np.isnan(GROUND[i])]
    if gi:
        dd = [bpolys[i].distance(pt) for i in gi]
        ground = float(GROUND[gi[int(np.argmin(dd))]])
    return out, ground


# ---------------------------------------------------------------- horizons ---

def rasterize_tile(x0, y0, size, bpolys, btop, btree, trees, ttree):
    """Height rasters (absolute metres) for buildings+conifers and deciduous crowns."""
    n = int(size / RES)
    hard = np.zeros((n, n), np.float32)
    box = Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])
    for i in btree.query(box):
        # draw into a small mask per building so courtyards (holes) stay open
        # and overlapping parts keep the higher top
        poly = bpolys[i]
        minx, miny, maxx, maxy = poly.bounds
        c0, r0 = int((minx - x0) / RES), int((y0 + size - maxy) / RES)
        c1, r1 = int((maxx - x0) / RES) + 2, int((y0 + size - miny) / RES) + 2
        w, h = c1 - c0, r1 - r0
        img = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(img)
        to_px = lambda ring: [((x - x0) / RES - c0, (y0 + size - y) / RES - r0) for x, y in ring.coords]  # noqa: E731
        draw.polygon(to_px(poly.exterior), fill=1)
        for hole in poly.interiors:
            draw.polygon(to_px(hole), fill=0)
        m = np.asarray(img, bool)
        # clip to the tile
        cc0, rr0, cc1, rr1 = max(c0, 0), max(r0, 0), min(c1, n), min(r1, n)
        if cc0 >= cc1 or rr0 >= rr1:
            continue
        m = m[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
        sub = hard[rr0:rr1, cc0:cc1]
        sub[m] = np.maximum(sub[m], btop[i])

    ttop = np.zeros((n, n), np.float32)
    tbase = np.full((n, n), np.inf, np.float32)
    tx, ty, th, td, tev, tground = trees
    for i in ttree.query(box.buffer(15)):
        r = td[i] / 2
        cx, cy = (tx[i] - x0) / RES, (y0 + size - ty[i]) / RES
        rr = r / RES
        c0, c1 = int(max(cx - rr, 0)), int(min(cx + rr + 1, n))
        r0, r1 = int(max(cy - rr, 0)), int(min(cy + rr + 1, n))
        if c0 >= c1 or r0 >= r1:
            continue
        yy, xx = np.mgrid[r0:r1, c0:c1]
        q = ((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2) / (rr * rr)
        m = q < 1
        if not m.any():
            continue
        top = th[i]
        base = min(max(2.5, top * 0.3), 6.0) if not tev[i] else 0.5
        mid, half = (top + base) / 2, (top - base) / 2
        s = np.sqrt(np.clip(1 - q, 0, 1))
        z_top = tground[i] + mid + half * s
        z_bot = tground[i] + mid - half * s
        if tev[i]:
            sub = hard[r0:r1, c0:c1]
            sub[m] = np.maximum(sub[m], z_top[m])
        else:
            st, sb = ttop[r0:r1, c0:c1], tbase[r0:r1, c0:c1]
            st[m] = np.maximum(st[m], z_top[m])
            sb[m] = np.minimum(sb[m], z_bot[m])
    return hard, ttop, tbase


_AZ = (AZ_MIN - 0.5 + (np.arange(AZ_BINS * SUB_RAYS) + 0.5) / SUB_RAYS) * math.pi / 180
_D = np.arange(RES, MAX_DIST + RES / 2, RES / 2, dtype=np.float32)  # half-cell steps
_DX = (np.sin(_AZ)[:, None] * _D[None, :]).astype(np.float32)
_DY = (np.cos(_AZ)[:, None] * _D[None, :]).astype(np.float32)


def horizon_for_point(px, py, z0, x0, y0, size, hard, ttop, tbase):
    n = hard.shape[0]
    d = _D
    col = np.floor((px - x0 + _DX) / RES).astype(np.int32)
    row = np.floor((y0 + size - py - _DY) / RES).astype(np.int32)
    ok = (col >= 0) & (col < n) & (row >= 0) & (row < n)
    col = np.clip(col, 0, n - 1)
    row = np.clip(row, 0, n - 1)

    h = np.where(ok, hard[row, col], 0.0)
    tan_b = np.max((h - z0) / d[None, :], axis=1)

    tt = np.where(ok, ttop[row, col], 0.0)
    tb = np.where(ok, tbase[row, col], np.inf)
    has = tt > z0
    tan_hi = np.max(np.where(has, (tt - z0) / d[None, :], -np.inf), axis=1)
    tan_lo = np.min(np.where(has, (np.maximum(tb, z0) - z0) / d[None, :], np.inf), axis=1)

    def per_bin(v, fn):
        return fn(v.reshape(AZ_BINS, SUB_RAYS), axis=1)

    b = np.degrees(np.arctan(np.maximum(per_bin(tan_b, np.max), 0)))
    hi = per_bin(tan_hi, np.max)
    lo = per_bin(tan_lo, np.min)
    tree_any = np.isfinite(hi) & (hi > 0)
    hi_deg = np.where(tree_any, np.degrees(np.arctan(np.where(tree_any, hi, 0))), 0)
    lo_deg = np.where(tree_any, np.degrees(np.arctan(np.where(np.isfinite(lo), lo, 0))), 0)
    # a tree band entirely below the building skyline is irrelevant
    tree_any &= hi_deg > b
    q = lambda v: np.clip(np.round(v * 2), 0, 180).astype(np.uint8)  # noqa: E731
    out = np.zeros((AZ_BINS, 3), np.uint8)
    out[:, 0] = q(b)
    out[:, 1] = np.where(tree_any, np.maximum(q(lo_deg), 1), 0)
    out[:, 2] = np.where(tree_any, np.maximum(q(hi_deg), 1), 0)
    return out


# -------------------------------------------------------------------- main ---

GROUND = None


def main():
    global GROUND
    t0 = time.time()
    print("loading buildings…")
    bpolys, btop, GROUND = load_buildings()
    btree = STRtree(bpolys)
    print(f"  {len(bpolys)} building parts ({time.time() - t0:.0f}s)")

    print("loading trees…")
    tx, ty, th, td, tev = load_trees()
    tpts = points(np.column_stack([tx, ty]))
    ttree = STRtree(tpts)
    nb = btree.query_nearest(tpts, max_distance=200, return_distance=False)
    tground = np.full(len(tx), np.nan)
    tground[nb[0]] = GROUND[nb[1]]
    tground = np.where(np.isnan(tground), np.nanmedian(GROUND), tground)
    trees = (tx, ty, th, td, tev, tground)
    print(f"  {len(tx)} trees, {tev.sum()} evergreen ({time.time() - t0:.0f}s)")

    print("loading gardens and venues…")
    gardens = []
    for f in load_features("schanigarten"):
        p = f["properties"]
        x, y = f["geometry"]["coordinates"][:2]
        gardens.append({
            "x": x, "y": y, "area": p["FLAECHE"], "kind": p["NAME"].replace("Schanigarten ", ""),
            "street": p["STRASSE"], "street_n": norm_street(p["STRASSE"]), "onr": p["ONR"], "plz": p["PLZ"],
        })
    pois = load_osm()
    match_names(gardens, pois)
    print(f"  {len(gardens)} gardens, {sum(1 for g in gardens if g['poi'])} matched to a venue")

    print("modelling garden shapes…")
    streets = load_streets()
    for g in gardens:
        g["samples"], g["ground"] = garden_samples(g, bpolys, btree, streets)
        if np.isnan(g["ground"]):
            g["ground"] = float(np.nanmedian(GROUND))
    from collections import Counter
    print("  placement:", dict(Counter(g["placement"] for g in gardens)))

    print("computing skylines…")
    tiles = defaultdict(list)
    for gi, g in enumerate(gardens):
        tiles[(int(g["x"] // TILE), int(g["y"] // TILE))].append(gi)
    horizons = {}
    for k, ((tx_, ty_), gis) in enumerate(sorted(tiles.items())):
        x0, y0 = tx_ * TILE - MAX_DIST - 20, ty_ * TILE - MAX_DIST - 20
        size = TILE + 2 * MAX_DIST + 40
        hard, ttop, tbase = rasterize_tile(x0, y0, size, bpolys, btop, btree, trees, ttree)
        for gi in gis:
            g = gardens[gi]
            z0 = g["ground"] + EYE
            horizons[gi] = [horizon_for_point(sx, sy, z0, x0, y0, size, hard, ttop, tbase)
                            for sx, sy in g["samples"]]
        if k % 10 == 0:
            print(f"  tile {k + 1}/{len(tiles)} ({time.time() - t0:.0f}s)")

    print("writing output…")
    write_output(gardens, horizons)
    print(f"done in {time.time() - t0:.0f}s")


def write_output(gardens, horizons):
    # group gardens into places: one per venue, or per address if unnamed
    groups = defaultdict(list)
    for gi, g in enumerate(gardens):
        key = ("osm", g["poi"]["osm"]) if g["poi"] else ("addr", g["street_n"], g["onr"])
        groups[key].append(gi)

    places, blobs, offset = [], [], 0
    for key, gis in groups.items():
        gs = [gardens[i] for i in gis]
        poi = gs[0]["poi"]
        pts = [p for g in gs for p in g["samples"]]
        cx, cy = np.mean([g["x"] for g in gs]), np.mean([g["y"] for g in gs])
        lon, lat = to_wgs.transform(cx, cy)
        sample_ll = [to_wgs.transform(px, py) for px, py in pts]
        place = {
            "id": len(places),
            "name": poi["name"] if poi else None,
            "type": poi["amenity"] if poi else None,
            "addr": f"{gs[0]['street']} {gs[0]['onr']}",
            "plz": gs[0]["plz"],
            "lat": round(lat, 6), "lon": round(lon, 6),
            "area": round(sum(g["area"] or 0 for g in gs), 1),
            "kinds": sorted({g["kind"] for g in gs}),
            "p0": offset, "n": len(pts),
            "pts": [[round(a, 6), round(b, 6)] for a, b in sample_ll],
        }
        if poi:
            for k in ("cuisine", "hours", "web"):
                if poi[k]:
                    place[k] = poi[k]
        places.append(place)
        for gi in gis:
            blobs.extend(horizons[gi])
        offset += len(pts)

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"azMin": AZ_MIN, "azBins": AZ_BINS, "generated": time.strftime("%Y-%m-%d"), "points": offset}
    (OUT / "places.json").write_text(json.dumps({"meta": meta, "places": places}, ensure_ascii=False,
                                                separators=(",", ":")))
    np.concatenate(blobs).astype(np.uint8).tofile(OUT / "horizons.bin")
    print(f"  {len(places)} places, {offset} sample points, "
          f"horizons.bin {(OUT / 'horizons.bin').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
