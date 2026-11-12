"""Admin for stapel-inventory.

The ledger and holds are read-only; ``on_hand`` moves only through the
services (receive / adjust / transfer, the checkout hooks), never a raw
field edit, so every unit has its movement.
"""
from django.contrib import admin

from .models import Location, StockHold, StockHoldLine, StockItem, StockMovement


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "priority", "is_default", "active")
    list_filter = ("active", "is_default")
    search_fields = ("code", "name")


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("purchasable_type", "purchasable_key", "location", "on_hand", "alert_state",
                    "low_stock_threshold", "oversell_policy")
    list_filter = ("location", "alert_state", "purchasable_type", "oversell_policy")
    search_fields = ("purchasable_key",)
    readonly_fields = ("purchasable_type", "purchasable_key", "location", "on_hand", "alert_state",
                       "created_at", "updated_at")

    def has_add_permission(self, request):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("id", "stock_item", "kind", "on_hand_delta", "held_delta", "on_hand_after",
                    "reason", "actor_id", "created_at")
    list_filter = ("kind",)
    search_fields = ("stock_item__purchasable_key", "ref_key", "reason")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StockHoldLineInline(admin.TabularInline):
    model = StockHoldLine
    extra = 0
    can_delete = False
    readonly_fields = ("stock_item", "line_index", "quantity", "backordered")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockHold)
class StockHoldAdmin(admin.ModelAdmin):
    list_display = ("token", "attempt_key", "status", "expires_at", "reference", "created_at")
    list_filter = ("status",)
    search_fields = ("attempt_key", "reference")
    readonly_fields = ("token", "attempt_key", "user_id", "status", "expires_at", "reference",
                       "release_reason", "created_at", "updated_at")
    inlines = [StockHoldLineInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
