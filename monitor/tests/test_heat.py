"""Heat islands: Landsat decoding, 200 m cells, normals, layers, pipeline and pages."""
import json
import shutil
import tempfile
from datetime import date, timedelta
from unittest import mock

import numpy as np
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from monitor.categories import TREES
from monitor.models import City, HeatScene
from monitor.services import analysis, heat, pipeline, raster

from .test_pipeline import COMMUNE, FOOTPRINT, fake_fetch


class DecodeTests(SimpleTestCase):
    def test_qa_bits(self):
        dn = np.full((1, 6), 90000 // 2, "uint16")          # some valid digital number
        dn[0, 5] = 0                                          # no data
        qa = np.array([[21824, 1 << 3, 1 << 4, 21824 | (1 << heat.QA_WATER), 1 << 5, 21824]], "uint16")
        celsius, water = heat.decode(dn, qa, 0.00341802, 149.0)
        expected = 45000 * 0.00341802 + 149 - 273.15
        self.assertAlmostEqual(float(celsius[0, 0]), expected, places=3)
        self.assertTrue(np.isnan(celsius[0, 1]))   # cloud
        self.assertTrue(np.isnan(celsius[0, 2]))   # cloud shadow
        self.assertTrue(np.isfinite(celsius[0, 3]) and water[0, 3])  # water: measured, flagged
        self.assertTrue(np.isnan(celsius[0, 4]))   # snow
        self.assertTrue(np.isnan(celsius[0, 5]))   # fill
        self.assertEqual(int(water.sum()), 1)

    def test_block_sum_pads(self):
        out = heat.block_sum(np.ones((3, 5)), 2, (2, 3))
        np.testing.assert_array_equal(out, [[4, 4, 2], [2, 2, 1]])


def synthetic_masks(h=40, w=60, tree_cols=20):
    """10 m grid: the commune fills it; the western third is a wood."""
    grid = raster.Grid(0.0, 0.0, 10.0, w, h, 1.0)
    inside = np.ones((h, w), bool)
    categories = np.zeros((h, w), "uint8")
    categories[:, :tree_cols] = TREES
    return pipeline.Masks(grid, inside, categories)


class CellTests(SimpleTestCase):
    def setUp(self):
        self.hm = heat.HeatMasks.from_masks(synthetic_masks())

    def test_grids_and_shares(self):
        self.assertEqual(self.hm.read_grid.shape, (20, 30))
        self.assertEqual(self.hm.cell_grid.shape, (2, 3))
        self.assertTrue(self.hm.cells.all())
        np.testing.assert_allclose(self.hm.tree_share, [[1, 0, 0], [1, 0, 0]])
        self.assertEqual(self.hm.geometry()["size_m"], 200)

    def test_aggregate_ignores_clouds_and_water(self):
        lst = np.full((20, 30), 30.0, "float32")
        lst[:, 10:] = 40.0
        water = np.zeros((20, 30), bool)
        water[:10, 10:20] = True
        lst[:10, 10:20] = 15.0                 # cold river in the middle cell: left out of its mean
        lst[10:, 20:] = np.nan                 # cloud over the south-east cell
        valid, cells = heat.aggregate(lst, water, self.hm)
        self.assertAlmostEqual(cells[0, 0], 30.0)
        self.assertTrue(np.isnan(cells[0, 1]))  # all water: no land value
        self.assertAlmostEqual(cells[1, 1], 40.0)
        self.assertTrue(np.isnan(cells[1, 2]))  # under cloud
        self.assertAlmostEqual(valid, 400 / 500)

    def test_partly_cloudy_cell_needs_half_its_land(self):
        lst = np.full((20, 30), 35.0, "float32")
        lst[:6, :10] = np.nan                  # 60 % of the north-west cell hidden
        _, cells = heat.aggregate(lst, np.zeros((20, 30), bool), self.hm)
        self.assertTrue(np.isnan(cells[0, 0]))
        lst[:4, :10] = 35.0                    # only 20 % hidden
        _, cells = heat.aggregate(lst, np.zeros((20, 30), bool), self.hm)
        self.assertAlmostEqual(cells[0, 0], 35.0)


class NormalTests(SimpleTestCase):
    def test_daily_and_season_normals(self):
        # a sine year: coldest mid-January (5 °C), hottest mid-July (25 °C), 30 years
        days = {}
        day = date(1991, 1, 1)
        while day <= date(2020, 12, 31):
            doy = day.timetuple().tm_yday
            days[day] = 15 - 10 * np.cos(2 * np.pi * (doy - 15) / 365)
            day += timedelta(days=1)
        normals = heat.daily_normals(days)
        self.assertEqual(len(normals), 365)
        self.assertAlmostEqual(heat.normal_of(normals, date(2025, 7, 15)), 25, delta=0.2)
        self.assertAlmostEqual(heat.normal_of(normals, date(2024, 2, 29)), heat.normal_of(normals, date(2025, 2, 28)))
        seasons = heat.season_normals(normals)
        self.assertGreater(seasons["summer"], seasons["spring"])
        self.assertGreater(seasons["spring"], seasons["winter"])
        self.assertAlmostEqual(seasons["spring"], seasons["autumn"], delta=1.0)


class LayerTests(SimpleTestCase):
    """The heat island map must not depend on the weather of each day."""

    def scenes(self, *grids):
        return [mock.Mock(heat_path=f"{i}") for i, _ in enumerate(grids)]

    def test_modes(self):
        island = np.array([[0.0, 4.0], [-2.0, np.nan]])
        hot_day, mild_day, last_year = island + 40, island + 25, island + 30 - np.array([[0, 2.0], [0, 0]])
        stacks = {"0": hot_day, "1": mild_day, "2": last_year}
        with mock.patch.object(heat, "load", side_effect=lambda s: stacks[s.heat_path]):
            this, other = [mock.Mock(heat_path="0"), mock.Mock(heat_path="1")], [mock.Mock(heat_path="2")]
            city = heat.layer("city", this, other)
            # same island on both days whatever the temperature, around the commune mean
            np.testing.assert_allclose(city, island - np.nanmean(island))
            np.testing.assert_allclose(heat.layer("lst", this), island + 32.5)
            # this season vs last year: +2.5 everywhere, +4.5 where the island got stronger
            np.testing.assert_allclose(heat.layer("normal", this, other), [[2.5, 4.5], [2.5, np.nan]])
            self.assertIsNone(heat.layer("normal", this, []))


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #
HEAT_DAYS = ["2024-07-12", "2024-08-05", "2025-01-20", "2025-07-15", "2025-08-08", "2025-08-24"]


def fake_heat_search(bbox, start, end, max_cloud=50):
    return [{"id": f"LC_{d}", "geometry": FOOTPRINT, "assets": {},
             "properties": {"datetime": f"{d}T10:50:00Z", "eo:cloud_cover": 3}}
            for d in HEAT_DAYS if start <= date.fromisoformat(d) <= end]


def fake_heat_reader(item, grid):
    """The eastern half is 5 °C hotter; summer 2025 is 3 °C hotter than 2024; 24/08/2025 is cloudy."""
    day = item["id"][3:]
    base = {"01": 8.0, "07": 35.0, "08": 36.0}[day[5:7]] + (3 if day.startswith("2025-0") and day[5:7] != "01" else 0)
    lst = np.full(grid.shape, base, "float32")
    lst[:, grid.width // 2:] += 5
    if day == "2025-08-24":
        lst[:] = np.nan
    return lst, np.zeros(grid.shape, bool)


def fake_air(lat, lon, days):
    return {d: 30.0 + d.day / 10 for d in days}


def fake_normals(lat, lon, start, end):
    return {date(2001, 1, 1) + timedelta(days=i): 20.0 for i in range(365)}


class HeatPipelineTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(RASTER_ROOT=self.tmp)
        self.override.enable()
        self.city = City.objects.create(code="49007", name="Angers", department="49", population=150000, geometry=COMMUNE)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_heat(self):
        pipeline.update_heat(self.city, date(2024, 1, 1), date(2025, 11, 30), log=lambda *a: None,
                             reader=fake_heat_reader, search=fake_heat_search, fetch=fake_fetch,
                             fetch_air=fake_air, fetch_normals=fake_normals)
        self.city.refresh_from_db()

    def test_pipeline(self):
        self.run_heat()
        self.assertIn("5 date(s) Landsat ajoutée(s), 1 trop nuageuse(s)", self.city.status_message)
        scene = self.city.heat_scenes.get(date="2025-07-15")
        self.assertEqual(scene.air_tmax, 31.5)
        self.assertEqual(scene.normal_tmax, 20.0)
        self.assertAlmostEqual(scene.mean_lst, 40.5, delta=0.6)  # 38 / 43 halves (float16 storage)
        self.assertTrue((self.city.raster_dir / "heat" / "normals.json").exists())
        self.run_heat()  # nothing new
        self.assertIn("aucune nouvelle image", self.city.status_message)

    def test_pages(self):
        self.run_heat()
        code = self.city.code
        overview = self.client.get(reverse("monitor:heat_data", args=[code])).json()
        self.assertEqual([s["key"] for s in overview["seasons"]], ["2024-summer", "2025-winter", "2025-summer"])
        summer = overview["seasons"][-1]
        self.assertEqual((summer["count"], summer["normal_tmax"]), (2, 20.0))
        self.assertAlmostEqual(summer["air_tmax"], 31.15, delta=0.06)

        def layer(mode, key):
            return self.client.get(reverse("monitor:heat_layer", args=[code, mode, key]))

        island = layer("city", "2025-summer").json()
        values = [v for row in island["values"] for v in row if v is not None]
        self.assertAlmostEqual(max(values) - min(values), 5, delta=0.1)  # east 5 °C hotter
        self.assertAlmostEqual(sum(values) / len(values), 0, delta=0.6)
        self.assertEqual(island["dates"], ["2025-07-15", "2025-08-08"])

        normal = layer("normal", "2025-summer").json()
        values = [v for row in normal["values"] for v in row if v is not None]
        self.assertAlmostEqual(min(values), 3, delta=0.1)
        self.assertEqual(normal["other_years"], [2024])
        self.assertIsNone(layer("normal", "2025-winter").json()["values"])  # no other winter
        self.assertEqual(layer("normal", "2025-07-15").status_code, 404)    # needs a season
        self.assertEqual(layer("lst", "2025-07-15").status_code, 200)
        self.assertEqual(layer("bogus", "2025-summer").status_code, 404)
        self.assertEqual(layer("city", "2025-08-24").status_code, 404)      # cloudy date

        page = self.client.get(reverse("monitor:city", args=[code])).content.decode()
        self.assertIn('id="heat-map"', page)
        self.assertIn("monitor/heat.js", page)

    def test_page_without_heat(self):
        page = self.client.get(reverse("monitor:city", args=[self.city.code])).content.decode()
        self.assertNotIn('id="heat-map"', page)
        self.assertEqual(self.client.get(reverse("monitor:heat_data", args=[self.city.code])).status_code, 404)

    @override_settings(WRITE_REQUIRES_LOGIN=True)
    def test_only_staff_can_start_heat(self):
        url = reverse("monitor:run_heat", args=[self.city.code])
        form = {"heat-start": "2024-01-01", "heat-end": "2025-11-30", "heat-max_cloud": 50}
        with mock.patch.object(pipeline, "start_in_background") as start:
            self.assertEqual(self.client.post(url, form).status_code, 302)
            start.assert_not_called()
            self.client.force_login(User.objects.create_user("admin", is_staff=True))
            self.client.post(url, form)
            start.assert_called_once()
            self.assertIs(start.call_args.kwargs["job"], pipeline.update_heat)


class RefreshAllTests(TestCase):
    def test_retries_errors_and_keeps_going(self):
        from io import StringIO
        from unittest import mock
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from monitor.models import HeatScene
        angers = City.objects.create(code="49007", name="Angers", department="49", population=1, geometry=COMMUNE)
        City.objects.create(code="44109", name="Nantes", department="44", population=1, geometry=COMMUNE)
        HeatScene.objects.create(city=angers, date=date(2022, 8, 2), status=HeatScene.ERROR)
        HeatScene.objects.create(city=angers, date=date(2022, 8, 10), status=HeatScene.OK)
        calls = []
        def boom(city, start, end, log):
            calls.append(("heat", city.code))
            if city.code == "49007":
                raise RuntimeError("Planetary Computer down")
        with mock.patch.object(pipeline, "update_city", lambda city, start, end, log: calls.append(("ndvi", city.code))), \
                mock.patch.object(pipeline, "update_heat", boom):
            with self.assertRaisesMessage(CommandError, "Angers (49007) (chaleur)"):
                call_command("refresh_all", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(calls, [("ndvi", "49007"), ("heat", "49007"), ("ndvi", "44109"), ("heat", "44109")])
        self.assertEqual(list(angers.heat_scenes.values_list("status", flat=True)), [HeatScene.OK])
