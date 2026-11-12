"""DRF views for stapel-inventory — thin over ``services``.

Every view declares its guest stance. The availability read is public (a
storefront renders it for visitors) and discloses only what
``PUBLIC_AVAILABILITY`` allows; everything else is staff-only and refuses
guests.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from stapel_core.django.api.errors import StapelErrorResponse
from stapel_core.django.api.permissions import ANONYMOUS_ALLOWED, ANONYMOUS_DENIED, IsStaffUser
from stapel_core.django.api.views import StapelAPIView
from stapel_core.flows import flow_step

from .errors import ERR_400_TOO_MANY_KEYS, ERR_404_STOCK_ITEM_NOT_FOUND
from .flows import SHOP, STOCK
from .models import AlertState, MovementKind, StockItem, StockMovement
from .pagination import MovementAnchorPagination, StockAnchorPagination
from .presenters import get_availability_presenter, get_stock_presenter
from .serializers import (
    AvailabilityResponseSerializer,
    LocationRequestSerializer,
    LocationResponseSerializer,
    LocationUpdateRequestSerializer,
    StockAdjustRequestSerializer,
    StockItemPageSerializer,
    StockItemResponseSerializer,
    StockItemUpdateRequestSerializer,
    StockMovementPageSerializer,
    StockMovementResponseSerializer,
    StockReceiveRequestSerializer,
    StockTransferRequestSerializer,
    StockTransferResponseSerializer,
)
from .services import InventoryError
from .services import stock as stock_service

TAGS = ["Inventory"]


class _InventoryView(StapelAPIView):
    """Renders a service refusal as the fleet error envelope."""

    def handle_exception(self, exc):
        if isinstance(exc, InventoryError):
            return StapelErrorResponse(exc.status, exc.key, exc.params)
        return super().handle_exception(exc)

    def _actor(self, request) -> str:
        return str(request.user.pk)

    def _item_response(self, item, *, status=200):
        presenter = get_stock_presenter()()
        return self.serialized_response(presenter.items([item])[0], status=status)


class _StaffView(_InventoryView):
    permission_classes = [IsStaffUser]
    stapel_anonymous_access = ANONYMOUS_DENIED


def _anchor_parameters(paginator):
    return [
        OpenApiParameter("anchor", str, OpenApiParameter.QUERY, required=False,
                         description="Opaque anchor from next_anchor/prev_anchor (exclusive)."),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False,
                         description=f"Page size, default {paginator.page_size}, "
                                     f"max {paginator.max_page_size}."),
        OpenApiParameter("direction", str, OpenApiParameter.QUERY, required=False,
                         enum=["next", "prev"], description="Walk direction from the anchor."),
    ]


PAIR_FILTERS = [
    OpenApiParameter("purchasable_type", str, OpenApiParameter.QUERY, required=False),
    OpenApiParameter("purchasable_key", str, OpenApiParameter.QUERY, required=False),
]


@extend_schema(tags=TAGS)
class AvailabilityView(_InventoryView):
    """Availability of a batch of keys of one purchasable type, as the public may see it."""

    permission_classes = [permissions.AllowAny]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = AvailabilityResponseSerializer

    @extend_schema(
        operation_id="inventory_availability_retrieve",
        parameters=[
            OpenApiParameter("purchasable_type", str, OpenApiParameter.QUERY, required=True),
            OpenApiParameter("keys", str, OpenApiParameter.QUERY, required=True, many=True,
                             description="Purchasable keys (repeatable)."),
        ],
        responses={200: AvailabilityResponseSerializer},
    )
    @flow_step(SHOP, order=1, note="The page asks whether its items are in stock")
    def get(self, request):
        from rest_framework import serializers

        from .conf import inventory_settings

        purchasable_type = serializers.CharField(max_length=32).run_validation(
            request.query_params.get("purchasable_type")
        )
        keys = [k for k in request.query_params.getlist("keys") if k]
        limit = int(inventory_settings.MAX_AVAILABILITY_KEYS)
        if len(keys) > limit:
            raise InventoryError(400, ERR_400_TOO_MANY_KEYS, {"max": limit})
        keys = [serializers.CharField(max_length=255).run_validation(k) for k in keys]
        entries = stock_service.availability(purchasable_type, keys)
        dto = get_availability_presenter()().present(purchasable_type, entries)
        return self.serialized_response(dto)


@extend_schema(tags=TAGS)
class LocationListView(_StaffView):
    request_serializer_class = LocationRequestSerializer
    response_serializer_class = LocationResponseSerializer

    @extend_schema(operation_id="inventory_locations_list",
                   responses={200: LocationResponseSerializer(many=True)})
    @flow_step(STOCK, order=1, note="See the locations stock sits in")
    def get(self, request):
        presenter = get_stock_presenter()()
        data = [presenter.location(loc) for loc in stock_service.list_locations()]
        return self.serialized_response(data, many=True)

    @extend_schema(operation_id="inventory_locations_create", request=LocationRequestSerializer,
                   responses={201: LocationResponseSerializer})
    @flow_step(STOCK, order=2, note="Add a warehouse or a shop floor")
    def post(self, request):
        location = stock_service.create_location(self.validated_request_data(request))
        return self.serialized_response(get_stock_presenter()().location(location), status=201)


@extend_schema(tags=TAGS)
class LocationDetailView(_StaffView):
    request_serializer_class = LocationUpdateRequestSerializer
    response_serializer_class = LocationResponseSerializer

    @extend_schema(operation_id="inventory_locations_partial_update",
                   request=LocationUpdateRequestSerializer,
                   responses={200: LocationResponseSerializer})
    @flow_step(STOCK, order=2, note="Rename, reprioritise, switch off or make a location the default")
    def patch(self, request, code):
        data = self.validated_request_data(request)
        location = stock_service.update_location(code, data)
        return self.serialized_response(get_stock_presenter()().location(location))


@extend_schema(tags=TAGS)
class StockListView(_StaffView):
    pagination_class = StockAnchorPagination
    response_serializer_class = StockItemResponseSerializer

    @extend_schema(
        operation_id="inventory_stock_list",
        parameters=[
            *PAIR_FILTERS,
            OpenApiParameter("location", str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("alert_state", str, OpenApiParameter.QUERY, required=False,
                             enum=[v for v, _ in AlertState.choices]),
            *_anchor_parameters(StockAnchorPagination),
        ],
        responses={200: StockItemPageSerializer},
    )
    @flow_step(STOCK, order=3, note="Walk the stock list, filter what runs low")
    def get(self, request):
        qs = StockItem.objects.select_related("location")
        params = request.query_params
        for name, lookup in (("purchasable_type", "purchasable_type"),
                             ("purchasable_key", "purchasable_key"),
                             ("location", "location__code"),
                             ("alert_state", "alert_state")):
            if params.get(name):
                qs = qs.filter(**{lookup: params[name]})
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = StockItemResponseSerializer(get_stock_presenter()().items(page), many=True).data
        return paginator.get_paginated_response(data)


@extend_schema(tags=TAGS)
class StockDetailView(_StaffView):
    request_serializer_class = StockItemUpdateRequestSerializer
    response_serializer_class = StockItemResponseSerializer

    @extend_schema(operation_id="inventory_stock_partial_update",
                   request=StockItemUpdateRequestSerializer,
                   responses={200: StockItemResponseSerializer})
    @flow_step(STOCK, order=4, note="Set a low-stock threshold or the oversell policy of one item")
    def patch(self, request, item_id):
        data = self.validated_request_data(request)
        return self._item_response(stock_service.update_item(item_id, data))


@extend_schema(tags=TAGS)
class StockReceiveView(_StaffView):
    request_serializer_class = StockReceiveRequestSerializer
    response_serializer_class = StockItemResponseSerializer

    @extend_schema(operation_id="inventory_stock_receive", request=StockReceiveRequestSerializer,
                   responses={200: StockItemResponseSerializer})
    @flow_step(STOCK, order=2, note="Receive a delivery")
    def post(self, request):
        d = self.validated_request_data(request)
        item = stock_service.receive(d["purchasable_type"], d["purchasable_key"], d.get("location"),
                                     d["quantity"], reason=d.get("reason", ""), actor_id=self._actor(request))
        return self._item_response(item)


@extend_schema(tags=TAGS)
class StockAdjustView(_StaffView):
    request_serializer_class = StockAdjustRequestSerializer
    response_serializer_class = StockItemResponseSerializer

    @extend_schema(operation_id="inventory_stock_adjust", request=StockAdjustRequestSerializer,
                   responses={200: StockItemResponseSerializer})
    @flow_step(STOCK, order=3, note="Correct a count, with a reason")
    def post(self, request):
        d = self.validated_request_data(request)
        item = stock_service.adjust(d["purchasable_type"], d["purchasable_key"], d.get("location"),
                                    delta=d.get("delta"), on_hand=d.get("on_hand"), reason=d["reason"],
                                    actor_id=self._actor(request))
        return self._item_response(item)


@extend_schema(tags=TAGS)
class StockTransferView(_StaffView):
    request_serializer_class = StockTransferRequestSerializer
    response_serializer_class = StockTransferResponseSerializer

    @extend_schema(operation_id="inventory_stock_transfer", request=StockTransferRequestSerializer,
                   responses={200: StockTransferResponseSerializer})
    @flow_step(STOCK, order=4, note="Move units between locations")
    def post(self, request):
        d = self.validated_request_data(request)
        result = stock_service.transfer(d["purchasable_type"], d["purchasable_key"], d["from_location"],
                                        d["to_location"], d["quantity"], reason=d.get("reason", ""),
                                        actor_id=self._actor(request))
        return self.serialized_response(get_stock_presenter()().transfer(result))


@extend_schema(tags=TAGS)
class MovementListView(_StaffView):
    pagination_class = MovementAnchorPagination
    response_serializer_class = StockMovementResponseSerializer

    @extend_schema(
        operation_id="inventory_movements_list",
        parameters=[
            OpenApiParameter("stock_item", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False),
            *PAIR_FILTERS,
            OpenApiParameter("kind", str, OpenApiParameter.QUERY, required=False,
                             enum=[v for v, _ in MovementKind.choices]),
            *_anchor_parameters(MovementAnchorPagination),
        ],
        responses={200: StockMovementPageSerializer},
    )
    @flow_step(STOCK, order=5, note="Read the ledger behind a count")
    def get(self, request):
        qs = StockMovement.objects.select_related("stock_item__location")
        params = request.query_params
        if params.get("stock_item"):
            if not params["stock_item"].isdigit():
                raise InventoryError(404, ERR_404_STOCK_ITEM_NOT_FOUND)
            qs = qs.filter(stock_item_id=int(params["stock_item"]))
        for name, lookup in (("purchasable_type", "stock_item__purchasable_type"),
                             ("purchasable_key", "stock_item__purchasable_key"),
                             ("kind", "kind")):
            if params.get(name):
                qs = qs.filter(**{lookup: params[name]})
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        presenter = get_stock_presenter()()
        data = StockMovementResponseSerializer([presenter.movement(m) for m in page], many=True).data
        return paginator.get_paginated_response(data)
