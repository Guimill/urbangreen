"""Aggregations for the pages: time series, seasonal summaries and seasonal composites."""
from collections import defaultdict
from datetime import date
from statistics import median

import numpy as np

from ..categories import CATEGORIES, SEASON_LABELS, SEASON_ORDER, season_label
from ..models import HeatScene, Scene
from . import heat, raster

METRICS = ("mean_ndvi", "green_share", "dense_share")
MAX_COMPOSITE_SCENES = 15


def _round(value, digits=3):
    return None if value is None else round(value, digits)


def ok_scenes(city):
    return list(city.scenes.filter(status=Scene.OK).prefetch_related("observations").order_by("date"))


def season_key(season, year):
    return f"{year}-{season}"


def city_data(city):
    """Everything the city page charts need, as plain JSON-able data."""
    scenes = ok_scenes(city)
    series = []
    by_season = defaultdict(list)  # (year, season) -> [{category: observation}]
    for scene in scenes:
        obs = {o.category: o for o in scene.observations.all()}
        season, year = scene.season
        by_season[(year, season)].append(obs)
        series.append({
            "date": scene.date.isoformat(),
            "season": season_label(season, year),
            "valid": _round(scene.valid_fraction, 2),
            "values": {k: {m: _round(getattr(o, m)) for m in METRICS} for k, o in obs.items()},
        })

    seasons = []
    for (year, season) in sorted(by_season, key=lambda ys: (ys[0], SEASON_ORDER[ys[1]])):
        group = by_season[(year, season)]
        values = {}
        for key, _, _ in CATEGORIES:
            values[key] = {}
            for metric in METRICS:
                v = [getattr(obs[key], metric) for obs in group if key in obs and getattr(obs[key], metric) is not None]
                # median: one hazy date should not move a whole season
                values[key][metric] = _round(median(v)) if v else None
        seasons.append({"key": season_key(season, year), "label": season_label(season, year),
                        "year": year, "season": season, "scenes": len(group), "values": values})

    return {
        "categories": [{"key": k, "label": label, "area_ha": city.osm_summary.get(k, {}).get("area_ha")}
                       for k, _, label in CATEGORIES],
        "series": series,
        "seasons": seasons,
        "amplitude": amplitude(seasons),
    }


def amplitude(seasons):
    """Summer minus winter of the same year, for every year that has both."""
    index = {(s["year"], s["season"]): s for s in seasons}
    out = []
    for year in sorted({s["year"] for s in seasons}):
        summer, winter = index.get((year, "summer")), index.get((year, "winter"))
        if not (summer and winter):
            continue
        values = {}
        for key, _, _ in CATEGORIES:
            values[key] = {}
            for metric in METRICS:
                a, b = summer["values"][key][metric], winter["values"][key][metric]
                values[key][metric] = _round(a - b) if a is not None and b is not None else None
        out.append({"year": year, "values": values})
    return out


def summary_numbers(data):
    """Headline numbers of the latest year with a summer and a winter."""
    if not data["amplitude"]:
        return None
    latest = data["amplitude"][-1]
    year = latest["year"]
    index = {(s["year"], s["season"]): s for s in data["seasons"]}
    summer, winter = index[(year, "summer")], index[(year, "winter")]
    return {
        "year": year,
        "summer_green": summer["values"]["all"]["green_share"],
        "winter_green": winter["values"]["all"]["green_share"],
        "amplitude_ndvi": latest["values"]["all"]["mean_ndvi"],
        "scenes": summer["scenes"] + winter["scenes"],
    }


def season_scenes(city, year, season):
    return [s for s in city.scenes.filter(status=Scene.OK).order_by("date") if s.season == (season, year)]


def season_composite(city, year, season):
    """Median NDVI of a season, or None when no date is available.

    Stored in the cache folder (int8, ~100 KB) so that comparing two seasons only reads two files.
    """
    scenes = season_scenes(city, year, season)
    if not scenes:
        return None, scenes
    folder = city.raster_dir / "cache"
    path = folder / f"composite-{year}-{season}-{composite_version(scenes)}.npz"
    try:
        with np.load(path) as d:
            return raster.dequantize(d["ndvi"]), scenes
    except (OSError, KeyError, ValueError):
        pass
    values = _season_median(scenes)
    if values is not None:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"composite-{year}-{season}-*.npz"):  # older versions of this season
            old.unlink(missing_ok=True)
        tmp = folder / (path.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, ndvi=raster.quantize(values))
        tmp.replace(path)
    return values, scenes


def _season_median(scenes):
    stack = []
    for scene in scenes[-MAX_COMPOSITE_SCENES:]:
        try:
            with np.load(scene.ndvi_path) as data:
                stack.append(data["ndvi"])
        except OSError:
            continue
    return raster.composite(stack) if stack else None


def composite_version(scenes):
    """Changes whenever the set of dates changes: used to name cached images."""
    return f"{len(scenes)}-{max((s.pk for s in scenes), default=0)}"


def overlays(city):
    """Map layers available for a city."""
    scenes = list(city.scenes.filter(status=Scene.OK).order_by("date"))
    seasons = sorted({s.season for s in scenes}, key=lambda sy: (sy[1], SEASON_ORDER[sy[0]]))
    years = sorted({y for season, y in seasons if season == "summer" and ("winter", y) in seasons})
    return {
        "dates": [s.date.isoformat() for s in scenes],
        "seasons": [{"key": season_key(s, y), "label": season_label(s, y),
                     "count": sum(1 for sc in scenes if sc.season == (s, y))} for s, y in seasons],
        "diffs": years,
    }



# --------------------------------------------------------------------------- #
# Heat islands (Landsat surface temperature on 200 m cells)
# --------------------------------------------------------------------------- #
def _cells_json(values, digits=2):
    """2-D array -> nested lists, None where NaN (JSON has no NaN)."""
    values = values.astype("float64")
    out = np.round(values, digits).astype(object)
    out[~np.isfinite(values)] = None
    return out.tolist()


def heat_scenes(city):
    return list(city.heat_scenes.filter(status=HeatScene.OK).order_by("date"))


def heat_version(city):
    scenes = city.heat_scenes.filter(status=HeatScene.OK)
    return f"{scenes.count()}-{max(scenes.values_list('pk', flat=True), default=0)}"


def _mean(values, digits=1):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None


def heat_masks(city):
    from .pipeline import Masks
    return heat.HeatMasks.from_masks(Masks.load(city))


def heat_overview(city):
    """Dates, seasons and cell geometry of the heat section (None when there is no Landsat date)."""
    scenes = heat_scenes(city)
    if not scenes:
        return None
    try:
        hm = heat_masks(city)
    except (OSError, ValueError, KeyError):
        return None
    by_season = defaultdict(list)
    for s in scenes:
        by_season[s.season[::-1]].append(s)
    seasons = []
    for (year, season) in sorted(by_season, key=lambda ys: (ys[0], SEASON_ORDER[ys[1]])):
        group = by_season[(year, season)]
        seasons.append({"key": season_key(season, year), "label": season_label(season, year), "count": len(group),
                        "mean_lst": round(median(s.mean_lst for s in group), 1),
                        "air_tmax": _mean(s.air_tmax for s in group), "normal_tmax": _mean(s.normal_tmax for s in group)})
    return {
        "grid": hm.geometry(),
        "cells": hm.cells.tolist(),
        "tree_share": _cells_json(hm.tree_share, 2),
        "green_share": _cells_json(hm.green_share, 2),
        "dates": [{"date": s.date.isoformat(), "season": s.season_label, "valid": round(s.valid_fraction, 2),
                   "mean_lst": round(s.mean_lst, 1), "air_tmax": s.air_tmax, "normal_tmax": s.normal_tmax}
                  for s in scenes],
        "seasons": seasons,
    }


def parse_heat_key(key):
    """"2025-07-10" -> ("date", date); "2025-summer" -> ("season", (2025, "summer")); else None."""
    try:
        if len(key) == 10:
            return "date", date.fromisoformat(key)
        year, season = key.split("-")
        if season in SEASON_LABELS:
            return "season", (int(year), season)
    except ValueError:
        pass
    return None


def heat_layer_scenes(city, mode, key):
    """(scenes, other years' scenes of the same season) of a layer, or None when the key is unknown."""
    parsed = parse_heat_key(key)
    if parsed is None or mode not in heat.MODES:
        return None
    kind, value = parsed
    scenes = heat_scenes(city)
    if kind == "date":
        if mode == "normal":
            return None
        chosen = [s for s in scenes if s.date == value]
        return (chosen, []) if chosen else None
    year, season = value
    chosen = [s for s in scenes if s.season == (season, year)]
    others = [s for s in scenes if s.season[0] == season and s.season[1] != year]
    return (chosen, others) if chosen else None


def heat_layer(city, mode, key, chosen, others):
    """Cell values of one map layer, with the NDVI of the same season for the vegetation chart."""
    from .pipeline import Masks
    values = heat.layer(mode, chosen, others)
    masks = Masks.load(city)
    hm = heat.HeatMasks.from_masks(masks)
    season, year = chosen[0].season
    ndvi = None
    composite, _ = season_composite(city, year, season)
    if composite is not None:
        land = np.where(composite > 0, composite, np.nan)  # water (NDVI < 0) left out like in the heat cells
        ndvi = heat.cell_mean(land, heat.NDVI_FACTOR, hm.cell_grid.shape, masks.inside)
        ndvi[~hm.cells] = np.nan
    return {
        "mode": mode, "key": key, "dates": [s.date.isoformat() for s in chosen],
        "other_years": sorted({s.season[1] for s in others}) if mode == "normal" else [],
        "values": None if values is None else _cells_json(values, 2),
        "ndvi": None if ndvi is None else _cells_json(ndvi, 3),
        "air_tmax": _mean(s.air_tmax for s in chosen), "normal_tmax": _mean(s.normal_tmax for s in chosen),
        "message": "" if values is not None else "Pas d'autre année avec cette saison pour calculer une normale.",
    }
