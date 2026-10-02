import logging
import time

from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.routing import get_route
from .services.fuel_optimizer import optimize_fuel_stops, MAX_RANGE_MILES, MPG, TANK_GALLONS
from .services.map_builder import build_map_url, build_static_map_url

logger = logging.getLogger(__name__)


class FuelRouteView(APIView):
    """
    Returns an optimized fuel stop plan for a road trip between two US locations.
    Accepts both GET (query params) and POST (JSON body).
    """

    def get(self, request):
        return self._process(request, RouteRequestSerializer(data=request.query_params))

    def post(self, request):
        return self._process(request, RouteRequestSerializer(data=request.data))

    def _process(self, request, serializer: RouteRequestSerializer) -> Response:
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start = serializer.validated_data["start"]
        end = serializer.validated_data["end"]
        t0 = time.perf_counter()

        try:
            route = get_route(start, end)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Routing error")
            return Response({"error": "Routing service unavailable"}, status=status.HTTP_502_BAD_GATEWAY)

        try:
            result = optimize_fuel_stops(route["waypoints"], route["bbox"], route["distance_miles"])
        except Exception:
            logger.exception("Optimization error")
            return Response({"error": "Fuel optimization failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        fuel_stops    = result["fuel_stops"]
        total_gallons = result["total_gallons"]
        total_cost    = result["total_cost_usd"]
        total_detour  = result.get("total_detour_miles", 0)
        total_actual  = result.get("total_actual_miles", round(route["distance_miles"], 1))
        avg_price     = round(total_cost / total_gallons, 4) if total_gallons else 0

        return Response({
            "start_location": route["start_location"],
            "end_location": route["end_location"],
            # total_distance_miles = highway miles + all round-trip detours to stations
            "total_distance_miles": total_actual,
            "highway_distance_miles": round(route["distance_miles"], 1),
            "total_detour_miles": total_detour,
            "estimated_duration_hours": round(route["duration_seconds"] / 3600, 2),
            "total_gallons_needed": total_gallons,
            "total_fuel_cost_usd": total_cost,
            "average_price_per_gallon": avg_price,
            "fuel_stops_count": len(fuel_stops),
            "fuel_stops": fuel_stops,
            "interactive_map_url": request.build_absolute_uri(
                build_map_url(route["start_coords"], route["end_coords"], fuel_stops, route["geometry"], start=start, end=end)
            ),
            "static_map_url": build_static_map_url(route["bbox"]),
            "route_geometry": route["geometry"],
            "_meta": {
                "processing_time_seconds": round(time.perf_counter() - t0, 3),
                "stations_considered": result["stations_considered"],
                "vehicle_mpg": MPG,
                "vehicle_max_range_miles": MAX_RANGE_MILES,
                "tank_size_gallons": TANK_GALLONS,
            },
        })


class MapView(TemplateView):
    """Serves the interactive Leaflet map page at /map/?start=...&end=..."""
    template_name = "route/map.html"
