"""python manage.py diagnose_trees Angers [--a 2026-winter --b 2026-summer] [--output rapport.md]"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from monitor.categories import SEASON_LABELS
from monitor.models import City
from monitor.services import analysis, trees


def parse_season(value):
    try:
        year, season = value.split("-")
        if season not in SEASON_LABELS:
            raise ValueError
        return int(year), season
    except ValueError:
        raise CommandError(f"Saison invalide « {value} » : attendu AAAA-winter|spring|summer|autumn")


class Command(BaseCommand):
    help = ("Teste pourquoi la catégorie « arbres » peut être plus verte en hiver qu'en été "
            "(mélange de pixels, type d'arbre, feuillage, météo) et écrit un rapport Markdown.")

    def add_arguments(self, parser):
        parser.add_argument("city", help="code INSEE ou nom d'une commune déjà suivie")
        parser.add_argument("--a", help="saison A, ex. 2026-winter (défaut : dernier hiver ayant un été)")
        parser.add_argument("--b", help="saison B, ex. 2026-summer (défaut : l'été de la même année)")
        parser.add_argument("--output", help="fichier Markdown (défaut : affiché)")
        parser.add_argument("--no-weather", action="store_true", help="ne pas interroger Open-Meteo")

    def handle(self, *args, **opts):
        city = City.objects.filter(Q(code=opts["city"]) | Q(name__iexact=opts["city"])).first()
        if city is None:
            raise CommandError(f"Commune inconnue : {opts['city']}")
        years = analysis.overlays(city)["diffs"]
        if opts["a"] and opts["b"]:
            a, b = parse_season(opts["a"]), parse_season(opts["b"])
        elif years:
            a, b = (years[-1], "winter"), (years[-1], "summer")
        else:
            raise CommandError("Pas encore d'hiver et d'été analysés : précisez --a et --b")
        log = lambda m: self.stderr.write(m)  # noqa: E731
        try:
            result = trees.diagnose(city, a, b, weather=not opts["no_weather"], log=log)
        except ValueError as exc:
            raise CommandError(str(exc))
        text = trees.report_markdown(result)
        if opts["output"]:
            Path(opts["output"]).write_text(text, encoding="utf-8")
            self.stderr.write(f"Rapport écrit dans {opts['output']}")
        else:
            self.stdout.write(text)
