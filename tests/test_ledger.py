"""The ledger is append-only, and the hookless order fallback writes to it once."""
import pytest
from django.db import transaction
from stapel_core.comm import emit

from stapel_inventory.models import MovementKind, StockItem, StockMovement

from .factories import stock_of

pytestmark = pytest.mark.django_db


class TestAppendOnly:
    def test_no_rewrite_no_delete(self):
        stock_of("a", 1)
        move = StockMovement.objects.get()
        move.reason = "edited"
        with pytest.raises(TypeError):
            move.save()
        with pytest.raises(TypeError):
            move.delete()
        with pytest.raises(TypeError):
            StockMovement.objects.update(reason="x")
        with pytest.raises(TypeError):
            StockMovement.objects.all().delete()
        assert StockMovement.objects.get().reason == "fixture"


def _placed(order_id, lines):
    with transaction.atomic():
        emit("orders.order.placed", {
            "order_id": order_id, "user_id": "7", "currency": "USD", "total": "10.00",
            "placed_at": "2026-11-12T10:00:00+00:00",
            "lines": [{"purchasable_type": "listing", "purchasable_key": k, "quantity": q,
                       "unit_price": "1.00", "seller_id": None} for k, q in lines],
        })


class TestOrderPlacedFallback:
    def test_off_by_default(self):
        item = stock_of("a", 5)
        _placed("1", [("a", 2)])
        item.refresh_from_db()
        assert item.on_hand == 5

    def test_decrements_once_per_order(self, settings):
        settings.STAPEL_INVENTORY = {"DECREMENT_ON_ORDER_PLACED": True}
        item = stock_of("a", 5)
        _placed("1", [("a", 2), ("ghost", 1)])
        _placed("1", [("a", 2)])
        item.refresh_from_db()
        assert item.on_hand == 3
        move = StockMovement.objects.get(kind=MovementKind.COMMIT)
        assert (move.ref_type, move.ref_key) == ("order", "1")

    def test_an_order_is_the_fact_even_beyond_stock(self, settings, captured_events):
        settings.STAPEL_INVENTORY = {"DECREMENT_ON_ORDER_PLACED": True}
        stock_of("a", 1)
        _placed("2", [("a", 3)])
        assert StockItem.objects.get().on_hand == -2
        assert [e.event_type for e in captured_events][-2:] == ["inventory.stock.oversold", "inventory.stock.out"]
