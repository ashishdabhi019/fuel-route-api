"""
Management command: load_fuel_data
Loads fuel station data from the CSV file into the database.

Usage:
    python manage.py load_fuel_data
    python manage.py load_fuel_data --csv-path /path/to/fuel-prices.csv
"""
import csv
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from route.models import FuelStation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load fuel station data from the OPIS CSV file into the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            type=str,
            default=None,
            help="Path to the fuel prices CSV file (defaults to FUEL_CSV_PATH in settings)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            default=False,
            help="Clear existing data before loading",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"] or settings.FUEL_CSV_PATH
        csv_path = Path(csv_path)

        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        self.stdout.write(f"Loading fuel data from: {csv_path}")

        if options["clear"]:
            self.stdout.write("Clearing existing fuel station data...")
            FuelStation.objects.all().delete()

        # De-duplicate by (city, state) for geocoding efficiency
        # Keep one entry per (city, state, opis_id) for station uniqueness
        seen_ids = set()
        records = []

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    opis_id = int(row["OPIS Truckstop ID"].strip())
                    name = row["Truckstop Name"].strip()
                    address = row["Address"].strip()
                    city = row["City"].strip()
                    state = row["State"].strip()
                    rack_id_str = row["Rack ID"].strip()
                    price_str = row["Retail Price"].strip()

                    if not price_str or not city or not state:
                        continue

                    rack_id = int(rack_id_str) if rack_id_str else None
                    price = float(price_str)

                    # Skip duplicates (same opis_id + name combo)
                    key = (opis_id, name)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)

                    records.append(FuelStation(
                        opis_id=opis_id,
                        name=name,
                        address=address,
                        city=city,
                        state=state,
                        rack_id=rack_id,
                        retail_price=price,
                    ))
                except (ValueError, KeyError) as e:
                    logger.warning(f"Skipping row due to error: {e} | row: {row}")
                    continue

        self.stdout.write(f"Inserting {len(records)} unique fuel stations...")

        # Batch insert for performance
        with transaction.atomic():
            FuelStation.objects.bulk_create(records, ignore_conflicts=True, batch_size=500)

        count = FuelStation.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"✓ Successfully loaded {count} fuel stations into the database."
        ))
