import logging
import math

import numpy as np
from django.conf import settings

from route.models import FuelStation

logger = logging.getLogger(__name__)

MAX_RANGE_MILES = settings.VEHICLE_MAX_RANGE_MILES
MPG = settings.VEHICLE_MPG
TANK_GALLONS = settings.TANK_SIZE_GALLONS
CORRIDOR_MILES = 75

# OSRM returns ~30 waypoints per mile for US highways. Sampling every 30th point
# gives us roughly one point per mile — accurate enough for station matching and
# about 30x faster than processing the full geometry.
ROUTE_SAMPLE_RATE = 30


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def simplify_route(waypoints: list[list[float]]) -> list[list[float]]:
    if len(waypoints) <= ROUTE_SAMPLE_RATE * 2:
        return waypoints
    indices = list(range(0, len(waypoints), ROUTE_SAMPLE_RATE))
    # Always include the last point so the cumulative distance reaches the true end
    if indices[-1] != len(waypoints) - 1:
        indices.append(len(waypoints) - 1)
    return [waypoints[i] for i in indices]


def build_cumulative_distances(waypoints: list[list[float]]) -> np.ndarray:
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
    For each station, find the nearest point on the simplified route and assign
    a route_distance_miles value. Stations beyond CORRIDOR_MILES are dropped.
    Returns stations sorted by position along the route.
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
        # Longitude degrees shrink as you move away from the equator, so we scale
        # by cos(lat) to make the east-west distances comparable to north-south ones.
        dlon = (wp_lons - slon) * math.cos(math.radians(slat))
        nearest_idx = int(np.argmin(dlat ** 2 + dlon ** 2))
        # Convert from degrees to miles (1 degree latitude ≈ 69 miles)
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
    min_lon, min_lat, max_lon, max_lat = bbox
    # Add a 0.5-degree buffer (~35 miles) so we don't miss stations right at the
    # edge of the bounding box, especially on diagonal routes.
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
    Greedy cheapest-in-range selection. At each step, pick the lowest-priced
    reachable station from which the journey can still continue, then fill to
    a full tank. Repeat until the destination is within range.

    The assignment specifies: total fuel cost = highway_distance / 10 MPG × price.
    Detour info is recorded per stop for reference but does not affect the
    fuel budget or reported totals.
    """
    if not stations:
        return []

    stops = []
    current_pos = 0.0
    # Track fuel consumed along the highway route only (ignoring detour miles)
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

        # Filter to stations we can actually continue from — i.e. the destination
        # or at least one more station is reachable after stopping there.
        # Without this, we might pick a cheap station that leaves us stranded.
        valid = [
            s for s in candidates
            if s["route_distance_miles"] + MAX_RANGE_MILES >= total_miles
            or any(
                s["route_distance_miles"] < ns["route_distance_miles"] <= s["route_distance_miles"] + MAX_RANGE_MILES
                for ns in stations
            )
        ]

        # Fall back to any candidate if the valid filter comes up empty (sparse coverage)
        best = min(valid or candidates, key=lambda x: x["retail_price"])

        # Highway miles driven since last stop (detour not counted against fuel budget)
        miles_to_stop = best["route_distance_miles"] - current_pos
        detour_miles = best["perp_distance_miles"] * 2

        # Gallons used on the highway leg only
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
        # Full tank after fill-up; reachable range resets to max
        fuel_remaining = MAX_RANGE_MILES

    return stops


def optimize_fuel_stops(waypoints: list[list[float]], bbox: list[float], total_miles: float) -> dict:
    raw_stations = get_stations_in_bbox(bbox)
    if not raw_stations:
        return {"fuel_stops": [], "total_gallons": round(total_miles / MPG, 2), "total_cost_usd": 0, "stations_considered": 0}

    simplified = simplify_route(waypoints)
    logger.info(f"Route: {len(waypoints)} -> {len(simplified)} waypoints")

    cumulative = build_cumulative_distances(simplified)
    corridor = project_stations_onto_route(simplified, cumulative, raw_stations)
    logger.info(f"Stations in corridor: {len(corridor)}")

    if not corridor:
        return {"fuel_stops": [], "total_gallons": round(total_miles / MPG, 2), "total_cost_usd": 0, "stations_considered": 0}

    fuel_stops = select_fuel_stops(corridor, total_miles)

    # Total gallons = highway distance / 10 MPG (as specified in the assessment)
    # Detour info is shown per stop for reference but not added to the total
    total_gallons = round(total_miles / MPG, 2)
    total_cost    = round(sum(s["cost_at_stop"] for s in fuel_stops), 2)

    return {
        "fuel_stops": fuel_stops,
        "total_gallons": total_gallons,
        "total_cost_usd": total_cost,
        "stations_considered": len(corridor),
    }
