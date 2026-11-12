"""Root URLconf for stapel-inventory — v1 canon mount.

The host mounts the module under ``inventory/api/``; this module contributes
the ``v1/`` segment:

    path("inventory/api/", include("stapel_inventory.urls"))   # -> /inventory/api/v1/...
"""
from django.urls import include, path

from stapel_inventory.urls_v1 import GATE_REGISTRY  # noqa: F401  (re-export)

urlpatterns = [
    path("v1/", include("stapel_inventory.urls_v1")),
]
