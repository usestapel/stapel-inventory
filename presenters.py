"""Presenters for stapel-inventory — the DTO-building layer (§55).

Views never instantiate a ``dto.py`` dataclass (SWAP002) nor import a
concrete presenter (SWAP001); they resolve one through ``get_presenter`` so
a host can swap the presentation via ``STAPEL_SWAP`` without forking.

The availability presenter is also where the public disclosure policy
(``PUBLIC_AVAILABILITY``) is applied: the Function answers exact numbers,
the public endpoint answers what the policy allows.
"""
from __future__ import annotations

from stapel_core.django.swappable import declare_swap, get_presenter

from .dto import (
    AvailabilityDTO,
    AvailabilityItemDTO,
    LocationDTO,
    StockItemDTO,
    StockMovementDTO,
    StockTransferDTO,
)

AVAILABILITY_PRESENTER_KEY = "STAPEL_INVENTORY_AVAILABILITY_PRESENTER"
STOCK_PRESENTER_KEY = "STAPEL_INVENTORY_STOCK_PRESENTER"
DEFAULT_AVAILABILITY_PRESENTER = "stapel_inventory.presenters.AvailabilityPresenter"
DEFAULT_STOCK_PRESENTER = "stapel_inventory.presenters.StockPresenter"

declare_swap(AVAILABILITY_PRESENTER_KEY, DEFAULT_AVAILABILITY_PRESENTER)
declare_swap(STOCK_PRESENTER_KEY, DEFAULT_STOCK_PRESENTER)


def public_state(entry: dict) -> tuple[str, int | None]:
    """``(state, quantity)`` the public may see for one availability entry."""
    from .conf import inventory_settings

    mode = inventory_settings.PUBLIC_AVAILABILITY
    if not entry["tracked"]:
        return ("untracked" if entry["in_stock"] else "out_of_stock"), None
    available = int(entry["available"])
    if available > 0:
        few = mode != "in_stock" and available <= int(inventory_settings.FEW_LEFT_THRESHOLD)
        state = "few_left" if few else "in_stock"
    else:
        state = "backorder" if entry["policy"] == "backorder" else "out_of_stock"
    quantity = max(available, 0) if mode == "exact" else None
    return state, quantity


class AvailabilityPresenter:
    """``services.stock.availability`` output -> AvailabilityDTO under the disclosure policy."""

    def present(self, purchasable_type: str, entries: dict) -> AvailabilityDTO:
        items = []
        for key, entry in entries.items():
            state, quantity = public_state(entry)
            items.append(AvailabilityItemDTO(
                purchasable_type=purchasable_type, purchasable_key=key, state=state, quantity=quantity,
            ))
        return AvailabilityDTO(items=items)


class StockPresenter:
    """Stock items, locations, transfers and ledger rows -> DTOs."""

    def location(self, location) -> LocationDTO:
        return LocationDTO(code=location.code, name=location.name, priority=location.priority,
                           is_default=location.is_default, active=location.active)

    def item(self, item, held: int = 0, location_code: str | None = None) -> StockItemDTO:
        return StockItemDTO(
            id=item.pk,
            purchasable_type=item.purchasable_type,
            purchasable_key=item.purchasable_key,
            location=location_code or item.location.code,
            on_hand=item.on_hand,
            held=held,
            available=item.on_hand - held,
            low_stock_threshold=item.low_stock_threshold,
            oversell_policy=item.oversell_policy,
            alert_state=item.alert_state,
            updated_at=item.updated_at,
        )

    def items(self, items) -> list[StockItemDTO]:
        from .services.stock import held_by_item

        items = list(items)
        held = held_by_item([i.pk for i in items])
        return [self.item(i, held.get(i.pk, 0)) for i in items]

    def transfer(self, result) -> StockTransferDTO:
        source, target = self.items([result.source, result.target])
        return StockTransferDTO(source=source, target=target)

    def movement(self, movement) -> StockMovementDTO:
        item = movement.stock_item
        return StockMovementDTO(
            id=movement.pk,
            stock_item=item.pk,
            purchasable_type=item.purchasable_type,
            purchasable_key=item.purchasable_key,
            location=item.location.code,
            kind=movement.kind,
            on_hand_delta=movement.on_hand_delta,
            held_delta=movement.held_delta,
            on_hand_after=movement.on_hand_after,
            reason=movement.reason,
            ref_type=movement.ref_type,
            ref_key=movement.ref_key,
            actor_id=movement.actor_id,
            created_at=movement.created_at,
        )


def get_availability_presenter() -> type:
    return get_presenter(AVAILABILITY_PRESENTER_KEY, default=DEFAULT_AVAILABILITY_PRESENTER)


def get_stock_presenter() -> type:
    return get_presenter(STOCK_PRESENTER_KEY, default=DEFAULT_STOCK_PRESENTER)


__all__ = [
    "AVAILABILITY_PRESENTER_KEY",
    "STOCK_PRESENTER_KEY",
    "AvailabilityPresenter",
    "StockPresenter",
    "get_availability_presenter",
    "get_stock_presenter",
    "public_state",
]
