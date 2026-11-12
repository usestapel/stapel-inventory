"""Beat: hold expiry and the ledger/cache reconciliation."""
import datetime as dt
import logging

import pytest
from django.core.management import call_command
from django.utils import timezone

from stapel_inventory import beat
from stapel_inventory.models import HoldStatus, StockHold, StockHoldLine, StockItem
from stapel_inventory.services import reconcile

from . import orders
from .factories import available, stock_of

pytestmark = pytest.mark.django_db


def test_schedule_names_both_passes():
    schedule = beat.get_inventory_beat_schedule()
    assert {e["task"] for e in schedule.values()} == {beat.EXPIRE_TASK_NAME, beat.RECONCILE_TASK_NAME}
    assert schedule[beat.EXPIRE_BEAT_KEY]["schedule"] == 60.0


def test_only_due_holds_expire():
    stock_of("a", 5)
    due, live = orders.attempt_key(), orders.attempt_key()
    orders.reserve(due, [orders.line(0, "a", 2)])
    orders.reserve(live, [orders.line(0, "a", 1)])
    StockHold.objects.filter(attempt_key=due).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    assert beat.expire_holds() == 1
    assert StockHold.objects.get(attempt_key=live).status == HoldStatus.ACTIVE
    assert available("a") == 4
    assert beat.expire_holds() == 0


def test_ttl_comes_from_settings(settings):
    settings.STAPEL_INVENTORY = {"HOLD_TTL_SECONDS": 60}
    stock_of("a", 1)
    key = orders.attempt_key()
    before = timezone.now()
    orders.reserve(key, [orders.line(0, "a", 1)])
    expires = StockHold.objects.get(attempt_key=key).expires_at
    assert dt.timedelta(seconds=59) < expires - before < dt.timedelta(seconds=61)


def test_default_ttl_outlives_the_orders_orphan_sweep():
    from stapel_inventory.conf import inventory_settings

    assert inventory_settings.HOLD_TTL_SECONDS > 900


class TestReconcile:
    def test_clean_ledger_reports_nothing(self):
        stock_of("a", 5)
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])
        assert reconcile.reconcile() == {"on_hand": [], "held": []}

    def test_cache_written_behind_the_ledger_is_repaired(self, caplog):
        item = stock_of("a", 5)
        StockItem.objects.filter(pk=item.pk).update(on_hand=42)
        with caplog.at_level(logging.ERROR):
            found = reconcile.reconcile()
        assert found["on_hand"] == [{"stock_item_id": item.pk, "cached": 42, "ledger": 5}]
        assert "repaired from the ledger" in caplog.text
        item.refresh_from_db()
        assert item.on_hand == 5

    def test_report_only(self, settings):
        settings.STAPEL_INVENTORY = {"RECONCILE_REPAIR": False}
        item = stock_of("a", 5)
        StockItem.objects.filter(pk=item.pk).update(on_hand=1)
        assert beat.reconcile_stock() == 1
        item.refresh_from_db()
        assert item.on_hand == 1

    def test_hold_count_drift_is_reported_never_repaired(self):
        stock_of("a", 5)
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])
        StockHoldLine.objects.update(quantity=3)
        found = reconcile.reconcile()
        assert found["held"][0]["ledger"] == 2 and found["held"][0]["holds"] == 3

    def test_housekeeping_command(self, capsys):
        stock_of("a", 5)
        call_command("inventory_housekeeping", "--report-only")
        assert "expired 0, on_hand drift 0, held drift 0" in capsys.readouterr().out
