"""Release and commit over every hold state: only ``cancelled`` undoes a commit.

The table is the contract (orders CONTRACT-DELTAS 13, 16, 17, 22): a release
with ``checkout_failed`` or ``orphaned`` for a committed pair is a no-op that
answers ok; ``cancelled`` restocks (``RESTOCK_ON_CANCEL``). A commit after a
release or expiry that was not a cancel still decrements (the placed order
is the fact); after a cancel it is a no-op.
"""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_inventory.models import HoldStatus, StockHold
from stapel_inventory.services import holds

from . import orders
from .factories import stock_of

pytestmark = pytest.mark.django_db

REASONS = ("checkout_failed", "orphaned", "cancelled")
STATES = ("unknown", "active", "committed", "expired", "returned",
          "released:checkout_failed", "released:orphaned", "released:cancelled")
A, C, X, E, RT = (HoldStatus.ACTIVE, HoldStatus.COMMITTED, HoldStatus.RELEASED,
                  HoldStatus.EXPIRED, HoldStatus.RETURNED)


def _arrange(state) -> str:
    stock_of("a", 5)
    key = orders.attempt_key()
    if state == "unknown":
        return key
    assert orders.reserve(key, [orders.line(0, "a", 2)])["ok"] is True
    if state in ("committed", "returned"):
        orders.commit(key, order_id="9")
    if state == "returned":
        orders.release(key, order_id="9", reason="cancelled")
    elif state == "expired":
        StockHold.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        assert holds.expire_due() == 1
    elif state.startswith("released:"):
        orders.release(key, reason=state.split(":", 1)[1])
    return key


def _observe(key):
    """(hold status, on_hand, held) of the one stock item."""
    from stapel_inventory.models import StockItem
    from stapel_inventory.services.stock import held_by_item

    item = StockItem.objects.get()
    hold = StockHold.objects.filter(attempt_key=key).first()
    return (None if hold is None else hold.status, item.on_hand,
            held_by_item([item.pk], timezone.now()).get(item.pk, 0))


#: (state, reason) -> observation after the release.
RELEASE_TABLE = {
    **{("unknown", r): (None, 5, 0) for r in REASONS},
    **{("active", r): (X, 5, 0) for r in REASONS},
    ("committed", "checkout_failed"): (C, 3, 0),
    ("committed", "orphaned"): (C, 3, 0),
    ("committed", "cancelled"): (RT, 5, 0),
    **{("returned", r): (RT, 5, 0) for r in REASONS},
    **{("expired", r): (E, 5, 0) for r in REASONS},
    **{(f"released:{first}", r): (X, 5, 0) for first in REASONS for r in REASONS},
}

#: state -> observation after a commit.
COMMIT_TABLE = {
    "unknown": (None, 5, 0),
    "active": (C, 3, 0),
    "committed": (C, 3, 0),
    "returned": (RT, 5, 0),
    "expired": (C, 3, 0),
    "released:checkout_failed": (C, 3, 0),
    "released:orphaned": (C, 3, 0),
    "released:cancelled": (X, 5, 0),
}


def test_the_tables_cover_every_pair():
    assert set(RELEASE_TABLE) == {(s, r) for s in STATES for r in REASONS}
    assert set(COMMIT_TABLE) == set(STATES)


@pytest.mark.parametrize("state,reason", sorted(RELEASE_TABLE))
def test_release(state, reason):
    key = _arrange(state)
    for _ in range(2):
        order_id = "9" if reason == "cancelled" else None
        assert orders.release(key, order_id=order_id, reason=reason)["ok"] is True
        assert _observe(key) == RELEASE_TABLE[(state, reason)]


@pytest.mark.parametrize("state", sorted(COMMIT_TABLE))
def test_commit(state):
    key = _arrange(state)
    assert orders.commit(key, order_id="9")["ok"] is True
    assert _observe(key) == COMMIT_TABLE[state]


@pytest.mark.parametrize("first", ["released:checkout_failed", "released:orphaned", "expired"])
def test_a_cancel_after_another_end_stops_a_late_commit(first):
    key = _arrange(first)
    orders.release(key, order_id="9", reason="cancelled")
    assert StockHold.objects.get(attempt_key=key).release_reason == "cancelled"
    orders.commit(key, order_id="9")
    assert _observe(key)[1:] == (5, 0)


def test_a_release_without_a_reason_never_restocks_a_committed_hold():
    key = _arrange("committed")
    assert holds.release({"attempt_key": key, "token": None, "order_id": "9"})["ok"] is True
    assert _observe(key) == (C, 3, 0)
