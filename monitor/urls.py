from django.urls import path, register_converter
from django.urls.converters import StringConverter

from . import views


class DateConverter(StringConverter):
    regex = r"\d{4}-\d{2}-\d{2}"


register_converter(DateConverter, "isodate")

app_name = "monitor"
urlpatterns = [
    path("", views.index, name="index"),
    path("communes/ajouter/", views.add_city, name="add_city"),
    path("communes/<str:code>/", views.city_detail, name="city"),
    path("communes/<str:code>/analyser/", views.run, name="run"),
    path("communes/<str:code>/statut.json", views.status, name="status"),
    path("communes/<str:code>/donnees.json", views.data, name="data"),
    path("communes/<str:code>/ndvi/<isodate:day>.png", views.scene_png, name="scene_png"),
    path("communes/<str:code>/saison/<str:key>.png", views.season_png, name="season_png"),
    path("communes/<str:code>/ecart/<int:year>.png", views.diff_png, name="diff_png"),
    path("communes/<str:code>/categories.png", views.categories_png, name="categories_png"),
]
