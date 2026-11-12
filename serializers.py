"""Serializers for the stapel-inventory API.

The class names are the codegen contract: drf-spectacular names each
component after its serializer minus the ``Serializer`` suffix, and the
frontend pair generates its client from those names. Every CharField
declares a ``max_length``; quantities are bounded by ``MAX_LINE_QUANTITY``'s
column-safe ceiling.
"""
from rest_framework import serializers
from stapel_core.django.api.errors import StapelValidationError

from . import models as m
from .errors import ERR_400_ADJUST_AMBIGUOUS

#: A request never moves more than this many units (well inside IntegerField).
QUANTITY_CEILING = 1_000_000_000

AVAILABILITY_STATES = ("in_stock", "few_left", "out_of_stock", "backorder", "untracked")


def _quantity(**kwargs):
    return serializers.IntegerField(min_value=1, max_value=QUANTITY_CEILING, **kwargs)


def _reason(required=False):
    kwargs = {"required": required, "allow_blank": not required}
    if not required:
        kwargs["default"] = ""
    return serializers.CharField(max_length=m.REASON_MAX_LENGTH, **kwargs)


class PurchasableFields(serializers.Serializer):
    purchasable_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH)
    purchasable_key = serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH)


# ── Public availability ───────────────────────────────────────────────────


class AvailabilityItemSerializer(serializers.Serializer):
    purchasable_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH)
    purchasable_key = serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH)
    state = serializers.ChoiceField(choices=[(s, s) for s in AVAILABILITY_STATES])
    quantity = serializers.IntegerField(
        min_value=0, allow_null=True,
        help_text="Units available; only under PUBLIC_AVAILABILITY='exact', else null.",
    )


class AvailabilityResponseSerializer(serializers.Serializer):
    items = AvailabilityItemSerializer(many=True)


# ── Locations ─────────────────────────────────────────────────────────────


class LocationResponseSerializer(serializers.Serializer):
    code = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    name = serializers.CharField(max_length=m.LOCATION_NAME_MAX_LENGTH)
    priority = serializers.IntegerField()
    is_default = serializers.BooleanField()
    active = serializers.BooleanField()


class LocationRequestSerializer(serializers.Serializer):
    code = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    name = serializers.CharField(max_length=m.LOCATION_NAME_MAX_LENGTH)
    priority = serializers.IntegerField(required=False, default=100, min_value=-10000, max_value=10000)
    is_default = serializers.BooleanField(required=False, default=False)
    active = serializers.BooleanField(required=False, default=True)


class LocationUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=m.LOCATION_NAME_MAX_LENGTH, required=False)
    priority = serializers.IntegerField(required=False, min_value=-10000, max_value=10000)
    is_default = serializers.BooleanField(required=False)
    active = serializers.BooleanField(required=False)


# ── Stock ─────────────────────────────────────────────────────────────────


class StockItemResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    purchasable_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH)
    purchasable_key = serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH)
    location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    on_hand = serializers.IntegerField()
    held = serializers.IntegerField(min_value=0)
    available = serializers.IntegerField()
    low_stock_threshold = serializers.IntegerField(min_value=0, allow_null=True)
    oversell_policy = serializers.ChoiceField(choices=m.OversellPolicy.choices, allow_blank=True)
    alert_state = serializers.ChoiceField(choices=m.AlertState.choices)
    updated_at = serializers.DateTimeField()


class StockItemPageSerializer(serializers.Serializer):
    """The anchor envelope of the stock list (schema only — the paginator builds it)."""

    items = StockItemResponseSerializer(many=True)
    next_anchor = serializers.CharField(max_length=64, allow_null=True)
    prev_anchor = serializers.CharField(max_length=64, allow_null=True)
    has_next = serializers.BooleanField()
    has_prev = serializers.BooleanField()
    count = serializers.IntegerField()


class StockItemUpdateRequestSerializer(serializers.Serializer):
    low_stock_threshold = serializers.IntegerField(min_value=0, max_value=QUANTITY_CEILING,
                                                   allow_null=True, required=False)
    oversell_policy = serializers.ChoiceField(choices=m.OversellPolicy.choices, allow_blank=True,
                                              required=False)


class StockReceiveRequestSerializer(PurchasableFields):
    location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH, required=False,
                                     allow_blank=True, default="")
    quantity = _quantity()
    reason = _reason()


class StockAdjustRequestSerializer(PurchasableFields):
    location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH, required=False,
                                     allow_blank=True, default="")
    delta = serializers.IntegerField(required=False, min_value=-QUANTITY_CEILING,
                                     max_value=QUANTITY_CEILING,
                                     help_text="Signed change; give this or on_hand.")
    on_hand = serializers.IntegerField(required=False, min_value=0, max_value=QUANTITY_CEILING,
                                       help_text="The counted quantity; give this or delta.")
    reason = _reason(required=True)

    def validate(self, attrs):
        if ("delta" in attrs) == ("on_hand" in attrs):
            raise StapelValidationError(ERR_400_ADJUST_AMBIGUOUS)
        return attrs


class StockTransferRequestSerializer(PurchasableFields):
    from_location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    to_location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    quantity = _quantity()
    reason = _reason()


class StockTransferResponseSerializer(serializers.Serializer):
    source = StockItemResponseSerializer()
    target = StockItemResponseSerializer()


# ── Ledger ────────────────────────────────────────────────────────────────


class StockMovementResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    stock_item = serializers.IntegerField()
    purchasable_type = serializers.CharField(max_length=m.PURCHASABLE_TYPE_MAX_LENGTH)
    purchasable_key = serializers.CharField(max_length=m.PURCHASABLE_KEY_MAX_LENGTH)
    location = serializers.SlugField(max_length=m.LOCATION_CODE_MAX_LENGTH)
    kind = serializers.ChoiceField(choices=m.MovementKind.choices)
    on_hand_delta = serializers.IntegerField()
    held_delta = serializers.IntegerField()
    on_hand_after = serializers.IntegerField()
    reason = serializers.CharField(max_length=m.REASON_MAX_LENGTH, allow_blank=True)
    ref_type = serializers.CharField(max_length=m.REF_TYPE_MAX_LENGTH, allow_blank=True)
    ref_key = serializers.CharField(max_length=m.REF_KEY_MAX_LENGTH, allow_blank=True)
    actor_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH)
    created_at = serializers.DateTimeField()


class StockMovementPageSerializer(serializers.Serializer):
    """The anchor envelope of the ledger (schema only — the paginator builds it)."""

    items = StockMovementResponseSerializer(many=True)
    next_anchor = serializers.CharField(max_length=64, allow_null=True)
    prev_anchor = serializers.CharField(max_length=64, allow_null=True)
    has_next = serializers.BooleanField()
    has_prev = serializers.BooleanField()
    count = serializers.IntegerField()
