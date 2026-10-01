"""
Map service: Generates an interactive map URL or embed for the route.
Uses OpenStreetMap + a static map tile approach.
"""
import urllib.parse
import json


def build_map_url(
    start_coords: list[float],
    end_coords: list[float],
    fuel_stops: list[dict],
    route_geometry: dict,
) -> str:
    """
    Build a geojson.io URL that visualizes the route and fuel stops interactively.
    This is a free, no-auth-required map viewer based on OpenStreetMap.

    Returns a URL that can be opened in a browser.
    """
    features = []

    # Route line
    features.append({
        "type": "Feature",
        "geometry": route_geometry,
        "properties": {
            "stroke": "#4a90d9",
            "stroke-width": 4,
            "stroke-opacity": 0.8,
            "name": "Route",
        },
    })

    # Start marker
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": start_coords},
        "properties": {
            "marker-color": "#2ecc71",
            "marker-size": "large",
            "marker-symbol": "circle",
            "name": "Start",
        },
    })

    # End marker
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": end_coords},
        "properties": {
            "marker-color": "#e74c3c",
            "marker-size": "large",
            "marker-symbol": "circle",
            "name": "End",
        },
    })

    # Fuel stop markers
    for i, stop in enumerate(fuel_stops, 1):
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [stop["longitude"], stop["latitude"]],
            },
            "properties": {
                "marker-color": "#f39c12",
                "marker-size": "medium",
                "marker-symbol": "fuel",
                "name": f"Stop {i}: {stop['name']}",
                "price": f"${stop['retail_price_per_gallon']:.3f}/gal",
                "cost": f"${stop['cost_at_stop']:.2f}",
                "city": f"{stop['city']}, {stop['state']}",
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    geojson_str = json.dumps(geojson)

    # geojson.io supports data via URL hash (up to ~8KB)
    # For larger routes, we provide the raw GeoJSON directly
    encoded = urllib.parse.quote(geojson_str)
    url = f"https://geojson.io/#data=data:application/json,{encoded}"

    return url


def build_static_map_url(
    bbox: list[float],
    fuel_stops: list[dict],
) -> str:
    """
    Build a static OpenStreetMap URL centered on the route bounding box.
    Uses openstreetmap.org directly.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        f"https://www.openstreetmap.org/?bbox={min_lon},{min_lat},{max_lon},{max_lat}&layer=mapnik"
    )
