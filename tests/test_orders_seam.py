"""inventory against the orders seam as orders' own schemas define it (see ``orders_seam``)."""
import pytest

from stapel_inventory.models import StockHold, StockItem
from stapel_inventory.preset import SETTINGS_DEFAULTS

from .factories import stock_of
from .orders_seam import GUEST, line, quote_request, run_hook

pytestmark = pytest.mark.django_db

HOOK = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]["inventory"]


@pytest.mark.parametrize("user_id,actor_id", [(None, GUEST), ("7", "7")], ids=["guest", "account"])
def test_reserve_commit_release_as_orders_sends_them(user_id, actor_id):
    stock_of("sku-1", 5)
    request = quote_request([line(quantity=2)], {}, user_id=user_id, actor_id=actor_id)
    key = run_hook(HOOK, request, adjuster_answer=[], adjuster="other")
    hold = StockHold.objects.get(attempt_key=key)
    assert (hold.user_id, hold.actor_id, hold.status) == (user_id, actor_id, "returned")
    assert StockItem.objects.get().on_hand == 5
