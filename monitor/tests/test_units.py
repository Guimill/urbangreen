from datetime import date

import numpy as np
from rasterio.transform import rowcol
from django.test import SimpleTestCase
from shapely.geometry import box, mapping

from monitor.categories import AGRI, PARKS, TREES, UNMAPPED, season_of
from monitor.services import osm, raster, stac


class SeasonTests(SimpleTestCase):
    def test_december_belongs_to_next_winter(self):
        self.assertEqual(season_of(date(2024, 12, 15)), ("winter", 2025))
        self.assertEqual(season_of(date(2025, 2, 28)), ("winter", 2025))
        self.assertEqual(season_of(date(2025, 3, 1)), ("spring", 2025))
        self.assertEqual(season_of(date(2025, 8, 31)), ("summer", 2025))
        self.assertEqual(season_of(date(2025, 11, 30)), ("autumn", 2025))


def node(lon, lat):
    return {"lon": lon, "lat": lat}


class OsmTests(SimpleTestCase):
    def test_categories_priority(self):
        self.assertEqual(osm.category_of({"natural": "tree"}), TREES)
        self.assertEqual(osm.category_of({"leisure": "park", "natural": "wood"}), TREES)
        self.assertEqual(osm.category_of({"leisure": "park"}), PARKS)
        self.assertEqual(osm.category_of({"landuse": "farmland"}), AGRI)
        self.assertIsNone(osm.category_of({"building": "yes"}))

    def test_closed_way_is_polygon_and_open_way_is_dropped(self):
        ring = [node(0, 0), node(1, 0), node(1, 1), node(0, 0)]
        self.assertEqual(osm.element_geometry({"type": "way", "geometry": ring, "tags": {}}).geom_type, "Polygon")
        self.assertIsNone(osm.element_geometry({"type": "way", "geometry": ring[:3], "tags": {"landuse": "grass"}}))
        row = osm.element_geometry({"type": "way", "geometry": ring[:3], "tags": {"natural": "tree_row"}})
        self.assertEqual(row.geom_type, "LineString")

    def test_relation_with_split_outer_ring_and_hole(self):
        rel = {"type": "relation", "tags": {"leisure": "park"}, "members": [
            {"type": "way", "role": "outer", "geometry": [node(0, 0), node(4, 0), node(4, 4)]},
            {"type": "way", "role": "outer", "geometry": [node(4, 4), node(0, 4), node(0, 0)]},
            {"type": "way", "role": "inner", "geometry": [node(1, 1), node(2, 1), node(2, 2), node(1, 2), node(1, 1)]},
        ]}
        self.assertAlmostEqual(osm.element_geometry(rel).area, 16 - 1)

    def test_parse(self):
        data = {"elements": [
            {"type": "node", "lon": 0.5, "lat": 0.5, "tags": {"natural": "tree"}},
            {"type": "way", "tags": {"building": "yes"}, "geometry": [node(0, 0), node(1, 0), node(1, 1), node(0, 0)]},
        ]}
        out = osm.parse(data)
        self.assertEqual(len(out[TREES]), 1)
        self.assertEqual(sum(len(v) for v in out.values()), 1)


def item(item_id, day, cloud, geometry=None):
    return {"id": item_id, "properties": {"datetime": f"{day}T11:00:00Z", "eo:cloud_cover": cloud},
            "geometry": geometry or mapping(box(0, 0, 1, 1)), "assets": {}}


class StacTests(SimpleTestCase):
    def test_group_and_select(self):
        items = [item("a", "2025-06-01", 30), item("b", "2025-06-01", 10), item("c", "2025-06-05", 5),
                 item("d", "2025-06-09", 50), item("e", "2025-07-02", 70)]
        groups = stac.group_by_date(items)
        self.assertEqual([i["id"] for i in groups[date(2025, 6, 1)]], ["b", "a"])  # least cloudy first
        self.assertEqual(stac.select_dates(groups, per_month=2), [date(2025, 6, 1), date(2025, 6, 5), date(2025, 7, 2)])

    def test_footprint_coverage(self):
        area = box(0, 0, 2, 1)
        self.assertAlmostEqual(stac.footprint_coverage([item("a", "2025-01-01", 0)], area), 0.5)
        both = [item("a", "2025-01-01", 0), item("b", "2025-01-01", 0, mapping(box(1, 0, 2, 1)))]
        self.assertAlmostEqual(stac.footprint_coverage(both, area), 1.0)


class RasterTests(SimpleTestCase):
    commune = mapping(box(-0.56, 47.46, -0.54, 47.47))  # ~1.5 x 1.1 km

    def test_grid_has_10m_ground_pixels(self):
        grid = raster.Grid.for_geometry(self.commune)
        self.assertAlmostEqual(grid.pixel_area_m2, 100, places=6)
        self.assertTrue(140 < grid.width < 170 and 105 < grid.height < 130, grid.shape)
        (south, west), (north, east) = grid.bounds_latlng()
        self.assertTrue(west < -0.56 and east > -0.54 and south < 47.46 and north > 47.47)

    def test_category_raster_priority(self):
        grid = raster.Grid.for_geometry(self.commune)
        park = box(-0.56, 47.46, -0.55, 47.47)
        wood = box(-0.555, 47.46, -0.55, 47.47)  # inside the park
        cats = raster.category_raster(grid, {PARKS: [park], TREES: [wood], AGRI: [box(-0.56, 47.46, -0.54, 47.47)]})
        self.assertTrue({TREES, PARKS, AGRI} <= set(np.unique(cats)))

        def at(lon, lat):
            x, y = raster._to_3857(lon, lat)
            row, col = rowcol(grid.transform, x, y)
            return cats[row, col]

        self.assertEqual(at(-0.5525, 47.465), TREES)   # wood wins over park and farmland
        self.assertEqual(at(-0.5575, 47.465), PARKS)   # park wins over farmland
        self.assertEqual(at(-0.545, 47.465), AGRI)

    def test_ndvi_masks_clouds_and_quantize_roundtrip(self):
        red = np.array([[0.05, 0.05, 0.2]], "float32")
        nir = np.array([[0.45, 0.45, 0.2]], "float32")
        scl = np.array([[4, 9, 5]], "uint8")  # vegetation, cloud, bare soil
        values = raster.ndvi(red, nir, scl)
        self.assertAlmostEqual(float(values[0, 0]), 0.8, places=5)
        self.assertTrue(np.isnan(values[0, 1]))
        self.assertAlmostEqual(float(values[0, 2]), 0.0)
        back = raster.dequantize(raster.quantize(values))
        self.assertAlmostEqual(float(back[0, 0]), 0.8)
        self.assertTrue(np.isnan(back[0, 1]))

    def test_scene_stats(self):
        inside = np.ones((2, 4), bool)
        inside[1, 3] = False
        cats = np.array([[TREES, TREES, PARKS, UNMAPPED], [UNMAPPED, UNMAPPED, UNMAPPED, UNMAPPED]], "uint8")
        values = np.array([[0.8, 0.5, 0.2, np.nan], [0.1, -0.3, 0.5, 0.9]], "float32")
        scl = np.array([[4, 4, 4, 9], [5, 6, 4, 4]], "uint8")  # one cloud, one water pixel
        valid, stats = raster.scene_stats(values, scl, inside, cats)
        self.assertAlmostEqual(valid, 6 / 7)
        self.assertAlmostEqual(stats["trees"]["mean_ndvi"], 0.65, places=5)
        self.assertEqual(stats["trees"]["green_share"], 1.0)
        self.assertEqual(stats["trees"]["dense_share"], 0.5)
        # unmapped: 0.1 and 0.5 kept, water (-0.3) excluded, outside pixel ignored, cloudy pixel unseen
        self.assertEqual(stats["unmapped"]["pixels"], 2)
        self.assertAlmostEqual(stats["unmapped"]["mean_ndvi"], 0.3, places=5)
        self.assertAlmostEqual(stats["unmapped"]["coverage"], 2 / 4)
        self.assertIsNone(stats["agri"]["mean_ndvi"])
        self.assertEqual(stats["all"]["pixels"], 5)

    def test_png_outputs(self):
        values = np.array([[0.1, 0.5], [np.nan, 0.9]], "float32")
        inside = np.ones((2, 2), bool)
        for png in (raster.ndvi_png(values, inside), raster.diff_png(values, inside),
                    raster.categories_png(np.array([[1, 2], [3, 0]], "uint8"), inside)):
            self.assertTrue(png.startswith(b"\x89PNG"))
