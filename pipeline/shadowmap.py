"""Debug view: render a shadow map around a location at a given time, with the
garden sample points coloured by what the skyline data says.

    .venv/bin/python pipeline/shadowmap.py 48.2087 16.3700 "2026-10-23 14:00" out.png
    .venv/bin/python pipeline/shadowmap.py --city paris 48.8540 2.3326 "2026-06-21 13:00" out.png

Times are local to the city.

The shadow map is cast directly (independent of the skyline data), so the
two can be compared: sample points should be yellow where the ground is lit.
"""

import argparse
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw

import process as P


def sun_position(dt, lat, lon):
    """Low-precision solar position (good to ~0.5°), enough for a debug view."""
    t = dt.astimezone(ZoneInfo("UTC"))
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hour * 60 + eqt + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    zen = math.acos(cz)
    az = math.degrees(math.acos((math.sin(la) * cz - math.sin(decl)) / (math.cos(la) * math.sin(zen))))
    az = (az + 180) % 360 if ha > 0 else (540 - az) % 360
    return az, 90 - math.degrees(zen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="vienna")
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("when")
    ap.add_argument("out")
    a = ap.parse_args()
    C = P.configure(a.city)
    lat, lon, when, out = a.lat, a.lon, a.when, a.out
    dt = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(C.TZ))
    az, el = sun_position(dt, lat, lon)
    print(f"sun az {az:.1f}° el {el:.1f}°")

    cx, cy = P.from_wgs.transform(lon, lat)
    half = 150
    x0, y0, size = cx - half - P.MAX_DIST, cy - half - P.MAX_DIST, 2 * (half + P.MAX_DIST)

    bpolys, btop, P.GROUND = C.load_buildings()
    from shapely import STRtree, points
    btree = STRtree(bpolys)
    tx, ty, th, td, tev = C.load_trees()
    ttree = STRtree(points(np.column_stack([tx, ty])))
    ground0 = float(np.nanmedian(P.GROUND[btree.query(P.Point(cx, cy).buffer(100))]))
    trees = (tx, ty, th, td, tev, np.full(len(tx), ground0))
    hard, ttop, tbase = P.rasterize_tile(x0, y0, size, bpolys, btop, btree, trees, ttree)

    # cast shadows for the inner window: march from each ground cell towards the sun
    n = hard.shape[0]
    i0 = int(P.MAX_DIST / P.RES)
    w = int(2 * half / P.RES)
    rows, cols = np.mgrid[i0:i0 + w, i0:i0 + w]
    z0 = ground0 + P.EYE
    lit = np.ones((w, w), np.float32)
    sa, ca, te = math.sin(math.radians(az)), math.cos(math.radians(az)), math.tan(math.radians(el))
    for d in np.arange(P.RES, P.MAX_DIST, P.RES / 2):
        r = np.clip((rows - ca * d / P.RES).astype(int), 0, n - 1)
        c = np.clip((cols + sa * d / P.RES).astype(int), 0, n - 1)
        z = z0 + d * te
        lit[hard[r, c] > z] = 0
        inside_tree = (ttop[r, c] > z) & (tbase[r, c] < z)
        lit[inside_tree] = np.minimum(lit[inside_tree], 0.5)
    base = hard[i0:i0 + w, i0:i0 + w] > 0

    img = np.zeros((w, w, 3), np.uint8)
    img[...] = (250, 225, 140)                        # lit ground
    img[lit == 0.5] = (190, 200, 120)                 # through trees
    img[lit == 0] = (130, 140, 160)                   # shade
    img[base] = (70, 70, 80)                          # roofs
    im = Image.fromarray(img).resize((w * 2, w * 2), Image.NEAREST)
    draw = ImageDraw.Draw(im)

    data = json.loads((P.OUT / "places.json").read_text())
    H = np.fromfile(P.OUT / "horizons.bin", np.uint8).reshape(-1, data["meta"]["azBins"], 3)
    k = int(round(az)) - data["meta"]["azMin"]
    for pl in data["places"]:
        for j, (plon, plat) in enumerate(pl["pts"]):
            px, py = P.from_wgs.transform(plon, plat)
            u, v = (px - (cx - half)) / P.RES * 2, ((cy + half) - py) / P.RES * 2
            if not (0 <= u < w * 2 and 0 <= v < w * 2):
                continue
            hb = H[pl["p0"] + j, k]
            state = 0 if el <= hb[0] / 2 else (0.5 if hb[2] and hb[1] / 2 <= el <= hb[2] / 2 else 1)
            col = {1: (255, 150, 0), 0.5: (120, 160, 40), 0: (40, 60, 200)}[state]
            draw.ellipse([u - 5, v - 5, u + 5, v + 5], fill=col, outline=(255, 255, 255))
    im.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
