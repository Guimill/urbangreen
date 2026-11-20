from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from .categories import CATEGORIES, season_label, season_of


class City(models.Model):
    """A French commune being monitored."""

    STATUS_IDLE, STATUS_RUNNING, STATUS_ERROR = "idle", "running", "error"

    code = models.CharField("code INSEE", max_length=5, unique=True)
    name = models.CharField("nom", max_length=120)
    department = models.CharField("département", max_length=3, blank=True)
    population = models.PositiveIntegerField(null=True, blank=True)
    geometry = models.JSONField("contour (GeoJSON, WGS84)")
    created_at = models.DateTimeField(auto_now_add=True)

    osm_fetched_at = models.DateTimeField(null=True, blank=True)
    # {category: {"features": n, "area_ha": x}}
    osm_summary = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=10, default=STATUS_IDLE)
    status_message = models.TextField(blank=True)
    status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "commune"

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def raster_dir(self) -> Path:
        return Path(settings.RASTER_ROOT) / self.code

    @property
    def is_running(self):
        """Running and still alive: the pipeline reports progress at least every few minutes,
        so a "running" status that has not moved for 15 minutes was interrupted (server restart)."""
        return (
            self.status == self.STATUS_RUNNING
            and self.status_updated_at is not None
            and timezone.now() - self.status_updated_at < timedelta(minutes=15)
        )

    def set_status(self, status, message=""):
        self.status, self.status_message, self.status_updated_at = status, message, timezone.now()
        self.save(update_fields=["status", "status_message", "status_updated_at"])


class Scene(models.Model):
    """One acquisition date over a city (several Sentinel-2 tiles may be mosaicked)."""

    OK, SKIPPED, ERROR = "ok", "skipped", "error"

    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name="scenes")
    date = models.DateField()
    items = models.JSONField("produits Sentinel-2", default=list)
    cloud_cover = models.FloatField("nuages annoncés (%)", null=True)
    valid_fraction = models.FloatField("part de la commune exploitable", null=True)
    status = models.CharField(max_length=10, default=OK)
    message = models.TextField(blank=True)
    processed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["city", "date"], name="scene_city_date_unique")]

    def __str__(self):
        return f"{self.city.name} {self.date}"

    @property
    def season(self):
        return season_of(self.date)

    @property
    def season_label(self):
        return season_label(*self.season)

    @property
    def ndvi_path(self) -> Path:
        return self.city.raster_dir / "ndvi" / f"{self.date.isoformat()}.npz"


class Observation(models.Model):
    """Statistics of one green category on one scene."""

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="observations")
    category = models.CharField(max_length=10, choices=[(k, label) for k, _, label in CATEGORIES])
    pixels = models.PositiveIntegerField("pixels observés")
    coverage = models.FloatField("part de la catégorie observée")
    mean_ndvi = models.FloatField("NDVI moyen", null=True)
    green_share = models.FloatField("part végétalisée (NDVI ≥ 0,3)", null=True)
    dense_share = models.FloatField("part de végétation dense (NDVI ≥ 0,6)", null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scene", "category"], name="observation_scene_category_unique")]


class HeatScene(models.Model):
    """One Landsat 8/9 date over a city: surface temperature averaged on 200 m cells."""

    OK, SKIPPED, ERROR = Scene.OK, Scene.SKIPPED, Scene.ERROR

    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name="heat_scenes")
    date = models.DateField()
    items = models.JSONField("produits Landsat", default=list)
    cloud_cover = models.FloatField("nuages annoncés (%)", null=True)
    valid_fraction = models.FloatField("part de la commune exploitable", null=True)
    status = models.CharField(max_length=10, default=OK)
    message = models.TextField(blank=True)
    mean_lst = models.FloatField("température de surface moyenne (°C)", null=True)
    air_tmax = models.FloatField("température max. de l'air ce jour (°C)", null=True)
    normal_tmax = models.FloatField("normale 1991-2020 de ce jour (°C)", null=True)
    processed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]
        verbose_name = "date Landsat"
        constraints = [models.UniqueConstraint(fields=["city", "date"], name="heatscene_city_date_unique")]

    def __str__(self):
        return f"{self.city.name} {self.date} (chaleur)"

    @property
    def season(self):
        return season_of(self.date)

    @property
    def season_label(self):
        return season_label(*self.season)

    @property
    def heat_path(self) -> Path:
        return self.city.raster_dir / "heat" / f"{self.date.isoformat()}.npz"
