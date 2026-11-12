"""Low / out / back-in-stock facts fire on transitions only (dedupe)."""
import pytest

from stapel_inventory.services import stock

from . import orders
from .factories import stock_of

pytestmark = pytest.mark.django_db


def _names(events):
    return [e.event_type.rsplit(".", 1)[1] for e in events]


def test_a_shelf_selling_down_says_low_once_then_out_once(captured_events, settings):
    settings.STAPEL_INVENTORY = {"LOW_STOCK_THRESHOLD": 3}
    stock_of("a", 6)
    captured_events.clear()
    for _ in range(6):
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 1)])
        orders.commit(key)
    assert _names(captured_events) == ["low", "out"]
    low = captured_events[0].payload
    assert (low["available"], low["threshold"], low["location"]) == (3, 3, "main")


def test_restock_from_out_says_back_in_stock(captured_events):
    stock_of("a", 1)
    key = orders.attempt_key()
    orders.reserve(key, [orders.line(0, "a", 1)])
    captured_events.clear()
    stock.receive("listing", "a", None, 50, actor_id="s")
    assert _names(captured_events) == ["back_in_stock"]


def test_back_into_low_says_both(captured_events):
    stock_of("a", 0)
    captured_events.clear()
    stock.receive("listing", "a", None, 2, actor_id="s")
    assert _names(captured_events) == ["back_in_stock", "low"]


def test_a_released_hold_brings_an_item_back(captured_events):
    stock_of("a", 10)
    key = orders.attempt_key()
    orders.reserve(key, [orders.line(0, "a", 10)])
    assert _names(captured_events)[-1] == "out"
    captured_events.clear()
    orders.release(key)
    assert _names(captured_events) == ["back_in_stock"]


def test_low_to_ok_is_silent(captured_events):
    stock_of("a", 2)
    captured_events.clear()
    stock.receive("listing", "a", None, 100, actor_id="s")
    assert captured_events == []


def test_facts_roll_back_with_a_refused_reserve(captured_events):
    stock_of("a", 1)
    stock_of("b", 0)
    captured_events.clear()
    orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1), orders.line(1, "b", 1)])
    assert captured_events == []


def test_new_item_starts_out_and_its_first_receipt_is_back_in_stock(captured_events):
    from stapel_inventory.models import AlertState

    item = stock_of("fresh", 0)
    assert item.alert_state == AlertState.OUT
    stock.receive("listing", "fresh", None, 100, actor_id="s")
    assert _names(captured_events) == ["back_in_stock"]
