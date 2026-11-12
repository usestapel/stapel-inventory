"""Every domain refusal names the request field it is about (``params.field``).

A form can then put the message next to the input. On a transfer the
location refusals say which side: ``from_location`` or ``to_location``.
"""
import pytest

from .factories import location, stock_of

V1 = "/inventory/api/v1"

pytestmark = pytest.mark.django_db


def transfer(client, frm="main", to="back", quantity=1):
    return client.post(f"{V1}/stock/transfer/", {"purchasable_type": "listing", "purchasable_key": "a",
                                                  "from_location": frm, "to_location": to,
                                                  "quantity": quantity}, format="json")


def refusal(resp):
    body = resp.json()
    return resp.status_code, body["localizable_error"], body["params"].get("field")


def test_adjust_refusals(staff_client):
    stock_of("a", 2)
    adjust = {"purchasable_type": "listing", "purchasable_key": "a", "reason": "count"}
    both = staff_client.post(f"{V1}/stock/adjust/", {**adjust, "delta": -1, "on_hand": 1}, format="json")
    assert refusal(both) == (400, "error.400.stock_adjust_ambiguous", "delta")
    negative = staff_client.post(f"{V1}/stock/adjust/", {**adjust, "delta": -5}, format="json")
    assert refusal(negative) == (409, "error.409.stock_would_go_negative", "delta")
    unknown = staff_client.post(f"{V1}/stock/adjust/", {**adjust, "delta": 1, "location": "nope"}, format="json")
    assert refusal(unknown) == (404, "error.404.location_not_found", "location")


def test_transfer_refusals_say_which_side(staff_client):
    stock_of("a", 2)
    location("back")
    location("closed", active=False)
    assert refusal(transfer(staff_client, to="main")) == (400, "error.400.stock_transfer_same_location",
                                                          "to_location")
    assert refusal(transfer(staff_client, frm="nope")) == (404, "error.404.location_not_found", "from_location")
    assert refusal(transfer(staff_client, to="nope")) == (404, "error.404.location_not_found", "to_location")
    assert refusal(transfer(staff_client, to="closed")) == (409, "error.409.location_inactive", "to_location")
    short = transfer(staff_client, quantity=5)
    assert refusal(short) == (409, "error.409.insufficient_stock", "quantity")
    assert {k: short.json()["params"][k] for k in ("requested", "available", "purchasable_key")} == {
        "requested": 5, "available": 2, "purchasable_key": "a"}


def test_location_refusals(staff_client):
    staff_client.get(f"{V1}/locations/")
    dup = staff_client.post(f"{V1}/locations/", {"code": "main", "name": "x"}, format="json")
    assert refusal(dup) == (409, "error.409.location_exists", "code")
    off = staff_client.patch(f"{V1}/locations/main/", {"active": False}, format="json")
    assert refusal(off) == (409, "error.409.default_location_required", "active")
    assert staff_client.patch(f"{V1}/locations/nope/", {"name": "x"}, format="json").json()["params"]["field"] == "code"


def test_too_many_availability_keys(api_client, settings):
    settings.STAPEL_INVENTORY = {"MAX_AVAILABILITY_KEYS": 2}
    resp = api_client.get(f"{V1}/availability/?purchasable_type=listing&keys=a&keys=b&keys=c")
    assert refusal(resp) == (400, "error.400.availability_too_many_keys", "keys")
    assert resp.json()["params"]["max"] == 2
