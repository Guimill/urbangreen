"""End-to-end: pipeline with fake OSM / STAC / Sentinel-2, then every page and image."""
import shutil
import tempfile
from datetime import date
from unittest import mock

import numpy as np
from django.test import TestCase, override_settings
from django.urls import reverse
from shapely.geometry import box, mapping

from monitor.categories import PARKS, TREES
from monitor.models import City, Scene
from monitor.services import analysis, pipeline

COMMUNE = mapping(box(-0.56, 47.46, -0.54, 47.47))
FOOTPRINT = mapping(box(-1, 47, 0, 48))


def fake_fetch(south, west, north, east, log=print):
    return {TREES: [box(-0.56, 47.46, -0.555, 47.47)], PARKS: [box(-0.555, 47.46, -0.55, 47.47)]}


def fake_item(day, cloud=5):
    return {"id": f"S2_{day}", "geometry": FOOTPRINT, "assets": {},
            "properties": {"datetime": f"{day}T11:00:00Z", "eo:cloud_cover": cloud}}


DAYS = ["2025-01-10", "2025-02-12", "2025-04-03", "2025-07-08", "2025-08-02", "2025-08-20"]


def fake_search(bbox, start, end, max_cloud=40):
    return [fake_item(d) for d in DAYS if start <= date.fromisoformat(d) <= end]


def fake_reader(item, grid):
    """Greener in summer; 2025-08-20 is fully cloudy."""
    day = item["id"][3:]
    nir_level = 0.45 if day[5:7] in ("06", "07", "08") else 0.25
    red = np.full(grid.shape, 0.05, "float32")
    nir = np.full(grid.shape, nir_level, "float32")
    scl = np.full(grid.shape, 9 if day == "2025-08-20" else 4, "uint8")
    return red, nir, scl


class PipelineTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(RASTER_ROOT=self.tmp)
        self.override.enable()
        self.city = City.objects.create(code="49007", name="Angers", department="49", population=150000,
                                        geometry=COMMUNE)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pipeline(self, start=date(2024, 12, 1), end=date(2025, 8, 31)):
        pipeline.update_city(self.city, start, end, log=lambda *a: None,
                             reader=fake_reader, search=fake_search, fetch=fake_fetch)
        self.city.refresh_from_db()

    def test_pipeline_and_aggregates(self):
        self.run_pipeline()
        self.assertEqual(self.city.status, City.STATUS_IDLE)
        self.assertIn("5 date(s) ajoutée(s), 1 trop nuageuse(s)", self.city.status_message)
        self.assertEqual(self.city.scenes.filter(status=Scene.OK).count(), 5)
        self.assertGreater(self.city.osm_summary["trees"]["area_ha"], 0)

        summer = self.city.scenes.get(date="2025-07-08").observations.get(category="trees")
        self.assertAlmostEqual(summer.mean_ndvi, 0.8, places=3)
        self.assertEqual(summer.green_share, 1.0)

        data = analysis.city_data(self.city)
        self.assertEqual([s["label"] for s in data["seasons"]], ["Hiver 2025", "Printemps 2025", "Été 2025"])
        amp = data["amplitude"][0]
        self.assertEqual(amp["year"], 2025)
        self.assertAlmostEqual(amp["values"]["all"]["mean_ndvi"], 0.8 - 0.6667, places=2)
        self.assertEqual(analysis.overlays(self.city)["diffs"], [2025])

        # a second run downloads nothing new
        with mock.patch.object(pipeline, "process_date") as process:
            self.run_pipeline()
            process.assert_not_called()

    def test_failure_of_one_date_does_not_stop_the_run(self):
        def flaky(item, grid):
            if item["id"].endswith("2025-04-03"):
                raise OSError("HTTP 503")
            return fake_reader(item, grid)

        with self.assertLogs("monitor.services.pipeline", "ERROR"):
            pipeline.update_city(self.city, date(2025, 1, 1), date(2025, 8, 31), log=lambda *a: None,
                                 reader=flaky, search=fake_search, fetch=fake_fetch)
        self.assertEqual(Scene.objects.get(date="2025-04-03").status, Scene.ERROR)
        self.assertEqual(self.city.scenes.filter(status=Scene.OK).count(), 4)

    def test_pages_and_images(self):
        self.run_pipeline()
        code = self.city.code
        for url in [reverse("monitor:index"), reverse("monitor:index") + "?q=",
                    reverse("monitor:city", args=[code]), reverse("monitor:status", args=[code])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

        page = self.client.get(reverse("monitor:city", args=[code])).content.decode()
        self.assertIn("Surface végétalisée en été 2025", page)
        self.assertIn('id="city-config"', page)

        data = self.client.get(reverse("monitor:data", args=[code])).json()
        self.assertEqual(len(data["series"]), 5)
        self.assertEqual(data["overlays"]["diffs"], [2025])

        for url in [reverse("monitor:scene_png", args=[code, "2025-07-08"]),
                    reverse("monitor:season_png", args=[code, "2025-summer"]),
                    reverse("monitor:diff_png", args=[code, "2025-winter", "2025-summer"]),
                    reverse("monitor:diff_png", args=[code, "2025-spring", "2025-summer"]),
                    reverse("monitor:categories_png", args=[code])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response["Content-Type"], "image/png")
            self.assertTrue(response.content.startswith(b"\x89PNG"))
            self.assertEqual(self.client.get(url).content, response.content)  # served from cache

        for url in [reverse("monitor:scene_png", args=[code, "2025-08-20"]),  # cloudy date
                    reverse("monitor:season_png", args=[code, "2025-autumn"]),
                    reverse("monitor:season_png", args=[code, "nonsense"]),
                    reverse("monitor:diff_png", args=[code, "2024-winter", "2024-summer"]),
                    reverse("monitor:diff_png", args=[code, "2025-winter", "nonsense"])]:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_run_view_starts_background_job_once(self):
        url = reverse("monitor:run", args=[self.city.code])
        form = {"start": "2025-01-01", "end": "2025-08-31", "max_cloud": 40, "per_month": 3}
        with mock.patch.object(pipeline, "start_in_background") as start:
            self.client.post(url, form)
            start.assert_called_once()
        self.city.set_status(City.STATUS_RUNNING, "Image 1/3")
        with mock.patch.object(pipeline, "start_in_background") as start:
            self.client.post(url, form)
            start.assert_not_called()
        with mock.patch.object(pipeline, "start_in_background") as start:
            self.client.post(url, {**form, "start": "2025-09-01"})  # start after end
            start.assert_not_called()

    def test_add_city(self):
        fetched = {"code": "44184", "name": "Saint-Nazaire", "department": "44", "population": 72000,
                   "geometry": COMMUNE}
        with mock.patch("monitor.services.communes.fetch", return_value=fetched):
            response = self.client.post(reverse("monitor:add_city"), {"code": "44184"})
        self.assertRedirects(response, reverse("monitor:city", args=["44184"]))
        self.assertEqual(City.objects.get(code="44184").name, "Saint-Nazaire")
        self.assertEqual(self.client.get(reverse("monitor:city", args=["44184"])).status_code, 200)


class WritePermissionTests(TestCase):
    """With WRITE_REQUIRES_LOGIN (production), visitors can read but not start downloads."""

    def setUp(self):
        self.city = City.objects.create(code="49007", name="Angers", department="49", population=150000,
                                        geometry=COMMUNE)

    @override_settings(WRITE_REQUIRES_LOGIN=True)
    def test_visitors_are_sent_to_login(self):
        with mock.patch.object(pipeline, "start_in_background") as start:
            response = self.client.post(reverse("monitor:run", args=["49007"]),
                                        {"start": "2025-01-01", "end": "2025-08-31", "max_cloud": 40, "per_month": 3})
            start.assert_not_called()
        self.assertIn("/admin/login/", response["Location"])
        response = self.client.post(reverse("monitor:add_city"), {"code": "44184"})
        self.assertIn("/admin/login/", response["Location"])
        self.assertFalse(City.objects.filter(code="44184").exists())
        page = self.client.get(reverse("monitor:city", args=["49007"])).content.decode()
        self.assertNotIn('class="run-form"', page)
        self.assertIn("réservé aux administrateurs", page)

    @override_settings(WRITE_REQUIRES_LOGIN=True)
    def test_staff_can_start_an_analysis(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user("admin", is_staff=True))
        with mock.patch.object(pipeline, "start_in_background") as start:
            self.client.post(reverse("monitor:run", args=["49007"]),
                             {"start": "2025-01-01", "end": "2025-08-31", "max_cloud": 40, "per_month": 3})
            start.assert_called_once()


class VisitorLimitTests(TestCase):
    """Visitors can only make the server compute a limited number of new images."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(RASTER_ROOT=self.tmp, VISITOR_BUILDS_PER_HOUR=2)
        self.override.enable()
        self.city = City.objects.create(code="49007", name="Angers", department="49", population=150000,
                                        geometry=COMMUNE)
        pipeline.update_city(self.city, date(2024, 12, 1), date(2025, 8, 31), log=lambda *a: None,
                             reader=fake_reader, search=fake_search, fetch=fake_fetch)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_images_are_limited_cached_ones_are_free(self):
        code = self.city.code
        urls = [reverse("monitor:diff_png", args=[code, "2025-winter", "2025-summer"]),
                reverse("monitor:diff_png", args=[code, "2025-spring", "2025-summer"]),
                reverse("monitor:diff_png", args=[code, "2025-winter", "2025-spring"])]
        self.assertEqual(self.client.get(urls[0]).status_code, 200)
        self.assertEqual(self.client.get(urls[1]).status_code, 200)
        self.assertEqual(self.client.get(urls[2]).status_code, 429)   # third new image
        self.assertEqual(self.client.get(urls[0]).status_code, 200)   # cached: still served
        # another visitor (NPM puts the real address last) has their own allowance
        other = self.client.get(urls[2], HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8")
        self.assertEqual(other.status_code, 200)

    def test_staff_is_not_limited(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user("admin", is_staff=True))
        for a, b in [("2025-winter", "2025-summer"), ("2025-spring", "2025-summer"), ("2025-winter", "2025-spring")]:
            self.assertEqual(self.client.get(reverse("monitor:diff_png", args=[self.city.code, a, b])).status_code, 200)

    def test_season_composite_is_reused(self):
        first, _ = analysis.season_composite(self.city, 2025, "summer")
        with mock.patch.object(analysis, "_season_median") as median:
            again, _ = analysis.season_composite(self.city, 2025, "summer")
            median.assert_not_called()
        np.testing.assert_allclose(first, again, atol=0.006)

    @override_settings(WRITE_REQUIRES_LOGIN=True)
    def test_visitors_cannot_search(self):
        with mock.patch("monitor.services.communes.search") as search:
            page = self.client.get(reverse("monitor:index") + "?q=nantes").content.decode()
            search.assert_not_called()
        self.assertNotIn('role="search"', page)
