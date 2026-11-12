"""Checkout holds: reserve, commit, release, expire — the orders hook contract.

Idempotent on ``attempt_key`` (the orders checkout idempotency key):

- reserve with a key whose hold is active with the same lines returns the
  same token; a committed hold answers ok; a released or expired hold — or
  an active one with different lines — is re-reserved under the same row
  (orders keeps the key across a refused submit);
- commit and release are no-ops on replay.

A reserve is all-or-nothing across lines: any refusal rolls the whole
transaction back and answers ``ok: false`` with every refusal at once.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import OrderedDict
from dataclasses import dataclass

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from stapel_core.comm import mutate_and_emit

from ..errors import ERR_409_INSUFFICIENT_STOCK, ERR_409_STOCK_NOT_TRACKED
from ..models import SYSTEM_ACTOR, HoldStatus, MovementKind, OversellPolicy, StockHold, StockHoldLine, StockItem
from . import facts
from .stock import (
    candidates_for,
    held_by_item,
    lock_items,
    locations_by_id,
    policy_of,
    purchasable_policy,
    refresh_alerts,
    write_movement,
)

logger = logging.getLogger(__name__)

REF_HOLD = "hold"
REF_ORDER = "order"

#: Release reasons orders sends (contract delta 13).
REASON_CANCELLED = "cancelled"
REASON_CHECKOUT_FAILED = "checkout_failed"
REASON_ORPHANED = "orphaned"


@dataclass(frozen=True)
class Line:
    """One purchasable of a reserve; duplicate lines are summed onto the first index."""

    index: int
    purchasable_type: str
    purchasable_key: str
    quantity: int

    @property
    def pair(self) -> tuple[str, str]:
        return (self.purchasable_type, self.purchasable_key)


class _Refused(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("refused")


def normalize_lines(raw_lines) -> list[Line]:
    merged: OrderedDict[tuple[str, str], Line] = OrderedDict()
    for position, raw in enumerate(raw_lines or []):
        pair = (str(raw["purchasable_type"]), str(raw["purchasable_key"]))
        index = int(raw.get("index", position))
        quantity = int(raw["quantity"])
        if pair in merged:
            prev = merged[pair]
            merged[pair] = Line(prev.index, pair[0], pair[1], prev.quantity + quantity)
        else:
            merged[pair] = Line(index, pair[0], pair[1], quantity)
    return list(merged.values())


def _pair_query(lines) -> Q:
    query = Q(pk__in=[])
    for line in lines:
        query |= Q(purchasable_type=line.purchasable_type, purchasable_key=line.purchasable_key)
    return query


def _ok(hold) -> dict:
    return {"ok": True, "token": str(hold.token) if hold is not None else None, "errors": []}


def _refusal(key: str, field=None, **params) -> dict:
    return {"code": key, "field": field, "params": params}


def _hold_pairs(hold) -> dict[tuple[str, str], int]:
    pairs: dict[tuple[str, str], int] = {}
    for line in hold.lines.select_related("stock_item"):
        pair = (line.stock_item.purchasable_type, line.stock_item.purchasable_key)
        pairs[pair] = pairs.get(pair, 0) + line.quantity
    return pairs


def _release_lines(hold, *, kind=MovementKind.RELEASE, restock=False) -> list:
    """Write the ledger rows that give a hold's units back (caller holds the locks)."""
    touched = []
    for line in hold.lines.select_related("stock_item"):
        item = line.stock_item
        if restock:
            write_movement(item, MovementKind.RESTOCK, on_hand_delta=line.quantity,
                           ref_type=REF_HOLD, ref_key=str(hold.pk))
        else:
            write_movement(item, kind, held_delta=-line.quantity, ref_type=REF_HOLD, ref_key=str(hold.pk))
        touched.append(item)
    return touched


def _hold_item_ids(attempt_key: str) -> list[int]:
    return list(
        StockHoldLine.objects.filter(hold__attempt_key=attempt_key).values_list("stock_item_id", flat=True)
    )


# ── reserve ───────────────────────────────────────────────────────────────


def actor_of(payload: dict) -> str | None:
    """The hold owner: ``actor_id`` (a guest's own id too), else ``user_id`` (older orders)."""
    raw = payload.get("actor_id") or payload.get("user_id")
    return None if raw in (None, "") else str(raw)


def reserve(payload: dict) -> dict:
    """Claim stock for every tracked line, or nothing. Returns ``{ok, token, errors}``."""
    attempt_key = str(payload["attempt_key"])
    lines = normalize_lines(payload.get("lines"))
    user_id = payload.get("user_id")
    owner = (None if user_id in (None, "") else str(user_id), actor_of(payload))
    for attempt in range(2):
        try:
            return _reserve_once(attempt_key, lines, *owner)
        except _Refused as refused:
            return {"ok": False, "token": None, "errors": refused.errors}
        except IntegrityError:
            # A concurrent reserve created the hold row first; the second
            # pass finds it and replays or re-reserves under its lock.
            if attempt:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _reserve_once(attempt_key, lines, user_id, actor_id) -> dict:
    from ..allocation import active_allocation
    from ..conf import inventory_settings

    now = timezone.now()
    with mutate_and_emit() as emit:
        candidate_ids = list(
            StockItem.objects.filter(_pair_query(lines), location__active=True).values_list("pk", flat=True)
        )
        items = lock_items([*candidate_ids, *_hold_item_ids(attempt_key)])
        hold = StockHold.objects.select_for_update().filter(attempt_key=attempt_key).first()
        touched: list = []
        if hold is not None:
            if hold.status in (HoldStatus.COMMITTED, HoldStatus.RETURNED):
                return _ok(hold)
            if hold.status == HoldStatus.ACTIVE:
                wanted = {line.pair: line.quantity for line in lines}
                if hold.expires_at > now and _hold_pairs(hold) == wanted:
                    return _ok(hold)
                touched += _release_lines(hold)
            hold.lines.all().delete()

        held = held_by_item(candidate_ids, now)
        locations = locations_by_id(i.location_id for i in items.values())
        strategy = active_allocation()
        errors, plan = [], []
        for line in lines:
            rows = [items[i] for i in sorted(candidate_ids)
                    if (items[i].purchasable_type, items[i].purchasable_key) == line.pair]
            field = f"lines.{line.index}.quantity"
            if not rows:
                if inventory_settings.TRACK_UNKNOWN == "deny":
                    errors.append(_refusal(
                        ERR_409_STOCK_NOT_TRACKED, field, index=line.index,
                        purchasable_type=line.purchasable_type, purchasable_key=line.purchasable_key,
                    ))
                continue
            candidates = candidates_for(rows, held, locations)
            allocations = strategy.allocate(line.quantity, candidates, purchasable_policy(rows, locations))
            if allocations is None:
                errors.append(_refusal(
                    ERR_409_INSUFFICIENT_STOCK, field, index=line.index,
                    purchasable_type=line.purchasable_type, purchasable_key=line.purchasable_key,
                    requested=line.quantity, available=strategy.available(candidates),
                ))
                continue
            plan.append((line, allocations))
        if errors:
            raise _Refused(errors)

        expires_at = now + dt.timedelta(seconds=int(inventory_settings.HOLD_TTL_SECONDS))
        if hold is None:
            hold = StockHold.objects.create(attempt_key=attempt_key, user_id=user_id, actor_id=actor_id,
                                            expires_at=expires_at)
        else:
            hold.status = HoldStatus.ACTIVE
            hold.expires_at = expires_at
            hold.user_id = user_id
            hold.actor_id = actor_id
            hold.release_reason = ""
            hold.save(update_fields=["status", "expires_at", "user_id", "actor_id", "release_reason",
                                     "updated_at"])
        for line, allocations in plan:
            for allocation in allocations:
                item = items[allocation.stock_item_id]
                StockHoldLine.objects.create(
                    hold=hold, stock_item=item, line_index=line.index,
                    quantity=allocation.quantity, backordered=allocation.backordered,
                )
                write_movement(item, MovementKind.HOLD, held_delta=allocation.quantity,
                               ref_type=REF_HOLD, ref_key=str(hold.pk))
                touched.append(item)
        refresh_alerts({i.pk: i for i in touched}.values(), emit, now)
        return _ok(hold)


# ── commit ────────────────────────────────────────────────────────────────


def _find(payload) -> StockHold | None:
    attempt_key = payload.get("attempt_key")
    token = payload.get("token")
    if attempt_key:
        hold = StockHold.objects.filter(attempt_key=str(attempt_key)).first()
    elif token:
        hold = StockHold.objects.filter(token=str(token)).first()
    else:
        return None
    if hold is not None and token and str(hold.token) != str(token):
        return None
    return hold


def commit(payload: dict) -> dict:
    """Turn the hold into a decrement (the order is placed). Returns ``{ok, token, errors}``."""
    found = _find(payload)
    if found is None:
        # Nothing was held under this key (no reserve landed, or the row is
        # gone): there are no lines to decrement. Answering ok stops the
        # orders retry loop; the ERROR line is the measurement.
        logger.error("inventory: commit for attempt %s finds no hold; nothing decremented",
                     payload.get("attempt_key"))
        return {"ok": True, "token": None, "errors": []}
    reference = str(payload.get("order_id") or "")[:64]
    now = timezone.now()
    with mutate_and_emit() as emit:
        items = lock_items(found.lines.values_list("stock_item_id", flat=True))
        hold = StockHold.objects.select_for_update().get(pk=found.pk)
        if hold.status in (HoldStatus.COMMITTED, HoldStatus.RETURNED):
            return _ok(hold)
        if hold.status in (HoldStatus.RELEASED, HoldStatus.EXPIRED) and hold.release_reason == REASON_CANCELLED:
            # The cancel overtook the (retried) commit: the order is gone.
            return _ok(hold)
        holding = hold.status == HoldStatus.ACTIVE
        if not holding:
            logger.warning(
                "inventory: committing hold %s that is already %s; the order is the fact, "
                "stock is decremented without a hold", hold.pk, hold.status,
            )
        for line in hold.lines.all():
            item = items[line.stock_item_id]
            write_movement(
                item, MovementKind.COMMIT, on_hand_delta=-line.quantity,
                held_delta=-line.quantity if holding else 0,
                ref_type=REF_HOLD, ref_key=str(hold.pk),
            )
        hold.status = HoldStatus.COMMITTED
        hold.reference = reference
        hold.actor_id = hold.actor_id or actor_of(payload)
        hold.save(update_fields=["status", "reference", "actor_id", "updated_at"])
        _after_decrement(items.values(), emit, now, reference=reference or str(hold.pk))
        return _ok(hold)


def _after_decrement(items, emit, now, *, reference: str) -> None:
    """Alert states, plus ``oversold`` for a ``deny`` item driven below zero."""
    items = list(items)
    if not items:
        return
    held = held_by_item([i.pk for i in items], now)
    locations = locations_by_id(i.location_id for i in items)
    for item in items:
        available = item.on_hand - held.get(item.pk, 0)
        code = locations[item.location_id].code
        if available < 0 and policy_of(item) == OversellPolicy.DENY:
            facts.oversold(item, code, available, emit, reference=reference)
        facts.refresh(item, available, code, emit)


# ── release ───────────────────────────────────────────────────────────────


def release(payload: dict) -> dict:
    """Give the hold back; only ``cancelled`` undoes a committed one. Idempotent.

    An active hold is released for any reason. A committed hold is restocked
    for ``cancelled`` (``RESTOCK_ON_CANCEL``); ``checkout_failed`` /
    ``orphaned`` on it are a no-op answering ok. A cancel that finds the hold
    released or expired is recorded so a late commit stays a no-op. A
    payload without a reason never touches a committed hold.
    """
    from ..conf import inventory_settings

    found = _find(payload)
    if found is None:
        return {"ok": True, "token": None, "errors": []}
    reason = str(payload.get("reason") or "")[:32]
    now = timezone.now()
    with mutate_and_emit() as emit:
        lock_items(found.lines.values_list("stock_item_id", flat=True))
        hold = StockHold.objects.select_for_update().get(pk=found.pk)
        if hold.status == HoldStatus.ACTIVE:
            touched = _release_lines(hold)
            hold.status = HoldStatus.RELEASED
        elif (hold.status == HoldStatus.COMMITTED and reason == REASON_CANCELLED
              and inventory_settings.RESTOCK_ON_CANCEL):
            touched = _release_lines(hold, restock=True)
            hold.status = HoldStatus.RETURNED
        elif (hold.status in (HoldStatus.RELEASED, HoldStatus.EXPIRED) and reason == REASON_CANCELLED
              and hold.release_reason != REASON_CANCELLED):
            hold.release_reason = reason
            hold.save(update_fields=["release_reason", "updated_at"])
            return _ok(hold)
        else:
            return _ok(hold)
        hold.release_reason = reason
        hold.save(update_fields=["status", "release_reason", "updated_at"])
        refresh_alerts(touched, emit, now)
        return _ok(hold)


# ── expiry (beat) ─────────────────────────────────────────────────────────


def expire_due(now=None, batch: int = 500) -> int:
    """Release active holds past ``expires_at``. One transaction per hold."""
    now = now or timezone.now()
    due = list(
        StockHold.objects.filter(status=HoldStatus.ACTIVE, expires_at__lte=now)
        .order_by("expires_at", "pk").values_list("pk", flat=True)[:batch]
    )
    expired = 0
    for hold_id in due:
        with mutate_and_emit() as emit:
            lock_items(StockHoldLine.objects.filter(hold_id=hold_id).values_list("stock_item_id", flat=True))
            hold = StockHold.objects.select_for_update().filter(pk=hold_id).first()
            if hold is None or hold.status != HoldStatus.ACTIVE or hold.expires_at > now:
                continue
            touched = _release_lines(hold)
            hold.status = HoldStatus.EXPIRED
            hold.release_reason = "expired"
            hold.save(update_fields=["status", "release_reason", "updated_at"])
            refresh_alerts(touched, emit, now)
            expired += 1
    return expired


# ── orders.order.placed fallback (DECREMENT_ON_ORDER_PLACED) ──────────────


def decrement_for_order(order_id: str, raw_lines) -> int:
    """Decrement stock for a placed order without a hold; idempotent on the order id."""
    from ..allocation import active_allocation
    from ..models import StockMovement

    lines = normalize_lines(raw_lines)
    if not lines:
        return 0
    now = timezone.now()
    with mutate_and_emit() as emit:
        candidate_ids = list(
            StockItem.objects.filter(_pair_query(lines), location__active=True).values_list("pk", flat=True)
        )
        items = lock_items(candidate_ids)
        if not items:
            return 0
        if StockMovement.objects.filter(ref_type=REF_ORDER, ref_key=order_id).exists():
            return 0
        held = held_by_item(candidate_ids, now)
        locations = locations_by_id(i.location_id for i in items.values())
        strategy = active_allocation()
        moved = 0
        for line in lines:
            rows = [items[i] for i in sorted(items)
                    if (items[i].purchasable_type, items[i].purchasable_key) == line.pair]
            if not rows:
                continue
            candidates = candidates_for(rows, held, locations)
            allocations = strategy.allocate(line.quantity, candidates, OversellPolicy.BACKORDER)
            for allocation in allocations or []:
                write_movement(items[allocation.stock_item_id], MovementKind.COMMIT,
                               on_hand_delta=-allocation.quantity, ref_type=REF_ORDER, ref_key=order_id,
                               actor_id=SYSTEM_ACTOR)
                moved += allocation.quantity
        _after_decrement(items.values(), emit, now, reference=order_id)
        return moved


__all__ = ["Line", "actor_of", "commit", "decrement_for_order", "expire_due", "normalize_lines", "release", "reserve"]
