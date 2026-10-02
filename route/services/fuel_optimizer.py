import logging
import math

import numpy as np
from django.conf import settings

from route.models import FuelStation

logger = logging.getLogger(__name__)

MAX_RANGE_MILES = settings.VEHICLE_MAX_RANGE_MILES
MPG = settings.VEHICLE_MPG
TANK_GALLONS = settings.TANK_SIZE_GALLONS

# Only look at stations within this lateral distance of the route
CORRIDOR_MILES = 75

# OSRM returns tens of thousands of coordinates for long trips.
# Sampling every 30th point gives roughly one point per mile, which
# is more than enough precision for matching stations to the route.
ROUTE_SAMPLE_RATE = 30


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculates the straight-line distance in miles between two GPS coordinates."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def simplify_route(waypoints: list[list[float]]) -> list[list[float]]:
    """Reduces the route to roughly one point per mile for faster processing."""
    if len(waypoints) <= ROUTE_SAMPLE_RATE * 2:
        return waypoints
    indices = list(range(0, len(waypoints), ROUTE_SAMPLE_RATE))
    # Make sure the final destination point is always included
    if indices[-1] != len(waypoints) - 1:
        indices.append(len(waypoints) - 1)
    return [waypoints[i] for i in indices]


def build_cumulative_distances(waypoints: list[list[float]]) -> np.ndarray:
    """Builds a running total of miles at each point along the route."""
    cumulative = np.zeros(len(waypoints))
    for i in range(1, len(waypoints)):
        a, b = waypoints[i - 1], waypoints[i]
        cumulative[i] = cumulative[i - 1] + haversine_miles(a[0], a[1], b[0], b[1])
    return cumulative


def project_stations_onto_route(
    waypoints: list[list[float]],
    cumulative: np.ndarray,
    stations: list[dict],
) -> list[dict]:
    """
    Finds the nearest route point for every station and records how far along
    the route that point is. Stations further than CORRIDOR_MILES from the
    route are dropped. Returns the list sorted by route position.
    """
    if not stations or not waypoints:
        return []

    wp = np.array(waypoints)
    wp_lons = wp[:, 0]
    wp_lats = wp[:, 1]
    enriched = []

    for station in stations:
        slat, slon = station["latitude"], station["longitude"]
        dlat = wp_lats - slat
        # Scale longitude by cos(lat) so east-west and north-south distances are comparable
        dlon = (wp_lons - slon) * math.cos(math.radians(slat))
        nearest_idx = int(np.argmin(dlat ** 2 + dlon ** 2))
        # One degree of latitude is about 69 miles
        perp_miles = math.sqrt(float(dlat[nearest_idx] ** 2 + dlon[nearest_idx] ** 2)) * 69.0

        if perp_miles > CORRIDOR_MILES:
            continue

        enriched.append({
            **station,
            "route_distance_miles": float(cumulative[nearest_idx]),
            "perp_distance_miles": perp_miles,
        })

    return sorted(enriched, key=lambda x: x["route_distance_miles"])


def get_stations_in_bbox(bbox: list[float]) -> list[dict]:
    """Pulls all geocoded stations from the database within the route bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    # Small buffer so we don't miss stations right on the edge of the bounding box
    buffer = 0.5
    qs = FuelStation.objects.filter(
        geocoded=True,
        latitude__gte=min_lat - buffer,
        latitude__lte=max_lat + buffer,
        longitude__gte=min_lon - buffer,
        longitude__lte=max_lon + buffer,
    ).values("id", "opis_id", "name", "address", "city", "state", "latitude", "longitude", "retail_price")

    logger.info(f"Stations in bbox: {qs.count()}")
    return [{**s, "retail_price": float(s["retail_price"])} for s in qs]


def select_fuel_stops(stations: list[dict], total_miles: float) -> list[dict]:
    """
    Greedy cheapest-in-range selection algorithm.

    Starting from mile 0, find every station reachable on the current tank,
    filter out any station that would leave us stranded with no way to continue,
    then pick the cheapest one and fill up to a full tank. Repeat until the
    destination is within range.

    Total cost is based on highway distance / 10 MPG as specified in the assessment.
    """
    if not stations:
        return []

    stops = []
    current_pos = 0.0
    fuel_remaining = MAX_RANGE_MILES

    while current_pos + fuel_remaining < total_miles:
        reachable_limit = current_pos + fuel_remaining

        candidates = [
            s for s in stations
            if current_pos < s["route_distance_miles"] <= reachable_limit
        ]

        if not candidates:
            logger.warning(f"No stations between {current_pos:.0f} and {reachable_limit:.0f} miles")
            break

        # Only pick a station if we can actually continue from it — either the
        # destination or at least one more station must be reachable from there.
        valid = [
            s for s in candidates
            if s["route_distance_miles"] + MAX_RANGE_MILES >= total_miles
            or any(
                s["route_distance_miles"] < ns["route_distance_miles"] <= s["route_distance_miles"] + MAX_RANGE_MILES
                for ns in stations
            )
        ]

        # If the valid filter is empty (very sparse coverage), fall back to all candidates
        best = min(valid or candidates, key=lambda x: x["retail_price"])

        miles_to_stop = best["route_distance_miles"] - current_pos
        detour_miles = best["perp_distance_miles"] * 2

        gallons_used = miles_to_stop / MPG
        gallons_remaining = (fuel_remaining / MPG) - gallons_used
        gallons_to_fill = max(0, TANK_GALLONS - gallons_remaining)

        stops.append({
            "station_id": best["id"],
            "opis_id": best["opis_id"],
            "name": best["name"],
            "address": best["address"],
            "city": best["city"],
            "state": best["state"],
            "latitude": best["latitude"],
            "longitude": best["longitude"],
            "retail_price_per_gallon": round(best["retail_price"], 5),
            "gallons_to_fill": round(gallons_to_fill, 3),
            "cost_at_stop": round(gallons_to_fill * best["retail_price"], 2),
            "route_distance_miles": round(best["route_distance_miles"], 1),
            "miles_off_route": round(best["perp_distance_miles"], 1),
            "detour_miles_roundtrip": round(detour_miles, 2),
        })

        current_pos = best["route_distance_miles"]
        fuel_remaining = MAX_RANGE_MILES

    return stops


def optimize_fuel_stops(waypoints: list[list[float]], bbox: list[float], total_miles: float) -> dict:
    """
    Main entry point. Fetches nearby stations, simplifies the route geometry,
    projects stations onto the route, runs the greedy selection, and returns
    the stops along with the total gallons and cost for the trip.
    """
    raw_stations = get_stations_in_bbox(bbox)
    if not raw_stations:
        return {"fuel_stops": [], "total_gallons": round(total_miles / MPG, 2), "total_cost_usd": 0, "stations_considered": 0}

    simplified = simplify_route(waypoints)
    logger.info(f"Route: {len(waypoints)} -> {len(simplified)} waypoints after sampling")

    cumulative = build_cumulative_distances(simplified)
    corridor = project_stations_onto_route(simplified, cumulative, raw_stations)
    logger.info(f"Stations in corridor: {len(corridor)}")

    if not corridor:
        return {"fuel_stops": [], "total_gallons": round(total_miles / MPG, 2), "total_cost_usd": 0, "stations_considered": 0}

    fuel_stops = select_fuel_stops(corridor, total_miles)

    # Total gallons = highway distance / 10 MPG, total cost = sum of what we paid at each stop
    total_gallons = round(total_miles / MPG, 2)
    total_cost = round(sum(s["cost_at_stop"] for s in fuel_stops), 2)

    return {
        "fuel_stops": fuel_stops,
        "total_gallons": total_gallons,
        "total_cost_usd": total_cost,
        "stations_considered": len(corridor),
    }
