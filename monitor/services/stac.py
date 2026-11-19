"""Sentinel-2 L2A scenes from a STAC API (Element 84 Earth Search by default)."""
from collections import defaultdict
from datetime import date

import requests
from django.conf import settings
from shapely.geometry import shape
from shapely.ops import unary_union

HEADERS = {"User-Agent": "urbangreen/0.1 (Sentinel-2 urban green monitor)"}
MAX_PAGES = 50


class StacError(RuntimeError):
    pass


def search(bbox, start: date, end: date, max_cloud=40):
    """All L2A items intersecting bbox (west, south, east, north) between two dates."""
    body = {
        "collections": [settings.STAC_COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": 100,
    }
    url, method, items = f"{settings.STAC_URL}/search", "POST", []
    for _ in range(MAX_PAGES):
        try:
            if method == "POST":
                response = requests.post(url, json=body, headers=HEADERS, timeout=60)
            else:
                response = requests.get(url, headers=HEADERS, timeout=60)
            response.raise_for_status()
            page = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise StacError(f"Recherche Sentinel-2 impossible : {exc}") from exc
        items.extend(page.get("features", []))
        nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        if not nxt or not page.get("features"):
            break
        url, method = nxt["href"], nxt.get("method", "GET").upper()
        if method == "POST":
            body = {**body, **nxt.get("body", {})} if nxt.get("merge") else nxt.get("body", body)
    return items


def item_date(item):
    return date.fromisoformat(item["properties"]["datetime"][:10])


def group_by_date(items):
    """{date: [items, least cloudy first]} — one date may need several tiles to cover a city."""
    groups = defaultdict(list)
    for item in items:
        groups[item_date(item)].append(item)
    for day in groups:
        groups[day].sort(key=lambda it: it["properties"].get("eo:cloud_cover", 100))
    return dict(groups)


def date_cloud(items):
    return sum(it["properties"].get("eo:cloud_cover", 100) for it in items) / len(items)


def footprint_coverage(items, area):
    """Share of a WGS84 area covered by the data footprints of some products."""
    footprints = unary_union([shape(it["geometry"]) for it in items if it.get("geometry")])
    return footprints.intersection(area).area / area.area if area.area else 0.0


def select_dates(groups, per_month=3):
    """The per_month least cloudy dates of every month, in chronological order."""
    by_month = defaultdict(list)
    for day, items in groups.items():
        by_month[(day.year, day.month)].append((date_cloud(items), day))
    chosen = []
    for candidates in by_month.values():
        chosen += [day for _, day in sorted(candidates)[:per_month]]
    return sorted(chosen)


def band(item, name):
    """(href, scale, offset) of a band asset; reflectance = DN * scale + offset."""
    asset = item["assets"][name]
    info = (asset.get("raster:bands") or [{}])[0]
    return asset["href"], info.get("scale", 1e-4), info.get("offset", 0.0)
