"""v1 URL set for stapel-inventory (plain paths, no router)."""
from typing import NamedTuple

from django.urls import path

from .views import (
    AvailabilityView,
    LocationDetailView,
    LocationListView,
    MovementListView,
    StockAdjustView,
    StockDetailView,
    StockListView,
    StockReceiveView,
    StockTransferView,
)

urlpatterns = [
    path("availability/", AvailabilityView.as_view(), name="inventory-availability"),
    path("locations/", LocationListView.as_view(), name="inventory-locations"),
    path("locations/<slug:code>/", LocationDetailView.as_view(), name="inventory-location-detail"),
    path("stock/", StockListView.as_view(), name="inventory-stock"),
    path("stock/receive/", StockReceiveView.as_view(), name="inventory-stock-receive"),
    path("stock/adjust/", StockAdjustView.as_view(), name="inventory-stock-adjust"),
    path("stock/transfer/", StockTransferView.as_view(), name="inventory-stock-transfer"),
    path("stock/<int:item_id>/", StockDetailView.as_view(), name="inventory-stock-detail"),
    path("movements/", MovementListView.as_view(), name="inventory-movements"),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns."""

    name: str
    flags: tuple
    patterns: tuple


#: No axis unmounts an endpoint (every axis is behavioural), so the surface
#: is one always-on block.
GATE_REGISTRY: dict = {
    "inventory.api": GateEntry("inventory.api", (), tuple(urlpatterns)),
}
