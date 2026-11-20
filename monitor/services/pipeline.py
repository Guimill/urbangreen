"""Monitoring pipeline: commune -> OSM green categories -> Sentinel-2 dates -> NDVI statistics."""
import logging
import threading
from datetime import date

import numpy as np
from django.db import close_old_connections, transaction
from django.utils import timezone
from shapely.geometry import shape

from ..categories import CATEGORIES
from ..models import City, HeatScene, Observation, Scene
from . import heat, osm, raster, stac

log_ = logging.getLogger(__name__)

MIN_VALID_FRACTION = 0.6  # a date is kept when at least 60 % of the commune is cloud-free


class Masks:
    """Grid, commune mask and category raster of a city, stored next to its NDVI rasters."""

    def __init__(self, grid, inside, categories):
        self.grid, self.inside, self.categories = grid, inside, categories

    @staticmethod
    def path(city):
        return city.raster_dir / "masks.npz"

    @classmethod
    def load(cls, city):
        grid = raster.Grid.for_geometry(city.geometry)
        with np.load(cls.path(city)) as data:
            inside, categories = data["inside"], data["categories"]
        if inside.shape != grid.shape:
            raise ValueError("masks do not match the commune grid")
        return cls(grid, inside, categories)

    def save(self, city):
        path = self.path(city)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, inside=self.inside, categories=self.categories)


def build_masks(city, fetch=osm.fetch, log=print):
    """Download the green spaces from OpenStreetMap and burn them on the city grid."""
    grid = raster.Grid.for_geometry(city.geometry)
    inside = raster.commune_mask(grid, city.geometry)
    west, south, east, north = grid.bbox_wgs84()
    log("OpenStreetMap : téléchargement des espaces verts…")
    features = fetch(south, west, north, east, log=log)
    categories = raster.category_raster(grid, features)
    masks = Masks(grid, inside, categories)
    masks.save(city)

    ha = grid.pixel_area_m2 / 10_000
    summary = {}
    for key, code, _ in CATEGORIES:
        zone = inside if code is None else inside & (categories == code)
        summary[key] = {"area_ha": round(float(zone.sum()) * ha, 1),
                        "features": len(features.get(code, [])) if code else None}
    city.osm_summary, city.osm_fetched_at = summary, timezone.now()
    city.save(update_fields=["osm_summary", "osm_fetched_at"])
    return masks


def get_masks(city, refresh=False, fetch=osm.fetch, log=print):
    if not refresh and city.osm_fetched_at:
        try:
            return Masks.load(city)
        except (OSError, ValueError, KeyError):
            pass
    return build_masks(city, fetch=fetch, log=log)


def process_date(city, day, items, masks, reader=raster.read_item):
    """Read, mosaic and measure one date. Returns the saved Scene."""
    red, nir, scl, used = raster.read_mosaic(items, masks.grid, masks.inside, reader=reader)
    values = raster.ndvi(red, nir, scl)
    valid, stats = raster.scene_stats(values, scl, masks.inside, masks.categories)

    with transaction.atomic():
        scene, _ = Scene.objects.update_or_create(
            city=city, date=day,
            defaults={"items": used or [it["id"] for it in items], "cloud_cover": stac.date_cloud(items),
                      "valid_fraction": valid, "status": Scene.OK, "message": ""},
        )
        scene.observations.all().delete()
        if valid < MIN_VALID_FRACTION:
            scene.status = Scene.SKIPPED
            scene.message = f"Seulement {valid:.0%} de la commune visible (nuages, ombres, neige)"
            scene.save(update_fields=["status", "message"])
            return scene
        Observation.objects.bulk_create(Observation(scene=scene, category=key, **s) for key, s in stats.items())

    path = scene.ndvi_path
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, ndvi=raster.quantize(values))
    return scene


def update_city(city, start: date, end: date, max_cloud=40, per_month=3, refresh_osm=False,
                log=print, reader=raster.read_item, search=stac.search, fetch=osm.fetch):
    """Fetch and analyse every new date of a period. Dates already processed are skipped."""

    def status(message):
        log(message)
        city.set_status(City.STATUS_RUNNING, message)

    status("Préparation…")
    try:
        masks = get_masks(city, refresh=refresh_osm, fetch=fetch, log=status)
        status("Recherche des images Sentinel-2…")
        groups = stac.group_by_date(search(masks.grid.bbox_wgs84(), start, end, max_cloud=max_cloud))
        # A date whose tiles only cover part of the commune (edge of an orbit) is useless:
        # drop it before downloading anything.
        commune = shape(city.geometry)
        groups = {d: items for d, items in groups.items()
                  if stac.footprint_coverage(items, commune) >= MIN_VALID_FRACTION}
        done = set(city.scenes.values_list("date", flat=True))
        todo = [d for d in stac.select_dates(groups, per_month) if d not in done]
        kept = skipped = failed = 0
        for i, day in enumerate(todo, 1):
            status(f"Image {i}/{len(todo)} : {day:%d/%m/%Y}")
            try:
                scene = process_date(city, day, groups[day], masks, reader=reader)
            except Exception as exc:  # one bad product must not stop the whole run
                log_.exception("scene %s %s", city.code, day)
                Scene.objects.update_or_create(city=city, date=day, defaults={
                    "items": [it["id"] for it in groups[day]], "status": Scene.ERROR, "message": str(exc)[:500]})
                failed += 1
                continue
            if scene.status == Scene.OK:
                kept += 1
            else:
                skipped += 1
        summary = (f"Terminé : {kept} date(s) ajoutée(s), {skipped} trop nuageuse(s)"
                   + (f", {failed} en erreur" if failed else "")
                   + (" — aucune nouvelle image" if not todo else ""))
        log(summary)
        city.set_status(City.STATUS_IDLE, summary)
    except Exception as exc:
        log_.exception("update %s", city.code)
        city.set_status(City.STATUS_ERROR, str(exc)[:1000])
        raise


def process_heat_date(city, day, items, hm, reader=heat.read_item):
    """Read one Landsat date, average it on the 200 m cells and save it. Returns the HeatScene."""
    lst, water, used = heat.read_mosaic(items, hm, reader=reader)
    valid, cells = heat.aggregate(lst, water, hm)
    ok = valid >= heat.MIN_VALID_FRACTION and np.isfinite(cells).any()
    scene, _ = HeatScene.objects.update_or_create(city=city, date=day, defaults={
        "items": used or [it["id"] for it in items], "cloud_cover": stac.date_cloud(items),
        "valid_fraction": valid, "status": HeatScene.OK if ok else HeatScene.SKIPPED,
        "message": "" if ok else f"Seulement {valid:.0%} de la commune visible (nuages, ombres)",
        "mean_lst": float(np.nanmean(cells)) if ok else None})
    if ok:
        path = scene.heat_path
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, lst=cells.astype("float16"))
    return scene


def add_air_temperatures(city, centre, fetch_air=heat.fetch_air, fetch_normals=heat._daily_tmax, log=print):
    """Air temperature of every image date and its 1991-2020 normal (context only, never blocking)."""
    scenes = list(city.heat_scenes.filter(status=HeatScene.OK))
    lon, lat = centre
    try:
        normals = heat.get_normals(city, centre, fetch=fetch_normals)
        missing = [s for s in scenes if s.air_tmax is None]
        air = fetch_air(lat, lon, [s.date for s in missing])
    except heat.HeatError as exc:
        log(f"Températures de l'air non disponibles : {exc}")
        return
    for scene in scenes:
        scene.normal_tmax = round(heat.normal_of(normals, scene.date), 1)
        if scene.date in air:
            scene.air_tmax = air[scene.date]
    HeatScene.objects.bulk_update(scenes, ["normal_tmax", "air_tmax"])


def update_heat(city, start: date, end: date, max_cloud=50, log=print, reader=heat.read_item,
                search=heat.search, fetch=osm.fetch, fetch_air=heat.fetch_air, fetch_normals=heat._daily_tmax):
    """Fetch and average every new Landsat 8/9 date of a period (dates already done are skipped)."""

    def status(message):
        log(message)
        city.set_status(City.STATUS_RUNNING, message)

    status("Chaleur : préparation…")
    try:
        masks = get_masks(city, fetch=fetch, log=status)
        hm = heat.HeatMasks.from_masks(masks)
        status("Recherche des images Landsat 8 et 9…")
        groups = stac.group_by_date(search(masks.grid.bbox_wgs84(), start, end, max_cloud=max_cloud))
        commune = shape(city.geometry)
        groups = {d: items for d, items in groups.items()
                  if stac.footprint_coverage(items, commune) >= heat.MIN_VALID_FRACTION}
        done = set(city.heat_scenes.values_list("date", flat=True))
        todo = sorted(d for d in groups if d not in done)
        kept = skipped = failed = 0
        for i, day in enumerate(todo, 1):
            status(f"Chaleur : image {i}/{len(todo)} : {day:%d/%m/%Y}")
            try:
                scene = process_heat_date(city, day, groups[day], hm, reader=reader)
            except Exception as exc:  # one bad product must not stop the whole run
                log_.exception("heat %s %s", city.code, day)
                HeatScene.objects.update_or_create(city=city, date=day, defaults={
                    "items": [it["id"] for it in groups[day]], "status": HeatScene.ERROR, "message": str(exc)[:500]})
                failed += 1
                continue
            kept, skipped = kept + (scene.status == HeatScene.OK), skipped + (scene.status != HeatScene.OK)
        status("Chaleur : températures de l'air (Open-Meteo)…")
        add_air_temperatures(city, commune.centroid.coords[0], fetch_air=fetch_air, fetch_normals=fetch_normals, log=log)
        summary = (f"Chaleur terminée : {kept} date(s) Landsat ajoutée(s), {skipped} trop nuageuse(s)"
                   + (f", {failed} en erreur" if failed else "")
                   + (" — aucune nouvelle image" if not todo else ""))
        log(summary)
        city.set_status(City.STATUS_IDLE, summary)
    except Exception as exc:
        log_.exception("heat %s", city.code)
        city.set_status(City.STATUS_ERROR, str(exc)[:1000])
        raise


def start_in_background(city, start, end, job=update_city, **options):
    """Run update_city (or update_heat) in a thread (for the "Analyser" buttons)."""
    city.set_status(City.STATUS_RUNNING, "En file d'attente…")

    def run():
        close_old_connections()
        try:
            job(City.objects.get(pk=city.pk), start, end, log=log_.info, **options)
        except Exception:
            pass  # already recorded on the city
        finally:
            close_old_connections()

    thread = threading.Thread(target=run, name=f"update-{city.code}", daemon=True)
    thread.start()
    return thread
