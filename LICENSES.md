# Licences and attribution

The code in this repository is the project's own. The data files the pipeline
produces (`app/data/**/places.json`, `horizons.bin`, `horizons/*.bin`) are
**derived databases** of the sources below and are published with the app, so
they carry those sources' terms.

## Vienna (`app/data/`)

| Source | Used for | Licence | Attribution |
|---|---|---|---|
| Stadt Wien OGD: Schanigärten (`SCHANIGARTENOGD`), Baukörpermodell (`FMZKBKMOGD`), Baumkataster (`BAUMKATOGD`) | gardens, buildings, trees | CC BY 4.0 | "Datenquelle: Stadt Wien – data.wien.gv.at" |
| OpenStreetMap | venue names, hours, website, cuisine; street centrelines | ODbL 1.0 | "© OpenStreetMap contributors" |
| basemap.at (app map tiles) | map | CC BY 4.0 | "basemap.at" |

Because OSM data is in `places.json`, the Vienna `places.json` is offered under
the **ODbL 1.0** as well.

## Paris (`app/data/paris/`)

| Source | Used for | Licence | Attribution |
|---|---|---|---|
| Ville de Paris: *Terrasses et étalages : Autorisations* (opendata.paris.fr, `terrasses-autorisations`) | terraces: position, size, type, business name | ODbL 1.0 | "Ville de Paris" |
| Ville de Paris: *Les arbres* (`les-arbres`) | trees: position, height, circumference, genus | ODbL 1.0 | "Ville de Paris" |
| IGN BD TOPO® v3: `batiment`, `arrondissement_municipal` (Géoplateforme WFS) | buildings and heights, ground elevation, arrondissements and postcodes | Licence Ouverte / Open Licence 2.0 | "IGN – BD TOPO®" |
| OpenStreetMap | venue names, hours, website, cuisine; street centrelines | ODbL 1.0 | "© OpenStreetMap contributors" |

**ODbL consequence (Paris and Vienna):** the derived databases (`places.json`,
`horizons*.bin`) are publicly downloadable by the app, so they must be offered
under the ODbL 1.0 with the attributions above. Showing results in the app is a
"produced work" and only needs the attribution; the data files themselves may
not be kept proprietary.

The app must show, for Paris: "Ville de Paris (ODbL) · IGN BD TOPO® (Licence
Ouverte) · © OpenStreetMap contributors (ODbL)" plus the map tile provider.
