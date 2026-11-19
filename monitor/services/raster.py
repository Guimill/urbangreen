"""Raster side: a fixed grid per city, Sentinel-2 reading, NDVI, statistics and overlay images.

Every raster of a city lives on the same grid, in Web Mercator (EPSG:3857) with
~10 m ground pixels, so that:
- scenes from different tiles / UTM zones line up pixel for pixel,
- the PNG overlays drop straight onto a Leaflet map with no reprojection.
Areas are slightly stretched in Mercator, but uniformly across a city, so the
shares and means computed here are not affected.
"""
import io
import math
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from ..categories import AGRI, CATEGORIES, CATEGORY_COLORS as _CAT_COLORS, DENSE_NDVI, GREEN_NDVI, OTHER, PARKS, TREES
from . import stac

GROUND_RES_M = 10
NODATA_Q = -128  # quantised NDVI: int8 = round(NDVI * 100), -128 = no data

# Scene classification (SCL) of L2A products
SCL_VALID = (4, 5, 6, 7)  # vegetation, bare soil, water, unclassified
SCL_WATER = 6

# GDAL settings for reading cloud-optimised GeoTIFFs over HTTPS efficiently
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}

# Buffers (metres) turning OSM points and lines into canopy footprints
TREE_RADIUS_M = 4
TREE_ROW_HALF_WIDTH_M = 4

_to_3857 = Transformer.from_crs(4326, 3857, always_xy=True).transform
_to_4326 = Transformer.from_crs(3857, 4326, always_xy=True).transform


def to_3857(geometry):
    return shp_transform(_to_3857, geometry)


@dataclass(frozen=True)
class Grid:
    x0: float      # west edge (EPSG:3857)
    y1: float      # north edge
    res: float     # pixel size in Mercator metres
    width: int
    height: int
    scale: float   # Mercator metres per ground metre (1 / cos(latitude))

    @classmethod
    def for_geometry(cls, geojson):
        geom = to_3857(shape(geojson))
        minx, miny, maxx, maxy = geom.bounds
        lat = _to_4326((minx + maxx) / 2, (miny + maxy) / 2)[1]
        scale = 1 / math.cos(math.radians(lat))
        res = GROUND_RES_M * scale
        x0, y1 = math.floor(minx / res) * res - res, math.ceil(maxy / res) * res + res
        width = int(math.ceil((maxx - x0) / res)) + 1
        height = int(math.ceil((y1 - miny) / res)) + 1
        return cls(x0, y1, res, width, height, scale)

    @property
    def transform(self):
        return from_origin(self.x0, self.y1, self.res, self.res)

    @property
    def shape(self):
        return self.height, self.width

    @property
    def pixel_area_m2(self):
        return (self.res / self.scale) ** 2

    def bounds_3857(self):
        return self.x0, self.y1 - self.height * self.res, self.x0 + self.width * self.res, self.y1

    def bounds_latlng(self):
        """[[south, west], [north, east]] for Leaflet."""
        minx, miny, maxx, maxy = self.bounds_3857()
        west, south = _to_4326(minx, miny)
        east, north = _to_4326(maxx, maxy)
        return [[south, west], [north, east]]

    def bbox_wgs84(self):
        (south, west), (north, east) = self.bounds_latlng()
        return west, south, east, north


# --------------------------------------------------------------------------- #
# Masks
# --------------------------------------------------------------------------- #
def commune_mask(grid, geojson):
    return rasterize([(to_3857(shape(geojson)), 1)], out_shape=grid.shape, transform=grid.transform,
                     fill=0, dtype="uint8").astype(bool)


def category_raster(grid, features):
    """features: {category code: [WGS84 geometries]} -> uint8 raster of category codes.

    Burned from lowest to highest priority, so trees overwrite parks, which overwrite
    other green spaces, which overwrite farmland. Points and lines are buffered into
    canopy footprints and burned with all_touched so that small trees are not lost.
    """
    out = np.zeros(grid.shape, dtype="uint8")
    m_tree, m_row = TREE_RADIUS_M * grid.scale, TREE_ROW_HALF_WIDTH_M * grid.scale
    for code in (AGRI, OTHER, PARKS, TREES):
        areas, thin = [], []
        for geom in features.get(code, []):
            g = to_3857(geom)
            if g.geom_type == "Point":
                thin.append(g.buffer(m_tree, quad_segs=4))
            elif g.geom_type in ("LineString", "MultiLineString"):
                thin.append(g.buffer(m_row, quad_segs=2))
            else:
                areas.append(g)
        for shapes, touched in ((areas, False), (thin, True)):
            if shapes:
                rasterize([(g, code) for g in shapes], out=out, transform=grid.transform, all_touched=touched)
    return out


# --------------------------------------------------------------------------- #
# Reading Sentinel-2
# --------------------------------------------------------------------------- #
def _read_band(href, grid, resampling):
    with rasterio.open(href) as src, WarpedVRT(
        src, crs="EPSG:3857", transform=grid.transform, width=grid.width, height=grid.height,
        resampling=resampling, src_nodata=0, nodata=0,
    ) as vrt:
        return vrt.read(1)


def read_item(item, grid):
    """(red, nir reflectance as float32, scl as uint8) of one product on the city grid."""
    jobs = [("red", Resampling.bilinear), ("nir", Resampling.bilinear), ("scl", Resampling.nearest)]
    with rasterio.Env(**GDAL_ENV), ThreadPoolExecutor(len(jobs)) as pool:
        futures = [pool.submit(_read_band, stac.band(item, name)[0], grid, rs) for name, rs in jobs]
        red_dn, nir_dn, scl = (f.result() for f in futures)
    _, red_scale, red_offset = stac.band(item, "red")
    _, nir_scale, nir_offset = stac.band(item, "nir")
    red = red_dn.astype("float32") * red_scale + red_offset
    nir = nir_dn.astype("float32") * nir_scale + nir_offset
    return red, nir, scl.astype("uint8")


def read_mosaic(items, grid, inside, reader=read_item):
    """Combine the products of one date: each pixel comes from the first (least cloudy)
    product that has valid data there. Stops early once the commune is fully covered."""
    red = np.zeros(grid.shape, "float32")
    nir = np.zeros(grid.shape, "float32")
    scl = np.zeros(grid.shape, "uint8")
    used = []
    for item in items:
        need = inside & ~np.isin(scl, SCL_VALID)
        if not need.any():
            break
        r, n, s = reader(item, grid)
        take = need & (s != 0)
        if take.any():
            red[take], nir[take], scl[take] = r[take], n[take], s[take]
            used.append(item["id"])
    return red, nir, scl, used


def ndvi(red, nir, scl):
    """NDVI as float32, NaN where the pixel is cloudy, shadowed, snowy or without data."""
    with np.errstate(divide="ignore", invalid="ignore"):
        value = (nir - red) / (nir + red)
    value = np.clip(value, -1, 1).astype("float32")
    value[~np.isin(scl, SCL_VALID) | ~np.isfinite(value)] = np.nan
    return value


def quantize(values):
    q = np.full(values.shape, NODATA_Q, dtype="int8")
    ok = np.isfinite(values)
    q[ok] = np.round(values[ok] * 100).astype("int8")
    return q


def dequantize(q):
    out = q.astype("float32") / 100
    out[q == NODATA_Q] = np.nan
    return out


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def scene_stats(values, scl, inside, categories):
    """Per-category statistics of one scene.

    Water pixels are left out (their negative NDVI would drag the means down).
    Returns (valid fraction of the commune, {category key: stats dict}).
    """
    observed = inside & np.isfinite(values)
    valid_fraction = float(observed.sum() / max(inside.sum(), 1))
    land = observed & (scl != SCL_WATER)
    stats = {}
    for key, code, _ in CATEGORIES:
        zone = inside if code is None else inside & (categories == code)
        seen = land if code is None else land & (categories == code)
        n = int(seen.sum())
        v = values[seen]
        stats[key] = {
            "pixels": n,
            "coverage": float(n / max(int(zone.sum()), 1)),
            "mean_ndvi": float(v.mean()) if n else None,
            "green_share": float((v >= GREEN_NDVI).mean()) if n else None,
            "dense_share": float((v >= DENSE_NDVI).mean()) if n else None,
        }
    return valid_fraction, stats


def composite(stack):
    """Per-pixel median of several quantised NDVI rasters (NaN where never observed)."""
    data = np.stack([dequantize(q) for q in stack])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # pixels never observed (all NaN)
        return np.nanmedian(data, axis=0)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def _hex(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _ramp(stops, n=256):
    """Linear RGB ramp through (position 0..1, hex) stops -> (n, 3) uint8."""
    pos = np.array([p for p, _ in stops])
    rgb = np.array([_hex(c) for _, c in stops], dtype=float)
    x = np.linspace(0, 1, n)
    return np.stack([np.interp(x, pos, rgb[:, i]) for i in range(3)], axis=1).astype("uint8")


# NDVI: one green hue, light (bare) -> dark (dense vegetation)
NDVI_RAMP = _ramp([(0, "#f3f1e8"), (0.35, "#c7dfa5"), (0.65, "#5fa35a"), (1, "#0d4a22")])
NDVI_RANGE = (0.0, 0.9)
# Seasonal difference: orange arm (greener in winter) - neutral grey - green arm (greener in summer)
DIFF_RAMP = _ramp([(0, "#b8451a"), (0.25, "#eb9c74"), (0.5, "#e6e4dd"), (0.75, "#79b86f"), (1, "#0d5a26")])
DIFF_RANGE = (-0.5, 0.5)
CATEGORY_COLORS = {code: _CAT_COLORS[key][0] for key, code in (("trees", TREES), ("parks", PARKS), ("other", OTHER), ("agri", AGRI))}


def ramp_css(ramp, steps=9):
    """CSS linear-gradient stops of a ramp, for the map legend."""
    idx = np.linspace(0, len(ramp) - 1, steps).astype(int)
    return ", ".join("#%02x%02x%02x" % tuple(ramp[i]) for i in idx)


def _to_png(rgba):
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def colorize(values, inside, ramp, value_range, alpha=225):
    lo, hi = value_range
    rgba = np.zeros(values.shape + (4,), "uint8")
    ok = inside & np.isfinite(values)
    idx = np.clip((values[ok] - lo) / (hi - lo) * (len(ramp) - 1), 0, len(ramp) - 1).astype(int)
    rgba[ok, :3] = ramp[idx]
    rgba[ok, 3] = alpha
    return _to_png(rgba)


def ndvi_png(values, inside):
    return colorize(values, inside, NDVI_RAMP, NDVI_RANGE)


def diff_png(values, inside):
    return colorize(values, inside, DIFF_RAMP, DIFF_RANGE)


def categories_png(categories, inside):
    rgba = np.zeros(categories.shape + (4,), "uint8")
    for code, color in CATEGORY_COLORS.items():
        zone = inside & (categories == code)
        rgba[zone, :3] = _hex(color)
        rgba[zone, 3] = 210
    return _to_png(rgba)
