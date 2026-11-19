from datetime import date

from django.core.management.base import BaseCommand, CommandError

from monitor.forms import default_start
from monitor.models import City
from monitor.services import communes, pipeline


class Command(BaseCommand):
    help = ("Ajoute une commune (code INSEE ou nom) si besoin, puis télécharge et analyse les images "
            "Sentinel-2 de la période. Les dates déjà traitées sont ignorées : la commande peut être "
            "relancée régulièrement (cron) pour suivre la commune.")

    def add_arguments(self, parser):
        parser.add_argument("commune", help="code INSEE (49007) ou nom (Angers)")
        parser.add_argument("--start", type=date.fromisoformat, default=None, help="AAAA-MM-JJ")
        parser.add_argument("--end", type=date.fromisoformat, default=None, help="AAAA-MM-JJ (défaut : aujourd'hui)")
        parser.add_argument("--max-cloud", type=int, default=40)
        parser.add_argument("--per-month", type=int, default=3)
        parser.add_argument("--refresh-osm", action="store_true")

    def handle(self, *args, **opts):
        city = self.get_city(opts["commune"])
        start, end = opts["start"] or default_start(), opts["end"] or date.today()
        self.stdout.write(f"{city} : {start} -> {end}")
        try:
            pipeline.update_city(city, start, end, max_cloud=opts["max_cloud"], per_month=opts["per_month"],
                                 refresh_osm=opts["refresh_osm"], log=self.stdout.write)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

    def get_city(self, value):
        try:
            if value.isdigit() or (len(value) == 5 and value[:2] in ("2A", "2B")):
                return City.objects.get(code=value)
            return City.objects.get(name__iexact=value)
        except City.DoesNotExist:
            pass
        try:
            code = value if value[:2].isdigit() or value[:2] in ("2A", "2B") else communes.search(value, limit=1)[0]["code"]
            data = communes.fetch(code)
        except (IndexError, communes.CommuneError) as exc:
            raise CommandError(f"Commune introuvable : {value}") from exc
        city, _ = City.objects.get_or_create(code=data["code"], defaults=data)
        self.stdout.write(self.style.SUCCESS(f"Commune ajoutée : {city}"))
        return city
