"""Stock fixtures built through the services (so the ledger is always right)."""
from __future__ import annotations

from stapel_inventory.models import Location, StockItem
from stapel_inventory.services import stock


def stock_of(key, quantity, *, ptype="listing", location=None, **item_fields) -> StockItem:
    item = stock.receive(ptype, key, location, quantity, reason="fixture", actor_id="staff-1") \
        if quantity else stock.get_or_create_item(ptype, key, stock.location_by_code(location))
    if item_fields:
        stock.update_item(item.pk, item_fields)
        item.refresh_from_db()
    return item


def location(code, *, priority=100, active=True) -> Location:
    stock.default_location()
    return Location.objects.create(code=code, name=code.title(), priority=priority, active=active)


def available(key, *, ptype="listing", location=None):
    entry = stock.availability(ptype, [key], location)[key]
    return entry["available"]
