"""URL routes for the route app."""
from django.urls import path
from .views import FuelRouteView, MapView

urlpatterns = [
    path("route/", FuelRouteView.as_view(), name="fuel-route"),
    path("map/", MapView.as_view(), name="fuel-map"),
]
