"""
Serializers for the route API.
"""
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """Input serializer for the fuel route API."""
    start = serializers.CharField(
        max_length=200,
        help_text="Starting location within the USA (e.g., 'New York, NY' or 'Los Angeles, CA')",
    )
    end = serializers.CharField(
        max_length=200,
        help_text="Destination location within the USA (e.g., 'Chicago, IL')",
    )

    def validate_start(self, value):
        return value.strip()

    def validate_end(self, value):
        return value.strip()


class FuelStopSerializer(serializers.Serializer):
    """Serializer for a single fuel stop result."""
    station_id = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    retail_price_per_gallon = serializers.FloatField()
    gallons_to_fill = serializers.FloatField()
    cost_at_stop = serializers.FloatField()
    route_distance_miles = serializers.FloatField(
        help_text="Distance from route start to this stop (miles)"
    )
    miles_off_route = serializers.FloatField(
        help_text="How far the station is from the route centerline (miles)"
    )


class RouteResponseSerializer(serializers.Serializer):
    """Output serializer for the fuel route API."""
    start_location = serializers.CharField()
    end_location = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    estimated_duration_hours = serializers.FloatField()

    # Fuel summary
    total_gallons_needed = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    average_price_per_gallon = serializers.FloatField()
    fuel_stops_count = serializers.IntegerField()
    fuel_stops = FuelStopSerializer(many=True)

    # Map
    interactive_map_url = serializers.URLField(
        help_text="Open this URL in a browser to view an interactive map of the route"
    )
    static_map_url = serializers.URLField(
        help_text="OpenStreetMap view of the route bounding box"
    )

    # Route geometry
    route_geometry = serializers.DictField(
        help_text="GeoJSON LineString geometry of the full route"
    )
