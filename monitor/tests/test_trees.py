"""The "trees greener in winter" explanation, checked on synthetic data.

The synthetic commune follows the hypothesis exactly: a deciduous crown has NDVI 0.35 in
winter and 0.85 in summer, a lawn 0.65 in winter and 0.25 in a dry summer, and every pixel
is the mix of the two weighted by the share of the pixel covered by the crown. If the
diagnosis is right, it must find: pure-canopy wood greener in summer, isolated trees
greener in winter, a change that grows with the canopy fraction, and a mixing model that
gives back the two end members.
"""
import numpy as np
from django.test import SimpleTestCase
from shapely.geometry import Point, box

from monitor.categories import OTHER, TREES
from monitor.services import raster, trees

CROWN = {"winter": 0.35, "summer": 0.85}
LAWN = {"winter": 0.65, "summer": 0.25}


def synthetic():
    # a 600 m x 400 m commune around Angers, a 200 m wood on the left and street trees on the right
    lon, lat, dlon, dlat = -0.56, 47.46, 0.008, 0.0036
    commune = box(lon, lat, lon + dlon, lat + dlat)
    grid = raster.Grid.for_geometry(commune.__geo_interface__)
    wood = box(lon + 0.0004, lat + 0.0005, lon + 0.003, lat + 0.0031)
    features = [trees.TreeFeature("wood", wood, {"leaf_cycle": "deciduous"})]
    for i in range(12):
        for j in range(5):
            # one tree every ~37 m: crowns never overlap
            tags = {"leaf_type": "needleleaved"} if (i, j) == (0, 0) else {"leaf_type": "broadleaved"}
            features.append(trees.TreeFeature("single", Point(lon + 0.0037 + i * 0.0005, lat + 0.0004 + j * 0.00065), tags))
    inside = raster.commune_mask(grid, commune.__geo_interface__)
    fraction = trees.canopy_fraction(grid, features)
    categories = np.where(fraction > 0, TREES, OTHER).astype("uint8")
    season = {s: (fraction * CROWN[s] + (1 - fraction) * LAWN[s]).astype("float32") for s in CROWN}
    return grid, features, inside, categories, fraction, season


class CanopyFractionTests(SimpleTestCase):
    def test_point_tree_covers_its_crown_area(self):
        grid, features, inside, categories, fraction, _ = synthetic()
        singles = [f for f in features if f.kind == "single"]
        crown_m2 = np.pi * raster.TREE_RADIUS_M ** 2
        # summed over the grid, each crown weighs ~50 m², i.e. ~half a 10 m pixel
        expected = len(singles) * crown_m2 / 100 + trees.canopy_fraction(grid, features[:1]).sum()
        self.assertAlmostEqual(fraction.sum() / expected, 1, delta=0.08)
        # a 50 m² crown in a 100 m² pixel: no pixel of an isolated tree is mostly canopy,
        # and the crown usually straddles several pixels, which dilutes it further
        only_singles = trees.canopy_fraction(grid, singles)
        self.assertLess(only_singles.max(), 0.6)
        self.assertGreater((only_singles > 0).sum(), 1.5 * len(singles))

    def test_wood_interior_is_pure_canopy(self):
        grid, features, *_ = synthetic()
        zones = trees.kind_zones(grid, features)
        wood_fraction = trees.canopy_fraction(grid, features[:1])
        self.assertTrue((wood_fraction[zones["wood_core"]] == 1).all())
        self.assertGreater(zones["wood_edge"].sum(), 0)
        # zones never overlap
        stack = np.stack([zones[k] for k, _ in trees.KINDS]).sum(axis=0)
        self.assertLessEqual(stack.max(), 1)


class ExplanationTests(SimpleTestCase):
    def setUp(self):
        self.grid, self.features, self.inside, self.categories, self.fraction, self.season = synthetic()
        self.result = trees.compare(self.season["winter"], self.season["summer"], self.inside, self.categories,
                                    trees.kind_zones(self.grid, self.features),
                                    trees.leaf_zones(self.grid, self.features), self.fraction, 0.01)

    def test_wood_greener_in_summer_isolated_trees_greener_in_winter(self):
        k = self.result["kinds"]
        self.assertAlmostEqual(k["wood_core"]["diff"], CROWN["summer"] - CROWN["winter"], places=5)
        self.assertEqual(k["wood_core"]["greener_in_a"], 0)
        self.assertLess(k["single"]["diff"], 0)
        self.assertGreater(k["single"]["greener_in_a"], 0.8)

    def test_change_grows_with_canopy_fraction(self):
        diffs = [b["diff"] for b in self.result["fraction"] if b["diff"] is not None]
        self.assertEqual(diffs, sorted(diffs))
        self.assertLess(diffs[0], 0)
        self.assertGreater(diffs[-1], 0)

    def test_mixing_model_recovers_crown_and_lawn(self):
        m = self.result["mixing"]
        self.assertAlmostEqual(m["a"]["crown"], CROWN["winter"], places=3)
        self.assertAlmostEqual(m["b"]["crown"], CROWN["summer"], places=3)
        self.assertAlmostEqual(m["a"]["around"], LAWN["winter"], places=3)
        self.assertAlmostEqual(m["b"]["around"], LAWN["summer"], places=3)
        # crown +0.5, lawn −0.4: the pixel turns greener in summer above 0.4 / 0.9 = 44 % canopy
        self.assertAlmostEqual(m["break_even"], 0.4 / 0.9, places=3)
        self.assertGreater(m["a"]["r2"], 0.99)

    def test_no_mixing_without_lawn_change(self):
        # control: if the lawn does not change, no tree pixel can be greener in winter
        flat = self.fraction * CROWN["summer"] + (1 - self.fraction) * LAWN["winter"]
        r = trees.compare(self.season["winter"], flat.astype("float32"), self.inside, self.categories,
                          trees.kind_zones(self.grid, self.features), trees.leaf_zones(self.grid, self.features),
                          self.fraction, 0.01)
        self.assertEqual(r["trees"]["greener_in_a"], 0)

    def test_leaf_tags_are_used(self):
        self.assertGreater(self.result["leaves"]["needleleaved"]["pixels"], 0)
        self.assertGreater(self.result["leaves"]["broadleaved"]["pixels"],
                           self.result["leaves"]["needleleaved"]["pixels"])
        self.assertEqual(self.result["leaves"]["evergreen"]["pixels"], 0)


class OsmAndWeatherTests(SimpleTestCase):
    def test_parse_keeps_kind_and_tags(self):
        data = {"elements": [
            {"type": "node", "lat": 47.46, "lon": -0.55, "tags": {"natural": "tree", "leaf_type": "needleleaved"}},
            {"type": "way", "tags": {"natural": "tree_row"},
             "geometry": [{"lat": 47.46, "lon": -0.55}, {"lat": 47.461, "lon": -0.55}]},
            {"type": "way", "tags": {"landuse": "forest"}, "geometry": [
                {"lat": 47.46, "lon": -0.55}, {"lat": 47.461, "lon": -0.55}, {"lat": 47.461, "lon": -0.549},
                {"lat": 47.46, "lon": -0.55}]},
        ]}
        parsed = trees.parse(data)
        self.assertEqual([f.kind for f in parsed], ["single", "row", "wood"])
        self.assertEqual(parsed[0].tags["leaf_type"], "needleleaved")

    def test_seasonal_water_balance(self):
        daily = {"time": ["2025-12-31", "2026-01-01", "2026-07-01", "2026-07-02"],
                 "precipitation_sum": [10, 5, 0, None], "et0_fao_evapotranspiration": [1, 1, 6, 6],
                 "temperature_2m_max": [8, 10, 30, 31]}
        w = trees.seasonal_water_balance(daily)
        self.assertEqual(w[(2026, "winter")], {"rain": 15, "et0": 2, "balance": 13, "tmax": 9.0, "days": 2})
        self.assertEqual(w[(2026, "summer")]["balance"], -6)
