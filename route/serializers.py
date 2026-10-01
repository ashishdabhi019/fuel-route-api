from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200)
    end = serializers.CharField(max_length=200)

    def validate_start(self, value):
        return value.strip()

    def validate_end(self, value):
        return value.strip()


class FuelStopSerializer(serializers.Serializer):
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
    route_distance_miles = serializers.FloatField()
    miles_off_route = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    start_location = serializers.CharField()
    end_location = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    estimated_duration_hours = serializers.FloatField()
    total_gallons_needed = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    average_price_per_gallon = serializers.FloatField()
    fuel_stops_count = serializers.IntegerField()
    fuel_stops = FuelStopSerializer(many=True)
    interactive_map_url = serializers.URLField()
    static_map_url = serializers.URLField()
    route_geometry = serializers.DictField()
