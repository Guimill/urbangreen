"""Urban heat islands: Landsat 8/9 land surface temperature averaged over 200 m cells.

Why surface temperature and not air temperature
-----------------------------------------------
No free source gives the air temperature inside a city at 200 m: weather reanalyses
(ERA5, used by Open-Meteo) have one value per 9-25 km, i.e. one value for a whole
town, and Météo-France stations are a few points. The thermal band of Landsat 8 and 9
(TIRS, 100 m native, delivered at 30 m) measures the temperature of the ground, roofs
and canopy at ~10:50 UTC. It is the standard source for mapping surface heat islands
(SUHI); it is hotter than the air in summer (a car park at 45 °C under 28 °C air) but
its contrasts between places follow the same causes: mineral surfaces, lack of trees,
lack of water.

So the air temperature is used only for context: for every image date, the day's
maximum air temperature at the city centre (Open-Meteo / ERA5) and the 1991-2020
normal of that calendar day tell whether the image was taken on a hot or an ordinary day.

Grid
----
Rasters are read at 20 m on the city grid (EPSG:3857, aligned with the 10 m NDVI grid)
and averaged into 200 m cells: 10 x 10 read pixels, 20 x 20 NDVI pixels. Water pixels
(Landsat QA) are left out of the cell means: a river is cool for reasons that have
nothing to do with the urban fabric.
"""
import json
import math
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import rasterio
import requests
from django.conf import settings
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from ..categories import OTHER, PARKS, SEASONS, TREES, season_of
from . import raster, stac

CELL_M = 200
READ_FACTOR = 2                                         # read pixels = 2 x 10 m
CELL_FACTOR = CELL_M // (raster.GROUND_RES_M * READ_FACTOR)  # 10 read pixels per cell side
NDVI_FACTOR = CELL_M // raster.GROUND_RES_M                  # 20 NDVI pixels per cell side

MIN_CELL_INSIDE = 0.25    # a cell is drawn when at least a quarter of it is in the commune
MIN_CELL_SEEN = 0.5       # ... and has a value when half of its land pixels are cloud-free
MIN_VALID_FRACTION = 0.6  # a date is kept when 60 % of the commune's land is cloud-free
PLATFORMS = ["landsat-8", "landsat-9"]  # Landsat 7 has striping gaps since 2003

# QA_PIXEL bits of Landsat Collection 2
QA_FILL, QA_DILATED, QA_CIRRUS, QA_CLOUD, QA_SHADOW, QA_SNOW, QA_WATER = 0, 1, 2, 3, 4, 5, 7
QA_REJECT = sum(1 << b for b in (QA_FILL, QA_DILATED, QA_CIRRUS, QA_CLOUD, QA_SHADOW, QA_SNOW))

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
NORMAL_PERIOD = (date(1991, 1, 1), date(2020, 12, 31))
NORMAL_SMOOTH_DAYS = 7    # daily normal = mean of +-7 calendar days over the 30 years


class HeatError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Grids and masks
# --------------------------------------------------------------------------- #
def sub_grid(grid, factor):
    return raster.Grid(grid.x0, grid.y1, grid.res * factor, math.ceil(grid.width / factor),
                       math.ceil(grid.height / factor), grid.scale)


def block_sum(values, factor, shape):
    """Sum of factor x factor blocks; the array is zero-padded to shape * factor."""
    h, w = shape
    padded = np.zeros((h * factor, w * factor), dtype="float64")
    padded[:values.shape[0], :values.shape[1]] = values
    return padded.reshape(h, factor, w, factor).sum(axis=(1, 3))


@dataclass
class HeatMasks:
    """Read grid (20 m) and cell grid (200 m) of a city, with what is fixed per cell."""
    read_grid: raster.Grid
    cell_grid: raster.Grid
    inside: np.ndarray        # commune mask on the read grid
    cells: np.ndarray         # cells drawn on the map
    tree_share: np.ndarray    # share of the cell under OSM trees
    green_share: np.ndarray   # share of the cell under OSM trees, parks and other green spaces

    @classmethod
    def from_masks(cls, masks):
        grid = masks.grid
        read_grid, cell_grid = sub_grid(grid, READ_FACTOR), sub_grid(grid, READ_FACTOR * CELL_FACTOR)
        inside = block_sum(masks.inside, READ_FACTOR, read_grid.shape) >= READ_FACTOR ** 2 / 2
        n = block_sum(masks.inside, NDVI_FACTOR, cell_grid.shape)
        with np.errstate(invalid="ignore", divide="ignore"):
            share = lambda zone: np.where(n > 0, block_sum(masks.inside & zone, NDVI_FACTOR, cell_grid.shape) / n, np.nan)
            trees = share(masks.categories == TREES)
            green = share(np.isin(masks.categories, (TREES, PARKS, OTHER)))
        return cls(read_grid, cell_grid, inside, n >= MIN_CELL_INSIDE * NDVI_FACTOR ** 2, trees, green)

    def geometry(self):
        """What the browser needs to draw the cells (EPSG:3857 metres)."""
        g = self.cell_grid
        return {"x0": g.x0, "y1": g.y1, "res": g.res, "nx": g.width, "ny": g.height, "size_m": CELL_M}


def cell_mean(values, factor, shape, inside=None):
    """Mean of the finite values of each cell (NaN when none)."""
    ok = np.isfinite(values) if inside is None else np.isfinite(values) & inside
    s = block_sum(np.where(ok, values, 0), factor, shape)
    n = block_sum(ok, factor, shape)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan)


# --------------------------------------------------------------------------- #
# Landsat search and reading
# --------------------------------------------------------------------------- #
_token = {"value": None, "expires": 0.0}


def sign(href):
    """Planetary Computer serves Landsat from Azure blobs that need a (free, anonymous) SAS token."""
    url = settings.HEAT_SIGN_URL
    if not url:
        return href
    if _token["value"] is None or _token["expires"] < time.time() + 300:
        try:
            response = requests.get(url, headers=stac.HEADERS, timeout=30)
            response.raise_for_status()
            _token["value"] = response.json()["token"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise HeatError(f"Jeton d'accès Landsat impossible à obtenir : {exc}") from exc
        _token["expires"] = time.time() + 45 * 60
    return f"{href}?{_token['value']}"


def search(bbox, start, end, max_cloud=50):
    return stac.search(bbox, start, end, max_cloud=max_cloud, url=settings.HEAT_STAC_URL,
                       collection=settings.HEAT_COLLECTION, query={"platform": {"in": PLATFORMS}})


def _read(href, grid, resampling):
    with rasterio.open(sign(href)) as src, WarpedVRT(
        src, crs="EPSG:3857", transform=grid.transform, width=grid.width, height=grid.height,
        resampling=resampling, src_nodata=0, nodata=0,
    ) as vrt:
        return vrt.read(1)


def read_item(item, grid):
    """(surface temperature in °C with NaN where unusable, water mask) on the read grid."""
    lwir = item["assets"]["lwir11"]
    info = (lwir.get("raster:bands") or [{}])[0]
    with rasterio.Env(**raster.GDAL_ENV):
        dn = _read(lwir["href"], grid, Resampling.bilinear)
        qa = _read(item["assets"]["qa_pixel"]["href"], grid, Resampling.nearest)
    return decode(dn, qa, info.get("scale", 0.00341802), info.get("offset", 149.0))


def decode(dn, qa, scale, offset):
    celsius = dn.astype("float32") * scale + offset - 273.15
    bad = (dn == 0) | (qa == 0) | ((qa & QA_REJECT) != 0)
    celsius[bad] = np.nan
    return celsius, (qa & (1 << QA_WATER)) != 0


def read_mosaic(items, hm, reader=read_item):
    """Each pixel from the least cloudy product that sees it (several Landsat rows may cover a city)."""
    lst = np.full(hm.read_grid.shape, np.nan, "float32")
    water = np.zeros(hm.read_grid.shape, bool)
    used = []
    for item in items:
        need = hm.inside & ~np.isfinite(lst)
        if not need.any():
            break
        t, w = reader(item, hm.read_grid)
        take = need & np.isfinite(t)
        if take.any():
            lst[take], water[take] = t[take], w[take]
            used.append(item["id"])
    return lst, water, used


def aggregate(lst, water, hm):
    """(share of the commune's land seen, cell means in °C)."""
    land = hm.inside & ~water
    seen = land & np.isfinite(lst)
    shape = hm.cell_grid.shape
    n_land = block_sum(land, CELL_FACTOR, shape)
    n_seen = block_sum(seen, CELL_FACTOR, shape)
    total = block_sum(np.where(seen, lst, 0), CELL_FACTOR, shape)
    keep = hm.cells & (n_seen >= MIN_CELL_SEEN * n_land) & (n_seen >= MIN_CELL_SEEN * MIN_CELL_INSIDE * CELL_FACTOR ** 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        cells = np.where(keep, total / np.maximum(n_seen, 1), np.nan).astype("float32")
    valid = float(seen.sum() / max(int(land.sum()), 1))
    return valid, cells


# --------------------------------------------------------------------------- #
# Air temperature (context): Open-Meteo archive = ERA5 reanalysis at the city centre
# --------------------------------------------------------------------------- #
def _daily_tmax(lat, lon, start, end):
    try:
        response = requests.get(OPEN_METEO_URL, headers=stac.HEADERS, timeout=120, params={
            "latitude": round(lat, 3), "longitude": round(lon, 3), "start_date": start.isoformat(),
            "end_date": end.isoformat(), "timezone": "Europe/Paris", "daily": "temperature_2m_max"})
        response.raise_for_status()
        daily = response.json()["daily"]
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise HeatError(f"Températures Open-Meteo indisponibles : {exc}") from exc
    return {date.fromisoformat(d): t for d, t in zip(daily["time"], daily["temperature_2m_max"]) if t is not None}


def fetch_air(lat, lon, days):
    """{date: maximum air temperature of the day (°C)}."""
    if not days:
        return {}
    return _daily_tmax(lat, lon, min(days), max(days))


def _calendar_index(day):
    """Day of a 365-day year (29 February counts as 28 February)."""
    return date(2001, day.month, min(day.day, 28) if day.month == 2 else day.day).timetuple().tm_yday - 1


def daily_normals(tmax_by_day):
    """365 smoothed daily normals from 30 years of daily maxima."""
    sums, counts = np.zeros(365), np.zeros(365)
    for day, t in tmax_by_day.items():
        i = _calendar_index(day)
        sums[i] += t
        counts[i] += 1
    raw = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    k = NORMAL_SMOOTH_DAYS
    window = np.concatenate([raw[-k:], raw, raw[:k]])  # the year is circular
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return [float(np.nanmean(window[i:i + 2 * k + 1])) for i in range(365)]


def normal_of(normals, day):
    return normals[_calendar_index(day)]


def season_normals(normals):
    """Mean daily maximum of each meteorological season over 1991-2020."""
    by_season = defaultdict(list)
    for i, value in enumerate(normals):
        by_season[season_of(date(2001, 1, 1) + timedelta(days=i))[0]].append(value)
    return {key: round(float(np.mean(by_season[key])), 1) for key, _ in SEASONS}


def get_normals(city, centre, fetch=_daily_tmax):
    """Daily normals of a city, downloaded once (30 years of ERA5) and kept next to its rasters."""
    path = city.raster_dir / "heat" / "normals.json"
    try:
        return json.loads(path.read_text())["daily"]
    except (OSError, ValueError, KeyError):
        pass
    lon, lat = centre
    normals = daily_normals(fetch(lat, lon, *NORMAL_PERIOD))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"period": [d.isoformat() for d in NORMAL_PERIOD], "source": "Open-Meteo ERA5",
                                "daily": [round(v, 2) for v in normals]}))
    return normals


# --------------------------------------------------------------------------- #
# Layers: what the map shows
# --------------------------------------------------------------------------- #
MODES = ("city", "normal", "lst")


def load(scene):
    with np.load(scene.heat_path) as d:
        return d["lst"].astype("float32")


def _median(stack):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # cells never observed
        return np.nanmedian(np.stack(stack), axis=0)


def anomaly(cells):
    """Cell minus the mean of the commune's cells on the same image."""
    return cells - np.nanmean(cells)


def layer(mode, scenes, others=()):
    """Cell values of a layer.

    city:   median over the dates of (cell − commune mean of that date): the heat island,
            free of the weather of each day.
    normal: median surface temperature of the dates minus the median of the same season
            in the other years (others): was this place hotter than usual?
    lst:    median surface temperature (°C).
    """
    stack = [load(s) for s in scenes]
    if mode == "city":
        return _median([anomaly(c) for c in stack])
    if mode == "lst":
        return _median(stack)
    if not others:
        return None
    return _median(stack) - _median([load(s) for s in others])
