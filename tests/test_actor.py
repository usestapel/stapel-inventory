"""``actor_id``: who owns a checkout's hold, a guest included.

Orders sends ``actor_id`` (always present: the guest's own id or the
account) next to ``user_id`` (null for a guest on the adjuster and hook payloads; the
``orders.order.placed`` fact keeps ``user_id`` = the acting account). Holds
record both; ``user.merged`` and GDPR export/erase follow either.
Payloads without ``actor_id`` record ``user_id`` as the actor.
"""
import pytest
from stapel_core.comm import emit, mutate_and_emit

from stapel_inventory.gdpr import InventoryGDPRProvider, erase_subject
from stapel_inventory.models import StockHold

from . import orders
from .factories import stock_of

pytestmark = pytest.mark.django_db

GUEST = "guest-42"


def guest_hold():
    stock_of("a", 5)
    key = orders.attempt_key()
    payload = {**orders.reserve_payload(key, [orders.line(0, "a", 1)], user_id=None), "actor_id": GUEST}
    from stapel_core.comm import call

    assert call(orders.HOOK["reserve"], payload)["ok"] is True
    return key


def test_a_guest_hold_records_the_actor():
    key = guest_hold()
    orders.commit(key, user_id=None)
    hold = StockHold.objects.get(attempt_key=key)
    assert (hold.user_id, hold.actor_id, hold.status) == (None, GUEST, "committed")


def test_a_payload_without_actor_id_records_the_user_id():
    stock_of("a", 5)
    key = orders.attempt_key()
    orders.reserve(key, [orders.line(0, "a", 1)], user_id="7")
    assert StockHold.objects.get().actor_id == "7"


def test_user_merged_moves_the_guest_hold():
    key = guest_hold()
    with mutate_and_emit():
        emit("user.merged", {"from_user_id": GUEST, "into_user_id": "8"})
    hold = StockHold.objects.get(attempt_key=key)
    assert (hold.user_id, hold.actor_id) == (None, "8")


def test_gdpr_covers_holds_keyed_by_actor():
    key = guest_hold()
    exported = InventoryGDPRProvider().export(GUEST)
    assert len(exported["holds"]) == 1
    assert erase_subject("account", GUEST)["holds"] == 1
    assert StockHold.objects.get(attempt_key=key).actor_id != GUEST


def test_erasure_counts_a_real_account_hold_once():
    stock_of("a", 5)
    orders.reserve(orders.attempt_key(), [orders.line(0, "a", 1)], user_id="7")
    assert erase_subject("account", "7")["holds"] == 1
    hold = StockHold.objects.get()
    assert hold.user_id == hold.actor_id != "7"
