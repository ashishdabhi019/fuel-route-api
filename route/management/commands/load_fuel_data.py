import csv
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from route.models import FuelStation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load fuel station data from a CSV file into the database"

    def add_arguments(self, parser):
        parser.add_argument("--csv-path", type=str, default=None)
        parser.add_argument("--clear", action="store_true", default=False,
                            help="Delete existing records before loading")

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"] or settings.FUEL_CSV_PATH)

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        self.stdout.write(f"Loading from: {csv_path}")

        if options["clear"]:
            FuelStation.objects.all().delete()

        seen = set()
        records = []

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    price_str = row["Retail Price"].strip()
                    city = row["City"].strip()
                    state = row["State"].strip()
                    if not all([price_str, city, state]):
                        continue

                    opis_id = int(row["OPIS Truckstop ID"].strip())
                    name = row["Truckstop Name"].strip()
                    key = (opis_id, name)
                    if key in seen:
                        continue
                    seen.add(key)

                    rack_id_str = row["Rack ID"].strip()
                    records.append(FuelStation(
                        opis_id=opis_id,
                        name=name,
                        address=row["Address"].strip(),
                        city=city,
                        state=state,
                        rack_id=int(rack_id_str) if rack_id_str else None,
                        retail_price=float(price_str),
                    ))
                except (ValueError, KeyError) as e:
                    logger.warning(f"Skipping row: {e}")

        with transaction.atomic():
            FuelStation.objects.bulk_create(records, ignore_conflicts=True, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {FuelStation.objects.count()} stations."
        ))
