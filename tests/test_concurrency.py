"""The oversell proof: real threads, real transactions, a database that locks.

Backends (``_codegen_settings.test_database``):

- local default: a SQLite file in ``IMMEDIATE`` mode — every transaction
  takes the database write lock at ``BEGIN``, so concurrent writers
  serialize on it. That proves the count is taken INSIDE the transaction
  (a count read before it races, see the inversion test), not the row-lock
  order.
- CI ``postgres`` job (``STAPEL_TEST_DATABASE_URL``): ``SELECT … FOR
  UPDATE`` row locks in ascending id order; the same tests then also prove
  that two reserves with opposite line orders never deadlock, and
  ``test_row_locks_are_taken_in_id_order`` reads the SQL itself.

Every test here asserts the backend it ran on, so a CI job that silently
fell back to SQLite cannot report a Postgres proof.
"""
import datetime as dt
import os
import threading

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from stapel_inventory.models import HoldStatus, StockHold, StockItem, StockMovement
from stapel_inventory.services import holds, reconcile, stock

from . import orders
from .factories import available, stock_of

ON_POSTGRES = bool(os.environ.get("STAPEL_TEST_DATABASE_URL"))
THREADS = 16

pytestmark = pytest.mark.django_db(transaction=True)


def _parallel(n, work):
    """Run ``work(i)`` in ``n`` threads released together; return results in order."""
    barrier = threading.Barrier(n)
    results, failures = [None] * n, []

    def runner(i):
        try:
            barrier.wait(timeout=30)
            results[i] = work(i)
        except Exception as exc:  # noqa: BLE001 - surfaced below
            failures.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a worker hung (deadlock?)"
    assert failures == [], failures
    return results


def test_backend_is_the_one_asked_for():
    assert connection.vendor == ("postgresql" if ON_POSTGRES else "sqlite")
    if not ON_POSTGRES:
        assert connection.settings_dict["NAME"] != ":memory:", "threads need a shared file"
        assert connection.settings_dict["OPTIONS"]["transaction_mode"] == "IMMEDIATE"


def test_no_oversell_under_parallel_reserves():
    item = stock_of("last", 5)
    keys = [orders.attempt_key() for _ in range(THREADS)]
    answers = _parallel(THREADS, lambda i: orders.reserve(keys[i], [orders.line(0, "last", 1)]))
    won = [a for a in answers if a["ok"]]
    lost = [a for a in answers if not a["ok"]]
    assert len(won) == 5 and len(lost) == THREADS - 5
    assert all(a["errors"][0]["code"] == "error.409.insufficient_stock" for a in lost)
    assert available("last") == 0
    assert StockHold.objects.filter(status=HoldStatus.ACTIVE).count() == 5
    item.refresh_from_db()
    assert item.on_hand == 5
    assert reconcile.drift() == {"on_hand": [], "held": []}


def test_no_oversell_through_reserve_and_commit():
    item = stock_of("hot", 7)

    def checkout(i):
        key = orders.attempt_key()
        answer = orders.reserve(key, [orders.line(0, "hot", 2)])
        if answer["ok"]:
            assert orders.commit(key, answer["token"], order_id=str(i))["ok"]
        return answer["ok"]

    won = sum(_parallel(THREADS, checkout))
    item.refresh_from_db()
    assert won == 3 and item.on_hand == 1
    assert reconcile.drift() == {"on_hand": [], "held": []}


def test_opposite_line_orders_do_not_deadlock():
    stock_of("x", THREADS)
    stock_of("y", THREADS)

    def work(i):
        lines = [orders.line(0, "x", 1), orders.line(1, "y", 1)]
        if i % 2:
            lines = [orders.line(0, "y", 1), orders.line(1, "x", 1)]
        return orders.reserve(orders.attempt_key(), lines)["ok"]

    assert all(_parallel(THREADS, work))
    assert available("x") == 0 and available("y") == 0


def test_parallel_replays_of_one_attempt_hold_once():
    stock_of("a", 10)
    key = orders.attempt_key()
    answers = _parallel(8, lambda i: orders.reserve(key, [orders.line(0, "a", 3)]))
    assert all(a["ok"] for a in answers)
    assert len({a["token"] for a in answers}) == 1
    assert available("a") == 7
    assert StockMovement.objects.filter(kind="hold").count() == 1


def test_parallel_commits_and_releases_are_idempotent():
    item = stock_of("a", 10)
    key = orders.attempt_key()
    token = orders.reserve(key, [orders.line(0, "a", 4)])["token"]
    _parallel(8, lambda i: orders.commit(key, token if i % 2 else None, order_id="5"))
    item.refresh_from_db()
    assert item.on_hand == 6
    _parallel(8, lambda i: orders.release(key, None, order_id="5", reason="cancelled"))
    item.refresh_from_db()
    assert item.on_hand == 10
    assert StockMovement.objects.filter(kind="restock").count() == 1


def test_staff_receipts_and_checkouts_interleave_without_losing_units():
    item = stock_of("mix", 0)

    def work(i):
        if i % 2:
            stock.receive("listing", "mix", None, 3, actor_id="staff-1")
            return None
        key = orders.attempt_key()
        answer = orders.reserve(key, [orders.line(0, "mix", 1)])
        if answer["ok"]:
            orders.commit(key, answer["token"])
        return answer["ok"]

    results = _parallel(THREADS, work)
    sold = sum(1 for r in results if r)
    item.refresh_from_db()
    assert item.on_hand == 3 * (THREADS // 2) - sold
    assert reconcile.drift() == {"on_hand": [], "held": []}


def test_crash_between_reserve_and_commit_is_released_by_the_beat():
    """The checkout process dies after reserve: no commit, no release ever arrives."""
    stock_of("a", 2)
    crashed = orders.attempt_key()
    assert orders.reserve(crashed, [orders.line(0, "a", 2)])["ok"]
    assert orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1)])["ok"] is False

    StockHold.objects.filter(attempt_key=crashed).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    from stapel_inventory.beat import expire_holds

    assert expire_holds() == 1
    hold = StockHold.objects.get(attempt_key=crashed)
    assert (hold.status, hold.release_reason) == (HoldStatus.EXPIRED, "expired")
    assert available("a") == 2
    assert StockMovement.objects.filter(kind="release", held_delta=-2).count() == 1
    assert orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])["ok"] is True
    assert reconcile.drift() == {"on_hand": [], "held": []}


def test_expiry_racing_a_commit_never_double_counts():
    item = stock_of("a", 4)
    keys = [orders.attempt_key() for _ in range(6)]
    for key in keys:
        orders.reserve(key, [orders.line(0, "a", 1)])
    StockHold.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    def work(i):
        if i == 0:
            return holds.expire_due()
        return orders.commit(keys[i - 1], order_id=str(i))["ok"]

    _parallel(len(keys) + 1, work)
    item.refresh_from_db()
    committed = StockHold.objects.filter(status=HoldStatus.COMMITTED).count()
    assert item.on_hand == 4 - committed
    assert reconcile.drift() == {"on_hand": [], "held": []}


def test_inversion_a_count_before_the_lock_oversells(monkeypatch):
    """The classic defect: availability read once, outside the locked section."""
    stock_of("last", 3)
    stale = stock.held_by_item(list(StockItem.objects.values_list("pk", flat=True)))
    monkeypatch.setattr(holds, "held_by_item", lambda ids, now=None: dict(stale))
    answers = _parallel(8, lambda i: orders.reserve(orders.attempt_key(), [orders.line(0, "last", 1)]))
    assert sum(1 for a in answers if a["ok"]) > 3, "the test must be able to see an oversell"
    entry = stock.availability("listing", ["last"])["last"]
    assert entry["held"] > entry["on_hand"] == 3


def test_lock_is_taken_before_the_count(monkeypatch):
    stock_of("a", 3)
    trail = []
    real_lock, real_count = holds.lock_items, holds.held_by_item

    def lock_spy(ids):
        trail.append("lock")
        return real_lock(ids)

    def count_spy(ids, now=None):
        trail.append("count")
        return real_count(ids, now)

    monkeypatch.setattr(holds, "lock_items", lock_spy)
    monkeypatch.setattr(holds, "held_by_item", count_spy)
    orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1)])
    assert trail.index("lock") < trail.index("count")


@pytest.mark.skipif(not ON_POSTGRES, reason="row locks exist on Postgres only (CI postgres job)")
def test_row_locks_are_taken_in_id_order():
    first = stock_of("b", 5)
    second = stock_of("a", 5)
    assert first.pk < second.pk
    with CaptureQueriesContext(connection) as ctx:
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1), orders.line(1, "b", 1)])
    locking = [q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]]
    assert locking, "no row lock was taken"
    assert 'ORDER BY "inventory_stockitem"."id" ASC' in locking[0]


@pytest.mark.skipif(not ON_POSTGRES, reason="row locks exist on Postgres only (CI postgres job)")
def test_inversion_without_row_locks_postgres_oversells(monkeypatch):
    """Drop FOR UPDATE and keep everything else: READ COMMITTED lets the race through."""
    import time

    stock_of("last", 3)
    real_count = stock.held_by_item

    def unlocked(ids):
        return {i.pk: i for i in StockItem.objects.filter(pk__in=sorted(set(ids))).order_by("pk")}

    def slow_count(ids, now=None):
        counted = real_count(ids, now)
        time.sleep(0.2)  # widen the window between the count and the write
        return counted

    monkeypatch.setattr(holds, "lock_items", unlocked)
    monkeypatch.setattr(holds, "held_by_item", slow_count)
    answers = _parallel(8, lambda i: orders.reserve(orders.attempt_key(), [orders.line(0, "last", 1)]))
    assert sum(1 for a in answers if a["ok"]) > 3, "without the row lock the race must show"
