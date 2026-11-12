"""Dataclass DTOs — the API models of stapel-inventory (never ORM instances)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class AvailabilityItemDTO:
    """One purchasable as the public may see it: a state, and a number only under the exact policy."""

    purchasable_type: str
    purchasable_key: str
    state: str
    quantity: int | None


@dataclass
class AvailabilityDTO:
    """The public availability answer for a batch of keys."""

    items: list[AvailabilityItemDTO] = field(default_factory=list)


@dataclass
class LocationDTO:
    """A place stock sits in; exactly one is the default."""

    code: str
    name: str
    priority: int
    is_default: bool
    active: bool


@dataclass
class StockItemDTO:
    """Stock of one purchasable at one location: on_hand (the ledger sum), held by live checkouts, available."""

    id: int
    purchasable_type: str
    purchasable_key: str
    location: str
    on_hand: int
    held: int
    available: int
    low_stock_threshold: int | None
    oversell_policy: str
    alert_state: str
    updated_at: dt.datetime


@dataclass
class StockTransferDTO:
    """The two stock items a transfer moved units between."""

    source: StockItemDTO
    target: StockItemDTO


@dataclass
class StockMovementDTO:
    """One append-only ledger row; on_hand_delta and held_delta are signed units."""

    id: int
    stock_item: int
    purchasable_type: str
    purchasable_key: str
    location: str
    kind: str
    on_hand_delta: int
    held_delta: int
    on_hand_after: int
    reason: str
    ref_type: str
    ref_key: str
    actor_id: str
    created_at: dt.datetime
