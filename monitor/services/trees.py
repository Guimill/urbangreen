"""Why can "trees" look as green, or greener, in winter than in summer?

A Sentinel-2 pixel is 10 m x 10 m (100 m²). A street tree mapped in OpenStreetMap as a
point is drawn as a 4 m-radius crown (about 50 m²), and every pixel it touches is put in
the "trees" category. Most of those pixels are therefore mostly lawn, pavement or roof,
and the NDVI they measure is a mix:

    NDVI(pixel) ≈ f · NDVI(crown) + (1 − f) · NDVI(what is around the crown)

with f the share of the pixel covered by the crown. In the Atlantic west of France the
lawns are green in winter (rain, mild temperatures) and yellow in a dry summer, while a
deciduous crown does the opposite. The less of the pixel the crown covers, the more the
pixel follows the lawn, and below roughly half coverage it is greener in winter.

This module tests that explanation on a commune, with five checks:

1. by kind of tree feature: the core of the woods (pure canopy) against their edges,
   tree rows and isolated trees;
2. by canopy fraction f, computed exactly on a 2 m sub-grid: the summer − winter change
   must grow with f;
3. a linear mixing model fitted on those pixels: it gives the NDVI change of a pure
   crown (f = 1) and of the surroundings (f = 0) on its own;
4. by leaf type (OSM leaf_type / leaf_cycle when mapped): evergreen conifers do not
   lose their needles, so they stay green in winter;
5. the weather (Open-Meteo archive): a summer water deficit (rain − evapotranspiration)
   dries the lawns and stresses the trees, which lowers summer NDVI.

Run it with ``python manage.py diagnose_trees <commune>``.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import requests
from affine import Affine
from rasterio.features import rasterize
from shapely.ops import unary_union

from ..categories import OTHER, PARKS, SEASON_LABELS, TREES, season_of
from . import analysis, osm, raster

CORE_DISTANCE_M = 20          # a wood pixel further than this from the edge is pure canopy
SUBPIXELS = 5                 # canopy fraction computed on a 2 m sub-grid
FRACTION_BINS = [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.0)]
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

KINDS = [
    ("wood_core", f"Cœur des bois (à plus de {CORE_DISTANCE_M} m de la lisière)"),
    ("wood_edge", f"Lisière des bois (moins de {CORE_DISTANCE_M} m)"),
    ("row", "Alignements d'arbres (natural=tree_row)"),
    ("single", "Arbres isolés (natural=tree)"),
]
LEAF_GROUPS = [
    ("broadleaved", "Feuillus (leaf_type=broadleaved)"),
    ("needleleaved", "Conifères (leaf_type=needleleaved)"),
    ("deciduous", "Caducs (leaf_cycle=deciduous)"),
    ("evergreen", "Persistants (leaf_cycle=evergreen)"),
]


# --------------------------------------------------------------------------- #
# OpenStreetMap tree features, with their tags
# --------------------------------------------------------------------------- #
@dataclass
class TreeFeature:
    kind: str                 # "wood", "row" or "single"
    geometry: object          # shapely, WGS84
    tags: dict = field(default_factory=dict)


def build_query(south, west, north, east):
    b = f"{south:.5f},{west:.5f},{north:.5f},{east:.5f}"
    return f"""[out:json][timeout:{osm.TIMEOUT_S - 30}][bbox:{b}];
( wr["landuse"="forest"]; wr["natural"~"^(wood|scrub)$"]; way["natural"="tree_row"]; node["natural"="tree"]; );
out geom;"""


def parse(data):
    out = []
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        natural = tags.get("natural")
        kind = {"tree": "single", "tree_row": "row"}.get(natural, "wood")
        geometry = osm.element_geometry(element)
        if geometry is not None and not geometry.is_empty:
            out.append(TreeFeature(kind, geometry, tags))
    return out


def fetch_features(grid, log=print):
    west, south, east, north = grid.bbox_wgs84()
    return parse(osm.overpass(build_query(south, west, north, east), log=log))


# --------------------------------------------------------------------------- #
# Rasters
# --------------------------------------------------------------------------- #
def _crown(grid, feature):
    """Footprint of a feature in EPSG:3857, with the same widths as the category raster."""
    g = raster.to_3857(feature.geometry)
    if g.geom_type == "Point":
        return g.buffer(raster.TREE_RADIUS_M * grid.scale, quad_segs=8)
    if g.geom_type in ("LineString", "MultiLineString"):
        return g.buffer(raster.TREE_ROW_HALF_WIDTH_M * grid.scale, quad_segs=4)
    return g


def _burn(grid, shapes, all_touched=False):
    if not shapes:
        return np.zeros(grid.shape, bool)
    return rasterize([(s, 1) for s in shapes], out_shape=grid.shape, transform=grid.transform,
                     all_touched=all_touched, dtype="uint8").astype(bool)


def canopy_fraction(grid, features, factor=SUBPIXELS):
    """Share (0–1) of every pixel covered by mapped crowns, computed on a finer sub-grid."""
    shapes = [(_crown(grid, f), 1) for f in features]
    if not shapes:
        return np.zeros(grid.shape, "float32")
    fine = Affine(grid.res / factor, 0, grid.x0, 0, -grid.res / factor, grid.y1)
    sub = rasterize(shapes, out_shape=(grid.height * factor, grid.width * factor), transform=fine, dtype="uint8")
    return sub.reshape(grid.height, factor, grid.width, factor).mean(axis=(1, 3)).astype("float32")


def kind_zones(grid, features, core_m=CORE_DISTANCE_M):
    """Exclusive pixel masks: wood core, wood edge, tree rows (outside woods), isolated trees (outside both)."""
    woods = unary_union([_crown(grid, f) for f in features if f.kind == "wood"] or [])
    wood = _burn(grid, [woods] if not woods.is_empty else [])
    core_shape = woods.buffer(-core_m * grid.scale) if not woods.is_empty else woods
    core = _burn(grid, [core_shape] if not core_shape.is_empty else [])
    rows = _burn(grid, [_crown(grid, f) for f in features if f.kind == "row"], all_touched=True) & ~wood
    single = _burn(grid, [_crown(grid, f) for f in features if f.kind == "single"], all_touched=True) & ~wood & ~rows
    return {"wood_core": core, "wood_edge": wood & ~core, "row": rows, "single": single}


def leaf_zones(grid, features):
    def zone(key, value):
        return _burn(grid, [_crown(grid, f) for f in features if f.tags.get(key) == value],
                     all_touched=True)
    return {"broadleaved": zone("leaf_type", "broadleaved"), "needleleaved": zone("leaf_type", "needleleaved"),
            "deciduous": zone("leaf_cycle", "deciduous"), "evergreen": zone("leaf_cycle", "evergreen")}


# --------------------------------------------------------------------------- #
# The tests themselves (pure functions on arrays: easy to check on synthetic data)
# --------------------------------------------------------------------------- #
def zone_stats(a, b, zone, pixel_ha):
    """NDVI of a zone in season A and B (pixels seen in both), and the share greener in A."""
    seen = zone & np.isfinite(a) & np.isfinite(b)
    n = int(seen.sum())
    if not n:
        return {"pixels": 0, "area_ha": 0.0, "a": None, "b": None, "diff": None, "greener_in_a": None}
    va, vb = a[seen], b[seen]
    return {"pixels": n, "area_ha": round(n * pixel_ha, 1), "a": float(va.mean()), "b": float(vb.mean()),
            "diff": float((vb - va).mean()), "greener_in_a": float((vb < va).mean())}


def fraction_bins(a, b, zone, fraction, pixel_ha, bins=FRACTION_BINS):
    out = []
    for lo, hi in bins:
        sel = fraction >= lo if lo == hi else (fraction >= lo) & (fraction < hi)
        out.append({"from": lo, "to": hi, **zone_stats(a, b, zone & sel, pixel_ha)})
    return out


def mixing_model(a, b, zone, fraction):
    """Least-squares fit of NDVI = crown·f + around·(1 − f) in each season.

    Returns the NDVI of a pure crown and of the surroundings in A and in B, and R².
    """
    seen = zone & np.isfinite(a) & np.isfinite(b)
    f = fraction[seen].astype("float64")
    if f.size < 10 or np.ptp(f) < 0.2:
        return None
    design = np.column_stack([f, 1 - f])
    out = {}
    for name, values in (("a", a[seen]), ("b", b[seen])):
        y = values.astype("float64")
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        r2 = 1 - ((y - design @ coef) ** 2).sum() / max(((y - y.mean()) ** 2).sum(), 1e-12)
        out[name] = {"crown": float(coef[0]), "around": float(coef[1]), "r2": float(r2)}
    out["crown_diff"] = out["b"]["crown"] - out["a"]["crown"]
    out["around_diff"] = out["b"]["around"] - out["a"]["around"]
    # canopy fraction above which a pixel gets greener from A to B
    slope = out["crown_diff"] - out["around_diff"]
    out["break_even"] = float(-out["around_diff"] / slope) if slope > 0 else None
    return out


def compare(a, b, inside, categories, zones, leaves, fraction, pixel_ha):
    """All the array-based checks for two seasonal NDVI composites A and B."""
    trees = inside & (categories == TREES)
    return {
        "trees": zone_stats(a, b, trees, pixel_ha),
        "kinds": {k: zone_stats(a, b, zones[k] & trees, pixel_ha) for k, _ in KINDS},
        "fraction": fraction_bins(a, b, trees, fraction, pixel_ha),
        "mixing": mixing_model(a, b, trees, fraction),
        "leaves": {k: zone_stats(a, b, leaves[k] & trees, pixel_ha) for k, _ in LEAF_GROUPS},
        "context": {"parks": zone_stats(a, b, inside & (categories == PARKS), pixel_ha),
                    "other": zone_stats(a, b, inside & (categories == OTHER), pixel_ha)},
    }


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #
def seasonal_water_balance(daily):
    """Open-Meteo daily JSON -> {(year, season): {rain, et0, balance, tmax, days}}."""
    acc = defaultdict(lambda: {"rain": 0.0, "et0": 0.0, "tmax": 0.0, "days": 0})
    for day, rain, et0, tmax in zip(daily["time"], daily["precipitation_sum"],
                                   daily["et0_fao_evapotranspiration"], daily["temperature_2m_max"]):
        if rain is None or et0 is None or tmax is None:
            continue
        season, year = season_of(date.fromisoformat(day))
        a = acc[(year, season)]
        a["rain"] += rain
        a["et0"] += et0
        a["tmax"] += tmax
        a["days"] += 1
    return {k: {"rain": round(v["rain"]), "et0": round(v["et0"]), "balance": round(v["rain"] - v["et0"]),
                "tmax": round(v["tmax"] / v["days"], 1), "days": v["days"]} for k, v in acc.items() if v["days"]}


def fetch_weather(lat, lon, start, end):
    response = requests.get(OPEN_METEO_URL, timeout=60, params={
        "latitude": round(lat, 3), "longitude": round(lon, 3), "start_date": start.isoformat(),
        "end_date": end.isoformat(), "timezone": "Europe/Paris",
        "daily": "precipitation_sum,temperature_2m_max,et0_fao_evapotranspiration"})
    response.raise_for_status()
    return seasonal_water_balance(response.json()["daily"])


# --------------------------------------------------------------------------- #
# Whole diagnosis of a commune
# --------------------------------------------------------------------------- #
def season_series(city, zones, trees_mask):
    """Median NDVI of each kind of tree feature, season by season."""
    by_season = defaultdict(list)
    for scene in analysis.ok_scenes(city):
        try:
            with np.load(scene.ndvi_path) as d:
                values = raster.dequantize(d["ndvi"])
        except OSError:
            continue
        row = {}
        for key, _ in KINDS:
            v = values[zones[key] & trees_mask]
            v = v[np.isfinite(v)]
            row[key] = float(v.mean()) if v.size else None
        by_season[scene.season[::-1]].append(row)
    order = {k: i for i, k in enumerate(SEASON_LABELS)}
    out = []
    for (year, season) in sorted(by_season, key=lambda ys: (ys[0], order[ys[1]])):
        rows = by_season[(year, season)]
        out.append({"year": year, "season": season, "dates": len(rows),
                    "values": {k: (float(np.median([r[k] for r in rows if r[k] is not None]))
                                   if any(r[k] is not None for r in rows) else None) for k, _ in KINDS}})
    return out


def diagnose(city, season_a, season_b, features=None, weather=True, log=print):
    """Run every check for one commune. season_a / season_b: (year, season)."""
    from .pipeline import Masks
    masks = Masks.load(city)
    grid = masks.grid
    if features is None:
        log("OpenStreetMap : téléchargement des arbres avec leurs attributs…")
        features = fetch_features(grid, log=log)
    a, scenes_a = analysis.season_composite(city, *season_a)
    b, scenes_b = analysis.season_composite(city, *season_b)
    if a is None or b is None:
        raise ValueError("Pas d'image pour l'une des deux saisons")
    log("Calcul de la part de couronne dans chaque pixel…")
    zones, leaves = kind_zones(grid, features), leaf_zones(grid, features)
    fraction = canopy_fraction(grid, features)
    pixel_ha = grid.pixel_area_m2 / 10_000
    result = compare(a, b, masks.inside, masks.categories, zones, leaves, fraction, pixel_ha)
    result.update({
        "city": city, "season_a": season_a, "season_b": season_b,
        "dates_a": len(scenes_a), "dates_b": len(scenes_b),
        "features": {k: sum(1 for f in features if f.kind == k) for k in ("wood", "row", "single")},
        "seasons": season_series(city, zones, masks.inside & (masks.categories == TREES)),
        "weather": None,
    })
    if weather:
        scenes = analysis.ok_scenes(city)
        lat = sum(grid.bounds_latlng()[i][0] for i in (0, 1)) / 2
        lon = sum(grid.bounds_latlng()[i][1] for i in (0, 1)) / 2
        start = min(s.date for s in scenes).replace(day=1)
        try:
            result["weather"] = fetch_weather(lat, lon, start, min(max(s.date for s in scenes), date.today()))
        except (requests.RequestException, KeyError, ValueError) as exc:
            log(f"Météo indisponible : {exc}")
    return result


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _n(v, digits=2, signed=False):
    if v is None:
        return "–"
    s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    return s.replace(".", ",").replace("-", "−")


def _pct(v):
    return "–" if v is None else f"{v * 100:.0f} %"


def _name(season):
    year, key = season
    return f"{SEASON_LABELS[key]} {year}"


def report_markdown(r):
    """A readable report (French, like the app) of a diagnose() result."""
    A, B = _name(r["season_a"]), _name(r["season_b"])
    city = r["city"]
    t = r["trees"]
    lines = [
        f"# Arbres : pourquoi parfois plus verts en {A.split()[0].lower()} qu'en {B.split()[0].lower()} ? — {city.name}",
        "",
        f"Comparaison **A = {A}** ({r['dates_a']} dates) et **B = {B}** ({r['dates_b']} dates). "
        f"NDVI moyen des pixels vus dans les deux saisons ; « plus vert en A » = part des pixels dont le NDVI baisse de A à B.",
        "",
        f"Catégorie « Arbres et bois » entière : NDVI {_n(t['a'])} → {_n(t['b'])} "
        f"(écart B − A {_n(t['diff'], signed=True)}), **{_pct(t['greener_in_a'])} des pixels plus verts en A**.",
        "",
        "## 1. Par type d'objet OpenStreetMap",
        "",
        f"Objets : {r['features']['wood']} bois/forêts/fourrés, {r['features']['row']} alignements, "
        f"{r['features']['single']} arbres isolés.",
        "",
        "| Type | Surface | NDVI A | NDVI B | B − A | Pixels plus verts en A |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in KINDS:
        s = r["kinds"][key]
        lines.append(f"| {label} | {_n(s['area_ha'], 0)} ha | {_n(s['a'])} | {_n(s['b'])} | "
                     f"{_n(s['diff'], signed=True)} | {_pct(s['greener_in_a'])} |")
    lines += ["", "## 2. Selon la part du pixel couverte par la couronne", "",
              "Part calculée sur une grille de 2 m (couronne de 4 m de rayon par arbre isolé, 8 m de large par alignement).", "",
              "| Couronne dans le pixel | Pixels | NDVI A | NDVI B | B − A | Pixels plus verts en A |",
              "|---|---:|---:|---:|---:|---:|"]
    for s in r["fraction"]:
        label = "100 %" if s["from"] == s["to"] else f"{s['from'] * 100:.0f}–{s['to'] * 100:.0f} %"
        lines.append(f"| {label} | {s['pixels']} | {_n(s['a'])} | {_n(s['b'])} | {_n(s['diff'], signed=True)} | "
                     f"{_pct(s['greener_in_a'])} |")
    m = r["mixing"]
    if m:
        lines += ["", "## 3. Modèle de mélange", "",
                  "NDVI(pixel) = f · NDVI(couronne) + (1 − f) · NDVI(alentours), ajusté sur tous les pixels « arbres » :", "",
                  "| | Couronne pure (f = 1) | Alentours (f = 0) | R² |", "|---|---:|---:|---:|",
                  f"| A — {A} | {_n(m['a']['crown'])} | {_n(m['a']['around'])} | {_n(m['a']['r2'])} |",
                  f"| B — {B} | {_n(m['b']['crown'])} | {_n(m['b']['around'])} | {_n(m['b']['r2'])} |",
                  f"| B − A | {_n(m['crown_diff'], signed=True)} | {_n(m['around_diff'], signed=True)} | |", ""]
        if m["break_even"] is not None:
            lines.append(f"Un pixel devient plus vert de A à B quand la couronne en couvre plus de "
                         f"**{m['break_even'] * 100:.0f} %**.")
    lines += ["", "## 4. Selon le feuillage (quand il est renseigné dans OSM)", "",
              "| Feuillage | Surface | NDVI A | NDVI B | B − A | Pixels plus verts en A |", "|---|---:|---:|---:|---:|---:|"]
    for key, label in LEAF_GROUPS:
        s = r["leaves"][key]
        lines.append(f"| {label} | {_n(s['area_ha'], 1)} ha | {_n(s['a'])} | {_n(s['b'])} | "
                     f"{_n(s['diff'], signed=True)} | {_pct(s['greener_in_a'])} |")
    lines += ["", "Pour comparaison, les pelouses et les parcs :", "",
              "| Catégorie | NDVI A | NDVI B | B − A |", "|---|---:|---:|---:|"]
    for key, label in (("other", "Autres espaces verts (pelouses…)"), ("parks", "Parcs et jardins")):
        s = r["context"][key]
        lines.append(f"| {label} | {_n(s['a'])} | {_n(s['b'])} | {_n(s['diff'], signed=True)} |")

    lines += ["", "## 5. Saison par saison (NDVI moyen, médiane des dates)", "",
              "| Saison | Dates | " + " | ".join(label.split(" (")[0] for _, label in KINDS) + " |",
              "|---|---:|" + "---:|" * len(KINDS)]
    for s in r["seasons"]:
        lines.append(f"| {_name((s['year'], s['season']))} | {s['dates']} | "
                     + " | ".join(_n(s["values"][k]) for k, _ in KINDS) + " |")
    if r["weather"]:
        lines += ["", "## 6. Météo (Open-Meteo, réanalyse ERA5)", "",
                  "Bilan hydrique = pluie − évapotranspiration de référence (ET0). Négatif : le sol s'assèche.", "",
                  "| Saison | Pluie | ET0 | Bilan | T max moyenne |", "|---|---:|---:|---:|---:|"]
        order = {k: i for i, k in enumerate(SEASON_LABELS)}
        for (year, season), w in sorted(r["weather"].items(), key=lambda kv: (kv[0][0], order[kv[0][1]])):
            lines.append(f"| {_name((year, season))} | {w['rain']} mm | {w['et0']} mm | {w['balance']:+d} mm | "
                         f"{_n(w['tmax'], 1)} °C |".replace("-", "−"))
    lines += ["", "## Verdict", ""] + verdict(r)
    return "\n".join(lines) + "\n"


def verdict(r):
    """Plain-language conclusions, only from what the numbers show."""
    out = []
    k, m = r["kinds"], r["mixing"]
    core, single = k["wood_core"], k["single"]
    if core["diff"] is not None and single["diff"] is not None:
        out.append(f"- Le cœur des bois (canopée pure) change de {_n(core['diff'], signed=True)} entre A et B, "
                   f"les arbres isolés de {_n(single['diff'], signed=True)} : "
                   + ("le signal « arbres » dépend surtout de ce qui entoure les arbres." if single["diff"] < core["diff"] - 0.1
                      else "les deux suivent la même tendance."))
    bins = [b for b in r["fraction"] if b["diff"] is not None]
    if len(bins) >= 3:
        rising = all(x["diff"] <= y["diff"] + 0.01 for x, y in zip(bins, bins[1:]))
        out.append(f"- Plus la couronne couvre le pixel, plus il verdit de A à B "
                   f"({_n(bins[0]['diff'], signed=True)} → {_n(bins[-1]['diff'], signed=True)})"
                   + (" : relation régulière, c'est la signature d'un mélange de pixels." if rising
                      else " : la relation n'est pas régulière, le mélange n'explique pas tout."))
    if m:
        out.append(f"- Modèle de mélange : couronne pure {_n(m['crown_diff'], signed=True)}, alentours "
                   f"{_n(m['around_diff'], signed=True)}"
                   + (f" ; il faut plus de {m['break_even'] * 100:.0f} % de couronne pour qu'un pixel soit plus vert en B."
                      if m["break_even"] is not None else "."))
    lv = r["leaves"]
    if lv["needleleaved"]["diff"] is not None and lv["broadleaved"]["diff"] is not None:
        out.append(f"- Conifères {_n(lv['needleleaved']['diff'], signed=True)} contre feuillus "
                   f"{_n(lv['broadleaved']['diff'], signed=True)} "
                   f"(attention : seulement {_n(lv['needleleaved']['area_ha'], 1)} ha de conifères renseignés).")
    wb = (r["weather"] or {}).get(r["season_b"])
    if wb and r["season_b"][1] == "summer":
        out.append(f"- {_name(r['season_b'])} : bilan hydrique " + f"{wb['balance']:+d} mm".replace("-", "−")
                   + " — une sécheresse jaunit les pelouses autour des arbres et stresse les arbres eux-mêmes.")
    return out
