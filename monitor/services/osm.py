"""Green spaces from OpenStreetMap (Overpass API), sorted into the app's categories."""
import time

import requests
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from ..categories import AGRI, OTHER, PARKS, TREES

OVERPASS_URLS = [
    # overpass-api.de balances between two backends; when one is down half the
    # requests fail at once, so one backend is also asked directly.
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "urbangreen/0.1 (Sentinel-2 urban green monitor)"}
ROUNDS = 3              # how many times every mirror is tried
TIMEOUT_S = 240


class OsmError(RuntimeError):
    pass


def build_query(south, west, north, east):
    b = f"{south:.5f},{west:.5f},{north:.5f},{east:.5f}"
    return f"""[out:json][timeout:{TIMEOUT_S - 30}][bbox:{b}];
(
  wr["landuse"="forest"]; wr["natural"~"^(wood|scrub)$"];
  way["natural"="tree_row"]; node["natural"="tree"];
  wr["leisure"~"^(park|garden|nature_reserve)$"]; wr["landuse"~"^(recreation_ground|village_green)$"];
  wr["landuse"~"^(grass|cemetery|allotments|flowerbed)$"]; wr["natural"~"^(grassland|heath)$"];
  wr["leisure"="pitch"]["surface"="grass"]; wr["leisure"="golf_course"];
  wr["landuse"~"^(farmland|meadow|orchard|vineyard|plant_nursery)$"];
);
out geom;"""


def category_of(tags):
    """Category code for an OSM element, or None. Trees first: a wood inside a park is trees."""
    landuse, natural, leisure = tags.get("landuse"), tags.get("natural"), tags.get("leisure")
    if landuse == "forest" or natural in ("wood", "scrub", "tree_row", "tree"):
        return TREES
    if leisure in ("park", "garden", "nature_reserve") or landuse in ("recreation_ground", "village_green"):
        return PARKS
    if landuse in ("grass", "cemetery", "allotments", "flowerbed") or natural in ("grassland", "heath"):
        return OTHER
    if leisure in ("pitch", "golf_course"):
        return OTHER
    if landuse in ("farmland", "meadow", "orchard", "vineyard", "plant_nursery"):
        return AGRI
    return None


def _coords(geometry):
    return [(p["lon"], p["lat"]) for p in geometry or [] if p]


def _relation_polygon(element):
    outers, inners = [], []
    for member in element.get("members", []):
        coords = _coords(member.get("geometry"))
        if member.get("type") != "way" or len(coords) < 2:
            continue
        (inners if member.get("role") == "inner" else outers).append(LineString(coords))
    if not outers:
        return None
    # rings may be split over several ways: node them, then rebuild the polygons
    shell = unary_union(list(polygonize(unary_union(outers))))
    if inners:
        shell = shell.difference(unary_union(list(polygonize(unary_union(inners)))))
    return None if shell.is_empty else shell


def element_geometry(element):
    """Shapely geometry (WGS84 lon/lat) of an Overpass element from "out geom"."""
    kind = element.get("type")
    if kind == "node":
        return Point(element["lon"], element["lat"])
    if kind == "way":
        coords = _coords(element.get("geometry"))
        if len(coords) >= 4 and coords[0] == coords[-1]:
            poly = Polygon(coords)
            return poly if poly.is_valid else poly.buffer(0)
        if len(coords) >= 2 and element.get("tags", {}).get("natural") == "tree_row":
            return LineString(coords)
        return None  # an unclosed way cannot be an area
    if kind == "relation":
        return _relation_polygon(element)
    return None


def parse(data):
    """Overpass JSON -> {category code: [geometries]}."""
    out = {TREES: [], PARKS: [], OTHER: [], AGRI: []}
    for element in data.get("elements", []):
        category = category_of(element.get("tags", {}))
        if category is None:
            continue
        geometry = element_geometry(element)
        if geometry is not None and not geometry.is_empty:
            out[category].append(geometry)
    return out


def fetch(south, west, north, east, log=print):
    """Download the green features of a bounding box, trying every mirror a few times."""
    query = build_query(south, west, north, east)
    errors = []
    for round_ in range(ROUNDS):
        for url in OVERPASS_URLS:
            try:
                response = requests.post(url, data={"data": query}, headers=HEADERS, timeout=TIMEOUT_S)
                response.raise_for_status()
                data = response.json()
                if "remark" in data and not data.get("elements"):
                    raise ValueError(data["remark"])
                return parse(data)
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{url}: {exc}")
                log(f"OpenStreetMap : {url.split('/')[2]} indisponible, essai suivant…")
        time.sleep(5 * (round_ + 1))
    raise OsmError("Overpass API indisponible :\n" + "\n".join(errors[-len(OVERPASS_URLS):]))
