import logging
import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "http://router.project-osrm.org"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "FuelRouteAPI/1.0",
    "Accept-Language": "en",
}


def get_coordinates_from_location(location_name: str) -> tuple[float, float]:
    """Geocode a US location string. Returns (longitude, latitude). Cached 24h."""
    cache_key = f"geocode_{location_name.lower().strip().replace(' ', '_').replace(',', '')}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    resp = requests.get(
        NOMINATIM_URL,
        params={"q": location_name, "format": "json", "limit": 1, "countrycodes": "us"},
        headers=NOMINATIM_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()

    if not results:
        raise ValueError(
            f"Location not found: '{location_name}'. "
            "Try a format like 'Chicago, IL' or 'New York, NY'."
        )

    lon = float(results[0]["lon"])
    lat = float(results[0]["lat"])
    logger.info(f"Geocoded '{location_name}' -> ({lat:.4f}, {lon:.4f})")

    cache.set(cache_key, (lon, lat), timeout=86400)
    return lon, lat


def get_route(start_location: str, end_location: str) -> dict:
    """
    Fetch a driving route from OSRM between two US locations.
    Makes at most 3 external calls (2 geocode + 1 route), all cached.
    """
    cache_key = f"route_{start_location}_{end_location}".lower().replace(" ", "_").replace(",", "")
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Route cache hit: {start_location} -> {end_location}")
        return cached

    start_lon, start_lat = get_coordinates_from_location(start_location)
    end_lon, end_lat = get_coordinates_from_location(end_location)

    url = f"{OSRM_BASE_URL}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}"
    resp = requests.get(
        url,
        params={"overview": "full", "geometries": "geojson", "steps": "false"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"No route found between '{start_location}' and '{end_location}'")

    route = data["routes"][0]
    coordinates = route["geometry"]["coordinates"]
    lons = [c[0] for c in coordinates]
    lats = [c[1] for c in coordinates]

    result = {
        "distance_miles": route["distance"] * 0.000621371,
        "duration_seconds": route["duration"],
        "geometry": route["geometry"],
        "waypoints": coordinates,
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "start_coords": [start_lon, start_lat],
        "end_coords": [end_lon, end_lat],
        "start_location": start_location,
        "end_location": end_location,
    }

    cache.set(cache_key, result, timeout=3600)
    logger.info(f"Route fetched: {result['distance_miles']:.1f} miles, {len(coordinates)} points")
    return result
