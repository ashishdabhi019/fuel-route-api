"""
Fuel optimization service — high-performance implementation.

Algorithm (optimized for speed):
1. Sample/thin the route waypoints (every Nth point) for fast spatial lookup
2. Build cumulative distance array along the route
3. For each station: use vectorized NumPy to find nearest route point in O(n) per station
   but over a much smaller simplified route
4. Apply greedy cheapest-in-range stop selection

Performance target: < 1 second for any US cross-country route.
"""
import logging
import math
from typing import Optional

import numpy as np
from django.conf import settings

from route.models import FuelStation

logger = logging.getLogger(__name__)

# Corridor: stations within this many degrees of route (1 deg ~ 69 miles at US latitudes)
CORRIDOR_DEG = 0.5   # ~35 miles each side
MAX_RANGE_MILES = settings.VEHICLE_MAX_RANGE_MILES  # 500
MPG = settings.VEHICLE_MPG  # 10
TANK_GALLONS = settings.TANK_SIZE_GALLONS  # 50

# Route simplification: sample every Nth waypoint for station projection
# At 34K points for NY→LA (2794 mi), every 50th point = 693 points (avg 4 mi spacing)
ROUTE_SAMPLE_RATE = 30  # Keep 1 in every 30 waypoints


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in miles (scalar)."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def simplify_route(waypoints: list[list[float]], sample_rate: int = ROUTE_SAMPLE_RATE) -> list[list[float]]:
    """
    Thin the route to every Nth point to speed up spatial operations.
    Always includes the first and last point.
    """
    if len(waypoints) <= sample_rate * 2:
        return waypoints
    indices = list(range(0, len(waypoints), sample_rate))
    if indices[-1] != len(waypoints) - 1:
        indices.append(len(waypoints) - 1)
    return [waypoints[i] for i in indices]


def build_cumulative_distances(waypoints: list[list[float]]) -> np.ndarray:
    """
    Build cumulative distance array (miles from route start) for each waypoint.
    Returns numpy array of shape (N,).
    """
    n = len(waypoints)
    cumulative = np.zeros(n)
    for i in range(1, n):
        a, b = waypoints[i - 1], waypoints[i]
        cumulative[i] = cumulative[i - 1] + haversine_miles(a[0], a[1], b[0], b[1])
    return cumulative


def project_stations_fast(
    simplified_waypoints: list[list[float]],
    cumulative: np.ndarray,
    stations: list[dict],
    total_route_miles: float,
) -> list[dict]:
    """
    Vectorized projection of stations onto the route polyline.
    Uses NumPy for fast nearest-neighbor search.

    For each station, finds the closest waypoint (not exact segment projection,
    but close enough given the dense sampling) and assigns its route distance.

    Returns stations enriched with 'route_distance_miles' and 'perp_distance_miles',
    filtered to corridor and sorted by route_distance_miles.
    """
    if not stations or not simplified_waypoints:
        return []

    # Build numpy arrays for waypoints: shape (N, 2)
    wp = np.array([[w[0], w[1]] for w in simplified_waypoints])  # [lon, lat]
    wp_lons = wp[:, 0]
    wp_lats = wp[:, 1]

    enriched = []

    for station in stations:
        slat = station["latitude"]
        slon = station["longitude"]

        # Vectorized distance to all simplified waypoints (degrees, approximate)
        # Using squared Euclidean in lat/lon space (fast, sufficient for ~1-degree scale)
        dlat = wp_lats - slat
        dlon = (wp_lons - slon) * math.cos(math.radians(slat))  # Longitude correction
        dist_sq = dlat ** 2 + dlon ** 2
        nearest_idx = int(np.argmin(dist_sq))
        nearest_dist_deg = math.sqrt(float(dist_sq[nearest_idx]))

        # Convert perpendicular distance to miles (1 deg lat ≈ 69 miles)
        perp_miles = nearest_dist_deg * 69.0

        # Skip stations too far from route
        if perp_miles > 75:
            continue

        route_dist = float(cumulative[nearest_idx])

        enriched.append({
            **station,
            "route_distance_miles": route_dist,
            "perp_distance_miles": perp_miles,
        })

    return sorted(enriched, key=lambda x: x["route_distance_miles"])


def get_stations_near_bbox(bbox: list[float], buffer: float = CORRIDOR_DEG) -> list[dict]:
    """
    Fetch geocoded fuel stations from DB within the route bounding box + buffer.
    Returns list of station dicts.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    qs = FuelStation.objects.filter(
        geocoded=True,
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat - buffer,
        latitude__lte=max_lat + buffer,
        longitude__gte=min_lon - buffer,
        longitude__lte=max_lon + buffer,
    ).values(
        "id", "opis_id", "name", "address", "city", "state",
        "latitude", "longitude", "retail_price"
    )

    count = qs.count()
    logger.info(f"Found {count} stations in bounding box")
    return [
        {**s, "retail_price": float(s["retail_price"])}
        for s in qs
    ]


def select_cheapest_stops(
    corridor_stations: list[dict],
    total_route_miles: float,
) -> list[dict]:
    """
    Greedy algorithm to select minimum-cost fuel stops.

    Strategy:
    - Start at position 0 with a full tank (MAX_RANGE_MILES)
    - At each step, look ahead to the full reachable window
    - Among stations we can reach, pick the cheapest one that keeps us able
      to continue (i.e., from which we can reach either the destination or
      another station)
    - Fill up to full at each stop

    Returns ordered list of stop dicts.
    """
    if not corridor_stations:
        return []

    fuel_stops = []
    current_pos = 0.0
    fuel_remaining = MAX_RANGE_MILES  # start full

    while True:
        reachable_limit = current_pos + fuel_remaining

        # Are we done? Can we reach the destination?
        if reachable_limit >= total_route_miles:
            break

        # Candidates: stations reachable from current position
        candidates = [
            s for s in corridor_stations
            if current_pos < s["route_distance_miles"] <= reachable_limit
        ]

        if not candidates:
            # No stations in range — log warning and break
            logger.warning(
                f"Gap in coverage: no stations between {current_pos:.0f} "
                f"and {reachable_limit:.0f} miles. "
                f"Cannot complete route with current data."
            )
            break

        # From each candidate, check if it allows further progress
        valid_candidates = []
        for s in candidates:
            next_reachable = s["route_distance_miles"] + MAX_RANGE_MILES
            # Can reach destination directly?
            if next_reachable >= total_route_miles:
                valid_candidates.append(s)
                continue
            # Can reach at least one more station?
            has_next = any(
                s["route_distance_miles"] < ns["route_distance_miles"] <= next_reachable
                for ns in corridor_stations
            )
            if has_next:
                valid_candidates.append(s)

        if not valid_candidates:
            # Fallback: take any reachable station (best effort)
            valid_candidates = candidates
            logger.warning("No fully-valid candidates found, using best-effort selection")

        # Pick cheapest valid candidate
        best = min(valid_candidates, key=lambda x: x["retail_price"])

        # Fuel accounting
        miles_to_stop = best["route_distance_miles"] - current_pos
        fuel_used = miles_to_stop / MPG
        fuel_before_fillup = (fuel_remaining / MPG - fuel_used)  # gallons remaining
        gallons_to_fill = max(0, TANK_GALLONS - fuel_before_fillup)

        fuel_stops.append({
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
        })

        current_pos = best["route_distance_miles"]
        fuel_remaining = MAX_RANGE_MILES  # filled up to full

    return fuel_stops


def optimize_fuel_stops(
    waypoints: list[list[float]],
    bbox: list[float],
    total_route_miles: float,
) -> dict:
    """
    Main entry point for fuel stop optimization.

    Steps:
    1. Fetch candidate stations from DB (bbox query — fast)
    2. Simplify route for spatial matching
    3. Project stations onto route using NumPy vectorization
    4. Greedy cheapest-in-range stop selection

    Returns:
    {
        "fuel_stops": [...],
        "total_gallons": float,
        "total_cost_usd": float,
        "stations_considered": int,
    }
    """
    # 1. Get candidates from DB
    raw_stations = get_stations_near_bbox(bbox)
    if not raw_stations:
        logger.warning("No geocoded stations in bounding box")
        return {
            "fuel_stops": [],
            "total_gallons": round(total_route_miles / MPG, 2),
            "total_cost_usd": 0,
            "stations_considered": 0,
        }

    # 2. Simplify route for fast spatial operations
    simplified = simplify_route(waypoints)
    logger.info(
        f"Route simplified: {len(waypoints)} -> {len(simplified)} waypoints "
        f"(1 in {ROUTE_SAMPLE_RATE})"
    )

    # 3. Build cumulative distance array
    cumulative = build_cumulative_distances(simplified)

    # 4. Project stations onto simplified route
    corridor_stations = project_stations_fast(
        simplified, cumulative, raw_stations, total_route_miles
    )
    logger.info(f"Stations within corridor: {len(corridor_stations)}")

    if not corridor_stations:
        logger.warning("No stations found within route corridor after projection")
        return {
            "fuel_stops": [],
            "total_gallons": round(total_route_miles / MPG, 2),
            "total_cost_usd": 0,
            "stations_considered": 0,
        }

    # 5. Select optimal stops
    fuel_stops = select_cheapest_stops(corridor_stations, total_route_miles)

    total_gallons = total_route_miles / MPG
    total_cost = sum(s["cost_at_stop"] for s in fuel_stops)

    return {
        "fuel_stops": fuel_stops,
        "total_gallons": round(total_gallons, 2),
        "total_cost_usd": round(total_cost, 2),
        "stations_considered": len(corridor_stations),
    }
