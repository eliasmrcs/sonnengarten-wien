"""Per-city configuration and loaders.

Each city module provides the metric CRS, where its raw data and outputs live,
how to download the raw data, and loaders that turn it into the shared
structures process.py works with (building polygons, trees, gardens, venues).
"""

import importlib

CITIES = ("vienna", "paris")


def load(name):
    if name not in CITIES:
        raise SystemExit(f"unknown city {name!r}, choose from {', '.join(CITIES)}")
    return importlib.import_module(f"cities.{name}")
