"""Anchor paginators of the staff lists (``StockItemPage``, ``StockMovementPage``).

Both anchor on the id, declared and sent as an opaque **string** (the page
serializers say so); a malformed anchor reads as the head of the list, never
a 500.
"""
from __future__ import annotations

from stapel_core.django.api.pagination import AnchorPagination


class _IdAnchorPagination(AnchorPagination):
    page_size = 50
    max_page_size = 200
    anchor_field = "id"
    anchor_type = "string"

    def paginate_queryset(self, queryset, request, view=None):
        anchor = request.query_params.get(self.anchor_param)
        if anchor is not None and not anchor.isdigit():
            request = _without_anchor(request, self.anchor_param)
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        for key in ("next_anchor", "prev_anchor"):
            if response.data[key] is not None:
                response.data[key] = str(response.data[key])
        return response


class LocationAnchorPagination(_IdAnchorPagination):
    """Locations in creation order (``priority`` is a field the client may sort by)."""

    ordering = "id"


class StockAnchorPagination(_IdAnchorPagination):
    """Stock items in creation order."""

    ordering = "id"


class MovementAnchorPagination(_IdAnchorPagination):
    """The ledger, newest first."""

    ordering = "-id"


def _without_anchor(request, param):
    class _Request:
        def __init__(self, inner):
            self._inner = inner
            self.query_params = inner.query_params.copy()
            self.query_params.pop(param, None)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    return _Request(request)
