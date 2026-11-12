"""Canonical-prefix URLconf for contract emission: the documented host mount."""
from django.urls import include, path

urlpatterns = [
    path("inventory/api/", include("stapel_inventory.urls")),
]
