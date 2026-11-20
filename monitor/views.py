import json
import threading

import numpy as np
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .context import can_edit
from .categories import CATEGORIES, CATEGORY_COLORS, CATEGORY_DESCRIPTIONS, DENSE_NDVI, GREEN_NDVI, SEASON_LABELS
from .forms import HeatRunForm, RunForm
from .models import City, Scene
from .services import analysis, communes, pipeline, raster


def index(request):
    # the commune search calls geo.api.gouv.fr: only offered to those who can add a commune
    query = request.GET.get("q", "").strip() if can_edit(request) else ""
    results, error = [], None
    if query:
        try:
            known = set(City.objects.values_list("code", flat=True))
            results = [dict(r, known=r["code"] in known) for r in communes.search(query)]
        except communes.CommuneError as exc:
            error = str(exc)
    cities = []
    for city in City.objects.all():
        scenes = city.scenes.filter(status=Scene.OK)
        cities.append({"city": city, "count": scenes.count(), "last": scenes.order_by("-date").first()})
    return render(request, "monitor/index.html",
                  {"query": query, "results": results, "error": error, "cities": cities})


@require_POST
def add_city(request):
    if not can_edit(request):
        return redirect_to_login(reverse("monitor:index"), settings.LOGIN_URL)
    code = request.POST.get("code", "").strip()
    city = City.objects.filter(code=code).first()
    if city is None:
        try:
            data = communes.fetch(code)
        except communes.CommuneError as exc:
            messages.error(request, str(exc))
            return redirect("monitor:index")
        city = City.objects.create(**data)
        messages.success(request, f"{city.name} ajoutée. Lancez l'analyse pour télécharger les images.")
    return redirect("monitor:city", code=city.code)


def city_detail(request, code):
    city = get_object_or_404(City, code=code)
    data = analysis.city_data(city)
    scenes = city.scenes.order_by("-date")
    return render(request, "monitor/city.html", {
        "city": city,
        "form": RunForm(),
        "heat_form": HeatRunForm(prefix="heat"),
        "heat_scenes": city.heat_scenes.order_by("-date")[:600],
        "heat_count": city.heat_scenes.filter(status="ok").count(),
        "summary": analysis.summary_numbers(data),
        "scene_counts": {s: scenes.filter(status=s).count() for s in (Scene.OK, Scene.SKIPPED, Scene.ERROR)},
        "scenes": scenes[:300],
        "config": {
            "running": city.is_running,
            "urls": {
                "data": reverse("monitor:data", args=[code]),
                "status": reverse("monitor:status", args=[code]),
                "date": reverse("monitor:scene_png", args=[code, "2000-01-01"]).replace("2000-01-01", "{key}"),
                "season": reverse("monitor:season_png", args=[code, "KEY"]).replace("KEY", "{key}"),
                "diff": reverse("monitor:diff_png", args=[code, "AAAA", "BBBB"]).replace("AAAA", "{a}").replace("BBBB", "{b}"),
                "categories": reverse("monitor:categories_png", args=[code]),
                "heat": reverse("monitor:heat_data", args=[code]),
                "heat_layer": reverse("monitor:heat_layer", args=[code, "MODE", "KEY"]).replace("MODE", "{mode}").replace("KEY", "{key}"),
            },
            "colors": CATEGORY_COLORS,
            "ramps": {"ndvi": raster.ramp_css(raster.NDVI_RAMP), "diff": raster.ramp_css(raster.DIFF_RAMP)},
            "ranges": {"ndvi": raster.NDVI_RANGE, "diff": raster.DIFF_RANGE},
            "thresholds": {"green": GREEN_NDVI, "dense": DENSE_NDVI},
        },
        "categories": [{"key": k, "label": label, "description": CATEGORY_DESCRIPTIONS.get(k, ""),
                        "area_ha": city.osm_summary.get(k, {}).get("area_ha"),
                        "features": city.osm_summary.get(k, {}).get("features")}
                       for k, _, label in CATEGORIES],
    })


@require_POST
def run(request, code):
    city = get_object_or_404(City, code=code)
    if not can_edit(request):
        return redirect_to_login(reverse("monitor:city", args=[code]), settings.LOGIN_URL)
    if city.is_running:
        messages.info(request, "Une analyse est déjà en cours.")
        return redirect("monitor:city", code=code)
    form = RunForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("monitor:city", code=code)
    d = form.cleaned_data
    pipeline.start_in_background(city, d["start"], d["end"], max_cloud=d["max_cloud"],
                                 per_month=d["per_month"], refresh_osm=d["refresh_osm"])
    return redirect("monitor:city", code=code)


@require_POST
def run_heat(request, code):
    city = get_object_or_404(City, code=code)
    if not can_edit(request):
        return redirect_to_login(reverse("monitor:city", args=[code]), settings.LOGIN_URL)
    if city.is_running:
        messages.info(request, "Une analyse est déjà en cours.")
        return redirect("monitor:city", code=code)
    form = HeatRunForm(request.POST, prefix="heat")
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("monitor:city", code=code)
    d = form.cleaned_data
    pipeline.start_in_background(city, d["start"], d["end"], job=pipeline.update_heat, max_cloud=d["max_cloud"])
    return redirect(reverse("monitor:city", args=[code]) + "#chaleur")


@require_GET
def status(request, code):
    city = get_object_or_404(City, code=code)
    return JsonResponse({
        "running": city.is_running,
        "status": city.status if city.status != City.STATUS_RUNNING or city.is_running else "interrupted",
        "message": city.status_message,
        "scenes": city.scenes.filter(status=Scene.OK).count(),
    })


@require_GET
def data(request, code):
    city = get_object_or_404(City, code=code)
    payload = analysis.city_data(city)
    payload["overlays"] = analysis.overlays(city)
    try:
        payload["bounds"] = raster.Grid.for_geometry(city.geometry).bounds_latlng()
    except ValueError:
        payload["bounds"] = None
    payload["geometry"] = city.geometry
    return JsonResponse(payload)


# --------------------------------------------------------------------------- #
# Map overlays (PNG on the city grid, cached on disk)
# --------------------------------------------------------------------------- #
def _png(content, cache=None, content_type="image/png"):
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600" if cache else "no-cache"
    return response


def _masks(city):
    try:
        return pipeline.Masks.load(city)
    except (OSError, ValueError, KeyError):
        raise Http404("Masques absents : lancez l'analyse")


# Images are computed on first request, then served from the disk cache. Building one
# costs up to a few seconds of CPU, so visitors may only trigger a limited number of
# builds, and builds run one at a time: a visitor clicking through every season pair
# cannot take more than one CPU core, and cached images are always served at once.
_BUILD_LOCK = threading.Semaphore(1)


def client_ip(request):
    # Nginx Proxy Manager appends the real client address last; earlier entries can be forged
    forwarded = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    return forwarded[-1] if forwarded else request.META.get("REMOTE_ADDR", "")


def _allow_build(request):
    if request.user.is_staff:
        return True
    key = f"builds:{client_ip(request)}"
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, 3600)
        count = 1
    return count <= settings.VISITOR_BUILDS_PER_HOUR


def _too_busy(message):
    response = HttpResponse(message, status=429, content_type="text/plain; charset=utf-8")
    response["Retry-After"] = "600"
    return response


def _cached(request, city, name, build, content_type="image/png"):
    path = city.raster_dir / "cache" / name
    if not path.exists():
        if not _allow_build(request):
            return _too_busy("Trop de nouvelles images demandées : réessayez dans une heure.")
        if not _BUILD_LOCK.acquire(timeout=30):
            return _too_busy("Serveur occupé : réessayez dans un instant.")
        try:
            if not path.exists():  # built by another request while this one waited
                content = build()
                if content is None:
                    raise Http404("Pas de données")
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(content)
                tmp.replace(path)
        finally:
            _BUILD_LOCK.release()
    return _png(path.read_bytes(), cache=True, content_type=content_type)


@require_GET
def scene_png(request, code, day):
    city = get_object_or_404(City, code=code)
    scene = get_object_or_404(Scene, city=city, date=day, status=Scene.OK)
    masks = _masks(city)
    try:
        with np.load(scene.ndvi_path) as d:
            values = raster.dequantize(d["ndvi"])
    except OSError:
        raise Http404("Raster absent")
    return _cached(request, city, f"scene-{scene.date}-{scene.pk}.png", lambda: raster.ndvi_png(values, masks.inside))


def _season(key):
    try:
        year, season = key.split("-")
        year = int(year)
    except ValueError:
        raise Http404
    if season not in SEASON_LABELS:
        raise Http404
    return year, season


@require_GET
def season_png(request, code, key):
    city = get_object_or_404(City, code=code)
    year, season = _season(key)
    scenes = analysis.season_scenes(city, year, season)
    if not scenes:
        raise Http404
    masks = _masks(city)

    def build():
        values, _ = analysis.season_composite(city, year, season)
        return None if values is None else raster.ndvi_png(values, masks.inside)

    return _cached(request, city, f"season-{key}-{analysis.composite_version(scenes)}.png", build)


@require_GET
def diff_png(request, code, a, b):
    """NDVI of season b minus NDVI of season a (keys like "2026-winter")."""
    city = get_object_or_404(City, code=code)
    (ya, sa), (yb, sb) = _season(a), _season(b)
    scenes_a, scenes_b = analysis.season_scenes(city, ya, sa), analysis.season_scenes(city, yb, sb)
    if not (scenes_a and scenes_b):
        raise Http404
    masks = _masks(city)

    def build():
        va, _ = analysis.season_composite(city, ya, sa)
        vb, _ = analysis.season_composite(city, yb, sb)
        return None if va is None or vb is None else raster.diff_png(vb - va, masks.inside)

    version = f"{analysis.composite_version(scenes_a)}-{analysis.composite_version(scenes_b)}"
    return _cached(request, city, f"diff-{a}-{b}-{version}.png", build)


@require_GET
def categories_png(request, code):
    city = get_object_or_404(City, code=code)
    masks = _masks(city)
    stamp = int(city.osm_fetched_at.timestamp()) if city.osm_fetched_at else 0
    return _cached(request, city, f"categories-{stamp}.png", lambda: raster.categories_png(masks.categories, masks.inside))



# --------------------------------------------------------------------------- #
# Heat islands
# --------------------------------------------------------------------------- #
@require_GET
def heat_data(request, code):
    city = get_object_or_404(City, code=code)
    overview = analysis.heat_overview(city)
    if overview is None:
        raise Http404("Pas de données de chaleur")
    return JsonResponse(overview)


@require_GET
def heat_layer(request, code, mode, key):
    """Cell values of one heat layer (JSON, cached on disk like the images)."""
    city = get_object_or_404(City, code=code)
    selection = analysis.heat_layer_scenes(city, mode, key)
    if selection is None:
        raise Http404
    chosen, others = selection
    season, year = chosen[0].season
    version = f"{analysis.heat_version(city)}-{analysis.composite_version(analysis.season_scenes(city, year, season))}"

    def build():
        return json.dumps(analysis.heat_layer(city, mode, key, chosen, others), separators=(",", ":")).encode()

    return _cached(request, city, f"heat-{mode}-{key}-{version}.json", build, content_type="application/json")
