from datetime import date

from django.core.management.base import BaseCommand, CommandError

from monitor.forms import LANDSAT_START, default_heat_start
from monitor.services import pipeline

from .monitor_city import Command as CityCommand


class Command(BaseCommand):
    help = ("Îlots de chaleur : télécharge les températures de surface Landsat 8/9 de la période, "
            "les moyenne sur des carrés de 200 m et ajoute la température de l'air (Open-Meteo) et sa "
            "normale 1991-2020. Les dates déjà traitées sont ignorées.")

    def add_arguments(self, parser):
        parser.add_argument("commune", help="code INSEE (49007) ou nom (Angers)")
        parser.add_argument("--start", type=date.fromisoformat, default=None, help="AAAA-MM-JJ (défaut : 10 ans)")
        parser.add_argument("--end", type=date.fromisoformat, default=None, help="AAAA-MM-JJ (défaut : aujourd'hui)")
        parser.add_argument("--max-cloud", type=int, default=50)

    def handle(self, *args, **opts):
        city = CityCommand(stdout=self.stdout, stderr=self.stderr).get_city(opts["commune"])
        start, end = max(opts["start"] or default_heat_start(), LANDSAT_START), opts["end"] or date.today()
        self.stdout.write(f"{city} : {start} -> {end}")
        try:
            pipeline.update_heat(city, start, end, max_cloud=opts["max_cloud"], log=self.stdout.write)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
