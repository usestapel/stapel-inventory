"""inventory.availability (exact, internal) and the public endpoint (disclosure policy)."""
import json
from pathlib import Path

import jsonschema
import pytest
from stapel_core.comm import call

from . import orders
from .factories import location, stock_of

V1 = "/inventory/api/v1"
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "functions"
                     / "inventory.availability.json").read_text())

pytestmark = pytest.mark.django_db


def _public(client, *keys, ptype="listing"):
    resp = client.get(f"{V1}/availability/", {"purchasable_type": ptype, "keys": list(keys)})
    assert resp.status_code == 200, resp.json()
    return {i["purchasable_key"]: (i["state"], i["quantity"]) for i in resp.json()["items"]}


class TestFunction:
    def test_exact_numbers_per_location(self):
        location("back")
        stock_of("a", 9)
        stock_of("a", 4, location="back")
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 2)])
        answer = call("inventory.availability", {"purchasable_type": "listing", "keys": ["a", "zz"]})
        jsonschema.validate(answer, SCHEMA["$defs"]["response"])
        a = answer["items"]["a"]
        assert (a["tracked"], a["on_hand"], a["held"], a["available"], a["in_stock"]) == (True, 9, 2, 7, True)
        assert {loc["code"]: loc["available"] for loc in a["locations"]} == {"main": 7, "back": 4}
        assert answer["items"]["zz"]["tracked"] is False and answer["items"]["zz"]["available"] is None

    def test_one_location(self):
        location("back")
        stock_of("a", 9)
        stock_of("a", 4, location="back")
        answer = call("inventory.availability", {"purchasable_type": "listing", "keys": ["a"],
                                                 "location": "back"})
        assert answer["items"]["a"]["available"] == 4

    def test_key_cap(self, settings):
        settings.STAPEL_INVENTORY = {"MAX_AVAILABILITY_KEYS": 2}
        with pytest.raises(Exception):
            call("inventory.availability", {"purchasable_type": "listing", "keys": ["a", "b", "c"]})

    def test_expired_holds_stop_counting_before_the_beat(self):
        import datetime as dt

        from django.utils import timezone

        from stapel_inventory.models import StockHold

        stock_of("a", 3)
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 3)])
        StockHold.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
        answer = call("inventory.availability", {"purchasable_type": "listing", "keys": ["a"]})
        assert answer["items"]["a"]["available"] == 3


class TestPublicEndpoint:
    def test_default_policy_few_left_no_numbers(self, api_client):
        stock_of("plenty", 50)
        stock_of("few", 3)
        stock_of("none", 0)
        stock_of("later", 0, oversell_policy="backorder")
        assert _public(api_client, "plenty", "few", "none", "later", "ghost") == {
            "plenty": ("in_stock", None), "few": ("few_left", None), "none": ("out_of_stock", None),
            "later": ("backorder", None), "ghost": ("untracked", None),
        }

    def test_exact_policy_discloses_numbers(self, api_client, settings):
        settings.STAPEL_INVENTORY = {"PUBLIC_AVAILABILITY": "exact"}
        stock_of("few", 3)
        stock_of("plenty", 50)
        assert _public(api_client, "few", "plenty") == {"few": ("few_left", 3), "plenty": ("in_stock", 50)}

    def test_in_stock_policy_is_boolean(self, api_client, settings):
        settings.STAPEL_INVENTORY = {"PUBLIC_AVAILABILITY": "in_stock"}
        stock_of("few", 1)
        assert _public(api_client, "few") == {"few": ("in_stock", None)}

    def test_untracked_under_deny_reads_out(self, api_client, settings):
        settings.STAPEL_INVENTORY = {"TRACK_UNKNOWN": "deny"}
        assert _public(api_client, "ghost") == {"ghost": ("out_of_stock", None)}

    def test_too_many_keys_400(self, api_client, settings):
        settings.STAPEL_INVENTORY = {"MAX_AVAILABILITY_KEYS": 1}
        resp = api_client.get(f"{V1}/availability/", {"purchasable_type": "listing", "keys": ["a", "b"]})
        assert resp.status_code == 400
        assert resp.json()["localizable_error"] == "error.400.availability_too_many_keys"

    def test_type_is_required(self, api_client):
        assert api_client.get(f"{V1}/availability/", {"keys": ["a"]}).status_code == 400

    def test_no_keys_is_an_empty_answer(self, api_client):
        resp = api_client.get(f"{V1}/availability/", {"purchasable_type": "listing"})
        assert resp.status_code == 200 and resp.json() == {"items": []}
