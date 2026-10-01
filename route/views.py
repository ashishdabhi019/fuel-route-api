"""
Views for the Fuel Route API.

GET  /api/route/?start=New+York,+NY&end=Los+Angeles,+CA
POST /api/route/  {"start": "New York, NY", "end": "Los Angeles, CA"}
"""
import logging
import time

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.routing import get_route
from .services.fuel_optimizer import optimize_fuel_stops
from .services.map_builder import build_map_url, build_static_map_url

logger = logging.getLogger(__name__)


class FuelRouteView(APIView):
    """
    API endpoint to compute an optimized fuel route between two US locations.

    Accepts both GET (query params) and POST (JSON body).

    Parameters:
        start (str): Starting location (e.g., "New York, NY")
        end (str): Destination location (e.g., "Los Angeles, CA")

    Returns:
        - Optimal fuel stops along the route
        - Total fuel cost (assuming 10 MPG)
        - Interactive map URL
        - Full route GeoJSON geometry
    """

    def get(self, request):
        serializer = RouteRequestSerializer(data=request.query_params)
        return self._process(serializer)

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        return self._process(serializer)

    def _process(self, serializer: RouteRequestSerializer) -> Response:
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start = serializer.validated_data["start"]
        end = serializer.validated_data["end"]

        t0 = time.perf_counter()

        try:
            # Step 1: Get route geometry (1 API call, cached)
            logger.info(f"Processing route: '{start}' -> '{end}'")
            route_data = get_route(start, end)
        except ValueError as e:
            return Response(
                {"error": str(e), "hint": "Please provide a valid US city/state location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Routing API error")
            return Response(
                {"error": f"Routing service error: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        total_miles = route_data["distance_miles"]
        waypoints = route_data["waypoints"]
        bbox = route_data["bbox"]

        # Step 2: Optimize fuel stops (DB only, no external API calls)
        try:
            optimization = optimize_fuel_stops(waypoints, bbox, total_miles)
        except Exception as e:
            logger.exception("Fuel optimization error")
            return Response(
                {"error": f"Fuel optimization error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        fuel_stops = optimization["fuel_stops"]
        total_gallons = optimization["total_gallons"]
        total_cost = optimization["total_cost_usd"]

        avg_price = (total_cost / total_gallons) if total_gallons > 0 else 0

        # Step 3: Build map URLs (no API calls)
        map_url = build_map_url(
            route_data["start_coords"],
            route_data["end_coords"],
            fuel_stops,
            route_data["geometry"],
        )
        static_url = build_static_map_url(bbox, fuel_stops)

        elapsed = time.perf_counter() - t0
        logger.info(f"Request completed in {elapsed:.2f}s")

        response_data = {
            "start_location": route_data["start_location"],
            "end_location": route_data["end_location"],
            "total_distance_miles": round(total_miles, 1),
            "estimated_duration_hours": round(route_data["duration_seconds"] / 3600, 2),
            "total_gallons_needed": total_gallons,
            "total_fuel_cost_usd": total_cost,
            "average_price_per_gallon": round(avg_price, 4),
            "fuel_stops_count": len(fuel_stops),
            "fuel_stops": fuel_stops,
            "interactive_map_url": map_url,
            "static_map_url": static_url,
            "route_geometry": route_data["geometry"],
            # Meta
            "_meta": {
                "processing_time_seconds": round(elapsed, 3),
                "stations_considered": optimization["stations_considered"],
                "vehicle_mpg": 10,
                "vehicle_max_range_miles": 500,
                "tank_size_gallons": 50,
            },
        }

        return Response(response_data, status=status.HTTP_200_OK)
