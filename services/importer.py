"""CSV stock import: one plan function for the dry run and the apply.

Columns (header row required): ``purchasable_type, purchasable_key,
quantity`` and optionally ``location`` (code; empty = the default) and
``low_stock_threshold``. Mode ``set`` (default) makes ``on_hand`` equal the
quantity — a stock count, written as an ``adjust`` movement; mode
``receive`` adds it as a ``receipt``. Every row is validated before any is
applied; a file with one bad row applies nothing.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass

from django.db import transaction

from ..models import (
    LOCATION_CODE_MAX_LENGTH,
    PURCHASABLE_KEY_MAX_LENGTH,
    PURCHASABLE_TYPE_MAX_LENGTH,
    SYSTEM_ACTOR,
    Location,
    StockItem,
)

MODES = ("set", "receive")
REQUIRED = ("purchasable_type", "purchasable_key", "quantity")
IMPORT_REASON = "csv import"


@dataclass(frozen=True)
class PlannedRow:
    line: int
    purchasable_type: str
    purchasable_key: str
    location: str
    quantity: int
    low_stock_threshold: int | None
    current: int
    change: int


@dataclass(frozen=True)
class Plan:
    rows: list
    errors: list

    @property
    def changes(self) -> int:
        return sum(1 for r in self.rows if r.change)


def _int(value, line, name, errors, *, allow_empty=False):
    value = (value or "").strip()
    if value == "" and allow_empty:
        return None
    try:
        number = int(value)
    except ValueError:
        errors.append(f"line {line}: {name} {value!r} is not a whole number")
        return None
    if number < 0:
        errors.append(f"line {line}: {name} must not be negative")
        return None
    return number


def plan(handle, *, mode: str = "set") -> Plan:
    """Read the CSV and say what applying it would do (no writes)."""
    from .stock import default_location

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    reader = csv.DictReader(handle)
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return Plan(rows=[], errors=[f"missing column(s): {', '.join(missing)}"])
    default_code = default_location().code
    known = set(Location.objects.filter(active=True).values_list("code", flat=True))
    rows, errors = [], []
    for line, raw in enumerate(reader, start=2):
        ptype = (raw.get("purchasable_type") or "").strip()
        pkey = (raw.get("purchasable_key") or "").strip()
        code = (raw.get("location") or "").strip() or default_code
        if not ptype or len(ptype) > PURCHASABLE_TYPE_MAX_LENGTH:
            errors.append(f"line {line}: purchasable_type is empty or longer than {PURCHASABLE_TYPE_MAX_LENGTH}")
            continue
        if not pkey or len(pkey) > PURCHASABLE_KEY_MAX_LENGTH:
            errors.append(f"line {line}: purchasable_key is empty or longer than {PURCHASABLE_KEY_MAX_LENGTH}")
            continue
        if len(code) > LOCATION_CODE_MAX_LENGTH or code not in known:
            errors.append(f"line {line}: location {code!r} is not an active location")
            continue
        quantity = _int(raw.get("quantity"), line, "quantity", errors)
        threshold = _int(raw.get("low_stock_threshold"), line, "low_stock_threshold", errors, allow_empty=True)
        if quantity is None:
            continue
        item = StockItem.objects.filter(purchasable_type=ptype, purchasable_key=pkey,
                                        location__code=code).first()
        current = item.on_hand if item else 0
        change = quantity - current if mode == "set" else quantity
        rows.append(PlannedRow(line, ptype, pkey, code, quantity, threshold, current, change))
    return Plan(rows=rows, errors=errors)


def apply(planned: Plan, *, mode: str = "set", actor_id: str = SYSTEM_ACTOR) -> int:
    """Apply a clean plan in one transaction. Returns the number of rows that changed stock."""
    from . import stock

    if planned.errors:
        raise ValueError("the plan has errors; nothing applied")
    applied = 0
    with transaction.atomic():
        for row in planned.rows:
            if mode == "set" and row.change:
                stock.adjust(row.purchasable_type, row.purchasable_key, row.location,
                             on_hand=row.quantity, reason=IMPORT_REASON, actor_id=actor_id)
            elif mode == "receive" and row.quantity:
                stock.receive(row.purchasable_type, row.purchasable_key, row.location,
                              row.quantity, reason=IMPORT_REASON, actor_id=actor_id)
            else:
                stock.get_or_create_item(row.purchasable_type, row.purchasable_key,
                                         stock.location_by_code(row.location))
            if row.change:
                applied += 1
            if row.low_stock_threshold is not None:
                item = StockItem.objects.get(purchasable_type=row.purchasable_type,
                                             purchasable_key=row.purchasable_key,
                                             location__code=row.location)
                stock.update_item(item.pk, {"low_stock_threshold": row.low_stock_threshold})
    return applied
