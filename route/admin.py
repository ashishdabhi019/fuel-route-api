from django.contrib import admin
from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "state", "retail_price", "geocoded", "latitude", "longitude"]
    list_filter = ["state", "geocoded"]
    search_fields = ["name", "city", "address"]
    ordering = ["state", "city", "retail_price"]
    readonly_fields = ["geocoded"]

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("state", "retail_price")
