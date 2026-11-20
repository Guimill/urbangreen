from datetime import date

from django.core.management.base import BaseCommand, CommandError

from monitor.forms import LANDSAT_START, default_heat_start, default_start
from monitor.models import City, HeatScene, Scene
from monitor.services import pipeline


class Command(BaseCommand):
    help = ("Met à jour toutes les communes : nouvelles images Sentinel-2 (végétation) et Landsat "
            "(chaleur). Les dates en erreur sont retentées, les dates déjà traitées sont ignorées. "
            "Lancée tous les 3 mois par le cron du serveur (deploy/refresh.sh).")

    def handle(self, *args, **opts):
        today, failed = date.today(), []
        for city in City.objects.order_by("name"):
            retried = (city.scenes.filter(status=Scene.ERROR).delete()[0]
                       + city.heat_scenes.filter(status=HeatScene.ERROR).delete()[0])
            self.stdout.write(f"== {city} ({retried} date(s) en erreur retentée(s))")
            jobs = (("végétation", pipeline.update_city, default_start(today)),
                    ("chaleur", pipeline.update_heat, max(default_heat_start(today), LANDSAT_START)))
            for label, job, start in jobs:
                try:
                    job(city, start, today, log=self.stdout.write)
                except Exception as exc:  # one commune or source down must not stop the others
                    failed.append(f"{city} ({label})")
                    self.stderr.write(f"{city} ({label}) : {exc}")
        if failed:
            raise CommandError("échec : " + ", ".join(failed))
