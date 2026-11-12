"""GDPR (export + erase keep the ledger whole) and the user.merged re-parent."""
import pytest
from stapel_core.comm import emit
from stapel_core.gdpr.owners import pseudonymize

from stapel_inventory.gdpr import InventoryGDPRProvider, erase_subject
from stapel_inventory.models import StockHold, StockItem, StockMovement
from stapel_inventory.services import stock

from . import orders
from .factories import stock_of

pytestmark = pytest.mark.django_db


def _scene():
    stock_of("a", 5)
    stock.receive("listing", "a", None, 2, actor_id="9")
    key = orders.attempt_key()
    orders.reserve(key, [orders.line(0, "a", 1)], user_id="9")
    return key


def test_export_names_holds_and_movements():
    _scene()
    data = InventoryGDPRProvider().export("9")
    assert data["holds"][0]["lines"] == [{"purchasable_type": "listing", "purchasable_key": "a", "quantity": 1}]
    assert [m["kind"] for m in data["movements"]] == ["receipt"]


def test_erase_pseudonymizes_and_keeps_the_numbers():
    key = _scene()
    item = StockItem.objects.get()
    assert erase_subject("account", "9") == {"holds": 1, "movements": 1}
    assert StockHold.objects.get(attempt_key=key).user_id == pseudonymize("9")
    assert not StockMovement.objects.filter(actor_id="9").exists()
    assert sum(StockMovement.objects.values_list("on_hand_delta", flat=True)) == item.on_hand == 7
    assert erase_subject("account", "9") == {"holds": 0, "movements": 0}
    assert InventoryGDPRProvider().export("9") == {"holds": [], "movements": []}


def test_other_subject_types_are_ignored():
    assert erase_subject("workspace", "9") is None


def test_user_merged_reparents_holds():
    key = _scene()
    from django.db import transaction

    with transaction.atomic():
        emit("user.merged", {"from_user_id": "9", "into_user_id": "12"})
    assert StockHold.objects.get(attempt_key=key).user_id == "12"
    assert StockMovement.objects.filter(actor_id="12").count() == 1
