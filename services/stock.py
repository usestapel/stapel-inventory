"""Stock primitives and the staff operations: receive, adjust, transfer.

Every change of ``on_hand`` goes through :func:`write_movement`, which
writes the ledger row and the cache together; callers hold the item's row
lock (:func:`lock_items`) inside one transaction. ``available`` is always
re-counted after the lock (:func:`held_by_item`), never carried in.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from stapel_core.comm import mutate_and_emit

from ..errors import (
    ERR_400_ADJUST_AMBIGUOUS,
    ERR_400_TRANSFER_SAME_LOCATION,
    ERR_404_LOCATION_NOT_FOUND,
    ERR_404_STOCK_ITEM_NOT_FOUND,
    ERR_409_INSUFFICIENT_STOCK,
    ERR_409_LOCATION_INACTIVE,
    ERR_409_STOCK_WOULD_GO_NEGATIVE,
)
from ..models import (
    SYSTEM_ACTOR,
    HoldStatus,
    Location,
    MovementKind,
    OversellPolicy,
    StockHoldLine,
    StockItem,
    StockMovement,
)
from . import InventoryError, facts


# ── Locations ─────────────────────────────────────────────────────────────


def default_location() -> Location:
    """The default location, created on first use (race-safe)."""
    from ..conf import inventory_settings

    found = Location.objects.filter(is_default=True).first()
    if found is not None:
        return found
    code = inventory_settings.DEFAULT_LOCATION_CODE
    try:
        with transaction.atomic():
            location, _ = Location.objects.get_or_create(
                code=code, defaults={"name": code.replace("-", " ").title(), "is_default": True}
            )
            if not location.is_default:
                location.is_default = True
                location.save(update_fields=["is_default", "updated_at"])
            return location
    except IntegrityError:
        return Location.objects.get(is_default=True)


def location_by_code(code: str | None, *, for_write: bool = True) -> Location:
    if not code:
        return default_location()
    location = Location.objects.filter(code=code).first()
    if location is None:
        raise InventoryError(404, ERR_404_LOCATION_NOT_FOUND, {"code": code})
    if for_write and not location.active:
        raise InventoryError(409, ERR_409_LOCATION_INACTIVE, {"code": code})
    return location


def locations_by_id(ids) -> dict[int, Location]:
    return {loc.pk: loc for loc in Location.objects.filter(pk__in=set(ids))}


def list_locations():
    default_location()
    return Location.objects.order_by("priority", "pk")


def create_location(values: dict) -> Location:
    from ..errors import ERR_409_LOCATION_EXISTS

    default_location()
    if Location.objects.filter(code=values["code"]).exists():
        raise InventoryError(409, ERR_409_LOCATION_EXISTS, {"code": values["code"]})
    try:
        with transaction.atomic():
            if values.get("is_default"):
                Location.objects.filter(is_default=True).update(is_default=False)
            return Location.objects.create(
                code=values["code"], name=values["name"], priority=values.get("priority", 100),
                is_default=bool(values.get("is_default")), active=values.get("active", True),
            )
    except IntegrityError:
        raise InventoryError(409, ERR_409_LOCATION_EXISTS, {"code": values["code"]}) from None


def update_location(code: str, values: dict) -> Location:
    from ..errors import ERR_409_DEFAULT_LOCATION_REQUIRED

    location = location_by_code(code, for_write=False)
    with transaction.atomic():
        location = Location.objects.select_for_update().get(pk=location.pk)
        if location.is_default and (values.get("active") is False or values.get("is_default") is False):
            raise InventoryError(409, ERR_409_DEFAULT_LOCATION_REQUIRED, {"code": code})
        if values.get("is_default") and not location.is_default:
            if values.get("active") is False or (not location.active and values.get("active") is not True):
                raise InventoryError(409, ERR_409_DEFAULT_LOCATION_REQUIRED, {"code": code})
            Location.objects.filter(is_default=True).update(is_default=False)
            location.is_default = True
        for field in ("name", "priority", "active"):
            if field in values:
                setattr(location, field, values[field])
        location.save()
    return location


# ── Locks and counts ──────────────────────────────────────────────────────


def lock_items(ids) -> dict[int, StockItem]:
    """Lock the stock items, ascending id — the one lock order of the module."""
    ordered = sorted(set(ids))
    if not ordered:
        return {}
    rows = StockItem.objects.select_for_update().filter(pk__in=ordered).order_by("pk")
    return {item.pk: item for item in rows}


def held_by_item(ids, now=None) -> dict[int, int]:
    """Units claimed by active, unexpired holds, per stock item."""
    now = now or timezone.now()
    rows = (
        StockHoldLine.objects.filter(
            stock_item_id__in=set(ids),
            hold__status=HoldStatus.ACTIVE,
            hold__expires_at__gt=now,
        )
        .values("stock_item_id")
        .annotate(total=Sum("quantity"))
    )
    return {row["stock_item_id"]: int(row["total"] or 0) for row in rows}


def policy_of(item) -> str:
    from ..conf import inventory_settings

    return item.oversell_policy or inventory_settings.OVERSELL


def get_or_create_item(purchasable_type: str, purchasable_key: str, location: Location) -> StockItem:
    """The item of the pair at the location; a concurrent create ends in one row."""
    lookup = dict(purchasable_type=purchasable_type, purchasable_key=purchasable_key, location=location)
    item = StockItem.objects.filter(**lookup).first()
    if item is not None:
        return item
    try:
        with transaction.atomic():
            return StockItem.objects.create(**lookup)
    except IntegrityError:
        return StockItem.objects.get(**lookup)


def write_movement(
    item: StockItem,
    kind: str,
    *,
    on_hand_delta: int = 0,
    held_delta: int = 0,
    reason: str = "",
    ref_type: str = "",
    ref_key: str = "",
    actor_id: str = SYSTEM_ACTOR,
) -> StockMovement:
    """Append one ledger row and move the ``on_hand`` cache with it (caller holds the lock)."""
    if on_hand_delta:
        item.on_hand += on_hand_delta
        item.save(update_fields=["on_hand", "updated_at"])
    return StockMovement.objects.create(
        stock_item=item,
        kind=kind,
        on_hand_delta=on_hand_delta,
        held_delta=held_delta,
        on_hand_after=item.on_hand,
        reason=reason[:200],
        ref_type=ref_type,
        ref_key=ref_key,
        actor_id=actor_id or SYSTEM_ACTOR,
    )


def refresh_alerts(items, emit, now=None) -> None:
    """Re-count ``available`` of locked items and move their alert state."""
    items = list(items)
    if not items:
        return
    held = held_by_item([i.pk for i in items], now)
    locations = locations_by_id(i.location_id for i in items)
    for item in items:
        available = item.on_hand - held.get(item.pk, 0)
        facts.refresh(item, available, locations[item.location_id].code, emit)


# ── Staff operations ──────────────────────────────────────────────────────


@dataclass
class TransferResult:
    source: StockItem
    target: StockItem


def receive(purchasable_type, purchasable_key, location_code, quantity, *, reason="", actor_id) -> StockItem:
    location = location_by_code(location_code)
    item = get_or_create_item(purchasable_type, purchasable_key, location)
    with mutate_and_emit() as emit:
        item = lock_items([item.pk])[item.pk]
        write_movement(item, MovementKind.RECEIPT, on_hand_delta=int(quantity), reason=reason,
                       actor_id=actor_id)
        refresh_alerts([item], emit)
    return item


def adjust(purchasable_type, purchasable_key, location_code, *, delta=None, on_hand=None, reason,
           actor_id) -> StockItem:
    """A correction by ``delta`` or a physical count (``on_hand``); never below zero."""
    if (delta is None) == (on_hand is None):
        raise InventoryError(400, ERR_400_ADJUST_AMBIGUOUS)
    location = location_by_code(location_code)
    item = get_or_create_item(purchasable_type, purchasable_key, location)
    with mutate_and_emit() as emit:
        item = lock_items([item.pk])[item.pk]
        change = int(delta) if delta is not None else int(on_hand) - item.on_hand
        if change < 0 and item.on_hand + change < 0:
            raise InventoryError(409, ERR_409_STOCK_WOULD_GO_NEGATIVE, {"on_hand": item.on_hand})
        if change:
            write_movement(item, MovementKind.ADJUST, on_hand_delta=change, reason=reason,
                           actor_id=actor_id)
        refresh_alerts([item], emit)
    return item


def transfer(purchasable_type, purchasable_key, from_code, to_code, quantity, *, reason="",
             actor_id) -> TransferResult:
    """Move units between locations; held units stay where they are held."""
    if from_code == to_code:
        raise InventoryError(400, ERR_400_TRANSFER_SAME_LOCATION)
    source_loc = location_by_code(from_code, for_write=False)
    target_loc = location_by_code(to_code)
    source = StockItem.objects.filter(
        purchasable_type=purchasable_type, purchasable_key=purchasable_key, location=source_loc
    ).first()
    if source is None:
        raise InventoryError(409, ERR_409_INSUFFICIENT_STOCK, {"available": 0})
    target = get_or_create_item(purchasable_type, purchasable_key, target_loc)
    ref = uuid.uuid4().hex
    with mutate_and_emit() as emit:
        locked = lock_items([source.pk, target.pk])
        source, target = locked[source.pk], locked[target.pk]
        available = source.on_hand - held_by_item([source.pk]).get(source.pk, 0)
        if int(quantity) > available:
            raise InventoryError(409, ERR_409_INSUFFICIENT_STOCK, {"available": max(available, 0)})
        write_movement(source, MovementKind.TRANSFER, on_hand_delta=-int(quantity), reason=reason,
                       ref_type="transfer", ref_key=ref, actor_id=actor_id)
        write_movement(target, MovementKind.TRANSFER, on_hand_delta=int(quantity), reason=reason,
                       ref_type="transfer", ref_key=ref, actor_id=actor_id)
        refresh_alerts([source, target], emit)
    return TransferResult(source=source, target=target)


def update_item(item_id: int, values: dict) -> StockItem:
    """Threshold and per-item oversell policy; the alert state follows at once."""
    if not StockItem.objects.filter(pk=item_id).exists():
        raise InventoryError(404, ERR_404_STOCK_ITEM_NOT_FOUND)
    with mutate_and_emit() as emit:
        item = lock_items([item_id])[item_id]
        fields = []
        if "low_stock_threshold" in values:
            item.low_stock_threshold = values["low_stock_threshold"]
            fields.append("low_stock_threshold")
        if "oversell_policy" in values:
            item.oversell_policy = values["oversell_policy"] or OversellPolicy.INHERIT
            fields.append("oversell_policy")
        if fields:
            item.save(update_fields=[*fields, "updated_at"])
        refresh_alerts([item], emit)
    return item


def available_of(item: StockItem, now=None) -> int:
    return item.on_hand - held_by_item([item.pk], now).get(item.pk, 0)


def held_of(item: StockItem, now=None) -> int:
    return held_by_item([item.pk], now).get(item.pk, 0)


# ── Availability ──────────────────────────────────────────────────────────


def candidates_for(items, held, locations):
    from ..allocation import Candidate

    return [
        Candidate(
            stock_item_id=item.pk,
            location_id=item.location_id,
            location_code=locations[item.location_id].code,
            priority=locations[item.location_id].priority,
            is_default=locations[item.location_id].is_default,
            available=item.on_hand - held.get(item.pk, 0),
        )
        for item in items
    ]


def purchasable_policy(items, locations) -> str:
    """The policy of the default-location item, else of the first item."""
    if not items:
        from ..conf import inventory_settings

        return inventory_settings.OVERSELL
    chosen = next((i for i in items if locations[i.location_id].is_default), items[0])
    return policy_of(chosen)


def availability(purchasable_type: str, keys, location_code: str | None = None) -> dict:
    """Exact availability per key (internal: numbers, policy, per location)."""
    from ..allocation import active_allocation
    from ..conf import inventory_settings

    keys = list(dict.fromkeys(str(k) for k in keys))
    now = timezone.now()
    query = Q(purchasable_type=purchasable_type, purchasable_key__in=keys, location__active=True)
    if location_code:
        query &= Q(location__code=location_code)
    items = list(StockItem.objects.filter(query).order_by("pk"))
    held = held_by_item([i.pk for i in items], now)
    locations = locations_by_id(i.location_id for i in items)
    strategy = active_allocation()
    by_key: dict[str, list] = {}
    for item in items:
        by_key.setdefault(item.purchasable_key, []).append(item)
    track_unknown = inventory_settings.TRACK_UNKNOWN
    result = {}
    for key in keys:
        rows = by_key.get(key, [])
        if not rows:
            result[key] = {
                "tracked": False, "on_hand": 0, "held": 0, "available": None,
                "in_stock": track_unknown != "deny", "policy": inventory_settings.OVERSELL,
                "low_stock": False, "locations": [],
            }
            continue
        candidates = candidates_for(rows, held, locations)
        if location_code:
            available = sum(max(c.available, 0) for c in candidates)
            selling = rows
        else:
            available = strategy.available(candidates)
            eligible_ids = {c.stock_item_id for c in strategy.eligible(candidates)}
            selling = [i for i in rows if i.pk in eligible_ids]
        policy = purchasable_policy(rows, locations)
        threshold = min((facts.threshold_of(i) for i in selling), default=facts.threshold_of(rows[0]))
        result[key] = {
            "tracked": True,
            "on_hand": sum(i.on_hand for i in selling),
            "held": sum(held.get(i.pk, 0) for i in selling),
            "available": available,
            "in_stock": available > 0 or policy == OversellPolicy.BACKORDER,
            "policy": policy,
            "low_stock": 0 < available <= threshold,
            "locations": [
                {"code": locations[i.location_id].code, "on_hand": i.on_hand,
                 "available": i.on_hand - held.get(i.pk, 0)}
                for i in rows
            ],
        }
    return result


__all__ = [
    "TransferResult",
    "adjust",
    "availability",
    "default_location",
    "get_or_create_item",
    "held_by_item",
    "lock_items",
    "receive",
    "transfer",
    "update_item",
    "write_movement",
]
