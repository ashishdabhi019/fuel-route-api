import csv
import logging
from pathlib import Path

import requests
from django.core.management.base import BaseCommand
from django.db import transaction

from route.models import FuelStation

logger = logging.getLogger(__name__)

US_CITIES_URL = "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
DEFAULT_CITIES_CSV = Path(__file__).resolve().parent.parent.parent.parent / "us_cities.csv"


def download_cities_csv(path: Path):
    path.write_bytes(requests.get(US_CITIES_URL, timeout=30).content)


def load_city_coords(csv_path: Path) -> dict:
    coords = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                state = row["STATE_CODE"].strip().upper()
                city = row["CITY"].strip().lower()
                coords[(city, state)] = (float(row["LATITUDE"]), float(row["LONGITUDE"]))
            except (ValueError, KeyError):
                continue
    return coords


class Command(BaseCommand):
    help = "Geocode fuel stations using an offline US cities dataset"

    def add_arguments(self, parser):
        parser.add_argument("--cities-csv", type=str, default=None)
        parser.add_argument("--download", action="store_true", default=False,
                            help="Re-download the cities dataset")
        parser.add_argument("--resume", action="store_true", default=True,
                            help="Skip already geocoded stations")
        parser.add_argument("--no-resume", action="store_false", dest="resume")

    def handle(self, *args, **options):
        cities_csv = Path(options["cities_csv"]) if options["cities_csv"] else DEFAULT_CITIES_CSV

        if not cities_csv.exists() or options["download"]:
            self.stdout.write("Downloading US cities dataset...")
            download_cities_csv(cities_csv)

        city_coords = load_city_coords(cities_csv)
        self.stdout.write(f"Loaded {len(city_coords)} cities")

        qs = FuelStation.objects.filter(geocoded=False) if options["resume"] else FuelStation.objects.all()
        self.stdout.write(f"Geocoding {qs.count()} stations...")

        BATCH = 500
        geocoded = failed = fuzzy = 0
        to_update = []

        for station in qs.iterator(chunk_size=BATCH):
            city = station.city.strip().lower()
            state = station.state.strip().upper()
            coords = city_coords.get((city, state))

            if not coords:
                alt = (
                    city
                    .replace("saint ", "st. ")
                    .replace("mount ", "mt. ")
                    .replace("fort ", "ft. ")
                )
                coords = city_coords.get((alt, state))
                if coords:
                    fuzzy += 1

            if coords:
                station.latitude, station.longitude, station.geocoded = coords[0], coords[1], True
                to_update.append(station)
                geocoded += 1
            else:
                failed += 1

            if len(to_update) >= BATCH:
                with transaction.atomic():
                    FuelStation.objects.bulk_update(to_update, ["latitude", "longitude", "geocoded"], batch_size=BATCH)
                to_update = []

        if to_update:
            with transaction.atomic():
                FuelStation.objects.bulk_update(to_update, ["latitude", "longitude", "geocoded"], batch_size=BATCH)

        total = FuelStation.objects.count()
        done = FuelStation.objects.filter(geocoded=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"Done: {geocoded} geocoded ({fuzzy} fuzzy), {failed} failed — "
            f"{done}/{total} total ({done/total*100:.1f}%)"
        ))
