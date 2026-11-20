from django.contrib import admin

from .models import City, HeatScene, Observation, Scene


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department", "population", "status", "osm_fetched_at")
    search_fields = ("name", "code")
    exclude = ("geometry",)
    readonly_fields = ("osm_summary", "status_message", "status_updated_at")


class ObservationInline(admin.TabularInline):
    model = Observation
    extra = 0
    can_delete = False
    readonly_fields = ("category", "pixels", "coverage", "mean_ndvi", "green_share", "dense_share")


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    list_display = ("city", "date", "status", "cloud_cover", "valid_fraction")
    list_filter = ("status", "city")
    date_hierarchy = "date"
    inlines = [ObservationInline]


@admin.register(HeatScene)
class HeatSceneAdmin(admin.ModelAdmin):
    list_display = ("city", "date", "status", "cloud_cover", "valid_fraction", "mean_lst", "air_tmax", "normal_tmax")
    list_filter = ("status", "city")
    date_hierarchy = "date"
