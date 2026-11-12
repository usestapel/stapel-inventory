"""Reconciliation: the ``on_hand`` cache and the hold counts against the ledger.

Two invariants, checked per stock item:

1. ``on_hand == Σ on_hand_delta`` — every writer keeps it under the item's
   row lock, so a drift means something wrote ``on_hand`` outside
   ``services``. The ledger is the source of truth: the cache is rewritten
   from it under the lock (``RECONCILE_REPAIR``, default on) and the drift is
   logged at ERROR either way.
2. ``Σ held_delta == Σ quantity of active hold lines`` — reported only; a
   hold count cannot be repaired without knowing which side lied.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from ..models import HoldStatus, StockHoldLine, StockItem, StockMovement
from .stock import lock_items

logger = logging.getLogger(__name__)


def _ledger_sum(field: str):
    return Coalesce(
        Subquery(
            StockMovement.objects.filter(stock_item=OuterRef("pk"))
            .order_by()
            .values("stock_item")
            .annotate(total=Sum(field))
            .values("total"),
            output_field=IntegerField(),
        ),
        Value(0),
    )


def _active_held():
    return Coalesce(
        Subquery(
            StockHoldLine.objects.filter(stock_item=OuterRef("pk"), hold__status=HoldStatus.ACTIVE)
            .order_by()
            .values("stock_item")
            .annotate(total=Sum("quantity"))
            .values("total"),
            output_field=IntegerField(),
        ),
        Value(0),
    )


def drift() -> dict[str, list[dict]]:
    """Every item whose cache or hold count disagrees with the ledger (read-only)."""
    rows = StockItem.objects.annotate(
        ledger_on_hand=_ledger_sum("on_hand_delta"),
        ledger_held=_ledger_sum("held_delta"),
        active_held=_active_held(),
    )
    on_hand = [
        {"stock_item_id": r["pk"], "cached": r["on_hand"], "ledger": r["ledger_on_hand"]}
        for r in rows.exclude(on_hand=F("ledger_on_hand")).values("pk", "on_hand", "ledger_on_hand")
    ]
    held = [
        {"stock_item_id": r["pk"], "ledger": r["ledger_held"], "holds": r["active_held"]}
        for r in rows.exclude(ledger_held=F("active_held")).values("pk", "ledger_held", "active_held")
    ]
    return {"on_hand": on_hand, "held": held}


def reconcile(repair: bool | None = None) -> dict[str, list[dict]]:
    """Report (and by default repair) drift. Returns what it found."""
    from ..conf import inventory_settings

    if repair is None:
        repair = bool(inventory_settings.RECONCILE_REPAIR)
    found = drift()
    for row in found["on_hand"]:
        logger.error(
            "inventory: stock item %s caches on_hand=%s but its ledger sums to %s — something "
            "wrote on_hand outside stapel_inventory.services%s",
            row["stock_item_id"], row["cached"], row["ledger"], "; repaired from the ledger" if repair else "",
        )
        if repair:
            _repair(row["stock_item_id"])
    for row in found["held"]:
        logger.error(
            "inventory: stock item %s ledger holds %s units but active holds claim %s",
            row["stock_item_id"], row["ledger"], row["holds"],
        )
    return found


def _repair(item_id: int) -> None:
    with transaction.atomic():
        item = lock_items([item_id]).get(item_id)
        if item is None:
            return
        total = StockMovement.objects.filter(stock_item_id=item_id).aggregate(t=Sum("on_hand_delta"))["t"] or 0
        if item.on_hand != total:
            item.on_hand = total
            item.save(update_fields=["on_hand", "updated_at"])


__all__ = ["drift", "reconcile"]
