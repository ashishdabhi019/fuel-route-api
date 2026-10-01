"""
Routing service: Calls OSRM (Project-OSRM) for route geometry and
Nominatim for geocoding start/end locations.

Both are 100% free with no API key required.

API call strategy (per request):
  - 2 Nominatim calls to geocode start + end (cached aggressively)
  - 1 OSRM directions call (cached for 1 hour)
  Total: ≤ 3 external API calls per unique route (1 on repeat calls)
"""
import logging
import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "http://router.project-osrm.org"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "FuelRouteAPI/1.0 (backend-assessment)",
    "Accept-Language": "en",
}


def get_coordinates_from_location(location_name: str) -> tuple[float, float]:
    """
    Geocode a location name using Nominatim (free OSM geocoder).
    Returns (longitude, latitude) tuple.
    Results are cached for 24 hours.
    Raises ValueError if location cannot be found.
    """
    cache_key = f"geocode_v2_{location_name.lower().strip().replace(' ', '_')}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Geocode cache hit: '{location_name}'")
        return cached

    params = {
        "q": location_name,
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
        "addressdetails": 0,
    }
    resp = requests.get(NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS, timeout=10)
    resp.raise_for_status()
    results = resp.json()

    if not results:
        raise ValueError(
            f"Could not find location: '{location_name}'. "
            "Please use a format like 'City, State' (e.g., 'Chicago, IL')."
        )

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    logger.info(f"Geocoded '{location_name}' -> lon={lon:.4f}, lat={lat:.4f}")

    cache.set(cache_key, (lon, lat), timeout=86400)  # Cache 24h
    return lon, lat


def get_route(start_location: str, end_location: str) -> dict:
    """
    Get driving route between two US locations using OSRM (free, no API key).

    API calls made:
      - 2x Nominatim (geocode start + end) — cached 24h
      - 1x OSRM route — cached 1h

    Returns a dict with:
        - distance_miles: total route distance
        - duration_seconds: estimated drive time
        - geometry: GeoJSON LineString of the full route
        - waypoints: list of [lon, lat] route coordinate points
        - bbox: [min_lon, min_lat, max_lon, max_lat]
        - start_coords: [lon, lat]
        - end_coords: [lon, lat]
    """
    # Check full route cache first
    route_cache_key = (
        f"osrm_route_v2_{start_location}_{end_location}"
        .replace(" ", "_").lower()
    )
    cached_route = cache.get(route_cache_key)
    if cached_route:
        logger.info(f"Route cache hit: '{start_location}' -> '{end_location}'")
        return cached_route

    # Geocode both locations (each individually cached)
    start_lon, start_lat = get_coordinates_from_location(start_location)
    end_lon, end_lat = get_coordinates_from_location(end_location)

    logger.info(
        f"Fetching OSRM route: ({start_lat:.4f},{start_lon:.4f}) "
        f"-> ({end_lat:.4f},{end_lon:.4f})"
    )

    # Single OSRM route call with full geometry
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
    )
    params = {
        "overview": "full",       # Full route geometry (not simplified)
        "geometries": "geojson",  # GeoJSON format
        "steps": "false",
        "annotations": "false",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(
            f"OSRM could not find a route between '{start_location}' and '{end_location}'"
        )

    route = data["routes"][0]
    distance_miles = route["distance"] * 0.000621371  # meters to miles
    duration_seconds = route["duration"]
    geometry = route["geometry"]  # GeoJSON LineString
    coordinates = geometry["coordinates"]  # list of [lon, lat]

    # Compute bounding box from route coordinates
    lons = [c[0] for c in coordinates]
    lats = [c[1] for c in coordinates]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    result = {
        "distance_miles": distance_miles,
        "duration_seconds": duration_seconds,
        "geometry": geometry,
        "waypoints": coordinates,
        "bbox": bbox,
        "start_coords": [start_lon, start_lat],
        "end_coords": [end_lon, end_lat],
        "start_location": start_location,
        "end_location": end_location,
    }

    cache.set(route_cache_key, result, timeout=3600)  # Cache 1h
    logger.info(
        f"OSRM route: {distance_miles:.1f} miles, "
        f"{duration_seconds/3600:.1f}h, {len(coordinates)} waypoints"
    )
    return result
