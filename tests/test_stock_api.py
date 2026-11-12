"""Staff API: locations, receive, adjust, transfer, thresholds, ledger; guest stance."""
import pytest

from stapel_inventory.models import Location, MovementKind, StockItem, StockMovement

from . import orders
from .factories import location, stock_of

V1 = "/inventory/api/v1"

pytestmark = pytest.mark.django_db


def _receive(client, key="a", quantity=5, **extra):
    return client.post(f"{V1}/stock/receive/", {"purchasable_type": "listing", "purchasable_key": key,
                                                "quantity": quantity, **extra}, format="json")


class TestGuestStance:
    @pytest.mark.parametrize("method,path", [
        ("get", "/locations/"), ("post", "/locations/"), ("get", "/stock/"),
        ("post", "/stock/receive/"), ("post", "/stock/adjust/"), ("post", "/stock/transfer/"),
        ("get", "/movements/"), ("patch", "/stock/1/"), ("patch", "/locations/main/"),
    ])
    def test_staff_only(self, method, path, api_client, client_for, user):
        assert getattr(api_client, method)(f"{V1}{path}", {}, format="json").status_code in (401, 403)
        assert getattr(client_for(user), method)(f"{V1}{path}", {}, format="json").status_code == 403


class TestReceive:
    def test_creates_the_item_at_the_default_location(self, staff_client, staff):
        resp = _receive(staff_client, quantity=7, reason="delivery 12")
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert (body["location"], body["on_hand"], body["held"], body["available"]) == ("main", 7, 0, 7)
        move = StockMovement.objects.get()
        assert (move.kind, move.on_hand_delta, move.reason, move.actor_id) == (
            MovementKind.RECEIPT, 7, "delivery 12", str(staff.pk))
        assert Location.objects.get().is_default

    def test_unknown_location_404(self, staff_client):
        resp = _receive(staff_client, location="nowhere")
        assert resp.status_code == 404
        assert resp.json()["localizable_error"] == "error.404.location_not_found"

    def test_inactive_location_409(self, staff_client):
        location("closed", active=False)
        assert _receive(staff_client, location="closed").status_code == 409

    def test_quantity_must_be_positive(self, staff_client):
        assert _receive(staff_client, quantity=0).status_code == 400


class TestAdjust:
    def _adjust(self, client, **body):
        return client.post(f"{V1}/stock/adjust/", {"purchasable_type": "listing", "purchasable_key": "a",
                                                   **body}, format="json")

    def test_delta_with_reason(self, staff_client):
        stock_of("a", 5)
        resp = self._adjust(staff_client, delta=-2, reason="damaged")
        assert resp.status_code == 200 and resp.json()["on_hand"] == 3
        assert StockMovement.objects.filter(kind=MovementKind.ADJUST).get().reason == "damaged"

    def test_count_sets_on_hand(self, staff_client):
        stock_of("a", 5)
        resp = self._adjust(staff_client, on_hand=9, reason="stocktake")
        assert resp.json()["on_hand"] == 9
        assert StockMovement.objects.filter(kind=MovementKind.ADJUST).get().on_hand_delta == 4

    def test_reason_is_required(self, staff_client):
        assert self._adjust(staff_client, delta=1).status_code == 400

    def test_delta_xor_count(self, staff_client):
        resp = self._adjust(staff_client, delta=1, on_hand=3, reason="x")
        assert resp.status_code == 400
        assert resp.json()["localizable_error"] == "error.400.stock_adjust_ambiguous"
        assert self._adjust(staff_client, reason="x").status_code == 400

    def test_never_below_zero(self, staff_client):
        stock_of("a", 2)
        resp = self._adjust(staff_client, delta=-3, reason="x")
        assert resp.status_code == 409
        assert resp.json()["localizable_error"] == "error.409.stock_would_go_negative"


class TestTransfer:
    def _transfer(self, client, quantity, **extra):
        return client.post(f"{V1}/stock/transfer/", {
            "purchasable_type": "listing", "purchasable_key": "a", "from_location": "main",
            "to_location": "back", "quantity": quantity, **extra}, format="json")

    def test_moves_units_with_one_ref(self, staff_client):
        stock_of("a", 5)
        location("back")
        resp = self._transfer(staff_client, 3)
        assert resp.status_code == 200, resp.json()
        assert (resp.json()["source"]["on_hand"], resp.json()["target"]["on_hand"]) == (2, 3)
        refs = set(StockMovement.objects.filter(kind=MovementKind.TRANSFER).values_list("ref_key", flat=True))
        assert len(refs) == 1

    def test_held_units_stay(self, staff_client):
        stock_of("a", 5)
        location("back")
        orders.reserve(orders.attempt_key(), [orders.line(0, "a", 4)])
        resp = self._transfer(staff_client, 2)
        assert resp.status_code == 409 and resp.json()["params"]["available"] == 1

    def test_same_location_400(self, staff_client):
        resp = staff_client.post(f"{V1}/stock/transfer/", {
            "purchasable_type": "listing", "purchasable_key": "a", "from_location": "main",
            "to_location": "main", "quantity": 1}, format="json")
        assert resp.status_code == 400

    def test_nothing_at_source(self, staff_client):
        location("back")
        assert self._transfer(staff_client, 1).status_code == 409


class TestLocations:
    def test_list_creates_the_default(self, staff_client):
        body = staff_client.get(f"{V1}/locations/").json()
        assert [loc["code"] for loc in body] == ["main"] and body[0]["is_default"]

    def test_create_and_make_default(self, staff_client):
        staff_client.get(f"{V1}/locations/")
        resp = staff_client.post(f"{V1}/locations/", {"code": "wh-2", "name": "Warehouse 2",
                                                      "is_default": True}, format="json")
        assert resp.status_code == 201
        assert list(Location.objects.filter(is_default=True).values_list("code", flat=True)) == ["wh-2"]

    def test_duplicate_code_409(self, staff_client):
        staff_client.get(f"{V1}/locations/")
        resp = staff_client.post(f"{V1}/locations/", {"code": "main", "name": "x"}, format="json")
        assert resp.status_code == 409

    def test_default_cannot_be_switched_off(self, staff_client):
        staff_client.get(f"{V1}/locations/")
        resp = staff_client.patch(f"{V1}/locations/main/", {"active": False}, format="json")
        assert resp.status_code == 409
        assert resp.json()["localizable_error"] == "error.409.default_location_required"

    def test_rename_and_reprioritise(self, staff_client):
        location("back", priority=5)
        resp = staff_client.patch(f"{V1}/locations/back/", {"name": "Back room", "priority": 1}, format="json")
        assert resp.status_code == 200 and resp.json()["priority"] == 1

    def test_unknown_404(self, staff_client):
        assert staff_client.patch(f"{V1}/locations/nope/", {"name": "x"}, format="json").status_code == 404


class TestStockList:
    def test_filters_and_pages(self, staff_client):
        for i in range(5):
            stock_of(f"k{i}", i)
        page = staff_client.get(f"{V1}/stock/", {"limit": 2}).json()
        assert len(page["items"]) == 2 and page["has_next"] and isinstance(page["next_anchor"], str)
        rest = staff_client.get(f"{V1}/stock/", {"limit": 10, "anchor": page["next_anchor"]}).json()
        assert len(rest["items"]) == 3
        out = staff_client.get(f"{V1}/stock/", {"alert_state": "out"}).json()
        assert [i["purchasable_key"] for i in out["items"]] == ["k0"]
        assert staff_client.get(f"{V1}/stock/", {"anchor": "junk"}).status_code == 200

    def test_threshold_patch_moves_the_alert(self, staff_client):
        item = stock_of("a", 8)
        resp = staff_client.patch(f"{V1}/stock/{item.pk}/", {"low_stock_threshold": 10}, format="json")
        assert resp.status_code == 200 and resp.json()["alert_state"] == "low"
        resp = staff_client.patch(f"{V1}/stock/{item.pk}/", {"oversell_policy": "backorder"}, format="json")
        assert resp.json()["oversell_policy"] == "backorder"

    def test_patch_unknown_404(self, staff_client):
        assert staff_client.patch(f"{V1}/stock/999/", {}, format="json").status_code == 404


class TestMovements:
    def test_ledger_newest_first_with_filters(self, staff_client):
        item = stock_of("a", 5)
        key = orders.attempt_key()
        orders.reserve(key, [orders.line(0, "a", 2)])
        orders.commit(key)
        body = staff_client.get(f"{V1}/movements/", {"stock_item": item.pk}).json()
        assert [m["kind"] for m in body["items"]] == ["commit", "hold", "receipt"]
        assert body["items"][0]["on_hand_after"] == 3
        holds_only = staff_client.get(f"{V1}/movements/", {"kind": "hold"}).json()
        assert [m["kind"] for m in holds_only["items"]] == ["hold"]
        assert staff_client.get(f"{V1}/movements/", {"stock_item": "x"}).status_code == 404

    def test_invariant_holds_after_everything(self, staff_client):
        stock_of("a", 5)
        _receive(staff_client, quantity=3)
        item = StockItem.objects.get()
        total = sum(StockMovement.objects.filter(stock_item=item).values_list("on_hand_delta", flat=True))
        assert total == item.on_hand == 8
