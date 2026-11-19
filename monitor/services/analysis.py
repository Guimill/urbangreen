"""Aggregations for the pages: time series, seasonal summaries and seasonal composites."""
from collections import defaultdict
from statistics import median

import numpy as np

from ..categories import CATEGORIES, SEASON_ORDER, season_label
from ..models import Scene
from . import raster

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

