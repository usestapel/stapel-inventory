"""Allocation strategies — which locations a reserve takes stock from.

A strategy is pure: it sees the candidate stock items of one purchasable
(already locked and counted by the service) and answers which units to take
from which item. It never touches the database.

Built-ins (``STAPEL_INVENTORY["ALLOCATION"]``):

- ``single_default`` — only the default location sells; the other
  locations are storage the staff transfers from.
- ``priority`` — active locations ordered by ``(priority, location id)``;
  a line may split across locations.

Under the ``backorder`` policy the part no location covers is held as
``backordered`` on the first eligible item; under ``deny`` the whole
reserve is refused. A strategy is registered by name
(:func:`register_allocation`) or named by dotted path in settings; the
environment may pick among the registered short names only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class Candidate:
    """One stock item of the purchasable, as counted under its row lock."""

    stock_item_id: int
    location_id: int
    location_code: str
    priority: int
    is_default: bool
    available: int


@dataclass(frozen=True)
class Allocation:
    """Units taken from one stock item; ``backordered`` is part of ``quantity``."""

    stock_item_id: int
    quantity: int
    backordered: int = 0


class AllocationStrategy:
    """Interface: ``eligible`` orders the selling items, ``allocate`` takes from them."""

    name = ""

    def eligible(self, candidates: list[Candidate]) -> list[Candidate]:  # pragma: no cover - interface
        raise NotImplementedError

    def available(self, candidates: list[Candidate]) -> int:
        """Units this strategy could sell now (negative items count as zero)."""
        return sum(max(c.available, 0) for c in self.eligible(candidates))

    def allocate(self, quantity: int, candidates: list[Candidate], policy: str) -> list[Allocation] | None:
        """Allocations covering ``quantity``, or ``None`` when ``deny`` cannot cover it."""
        pool = self.eligible(candidates)
        remaining = quantity
        taken: dict[int, int] = {}
        for candidate in pool:
            if remaining <= 0:
                break
            take = min(max(candidate.available, 0), remaining)
            if take > 0:
                taken[candidate.stock_item_id] = take
                remaining -= take
        backordered = 0
        if remaining > 0:
            if policy != "backorder" or not pool:
                return None
            first = pool[0].stock_item_id
            taken[first] = taken.get(first, 0) + remaining
            backordered = remaining
        first_id = pool[0].stock_item_id if pool else None
        return [
            Allocation(item_id, qty, backordered if item_id == first_id else 0)
            for item_id, qty in taken.items()
        ]


class SingleDefault(AllocationStrategy):
    name = "single_default"

    def eligible(self, candidates):
        return [c for c in candidates if c.is_default]


class Priority(AllocationStrategy):
    name = "priority"

    def eligible(self, candidates):
        return sorted(candidates, key=lambda c: (c.priority, c.location_id))


_BUILTINS: dict[str, Callable[[], AllocationStrategy]] = {
    "single_default": SingleDefault,
    "priority": Priority,
}
_registered: dict[str, Callable[[], AllocationStrategy]] = {}


def register_allocation(name: str, factory: Callable[[], AllocationStrategy]) -> None:
    """Register a strategy under a short name the settings/env may choose."""
    _registered[name] = factory


def reset_allocations() -> None:
    """Tests only."""
    _registered.clear()


def allocation_names() -> tuple[str, ...]:
    """The short names ``ALLOCATION`` accepts (also the env vocabulary)."""
    return tuple(sorted({**_BUILTINS, **_registered}))


def resolve_allocation(value) -> AllocationStrategy:
    """Resolver for ``inventory_settings.ALLOCATION``: short name or dotted path."""
    if isinstance(value, AllocationStrategy):
        return value
    name = str(value or "single_default")
    factory = _registered.get(name) or _BUILTINS.get(name)
    if factory is None:
        if "." not in name:
            raise ImproperlyConfigured(
                f'STAPEL_INVENTORY["ALLOCATION"] = {name!r} is not a known allocation '
                f"strategy. Known: {', '.join(allocation_names())}."
            )
        from django.utils.module_loading import import_string

        factory = import_string(name)
    strategy = factory() if callable(factory) else factory
    if not isinstance(strategy, AllocationStrategy):
        raise ImproperlyConfigured(
            f'STAPEL_INVENTORY["ALLOCATION"] = {name!r} does not produce an AllocationStrategy'
        )
    if not strategy.name:
        strategy.name = name
    return strategy


def active_allocation() -> AllocationStrategy:
    from .conf import inventory_settings

    return inventory_settings.ALLOCATION


__all__ = [
    "Allocation",
    "AllocationStrategy",
    "Candidate",
    "Priority",
    "SingleDefault",
    "active_allocation",
    "allocation_names",
    "register_allocation",
    "reset_allocations",
    "resolve_allocation",
]
