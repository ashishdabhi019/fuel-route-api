"""
Management command: geocode_stations
Geocodes fuel stations using a local US Cities database (no API calls needed!).

Strategy:
1. Load the US cities CSV (ID, STATE_CODE, CITY, LATITUDE, LONGITUDE)
2. Build an in-memory lookup: {(city_lower, state_code): (lat, lon)}
3. Bulk-update all FuelStation records with matching coordinates

This is completely offline and runs in seconds.

Usage:
    python manage.py geocode_stations
    python manage.py geocode_stations --cities-csv /path/to/us_cities.csv
"""
import csv
import logging
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from route.models import FuelStation

logger = logging.getLogger(__name__)

US_CITIES_URL = (
    "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
)
DEFAULT_CITIES_CSV = Path(__file__).resolve().parent.parent.parent.parent / "us_cities.csv"


def download_cities_csv(path: Path):
    """Download the US cities CSV if it doesn't exist locally."""
    print(f"Downloading US cities database to {path}...")
    resp = requests.get(US_CITIES_URL, timeout=30)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    print("Download complete.")


def load_city_coords(csv_path: Path) -> dict:
    """
    Load city coordinates from CSV into a dict:
    {(city_lower, state_code): (lat, lon)}
    """
    coords = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            state = row.get("STATE_CODE", "").strip().upper()
            city = row.get("CITY", "").strip().lower()
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except (ValueError, KeyError):
                continue
            coords[(city, state)] = (lat, lon)
    return coords


class Command(BaseCommand):
    help = "Geocode fuel stations using offline US cities database (fast, no API calls)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cities-csv",
            type=str,
            default=None,
            help=f"Path to US cities CSV (default: {DEFAULT_CITIES_CSV})",
        )
        parser.add_argument(
            "--download",
            action="store_true",
            default=False,
            help="Force re-download of the US cities CSV",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            default=True,
            help="Skip already geocoded stations (default: True)",
        )
        parser.add_argument(
            "--no-resume",
            action="store_false",
            dest="resume",
            help="Re-geocode all stations",
        )

    def handle(self, *args, **options):
        cities_csv = Path(options["cities_csv"]) if options["cities_csv"] else DEFAULT_CITIES_CSV

        # Download if needed
        if not cities_csv.exists() or options["download"]:
            download_cities_csv(cities_csv)

        self.stdout.write(f"Loading city coordinates from: {cities_csv}")
        city_coords = load_city_coords(cities_csv)
        self.stdout.write(f"Loaded {len(city_coords)} city/state coordinate pairs")

        # Get stations to geocode
        qs = FuelStation.objects.all()
        if options["resume"]:
            qs = qs.filter(geocoded=False)

        total = qs.count()
        self.stdout.write(f"Geocoding {total} fuel stations...")

        geocoded = 0
        failed = 0
        fuzzy_matched = 0

        # Process in batches
        batch_size = 500
        to_update = []

        for station in qs.iterator(chunk_size=batch_size):
            city_lower = station.city.strip().lower()
            state = station.state.strip().upper()

            coords = city_coords.get((city_lower, state))

            if not coords:
                # Try fuzzy: remove common suffixes and prefixes
                # e.g., "Saint Louis" -> "St. Louis", "Mount" -> "Mt."
                alt_city = (
                    city_lower
                    .replace("saint ", "st. ")
                    .replace("mount ", "mt. ")
                    .replace("fort ", "ft. ")
                    .replace("north ", "n. ")
                    .replace("south ", "s. ")
                )
                coords = city_coords.get((alt_city, state))
                if coords:
                    fuzzy_matched += 1

            if coords:
                station.latitude = coords[0]
                station.longitude = coords[1]
                station.geocoded = True
                to_update.append(station)
                geocoded += 1
            else:
                failed += 1
                if failed <= 20:
                    logger.debug(f"No coords for: {station.city}, {state}")

            # Bulk update in batches
            if len(to_update) >= batch_size:
                with transaction.atomic():
                    FuelStation.objects.bulk_update(
                        to_update, ["latitude", "longitude", "geocoded"], batch_size=batch_size
                    )
                to_update = []

        # Final batch
        if to_update:
            with transaction.atomic():
                FuelStation.objects.bulk_update(
                    to_update, ["latitude", "longitude", "geocoded"], batch_size=batch_size
                )

        total_geocoded = FuelStation.objects.filter(geocoded=True).count()
        coverage_pct = (total_geocoded / FuelStation.objects.count() * 100) if FuelStation.objects.count() else 0

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Geocoding complete!"
            f"\n  Stations geocoded: {geocoded}"
            f"\n  Fuzzy matches: {fuzzy_matched}"
            f"\n  Failed (not in US cities DB): {failed}"
            f"\n  Total geocoded in DB: {total_geocoded} ({coverage_pct:.1f}% coverage)"
        ))
