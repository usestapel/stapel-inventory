"""i18n error keys of stapel-inventory.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses. The checkout hook
refusals (``insufficient_stock``, ``stock_not_tracked``) ride the orders
checkout refusal as its code, so the shopper sees these keys too.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_TOO_MANY_KEYS = "error.400.availability_too_many_keys"
ERR_400_ADJUST_AMBIGUOUS = "error.400.stock_adjust_ambiguous"
ERR_400_TRANSFER_SAME_LOCATION = "error.400.stock_transfer_same_location"
ERR_404_LOCATION_NOT_FOUND = "error.404.location_not_found"
ERR_404_STOCK_ITEM_NOT_FOUND = "error.404.stock_item_not_found"
ERR_409_INSUFFICIENT_STOCK = "error.409.insufficient_stock"
ERR_409_STOCK_NOT_TRACKED = "error.409.stock_not_tracked"
ERR_409_LOCATION_INACTIVE = "error.409.location_inactive"
ERR_409_LOCATION_EXISTS = "error.409.location_exists"
ERR_409_DEFAULT_LOCATION_REQUIRED = "error.409.default_location_required"
ERR_409_STOCK_WOULD_GO_NEGATIVE = "error.409.stock_would_go_negative"

STAPEL_INVENTORY_ERRORS = {
    ERR_400_TOO_MANY_KEYS: "Ask for at most {max} items at once",
    ERR_400_ADJUST_AMBIGUOUS: "Give either a change or a counted quantity, not both",
    ERR_400_TRANSFER_SAME_LOCATION: "Pick two different locations",
    ERR_404_LOCATION_NOT_FOUND: "Location not found",
    ERR_404_STOCK_ITEM_NOT_FOUND: "Stock item not found",
    ERR_409_INSUFFICIENT_STOCK: "Only {available} left of this item",
    ERR_409_STOCK_NOT_TRACKED: "This item cannot be sold right now",
    ERR_409_LOCATION_INACTIVE: "This location is not active",
    ERR_409_LOCATION_EXISTS: "A location with this code already exists",
    ERR_409_DEFAULT_LOCATION_REQUIRED: "The default location cannot be switched off",
    ERR_409_STOCK_WOULD_GO_NEGATIVE: "Only {on_hand} on hand at this location",
}

#: Explicit remediation where the status heuristic would mislead.
STAPEL_INVENTORY_REMEDIATION = {
    ERR_409_INSUFFICIENT_STOCK: "retry",
}

register_service_errors(STAPEL_INVENTORY_ERRORS, remediation=STAPEL_INVENTORY_REMEDIATION)

__all__ = ["STAPEL_INVENTORY_ERRORS", *[k for k in dir() if k.startswith("ERR_")]]
