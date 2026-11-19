import numpy as np
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .categories import CATEGORIES, CATEGORY_COLORS, CATEGORY_DESCRIPTIONS, DENSE_NDVI, GREEN_NDVI, SEASON_LABELS
from .forms import RunForm
from .models import City, Scene
from .services import analysis, communes, pipeline, raster


def index(request):
    query = request.GET.get("q", "").strip()
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
                "diff": reverse("monitor:diff_png", args=[code, 2000]).replace("2000", "{key}"),
                "categories": reverse("monitor:categories_png", args=[code]),
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
def _png(content, cache=None):
    response = HttpResponse(content, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600" if cache else "no-cache"
    return response


def _masks(city):
    try:
        return pipeline.Masks.load(city)
    except (OSError, ValueError, KeyError):
        raise Http404("Masques absents : lancez l'analyse")


def _cached(city, name, build):
    path = city.raster_dir / "cache" / name
    if not path.exists():
        content = build()
        if content is None:
            raise Http404("Pas de données")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
    return _png(path.read_bytes(), cache=True)


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
    return _cached(city, f"scene-{scene.date}-{scene.pk}.png", lambda: raster.ndvi_png(values, masks.inside))


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

    return _cached(city, f"season-{key}-{analysis.composite_version(scenes)}.png", build)


@require_GET
def diff_png(request, code, year):
    city = get_object_or_404(City, code=code)
    summer = analysis.season_scenes(city, year, "summer")
    winter = analysis.season_scenes(city, year, "winter")
    if not (summer and winter):
        raise Http404
    masks = _masks(city)

    def build():
        s, _ = analysis.season_composite(city, year, "summer")
        w, _ = analysis.season_composite(city, year, "winter")
        return None if s is None or w is None else raster.diff_png(s - w, masks.inside)

    version = analysis.composite_version(summer + winter)
    return _cached(city, f"diff-{year}-{version}.png", build)


@require_GET
def categories_png(request, code):
    city = get_object_or_404(City, code=code)
    masks = _masks(city)
    stamp = int(city.osm_fetched_at.timestamp()) if city.osm_fetched_at else 0
    return _cached(city, f"categories-{stamp}.png", lambda: raster.categories_png(masks.categories, masks.inside))

