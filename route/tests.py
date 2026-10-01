"""
Tests for the Fuel Route API.
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from route.models import FuelStation
from route.services.fuel_optimizer import (
    haversine_miles,
    simplify_route,
    build_cumulative_distances,
    select_cheapest_stops,
    optimize_fuel_stops,
)


class HaversineTestCase(TestCase):
    """Test the haversine distance formula."""

    def test_new_york_to_los_angeles(self):
        """NY to LA should be approximately 2445 miles (straight line)."""
        dist = haversine_miles(-74.006, 40.7128, -118.2437, 34.0522)
        self.assertAlmostEqual(dist, 2445, delta=50)

    def test_same_point(self):
        """Distance from a point to itself should be 0."""
        dist = haversine_miles(-74.006, 40.7128, -74.006, 40.7128)
        self.assertAlmostEqual(dist, 0.0, places=5)

    def test_one_degree_latitude(self):
        """One degree of latitude should be approximately 69 miles."""
        dist = haversine_miles(-90.0, 40.0, -90.0, 41.0)
        self.assertAlmostEqual(dist, 69.0, delta=2)


class RouteSimplificationTestCase(TestCase):
    """Test route simplification."""

    def test_simplify_keeps_endpoints(self):
        waypoints = [[float(i), float(i)] for i in range(100)]
        simplified = simplify_route(waypoints, sample_rate=10)
        self.assertEqual(simplified[0], waypoints[0])
        self.assertEqual(simplified[-1], waypoints[-1])

    def test_simplify_reduces_points(self):
        waypoints = [[float(i), float(i)] for i in range(300)]
        simplified = simplify_route(waypoints, sample_rate=30)
        self.assertLess(len(simplified), len(waypoints))

    def test_simplify_short_route(self):
        """Short routes shouldn't be over-simplified."""
        waypoints = [[float(i), float(i)] for i in range(5)]
        simplified = simplify_route(waypoints, sample_rate=30)
        self.assertEqual(simplified, waypoints)


class GreedySelectionTestCase(TestCase):
    """Test the greedy fuel stop selection algorithm."""

    def _make_station(self, route_dist, price, name=None):
        return {
            "id": int(route_dist),
            "opis_id": int(route_dist),
            "name": name or f"Station at {route_dist}",
            "address": "Test Ave",
            "city": "Testville",
            "state": "TX",
            "latitude": 40.0,
            "longitude": -90.0,
            "retail_price": price,
            "route_distance_miles": route_dist,
            "perp_distance_miles": 1.0,
        }

    def test_no_stops_needed_for_short_route(self):
        """Route shorter than 500 miles needs no fuel stops."""
        stations = [self._make_station(200, 3.0), self._make_station(400, 3.0)]
        stops = select_cheapest_stops(stations, total_route_miles=400)
        self.assertEqual(len(stops), 0)

    def test_one_stop_needed(self):
        """600-mile route needs exactly one fuel stop."""
        stations = [
            self._make_station(200, 3.5),
            self._make_station(300, 2.9),  # Cheapest
            self._make_station(400, 3.2),
        ]
        stops = select_cheapest_stops(stations, total_route_miles=600)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["retail_price_per_gallon"], 2.9)

    def test_cheapest_station_selected(self):
        """Among valid stops, the cheapest should be selected."""
        stations = [
            self._make_station(100, 4.0),
            self._make_station(200, 2.5),  # Cheapest
            self._make_station(300, 3.8),
        ]
        stops = select_cheapest_stops(stations, total_route_miles=700)
        # With 500-mile range starting at 0, all stations are reachable from the start
        self.assertGreater(len(stops), 0)
        # First stop should be cheapest valid one
        self.assertLessEqual(stops[0]["retail_price_per_gallon"], 3.0)

    def test_cost_calculation(self):
        """Verify cost = gallons_to_fill * price."""
        stations = [self._make_station(300, 3.0, "Test Station")]
        stops = select_cheapest_stops(stations, total_route_miles=600)
        if stops:
            stop = stops[0]
            expected_cost = stop["gallons_to_fill"] * stop["retail_price_per_gallon"]
            self.assertAlmostEqual(stop["cost_at_stop"], round(expected_cost, 2), places=1)


class FuelStationModelTestCase(TestCase):
    """Test FuelStation model."""

    def setUp(self):
        FuelStation.objects.create(
            opis_id=1,
            name="Test Station",
            city="Dallas",
            state="TX",
            retail_price=2.999,
            latitude=32.7767,
            longitude=-96.797,
            geocoded=True,
        )

    def test_station_created(self):
        self.assertEqual(FuelStation.objects.count(), 1)
        station = FuelStation.objects.first()
        self.assertEqual(station.name, "Test Station")
        self.assertEqual(station.state, "TX")
        self.assertTrue(station.geocoded)

    def test_station_str(self):
        station = FuelStation.objects.first()
        self.assertIn("Test Station", str(station))
        self.assertIn("TX", str(station))


class FuelRouteAPITestCase(TestCase):
    """Integration tests for the route API endpoint."""

    def setUp(self):
        self.client = APIClient()
        # Create some test stations along a route
        stations_data = [
            {"opis_id": 1, "name": "Station A", "city": "City A", "state": "TX",
             "retail_price": 2.999, "latitude": 31.0, "longitude": -97.0, "geocoded": True},
            {"opis_id": 2, "name": "Station B", "city": "City B", "state": "TX",
             "retail_price": 3.099, "latitude": 32.0, "longitude": -97.5, "geocoded": True},
        ]
        for data in stations_data:
            FuelStation.objects.create(**data)

    def test_missing_parameters_returns_400(self):
        """Missing start/end should return 400."""
        resp = self.client.get("/api/route/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_end_returns_400(self):
        """Missing end should return 400."""
        resp = self.client.get("/api/route/?start=New York, NY")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_missing_parameters_returns_400(self):
        """POST with missing fields should return 400."""
        resp = self.client.post("/api/route/", {"start": "New York, NY"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("route.services.routing.get_route")
    def test_successful_route_response_structure(self, mock_get_route):
        """Test that a successful response has the expected structure."""
        mock_get_route.return_value = {
            "distance_miles": 300.0,
            "duration_seconds": 18000,
            "geometry": {"type": "LineString", "coordinates": [[-97.0, 30.0], [-98.0, 31.0]]},
            "waypoints": [[-97.0, 30.0], [-97.5, 30.5], [-98.0, 31.0]],
            "bbox": [-98.0, 30.0, -97.0, 31.0],
            "start_coords": [-97.0, 30.0],
            "end_coords": [-98.0, 31.0],
            "start_location": "Austin, TX",
            "end_location": "San Antonio, TX",
        }

        resp = self.client.get("/api/route/?start=Austin,+TX&end=San+Antonio,+TX")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        data = resp.json()
        # Check all required fields are present
        required_fields = [
            "start_location", "end_location", "total_distance_miles",
            "estimated_duration_hours", "total_gallons_needed", "total_fuel_cost_usd",
            "average_price_per_gallon", "fuel_stops_count", "fuel_stops",
            "interactive_map_url", "static_map_url", "route_geometry", "_meta",
        ]
        for field in required_fields:
            self.assertIn(field, data, f"Missing field: {field}")

        # Check types
        self.assertIsInstance(data["fuel_stops"], list)
        self.assertIsInstance(data["total_distance_miles"], (int, float))
        self.assertIsInstance(data["total_fuel_cost_usd"], (int, float))

    @patch("route.services.routing.get_route")
    def test_route_no_stops_for_short_distance(self, mock_get_route):
        """A short route (< 500 miles) should require no fuel stops."""
        mock_get_route.return_value = {
            "distance_miles": 200.0,
            "duration_seconds": 10800,
            "geometry": {"type": "LineString", "coordinates": [[-97.0, 30.0], [-98.0, 31.0]]},
            "waypoints": [[-97.0, 30.0], [-98.0, 31.0]],
            "bbox": [-98.0, 30.0, -97.0, 31.0],
            "start_coords": [-97.0, 30.0],
            "end_coords": [-98.0, 31.0],
            "start_location": "City A",
            "end_location": "City B",
        }

        resp = self.client.get("/api/route/?start=City+A&end=City+B")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["fuel_stops_count"], 0)
        self.assertEqual(data["total_fuel_cost_usd"], 0)

    @patch("route.services.routing.get_route")
    def test_post_request_works(self, mock_get_route):
        """POST request should work identically to GET."""
        mock_get_route.return_value = {
            "distance_miles": 200.0,
            "duration_seconds": 10800,
            "geometry": {"type": "LineString", "coordinates": [[-97.0, 30.0], [-98.0, 31.0]]},
            "waypoints": [[-97.0, 30.0], [-98.0, 31.0]],
            "bbox": [-98.0, 30.0, -97.0, 31.0],
            "start_coords": [-97.0, 30.0],
            "end_coords": [-98.0, 31.0],
            "start_location": "Austin, TX",
            "end_location": "Dallas, TX",
        }

        resp = self.client.post(
            "/api/route/",
            {"start": "Austin, TX", "end": "Dallas, TX"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
