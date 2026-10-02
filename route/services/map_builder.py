import urllib.parse


def build_map_url(
    start_coords: list[float],
    end_coords: list[float],
    fuel_stops: list[dict],
    route_geometry: dict,
    start: str = "",
    end: str = "",
) -> str:
    """Build a URL to the local interactive Leaflet map page."""
    params = urllib.parse.urlencode({"start": start, "end": end})
    return f"/api/map/?{params}"


def build_static_map_url(bbox: list[float]) -> str:
    """Build an OpenStreetMap URL framed to the route bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"https://www.openstreetmap.org/?bbox={min_lon},{min_lat},{max_lon},{max_lat}&layer=mapnik"
