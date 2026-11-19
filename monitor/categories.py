"""Green categories and seasons: the vocabulary shared by the pipeline, the views and the charts."""
from datetime import date

# Land-cover codes in the category raster. When OpenStreetMap features overlap,
# the lower code wins (a tree row inside a park counts as trees).
UNMAPPED, TREES, PARKS, OTHER, AGRI = 0, 1, 2, 3, 4

# key, raster code (None = every pixel of the commune), label. The order is the
# chart order and the colour order: it never changes, so a category keeps its colour.
CATEGORIES = [
    ("all", None, "Commune entière"),
    ("trees", TREES, "Arbres et bois"),
    ("parks", PARKS, "Parcs et jardins"),
    ("other", OTHER, "Autres espaces verts"),
    ("agri", AGRI, "Espaces agricoles"),
    ("unmapped", UNMAPPED, "Reste (bâti, jardins privés…)"),
]
CATEGORY_LABELS = {key: label for key, _, label in CATEGORIES}

# Categorical colours (light, dark), validated for colour-blind separation in this order.
# The same colour is used for a category on the charts and on the map.
CATEGORY_COLORS = {
    "all": ("#2a78d6", "#3987e5"),
    "trees": ("#008300", "#008300"),
    "parks": ("#eda100", "#c98500"),
    "other": ("#1baf7a", "#199e70"),
    "agri": ("#eb6834", "#d95926"),
    "unmapped": ("#6250d6", "#9085e9"),
}
CATEGORY_DESCRIPTIONS = {
    "trees": "Bois, forêts, haies, alignements et arbres isolés recensés dans OpenStreetMap",
    "parks": "Parcs, jardins publics, aires de loisirs",
    "other": "Pelouses, cimetières, jardins familiaux, terrains de sport en herbe, prairies naturelles",
    "agri": "Cultures, prés, vergers, vignes, pépinières",
    "unmapped": "Tout ce qui n'est pas cartographié comme espace vert : bâti, voirie, jardins privés…",
}

# NDVI thresholds
GREEN_NDVI = 0.3   # pixel counted as vegetated
DENSE_NDVI = 0.6   # dense, healthy vegetation

# Meteorological seasons. December belongs to the winter of the following year,
# so "Hiver 2025" = December 2024 + January and February 2025.
SEASONS = [("winter", "Hiver"), ("spring", "Printemps"), ("summer", "Été"), ("autumn", "Automne")]
SEASON_LABELS = dict(SEASONS)
SEASON_ORDER = {key: i for i, (key, _) in enumerate(SEASONS)}
_MONTH_SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                 6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def season_of(day: date):
    """(season key, season year) of a date."""
    return _MONTH_SEASON[day.month], day.year + (1 if day.month == 12 else 0)


def season_label(season, year):
    return f"{SEASON_LABELS[season]} {year}"
