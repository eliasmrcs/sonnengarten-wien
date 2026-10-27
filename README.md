# Sonnengarten Wien ☀︎

Which Schanigarten in Vienna has sun right now — and until when?

A phone-friendly web app that shows every approved Schanigarten in Vienna on a
map, coloured by how much of it is in the sun at the chosen time. Search by
café / bar / restaurant name or street, see a sun timeline for the whole day,
and find the nearest sunny spot from your location.

## How it works

1. **Data** (all open):
   - Schanigarten locations, size and type: Stadt Wien OGD `SCHANIGARTENOGD` (~3,000)
   - 3D building bodies with roof heights: Stadt Wien OGD `FMZKBKMOGD` (~680,000 parts)
   - Public trees with height and crown diameter: Stadt Wien OGD `BAUMKATOGD` (~233,000)
   - Venue names, type, hours, website: OpenStreetMap (Overpass)
   - Cloud forecast: Open-Meteo (live, in the app)
2. **Garden shape**: the city publishes the *address point* (inside the
   building), the street and the area of each garden. The pipeline finds the
   nearest point on that street (OSM centrelines), walks towards the address
   until it hits the façade, and puts the garden there as a strip parallel to
   the street: on the sidewalk right at the wall, in the parking lane a few
   metres out. 1–5 sample points are spread along the strip.
3. **Skyline per sample point**: buildings and tree crowns within 300 m are
   rasterised at 0.5 m; for every compass direction (1° steps) we store the
   elevation angle up to which buildings block the sky, plus the band of
   elevations covered by tree crowns. Conifers are treated as solid.
4. **In the app**: the sun position (NOAA algorithm) is compared with each
   point's skyline. Sun above the building skyline → sun; inside a tree band →
   filtered light (≈60 % passes bare crowns in early spring, ≈15 % in summer);
   otherwise shade. The garden's percentage is the share of its sample points in sun.

## Known limits

- Umbrellas, awnings and the actual table layout are unknown.
- Only public trees are in the register; trees in private courtyards are missing.
- Building heights are one height per building part (no roof shapes).
- Names come from OpenStreetMap; gardens without a nearby match show their address.

## Run it

The pipeline is city-configurable: everything city-specific lives in
`pipeline/cities/<city>.py` (sources, CRS, field mappings, name matching, garden
placement); `process.py` is shared. Vienna is the default.

```bash
python3 -m venv .venv && .venv/bin/pip install numpy shapely pillow pyproj
.venv/bin/python pipeline/download.py                 # Vienna: ~600 MB into data/raw/
.venv/bin/python pipeline/process.py                  # writes app/data/
.venv/bin/python pipeline/download.py --city paris    # Paris: ~310 MB into data/raw/paris/
.venv/bin/python pipeline/process.py --city paris     # writes app/data/paris/
python3 -m http.server 8765 --directory app
```

Then open http://localhost:8765. `process.py` runs the skyline step in parallel
(`--workers N`, default: CPU cores − 1); Vienna takes ~100 s, Paris ~250 s on 8 cores.

Paris output differs in one way: besides `horizons.bin`, the horizons are split
into 1 km chunks (`app/data/paris/horizons/<id>.bin.gz`, listed in
`places.json` → `meta.chunks`), because the whole file (26 MB) is too big for a
phone. The data contract per place (`p0`, `n`, …) is the same. See
`docs/paris-report.md` for sources, decisions and validation.

To check the model visually, `pipeline/shadowmap.py` casts a shadow map for any
spot and time (local time of the city) and overlays what the app data says for
each sample point; it also prints how many sample points agree with the cast map:

```bash
.venv/bin/python pipeline/shadowmap.py 48.2114 16.3618 "2027-04-20 15:00" data/debug/out.png
.venv/bin/python pipeline/shadowmap.py --city paris 48.8541 2.3330 "2026-06-21 13:50" data/debug/flore.png
```

Location access needs HTTPS (or localhost),
so put the `app/` folder on any static host (GitHub Pages, Netlify, Cloudflare
Pages) to use it on your phone. Re-run the two pipeline scripts every few
months to pick up new Schanigärten and terraces.

Data licences and attribution: see `LICENSES.md`.
