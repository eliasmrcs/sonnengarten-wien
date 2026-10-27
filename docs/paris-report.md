# Paris: data report

Date: 27.10.2026. Branch `paris` of `sonnengarten-wien`. Spec: `SPEC-paris.md` (27.10.2026).

**Result:** `app/data/paris/places.json` + `horizons.bin` in exactly the Vienna
format (plus 1 km chunks for phones), for all 20 arrondissements:
**11,338 places, 30,334 sample points**, computed in 263 s on 8 cores.
The Vienna output is unchanged after the refactor (byte-identical `horizons.bin`,
`places.json` identical except `meta.generated`).

## 1. Sources as found (verified 27.10.2026)

| Need | Source used | What the data actually has |
|---|---|---|
| Terraces | opendata.paris.fr `terrasses-autorisations` (ODbL), modified 27.10.2026 06:01 | 23,069 records, **all points** (no polygons), `typologie` (27 values), `adresse`, `arrondissement`, `nom_enseigne` (23,068 filled), `nom_societe`, `siret`, `longueur` / `largeur` in m (23,057 filled; mean 5.8 × 1.5 m), `periode_installation` (**empty in every record**), `lien_affichette` |
| Buildings | IGN BD TOPO v3.3 `BDTOPO_V3:batiment` via Géoplateforme WFS (`data.geopf.fr/wfs`), Licence Ouverte 2.0 | Fields as in the spec. Fetched in 1 km tiles touching Paris + 320 m (156 tiles, 5,000 features per page max): 209,152 buildings incl. 92/93/94 near the boundary. `altitude_maximale_toit` (ridge) only on 22 % |
| Arrondissements | BD TOPO `arrondissement_municipal` | 20 polygons with `code_postal`; used as boundary and for `plz` |
| Trees | opendata.paris.fr `les-arbres` (ODbL), modified 23.10.2026 | 220,112 trees: `hauteurenm` (int, 0 = unknown on 19,768), `circonferenceencm`, `genre`/`espece`, `stadedeveloppement`, `domanialite`. **No crown diameter** (as expected) |
| Names, hours, web, cuisine | OSM Overpass, `ref:INSEE=75056` | 16,881 venues (16,713 named, 7,844 with `opening_hours`) |
| Street centrelines | OSM Overpass, named `highway=*` in Paris | used to orient the terrace strips |

APUR "Volume bâti" and RGE ALTI were **not needed**: BD TOPO gives heights for
94 % of buildings and a ground elevation for nearly all of them (see §4).

All downloads ran from the Mac (`pipeline/download.py --city paris`, ~4 min, 310 MB in `data/raw/paris/`).

## 2. Decisions

1. **Pipeline:** `pipeline/cities/vienna.py` and `pipeline/cities/paris.py` hold everything city-specific;
   `process.py --city paris` runs the shared part. Vienna data stays at `app/data/` (so the
   GitHub Pages app and its service worker are untouched); Paris goes to `app/data/paris/`.
2. **Étalages excluded** (and contre-étalages, "commerce accessoire", "écran parallèle", 6 without type): 4,696 records.
3. **Typology → `kinds` code** (translated in the app):

   | Code | Typologies | Records |
   |---|---|---|
   | `ouverte` | TERRASSE OUVERTE, TERRASSES OUVERTES SUR TROTTOIR / VOIE PIÉTONNE | 10,977 |
   | `fermee` | TERRASSE FERMÉE, TERRASSES FERMÉES SUR TROTTOIR / VOIE PIÉTONNE | 1,813 |
   | `contre` | CONTRE-TERRASSE (PERMANENTE SUR STATIONNEMENT, OUVERTE, SUR TROTTOIR, SUR VOIE PIÉTONNE) | 608 |
   | `estivale` | TERRASSE ESTIVALE SUR TROTTOIR (FACE À / DÉSAXÉE PAR RAPPORT À LA DEVANTURE) | 1,447 |
   | `contre-estivale` | CONTRE TERRASSE ESTIVALE (SUR STATIONNEMENT, TROTTOIR, PLACES ET TERRE-PLEIN, VOIE / AIRE PIÉTONNE) | 3,528 |

   A summer contre-terrasse is one code (`contre-estivale`), so a place listing
   `["contre-estivale","ouverte"]` still tells which part is seasonal.
4. **Terrasses fermées: included and marked** (owner decision): blue ring on the map,
   "terrasse fermée / enclosed terrace" tag in list, sheet and detail. Sun % is computed as for open ones.
5. **⚠ Open question 5, summer terraces without dates: default taken = include and label.**
   The dataset has no dates at all (`periode_installation` is empty everywhere), so the
   app can't hide them by date from the data. 2,454 places are seasonal only (4,308 have some seasonal part).
   If you want them hidden outside the season, the app can do it with a fixed window:
   to my knowledge the Paris *Règlement des étalages et terrasses* (2021) allows summer terraces
   from 1 April to 31 October. Please confirm the dates before we use them.
6. **One place per business (SIRET):** terrace, contre-terrasse and summer terrace of one
   business become one place, one dot, with all their sample points (the `kinds` list shows the
   parts). 926 authorisations without SIRET are grouped by name + address.
   Big brasseries reach up to 30 points; **capped at 10 per place** (`MAX_POINTS`, kept points
   spread over all parts). Vienna has no cap (max 18 there today) to keep its output unchanged.
7. **Name:** the dataset's `nom_enseigne` (upper case, tidied to "Cafe de Flore"), replaced by the
   OSM name when an OSM venue within 80 m has a similar name (≥ 0.75 similarity), because OSM has
   proper casing and accents. OSM also gives hours, website, cuisine and the category.
   Without a match, the category is guessed from the name (café/bar/brasserie/pizz…) or left empty.
8. **Postcode:** from the arrondissement polygon the point lies in (75116 kept where the city says so);
   the dataset's own `arrondissement` field disagrees on 1,170 records, mostly 75016/75116 and points near
   arrondissement borders.

## 3. Terrace geometry

The spec expected address points (as in Vienna). The Paris points are better: **they lie on
the terrace itself.** Distance from the nearest building for points outside buildings (median, m):

| Type | n | 25 % | median | 75 % |
|---|---|---|---|---|
| ouverte, sidewalk | 9,245 | 0.4 | 0.7 | 1.3 |
| fermee, sidewalk | 990 | 0.5 | 0.8 | 1.3 |
| estivale, sidewalk | 1,301 | 0.6 | 1.1 | 2.2 |
| contre-estivale, parking | 2,299 | 2.9 | 3.3 | 3.9 |
| contre-estivale, sidewalk | 937 | 3.5 | 4.5 | 6.0 |
| contre, sidewalk | 278 | 3.7 | 6.0 | 8.4 |

Contre-terrasses already sit at the kerb or on the parking lane. So the rule is (`POINT_ON_GARDEN`):

- point **outside** any building (86 %): keep it as the strip centre; orient the strip along the nearest
  street; length = `longueur` (sample every ~5 m, 1–5 points per authorisation);
- point **inside** a footprint (14 %, mostly terrasses fermées whose glass box is part of the
  BD TOPO footprint): Vienna's façade walk, with the centre placed at `largeur / 2` in front of
  the façade (contre-terrasses: at the estimated kerb).
- every sample point is pushed out of buildings (`_inside`), as in Vienna.

Placement counter: facade 2,474, on-street 32, open 15,438, fallback-facade 59, fallback-open 256, as-given 113, fallback-as-given 1 ("open" = point kept as given; "facade" = point was inside a footprint)

Sample points per authorisation: 1: 8,345, 2: 8,046, 3: 1,584, 4: 316, 5: 82. Per place (after the cap): 1: 3,422, 2: 3,936, 3: 1,282, 4: 1,150, 5: 467, 6: 377, 7: 199, 8: 174, 9: 98, 10: 233.

## 4. Buildings and trees

**Buildings** (199,709 footprint parts after dropping 88 not in service and 9,355 light
constructions under 5 m):

| Roof height from | Parts | Share |
|---|---|---|
| `altitude_maximale_toit` (ridge) | 43,896 | 22.0 % |
| `altitude_minimale_toit` (gutter) | 128,603 | 64.4 % |
| `altitude_minimale_sol` + `hauteur` | 12,559 | 6.3 % |
| floors × 3 m + 1 m | 2,947 | 1.5 % |
| median of neighbours within 50 m | 11,704 | 5.9 % |

Ground elevation per terrace = `altitude_minimale_sol` of the nearest building within 40 m
(range 29–117 m across Paris, 1–99 %). Monuments come as several parts at plausible heights
(Notre-Dame towers 66 m, Sacré-Cœur 65 m, Saint-Sulpice 40 m, Panthéon 54 m; Tour Montparnasse 207 m).
No single absurd block; the Eiffel Tower is not a `batiment` at all.

**Trees:** 220,112 in the register; height used as given (0 < h ≤ 45 m) for 200,341,
derived from circumference (median height of trees in the same 20 cm circumference class) for
3,195, skipped 16,576 (no height and no circumference). Crown diameter =
`clamp(0.25 × h + 0.02 × circumference_cm, 2, 18)` m, the spec's rule: species-agnostic, gives
~9 m for a 20 m plane tree with 200 cm girth, which matches pruned Paris alignment trees better
than free-growing allometry. Crown base `min(max(2.5, 0.3 h), 6)` as in Vienna. 12,300 conifers
(Vienna genus list) are treated as solid. Plane trees (42,593), horse chestnuts (24,810) and limes
(23,070) are deciduous.

## 5. Size and loading

| File | Raw | gzip |
|---|---|---|
| `places.json` | 3.04 MB | 0.66 MB |
| `horizons.bin` (monolithic, data contract) | 25.6 MB | – |
| `horizons/*.bin.gz` (94 chunks of 1 km) | – | 6.7 MB total, median 60 KB (max 284 KB) |

The app (branch `paris` of sun-prototype) loads `places.json`, then only the chunks around the map
view at zoom ≥ 14 (a phone screen needs ~4–9 chunks, ~0.3–0.6 MB); farther dots stay grey until
you pan there. Chunks are stored gzipped because static hosts usually don't compress `.bin`; the
app inflates them with `DecompressionStream` and falls back to the monolithic `horizons.bin` where
that API is missing. **Not checked on Netlify yet** (no deploy, §11): on the first deploy, check that
`horizons/*.bin.gz` comes without `Content-Encoding` (the app handles both) and that `places.json`
is compressed.

## 6. Validation

### 6.1 Shadow maps (`docs/paris-validation/`, 21 June 2026, solar noon 13:50 and 18:00)

`shadowmap.py --city paris` casts shadows directly from the rasters and compares them with the
skyline verdict of every sample point in the 300 × 300 m window:

| Spot | 13:50 agree | 18:00 agree |
|---|---|---|
| Café de Flore / Deux Magots, Bd Saint-Germain ([noon](paris-validation/flore_noon.png), [18:00](paris-validation/flore_1800.png)) | 68/76 | 74/76 |
| Place de la Contrescarpe ([noon](paris-validation/contrescarpe_noon.png), [18:00](paris-validation/contrescarpe_1800.png)) | 37/42 | 31/42 |
| Quai de Valmy, Canal Saint-Martin ([noon](paris-validation/valmy_noon.png), [18:00](paris-validation/valmy_1800.png)) | 90/95 | 94/95 |
| Rue Montorgueil ([noon](paris-validation/montorgueil_noon.png), [18:00](paris-validation/montorgueil_1800.png)) | 186/202 | 196/202 |
| Place du Tertre, Montmartre ([noon](paris-validation/tertre_noon.png), [18:00](paris-validation/tertre_1800.png)) | 55/62 | 57/62 |
| **all** | | **888/954 (93 %)** |

Disagreements are points right at a shadow edge (the skyline takes the worst of three rays per
1° bin) and, on slopes, the debug view itself: it uses one ground height for the whole window, so
on Montmartre the buildings downhill cast no shadow in the picture (the pipeline uses each terrace's own ground).

### 6.2 Physical sanity check (model data, 21 June, share of points in direct sun, 0–9)

```
                                    08 09 10 11 12 13 14 15 16 17 18 19 20 21
Rue Montorgueil (N-S)  bldg   n= 56  1  1  2  2  3  4  4  7  5  1  1  0  0  0
Bd St-Germain north    bldg   n=118  0  1  4  8  9  9  9  9  9  9  8  7  6  0
Bd St-Germain north    +tree  n=118  0  0  1  3  4  4  4  4  4  3  2  1  0  0
Bd St-Germain south    bldg   n= 40  4  7  7  5  2  1  1  1  1  0  0  1  1  2
Quai de Valmy (W bank) bldg   n= 67  6  8  8  8  8  6  6  1  0  1  1  0  0  0
```

- Narrow N–S street: sun only around solar noon (Montorgueil runs slightly NNE, so the peak is ~15:00). ✓
- E–W boulevard, north side (even numbers): sun from 10:00 to 20:00 by buildings; the plane trees halve it. ✓
- South side: morning only. ✓  Quai de Valmy (buildings on the west, facing the canal): morning sun, shade after 15:00. ✓

### 6.3 Sun geometry

At the 21 June sunrise the sun stands at azimuth 52.8°, at sunset at 307.2° (pipeline solar
model, geometric horizon): the stored range 40–320° covers Paris. One sun position from the
centre (48.8566, 2.3522) is used for the whole city, as in Vienna.

### 6.4 Vienna regression

The original pipeline re-run first reproduced the committed files exactly (only `meta.generated`
differs). After the refactor: `horizons.bin` byte-identical, `places.json` identical except
`meta.generated`. Runtime 296 s → 103 s (7 workers).

## 7. Statistics

| | |
|---|---|
| Terraces downloaded / after filtering | 23,069 / 18,373 |
| Places (businesses) | 11,338 |
| With a name | dataset: 100 % of terraces; OSM match: 63.2 % of terraces |
| With opening hours | 29.5 % of places (Vienna: 69 %); limited by OSM: 63 % of terraces match a venue, 46 % of Paris venues have `opening_hours` |
| With website / cuisine | 19.3 % / 30.1 % |
| Category (OSM or guessed) | 61.9 % of places |
| Buildings with own height | 94.1 % (rest filled) |
| Trees used / with own height | 203,536 / 200,341 |
| Runtime | download ~4 min; process 263 s (8 cores) |

## 8. Known limits

- **Roof shapes:** 64 % of buildings only have the gutter height. Mansard roofs add a few metres,
  so shadows from Haussmann blocks are a bit short (sun reported a little too often in narrow streets).
- **Monuments** are lower than reality (domes, spires) for the same reason.
- **Terrace size** comes from the authorisation (`longueur` × `largeur`); actual furniture,
  awnings and parasols are unknown, as in Vienna.
- **Private trees** (courtyards) are missing; tree crowns are estimated, not measured.
- **Seasonal dates** are missing in the data (§2.5).
- **Ground** is taken from the nearest building, not from a terrain model; on stairs and steep
  streets (Montmartre) a terrace can be off by a few metres.
- **Category** unknown for 38.1 % of places (no OSM match and nothing in the name); they only show under "all".
