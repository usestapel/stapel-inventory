"""Stock facts: low, out, back in stock, oversold — deduplicated.

A fact fires on a transition of ``StockItem.alert_state`` only, never on
each write, so a shelf that sits at 2 units across a hundred sales says
"low" once. The emitter is the caller's ``mutate_and_emit`` emitter: the
fact commits with the movement that caused it (key = stock item id).

Transitions: ``* -> out`` emits ``out``; ``ok -> low`` emits ``low``;
``out -> low`` emits ``back_in_stock`` and ``low``; ``out -> ok`` emits
``back_in_stock``; ``low -> ok`` resets silently.
"""
from __future__ import annotations

from ..models import AlertState

EVENT_LOW = "inventory.stock.low"
EVENT_OUT = "inventory.stock.out"
EVENT_BACK = "inventory.stock.back_in_stock"
EVENT_OVERSOLD = "inventory.stock.oversold"


def threshold_of(item) -> int:
    from ..conf import inventory_settings

    if item.low_stock_threshold is not None:
        return int(item.low_stock_threshold)
    return int(inventory_settings.LOW_STOCK_THRESHOLD)


def state_for(available: int, threshold: int) -> str:
    if available <= 0:
        return AlertState.OUT
    if available <= threshold:
        return AlertState.LOW
    return AlertState.OK


def payload(item, location_code: str, available: int, **extra) -> dict:
    return {
        "stock_item_id": str(item.pk),
        "purchasable_type": item.purchasable_type,
        "purchasable_key": item.purchasable_key,
        "location": location_code,
        "on_hand": item.on_hand,
        "available": available,
        "threshold": threshold_of(item),
        **extra,
    }


def refresh(item, available: int, location_code: str, emit) -> list[str]:
    """Move ``alert_state`` to what ``available`` says; emit the transition's facts.

    The caller holds the item's row lock and saves nothing else after this
    for the item; the new state is written here.
    """
    old = item.alert_state
    new = state_for(available, threshold_of(item))
    if new == old:
        return []
    names = []
    if new == AlertState.OUT:
        names = [EVENT_OUT]
    elif new == AlertState.LOW:
        names = [EVENT_BACK, EVENT_LOW] if old == AlertState.OUT else [EVENT_LOW]
    elif old == AlertState.OUT:
        names = [EVENT_BACK]
    item.alert_state = new
    item.save(update_fields=["alert_state", "updated_at"])
    body = payload(item, location_code, available)
    for name in names:
        emit(name, body, key=str(item.pk))  # emit-check: ok — emit is the caller's mutate_and_emit emitter
    return names


def oversold(item, location_code: str, available: int, emit, *, reference: str) -> None:
    """A commit drove a ``deny`` item below zero (a hold expired before its order)."""
    emit(
        EVENT_OVERSOLD,
        payload(item, location_code, available, oversold=-available, reference=reference),
        key=str(item.pk),
    )


__all__ = ["EVENT_BACK", "EVENT_LOW", "EVENT_OUT", "EVENT_OVERSOLD", "oversold", "refresh", "state_for"]
