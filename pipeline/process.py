"""Turn a city's raw open data into the app's data files.

    .venv/bin/python pipeline/process.py [--city vienna|paris] [--workers N]

For every garden / terrace we
  1. attach a name from OpenStreetMap (address match, else nearest venue),
  2. model the garden as a strip in front of the nearest façade and put a few
     sample points on it,
  3. compute, for every sample point, the skyline in each compass direction:
     the elevation angle up to which buildings block the sky, and the band of
     elevations covered by tree crowns.

The app then only needs the current sun position to decide sun / shade.

Everything city-specific (sources, CRS, field mappings, name matching, garden
size and placement rules) lives in pipeline/cities/<city>.py; from "list of
gardens with x, y, area, kind, address" on, this file is shared.

Outputs (the city's OUT folder; Vienna: app/data/, Paris: app/data/paris/):
  places.json   one entry per venue, with its sample-point range
  horizons.bin  per sample point, AZ_BINS x 3 bytes (building, tree low, tree high)
                angles stored as round(deg * 2), tree bytes 0/0 = no tree
  horizons/     (cities with CHUNK set) the same bytes split into 1 km map chunks,
                gzipped (<id>.bin.gz), listed in places.json meta.chunks
                ({id, p0, points, bbox}), so phones load only what they show
"""

import argparse
import gzip
import json
import math
import multiprocessing
import time
from collections import Counter, defaultdict

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree, points, Point, Polygon, LineString, MultiLineString

import cities

AZ_MIN, AZ_MAX = 40, 320          # sun never rises north of ~50° in Vienna or Paris
AZ_BINS = AZ_MAX - AZ_MIN + 1     # 1° bins
SUB_RAYS = 3                      # rays per bin (bin max over -0.33°, 0, +0.33°)
RES = 0.5                         # raster resolution, metres
MAX_DIST = 300.0                  # how far obstacles are considered, metres
TILE = 400.0                      # processing tile size, metres
EYE = 1.2                         # height above ground of a seated guest

# set by configure(); module-level so shadowmap.py and worker processes see them
C = RAW = OUT = to_wgs = from_wgs = None


def configure(city):
    global C, RAW, OUT, to_wgs, from_wgs
    C = cities.load(city)
    RAW, OUT, to_wgs, from_wgs = C.RAW, C.OUT, C.to_wgs, C.from_wgs
    return C


# --------------------------------------------------------- garden shapes ---

def load_streets():
    """OSM street centrelines grouped by normalised name."""
    by_name = defaultdict(list)
    for e in json.loads((RAW / "osm_streets.json").read_text())["elements"]:
        geom = e.get("geometry")
        if not geom or len(geom) < 2:
            continue
        xy = [from_wgs.transform(c["lon"], c["lat"]) for c in geom]
        by_name[C.norm_street(e["tags"]["name"])].append(LineString(xy))
    global STREET_LINES, STREET_TREE
    STREET_LINES = [l for v in by_name.values() for l in v]
    STREET_TREE = STRtree(STREET_LINES)
    return {k: (MultiLineString(v) if len(v) > 1 else v[0]) for k, v in by_name.items()}


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

    length = C.garden_length(g)

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
            center = facade + normal * C.facade_offset(g, first)
            g["placement"] = ("fallback-" if g.get("placement") == "no-street" else "") + \
                ("facade" if first < dist else "open")
            if first >= dist:  # address point is in the open: trust it
                center = pr
            elif getattr(C, "POINT_ON_GARDEN", False) and not _inside(pr, bpolys, btree):
                # the city geocodes the garden itself (Paris): keep it, only
                # orient the strip along the street
                center = pr
                g["placement"] = g["placement"].replace("facade", "as-given")
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


def _tile_horizons(item):
    """Skylines for all gardens of one processing tile (runs in a worker)."""
    (tx_, ty_), gis = item
    bpolys, btop, btree, trees, ttree, gardens = _SHARED
    x0, y0 = tx_ * TILE - MAX_DIST - 20, ty_ * TILE - MAX_DIST - 20
    size = TILE + 2 * MAX_DIST + 40
    hard, ttop, tbase = rasterize_tile(x0, y0, size, bpolys, btop, btree, trees, ttree)
    out = {}
    for gi in gis:
        g = gardens[gi]
        z0 = g["ground"] + EYE
        out[gi] = [horizon_for_point(sx, sy, z0, x0, y0, size, hard, ttop, tbase)
                   for sx, sy in g["samples"]]
    return out


_SHARED = None


def main():
    global GROUND, _SHARED
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="vienna", choices=cities.CITIES)
    ap.add_argument("--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) - 1))
    args = ap.parse_args()
    configure(args.city)

    t0 = time.time()
    print(f"city: {C.NAME}")
    print("loading buildings…")
    bpolys, btop, GROUND = C.load_buildings()
    btree = STRtree(bpolys)
    print(f"  {len(bpolys)} building parts ({time.time() - t0:.0f}s)")

    print("loading trees…")
    tx, ty, th, td, tev = C.load_trees()
    tpts = points(np.column_stack([tx, ty]))
    ttree = STRtree(tpts)
    nb = btree.query_nearest(tpts, max_distance=200, return_distance=False)
    tground = np.full(len(tx), np.nan)
    tground[nb[0]] = GROUND[nb[1]]
    tground = np.where(np.isnan(tground), np.nanmedian(GROUND), tground)
    trees = (tx, ty, th, td, tev, tground)
    print(f"  {len(tx)} trees, {tev.sum()} evergreen ({time.time() - t0:.0f}s)")

    print("loading gardens and venues…")
    gardens = C.load_gardens()
    pois = C.load_pois()
    C.match_names(gardens, pois)
    print(f"  {len(gardens)} gardens, {sum(1 for g in gardens if g['poi'])} matched to a venue")

    print("modelling garden shapes…")
    streets = load_streets()
    for g in gardens:
        g["samples"], g["ground"] = garden_samples(g, bpolys, btree, streets)
        if np.isnan(g["ground"]):
            g["ground"] = float(np.nanmedian(GROUND))
    print("  placement:", dict(Counter(g["placement"] for g in gardens)))

    print(f"computing skylines ({args.workers} workers)…")
    tiles = defaultdict(list)
    for gi, g in enumerate(gardens):
        tiles[(int(g["x"] // TILE), int(g["y"] // TILE))].append(gi)
    items = sorted(tiles.items())
    _SHARED = (bpolys, btop, btree, trees, ttree, gardens)
    horizons = {}
    if args.workers > 1:
        # fork: workers inherit the loaded data instead of pickling it
        with multiprocessing.get_context("fork").Pool(args.workers) as pool:
            for k, res in enumerate(pool.imap_unordered(_tile_horizons, items)):
                horizons.update(res)
                if k % 10 == 0:
                    print(f"  tile {k + 1}/{len(items)} ({time.time() - t0:.0f}s)")
    else:
        for k, item in enumerate(items):
            horizons.update(_tile_horizons(item))
            if k % 10 == 0:
                print(f"  tile {k + 1}/{len(items)} ({time.time() - t0:.0f}s)")

    print("writing output…")
    write_output(gardens, horizons)
    print(f"done in {time.time() - t0:.0f}s")
    if hasattr(C, "STATS"):  # numbers for the city report
        stats = dict(C.STATS, placement=dict(Counter(g["placement"] for g in gardens)),
                     points_per_garden=dict(sorted(Counter(len(g["samples"]) for g in gardens).items())),
                     runtime_s=round(time.time() - t0), workers=args.workers)
        (C.ROOT / "data" / f"stats-{C.NAME}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False))


def write_output(gardens, horizons):
    # group gardens into places: one per venue (rule per city)
    groups = defaultdict(list)
    for gi, g in enumerate(gardens):
        groups[C.place_key(g)].append(gi)

    entries = []
    for key, gis in groups.items():
        gs = [gardens[i] for i in gis]
        poi = gs[0]["poi"]
        pts = [p for g in gs for p in g["samples"]]
        cx, cy = np.mean([g["x"] for g in gs]), np.mean([g["y"] for g in gs])
        lon, lat = to_wgs.transform(cx, cy)
        sample_ll = [to_wgs.transform(px, py) for px, py in pts]
        place = {
            "id": None,
            "name": poi["name"] if poi else gs[0].get("name"),
            "type": poi["amenity"] if poi else gs[0].get("type"),
            "addr": gs[0]["addr"],
            "plz": gs[0]["plz"],
            "lat": round(lat, 6), "lon": round(lon, 6),
            "area": round(sum(g["area"] or 0 for g in gs), 1),
            "kinds": sorted({g["kind"] for g in gs}),
            "p0": None, "n": len(pts),
            "pts": [[round(a, 6), round(b, 6)] for a, b in sample_ll],
        }
        if poi:
            for k in ("cuisine", "hours", "web"):
                if poi[k]:
                    place[k] = poi[k]
        blob = [h for gi in gis for h in horizons[gi]]
        chunk = None
        if getattr(C, "CHUNK", None):
            chunk = (int(cx // C.CHUNK), int(cy // C.CHUNK))
        entries.append((chunk, place, blob))

    if entries[0][0] is not None:
        # places of one chunk are contiguous, so each chunk file is one p0 range
        entries.sort(key=lambda e: e[0])

    places, blobs, chunks, offset = [], [], [], 0
    for chunk, place, blob in entries:
        place["id"], place["p0"] = len(places), offset
        if chunk is not None:
            if not chunks or chunks[-1]["key"] != chunk:
                chunks.append({"key": chunk, "id": f"{chunk[0]}_{chunk[1]}", "p0": offset, "points": 0,
                               "bbox": [999.0, 999.0, -999.0, -999.0]})
            c = chunks[-1]
            c["points"] += place["n"]
            for lon, lat in place["pts"]:
                c["bbox"] = [min(c["bbox"][0], lon), min(c["bbox"][1], lat),
                             max(c["bbox"][2], lon), max(c["bbox"][3], lat)]
        places.append(place)
        blobs.extend(blob)
        offset += place["n"]

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"azMin": AZ_MIN, "azBins": AZ_BINS, "generated": time.strftime("%Y-%m-%d"), "points": offset}
    allh = np.concatenate(blobs).astype(np.uint8)
    allh.tofile(OUT / "horizons.bin")
    if chunks:
        cdir = OUT / "horizons"
        cdir.mkdir(exist_ok=True)
        for old in cdir.glob("*.bin*"):
            old.unlink()
        rows = AZ_BINS  # allh has AZ_BINS rows of 3 bytes per sample point
        for c in chunks:
            # gzipped: static hosts rarely compress .bin; the app inflates with DecompressionStream
            raw = allh[c["p0"] * rows:(c["p0"] + c["points"]) * rows].tobytes()
            (cdir / f"{c['id']}.bin.gz").write_bytes(gzip.compress(raw, 9, mtime=0))
            del c["key"]
        meta["chunks"] = chunks
    (OUT / "places.json").write_text(json.dumps({"meta": meta, "places": places}, ensure_ascii=False,
                                                separators=(",", ":")))
    print(f"  {len(places)} places, {offset} sample points, "
          f"horizons.bin {(OUT / 'horizons.bin').stat().st_size / 1e6:.1f} MB"
          + (f", {len(chunks)} chunks" if chunks else ""))


if __name__ == "__main__":
    main()
