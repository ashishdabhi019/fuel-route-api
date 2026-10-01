import json
import urllib.parse


def build_map_url(
    start_coords: list[float],
    end_coords: list[float],
    fuel_stops: list[dict],
    route_geometry: dict,
) -> str:
    """Build a geojson.io URL showing the route line and all fuel stop markers."""
    features = [
        {
            "type": "Feature",
            "geometry": route_geometry,
            "properties": {"stroke": "#4a90d9", "stroke-width": 4, "stroke-opacity": 0.8, "name": "Route"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": start_coords},
            "properties": {"marker-color": "#2ecc71", "marker-size": "large", "name": "Start"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": end_coords},
            "properties": {"marker-color": "#e74c3c", "marker-size": "large", "name": "End"},
        },
    ]

    for i, stop in enumerate(fuel_stops, 1):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [stop["longitude"], stop["latitude"]]},
            "properties": {
                "marker-color": "#f39c12",
                "marker-size": "medium",
                "marker-symbol": "fuel",
                "name": f"Stop {i}: {stop['name']}",
                "price": f"${stop['retail_price_per_gallon']:.3f}/gal",
                "cost": f"${stop['cost_at_stop']:.2f}",
                "location": f"{stop['city']}, {stop['state']}",
            },
        })

    geojson = json.dumps({"type": "FeatureCollection", "features": features})
    return f"https://geojson.io/#data=data:application/json,{urllib.parse.quote(geojson)}"


def build_static_map_url(bbox: list[float]) -> str:
    """Build an OpenStreetMap URL framed to the route bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"https://www.openstreetmap.org/?bbox={min_lon},{min_lat},{max_lon},{max_lat}&layer=mapnik"
