"""The checkout hook, driven by a fake orders caller sending the §2 payloads."""
import datetime as dt
import json
from pathlib import Path

import jsonschema
import pytest
from django.db import transaction
from django.utils import timezone

from stapel_inventory.models import HoldStatus, MovementKind, StockHold, StockMovement
from stapel_inventory.services import holds

from . import orders
from .factories import available, location, stock_of

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas" / "functions"

pytestmark = pytest.mark.django_db


def _response_schema(name):
    return json.loads((SCHEMAS / f"{name}.json").read_text())["$defs"]["response"]


def _valid(name, answer):
    jsonschema.validate(answer, _response_schema(name))
    return answer


class TestReserve:
    def test_holds_and_lowers_available(self):
        stock_of("a", 5)
        key = orders.attempt_key()
        answer = _valid("inventory.checkout.reserve", orders.reserve(key, [orders.line(0, "a", 2)]))
        assert answer["ok"] is True and answer["errors"] == [] and answer["token"]
        assert available("a") == 3
        hold = StockHold.objects.get(attempt_key=key)
        assert hold.status == HoldStatus.ACTIVE and hold.user_id == "7"
        assert StockMovement.objects.filter(kind=MovementKind.HOLD).get().held_delta == 2

    def test_refusal_names_the_line(self):
        stock_of("a", 1)
        answer = _valid("inventory.checkout.reserve",
                        orders.reserve(orders.attempt_key(), [orders.line(3, "a", 2)]))
        assert answer["ok"] is False and answer["token"] is None
        assert answer["errors"] == [{
            "code": "error.409.insufficient_stock", "field": "lines.3.quantity",
            "params": {"index": 3, "purchasable_type": "listing", "purchasable_key": "a",
                       "requested": 2, "available": 1},
        }]

    def test_all_or_nothing_across_lines(self):
        stock_of("a", 5)
        stock_of("b", 1)
        key = orders.attempt_key()
        answer = orders.reserve(key, [orders.line(0, "a", 2), orders.line(1, "b", 3)])
        assert answer["ok"] is False and [e["params"]["index"] for e in answer["errors"]] == [1]
        assert available("a") == 5 and available("b") == 1
        assert not StockHold.objects.filter(attempt_key=key).exists()
        assert not StockMovement.objects.filter(kind=MovementKind.HOLD).exists()

    def test_every_refusal_is_reported(self):
        stock_of("a", 0)
        stock_of("b", 0)
        answer = orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1), orders.line(1, "b", 1)])
        assert [e["params"]["index"] for e in answer["errors"]] == [0, 1]

    def test_duplicate_lines_are_summed(self):
        stock_of("a", 3)
        answer = orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2), orders.line(1, "a", 2)])
        assert answer["ok"] is False
        assert answer["errors"][0]["params"]["requested"] == 4

    def test_untracked_passes_through(self):
        key = orders.attempt_key()
        answer = orders.reserve(key, [orders.line(0, "ghost", 50)])
        assert answer["ok"] is True
        assert StockHold.objects.get(attempt_key=key).lines.count() == 0

    def test_untracked_denied_when_asked(self, settings):
        settings.STAPEL_INVENTORY = {"TRACK_UNKNOWN": "deny"}
        answer = orders.reserve(orders.attempt_key(), [orders.line(0, "ghost", 1)])
        assert answer["ok"] is False
        assert answer["errors"][0]["code"] == "error.409.stock_not_tracked"

    def test_guest_user_id_is_null(self):
        stock_of("a", 1)
        key = orders.attempt_key()
        assert orders.reserve(key, [orders.line(0, "a", 1)], user_id=None)["ok"] is True
        assert StockHold.objects.get(attempt_key=key).user_id is None

    def test_rolled_back_with_the_callers_transaction(self):
        """In process, orders reserves inside its checkout transaction."""
        stock_of("a", 2)
        key = orders.attempt_key()
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                assert orders.reserve(key, [orders.line(0, "a", 2)])["ok"] is True
                raise RuntimeError("checkout failed after the reserve")
        assert available("a") == 2 and not StockHold.objects.exists()


class TestReserveIdempotency:
    def test_replay_returns_the_same_token(self):
        stock_of("a", 5)
        key = orders.attempt_key()
        first = orders.reserve(key, [orders.line(0, "a", 2)])
        second = orders.reserve(key, [orders.line(0, "a", 2)])
        assert first == second and available("a") == 3
        assert StockMovement.objects.filter(kind=MovementKind.HOLD).count() == 1

    def test_changed_lines_re_reserve_under_the_same_key(self):
        stock_of("a", 5)
        key = orders.attempt_key()
        first = orders.reserve(key, [orders.line(0, "a", 2)])
        second = orders.reserve(key, [orders.line(0, "a", 4)])
        assert second["ok"] and second["token"] == first["token"] and available("a") == 1

    def test_reserve_after_release_reserves_anew(self):
        stock_of("a", 2)
        key = orders.attempt_key()
        token = orders.reserve(key, [orders.line(0, "a", 2)])["token"]
        assert orders.release(key, token)["ok"] is True
        assert available("a") == 2
        again = orders.reserve(key, [orders.line(0, "a", 2)])
        assert again["ok"] and available("a") == 0
        assert StockHold.objects.get(attempt_key=key).status == HoldStatus.ACTIVE

    def test_reserve_after_expiry_reserves_anew(self):
        stock_of("a", 2)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        StockHold.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        assert available("a") == 2
        assert orders.reserve(key, [orders.line(0, "a", 2)])["ok"] is True
        assert available("a") == 0

    def test_reserve_on_a_committed_hold_answers_ok(self):
        stock_of("a", 2)
        key = orders.attempt_key()
        token = orders.reserve(key, [orders.line(0, "a", 2)])["token"]
        orders.commit(key, token)
        assert orders.reserve(key, [orders.line(0, "a", 2)]) == {"ok": True, "token": token, "errors": []}


class TestCommit:
    def test_decrements_on_hand_and_consumes_the_hold(self):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        token = orders.reserve(key, [orders.line(0, "a", 2)])["token"]
        answer = _valid("inventory.checkout.commit", orders.commit(key, token, order_id="77"))
        assert answer == {"ok": True, "token": token, "errors": []}
        item.refresh_from_db()
        assert item.on_hand == 3 and available("a") == 3
        hold = StockHold.objects.get(attempt_key=key)
        assert hold.status == HoldStatus.COMMITTED and hold.reference == "77"
        move = StockMovement.objects.get(kind=MovementKind.COMMIT)
        assert (move.on_hand_delta, move.held_delta, move.on_hand_after) == (-2, -2, 3)

    def test_replay_is_a_noop(self):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.commit(key)
        orders.commit(key)
        item.refresh_from_db()
        assert item.on_hand == 3
        assert StockMovement.objects.filter(kind=MovementKind.COMMIT).count() == 1

    def test_null_token_acts_on_the_attempt_key(self):
        stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 1)])
        assert orders.commit(key, None)["ok"] is True
        assert StockHold.objects.get(attempt_key=key).status == HoldStatus.COMMITTED

    def test_unknown_key_answers_ok_so_orders_stops_retrying(self, caplog):
        assert orders.commit(orders.attempt_key())["ok"] is True
        assert "finds no hold" in caplog.text

    def test_expired_hold_is_committed_anyway(self, captured_events):
        item = stock_of("a", 1)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 1)])
        StockHold.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        assert holds.expire_due() == 1
        rival = orders.attempt_key()
        orders.reserve(rival, [orders.line(0, "a", 1)])
        orders.commit(rival, order_id="1")
        captured_events.clear()
        assert orders.commit(key, order_id="2")["ok"] is True
        item.refresh_from_db()
        assert item.on_hand == -1
        oversold = [e for e in captured_events if e.event_type == "inventory.stock.oversold"]
        assert len(oversold) == 1 and oversold[0].payload["oversold"] == 1
        assert oversold[0].payload["reference"] == "2"


class TestRelease:
    def test_releases_an_active_hold(self):
        stock_of("a", 2)
        key = orders.attempt_key()
        token = orders.reserve(key, [orders.line(0, "a", 2)])["token"]
        answer = _valid("inventory.checkout.release", orders.release(key, token))
        assert answer["ok"] is True and available("a") == 2
        hold = StockHold.objects.get(attempt_key=key)
        assert (hold.status, hold.release_reason) == (HoldStatus.RELEASED, "checkout_failed")

    def test_unknown_key_is_ok(self):
        assert orders.release(orders.attempt_key()) == {"ok": True, "token": None, "errors": []}

    def test_cancel_after_commit_restocks_once(self):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.commit(key, order_id="9")
        orders.release(key, order_id="9", reason="cancelled")
        orders.release(key, order_id="9", reason="cancelled")
        item.refresh_from_db()
        assert item.on_hand == 5
        assert StockMovement.objects.filter(kind=MovementKind.RESTOCK).count() == 1
        assert StockHold.objects.get(attempt_key=key).status == HoldStatus.RETURNED

    def test_restock_is_a_switch(self, settings):
        settings.STAPEL_INVENTORY = {"RESTOCK_ON_CANCEL": False}
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.commit(key)
        orders.release(key, reason="cancelled")
        item.refresh_from_db()
        assert item.on_hand == 3

    def test_orphan_release_never_restocks_a_committed_hold(self):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.commit(key)
        orders.release(key, reason="orphaned")
        item.refresh_from_db()
        assert item.on_hand == 3

    def test_cancel_that_overtakes_the_commit_wins(self):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.release(key, order_id="9", reason="cancelled")
        assert orders.commit(key, order_id="9")["ok"] is True
        item.refresh_from_db()
        assert item.on_hand == 5
        assert StockHold.objects.get(attempt_key=key).status == HoldStatus.RELEASED


class TestAllocation:
    def test_single_default_sells_only_the_default_location(self):
        location("back", priority=10)
        stock_of("a", 1)
        stock_of("a", 10, location="back")
        assert available("a") == 1
        answer = orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])
        assert answer["errors"][0]["params"]["available"] == 1

    def test_priority_splits_across_locations(self, settings):
        settings.STAPEL_INVENTORY = {"ALLOCATION": "priority"}
        location("front", priority=1)
        stock_of("a", 1)
        stock_of("a", 2, location="front")
        key = orders.attempt_key()
        assert available("a") == 3
        assert orders.reserve(key, [orders.line(0, "a", 3)])["ok"] is True
        taken = {line.stock_item.location.code: line.quantity
                 for line in StockHold.objects.get(attempt_key=key).lines.select_related("stock_item__location")}
        assert taken == {"front": 2, "main": 1}

    def test_inactive_location_does_not_sell(self, settings):
        settings.STAPEL_INVENTORY = {"ALLOCATION": "priority"}
        location("closed", priority=1, active=False)
        stock_of("a", 0)
        from stapel_inventory.models import Location
        from stapel_inventory.services import stock

        closed = Location.objects.get(code="closed")
        item = stock.get_or_create_item("listing", "a", closed)
        stock.write_movement(item, MovementKind.RECEIPT, on_hand_delta=5)
        assert available("a") == 0

    def test_backorder_holds_beyond_stock(self, settings):
        settings.STAPEL_INVENTORY = {"OVERSELL": "backorder"}
        item = stock_of("a", 1)
        key = orders.attempt_key()
        assert orders.reserve(key, [orders.line(0, "a", 3)])["ok"] is True
        line = StockHold.objects.get(attempt_key=key).lines.get()
        assert (line.quantity, line.backordered) == (3, 2)
        orders.commit(key)
        item.refresh_from_db()
        assert item.on_hand == -2

    def test_per_item_policy_overrides_the_setting(self):
        stock_of("a", 0, oversell_policy="backorder")
        assert orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])["ok"] is True

    def test_registered_strategy_by_name(self, settings):
        from stapel_inventory.allocation import Priority, register_allocation

        register_allocation("nearest", Priority)
        settings.STAPEL_INVENTORY = {"ALLOCATION": "nearest"}
        from stapel_inventory.allocation import active_allocation

        assert isinstance(active_allocation(), Priority)


class TestPayloadContract:
    def test_reserve_payload_without_attempt_key_is_refused_by_schema(self):
        from stapel_core.comm import call

        payload = orders.reserve_payload("x", [orders.line(0, "a", 1)])
        del payload["attempt_key"]
        with pytest.raises(Exception):
            call(orders.HOOK["reserve"], payload)

    def test_release_reason_vocabulary(self):
        from stapel_core.comm import call

        with pytest.raises(Exception):
            call(orders.HOOK["release"], {"attempt_key": "k", "token": None, "order_id": None,
                                          "reason": "because"})
