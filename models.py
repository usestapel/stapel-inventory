"""Models of stapel-inventory.

A purchasable is the opaque pair ``(purchasable_type, purchasable_key)``
orders uses; nothing here has a foreign key into a sibling module. Users
are referenced by id strings (``str(user.pk)``), never foreign keys: an
erased row keeps its numbers under a pseudonymized id.

``StockItem.on_hand`` is a cache of the ``StockMovement`` ledger
(``on_hand = Σ on_hand_delta``); every write to it writes its movement in
the same transaction under the item's row lock. The ledger is append-only.
"""
from __future__ import annotations

import uuid

from django.db import models
from stapel_core.access import access

USER_ID_MAX_LENGTH = 64
PURCHASABLE_TYPE_MAX_LENGTH = 32
PURCHASABLE_KEY_MAX_LENGTH = 255
LOCATION_CODE_MAX_LENGTH = 32
LOCATION_NAME_MAX_LENGTH = 120
REASON_MAX_LENGTH = 200
REF_TYPE_MAX_LENGTH = 32
REF_KEY_MAX_LENGTH = 128
ATTEMPT_KEY_MAX_LENGTH = 128
REFERENCE_MAX_LENGTH = 64
RELEASE_REASON_MAX_LENGTH = 32

#: The actor id of movements no person caused (beat, checkout hooks).
SYSTEM_ACTOR = "system"


class OversellPolicy(models.TextChoices):
    INHERIT = "", "Inherit the setting"
    DENY = "deny", "Deny"
    BACKORDER = "backorder", "Backorder"


class AlertState(models.TextChoices):
    OK = "ok", "OK"
    LOW = "low", "Low"
    OUT = "out", "Out of stock"


class MovementKind(models.TextChoices):
    RECEIPT = "receipt", "Receipt"
    ADJUST = "adjust", "Adjustment"
    HOLD = "hold", "Hold"
    COMMIT = "commit", "Commit"
    RELEASE = "release", "Release"
    TRANSFER = "transfer", "Transfer"
    RESTOCK = "restock", "Restock"


class HoldStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMMITTED = "committed", "Committed"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"
    RETURNED = "returned", "Returned"


@access.ops
class Location(models.Model):
    """A place stock sits in. Exactly one is the default (partial unique)."""

    code = models.SlugField(max_length=LOCATION_CODE_MAX_LENGTH, unique=True)
    name = models.CharField(max_length=LOCATION_NAME_MAX_LENGTH)
    priority = models.IntegerField(default=100)
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="inventory_one_default_loc",
            ),
        ]

    def __str__(self) -> str:
        return self.code


@access.ops
class StockItem(models.Model):
    """Stock of one purchasable at one location; ``on_hand`` caches the ledger."""

    purchasable_type = models.CharField(max_length=PURCHASABLE_TYPE_MAX_LENGTH)
    purchasable_key = models.CharField(max_length=PURCHASABLE_KEY_MAX_LENGTH)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="items")
    on_hand = models.IntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(null=True, blank=True)
    oversell_policy = models.CharField(
        max_length=16, choices=OversellPolicy.choices, default=OversellPolicy.INHERIT, blank=True
    )
    alert_state = models.CharField(max_length=8, choices=AlertState.choices, default=AlertState.OUT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchasable_type", "purchasable_key", "location"],
                name="inventory_item_per_location",
            ),
        ]
        indexes = [
            models.Index(fields=["purchasable_type", "purchasable_key"], name="inventory_item_pair"),
            models.Index(fields=["alert_state"], name="inventory_item_alert"),
        ]

    def __str__(self) -> str:
        return f"{self.purchasable_type}:{self.purchasable_key}@{self.location_id}"


class MovementQuerySet(models.QuerySet):
    """The ledger is append-only: bulk rewrites are refused."""

    def update(self, **kwargs):
        raise TypeError("StockMovement is append-only; write a compensating movement instead")

    def reassign_actor(self, actor_id: str, new_actor_id: str) -> int:
        """The one rewrite the ledger allows: the actor id (GDPR erasure, account merge)."""
        return models.QuerySet.update(self.filter(actor_id=actor_id), actor_id=new_actor_id)

    def delete(self):
        raise TypeError("StockMovement is append-only; write a compensating movement instead")


@access.ops
class StockMovement(models.Model):
    """One ledger entry. ``on_hand_delta``/``held_delta`` are signed unit counts."""

    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=16, choices=MovementKind.choices)
    on_hand_delta = models.IntegerField(default=0)
    held_delta = models.IntegerField(default=0)
    on_hand_after = models.IntegerField()
    reason = models.CharField(max_length=REASON_MAX_LENGTH, blank=True, default="")
    ref_type = models.CharField(max_length=REF_TYPE_MAX_LENGTH, blank=True, default="")
    ref_key = models.CharField(max_length=REF_KEY_MAX_LENGTH, blank=True, default="")
    actor_id = models.CharField(max_length=USER_ID_MAX_LENGTH, default=SYSTEM_ACTOR)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MovementQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["stock_item", "-id"], name="inventory_mv_item"),
            models.Index(fields=["ref_type", "ref_key"], name="inventory_mv_ref"),
            models.Index(fields=["actor_id"], name="inventory_mv_actor"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise TypeError("StockMovement is append-only; write a compensating movement instead")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("StockMovement is append-only; write a compensating movement instead")


@access.ops
class StockHold(models.Model):
    """A checkout's claim on stock, keyed on the checkout attempt key."""

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    attempt_key = models.CharField(max_length=ATTEMPT_KEY_MAX_LENGTH, unique=True)
    user_id = models.CharField(max_length=USER_ID_MAX_LENGTH, null=True, blank=True)
    status = models.CharField(max_length=16, choices=HoldStatus.choices, default=HoldStatus.ACTIVE)
    expires_at = models.DateTimeField()
    reference = models.CharField(max_length=REFERENCE_MAX_LENGTH, blank=True, default="")
    release_reason = models.CharField(max_length=RELEASE_REASON_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="inventory_hold_expiry"),
            models.Index(fields=["user_id"], name="inventory_hold_user"),
        ]

    def __str__(self) -> str:
        return f"hold {self.token} ({self.status})"


@access.ops
class StockHoldLine(models.Model):
    """Units of one stock item a hold claims; ``backordered`` is part of ``quantity``."""

    hold = models.ForeignKey(StockHold, on_delete=models.CASCADE, related_name="lines")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="hold_lines")
    line_index = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()
    backordered = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["hold", "stock_item"], name="inventory_holdline_item"),
        ]
