"""Every response body the committed schema declares is the body the views send.

The operation list is DERIVED from ``docs/schema.json``: an operation with a
declared JSON 2xx body and no recipe here fails loudly. Every body is
validated against its declared schema (OAS ``nullable`` honoured), and each
read is driven in a populated and an empty state.
"""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from . import orders
from .factories import location, stock_of

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())
V1 = "/inventory/api/v1"

pytestmark = pytest.mark.django_db


def _json_schema(node):
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(body_schema):
    root = copy.deepcopy(body_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            for code, response in op.get("responses", {}).items():
                body = response.get("content", {}).get("application/json", {}).get("schema")
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


def _url(path, **params):
    for name, value in params.items():
        path = path.replace("{%s}" % name, str(value))
    assert "{" not in path, f"unfilled path parameter in {path}"
    return path


def _move(client, path, **body):
    return client.post(V1 + path, {"purchasable_type": "listing", "purchasable_key": "a", **body},
                       format="json")


def r_availability(env):
    stock_of("a", 3)
    stock_of("b", 50)
    populated = env.anon.get(V1 + "/availability/", {"purchasable_type": "listing",
                                                     "keys": ["a", "b", "ghost"]})
    empty = env.anon.get(V1 + "/availability/", {"purchasable_type": "listing"})
    return [populated], [empty]


def r_locations(env):
    location("back")
    return [env.staff.get(V1 + "/locations/")], []


def r_location_create(env):
    return [env.staff.post(V1 + "/locations/", {"code": "wh-2", "name": "Warehouse"}, format="json")], []


def r_location_patch(env):
    location("back")
    return [env.staff.patch(_url(V1 + "/locations/{code}/", code="back"), {"priority": 3},
                            format="json")], []


def r_stock_list(env):
    empty = env.staff.get(V1 + "/stock/")
    stock_of("a", 3, low_stock_threshold=1)
    stock_of("b", 0)
    return [env.staff.get(V1 + "/stock/?limit=1")], [empty]


def r_stock_patch(env):
    item = stock_of("a", 3)
    return [env.staff.patch(_url(V1 + "/stock/{item_id}/", item_id=item.pk),
                            {"low_stock_threshold": None, "oversell_policy": "backorder"}, format="json")], []


def r_receive(env):
    return [_move(env.staff, "/stock/receive/", quantity=4, reason="delivery")], []


def r_adjust(env):
    stock_of("a", 3)
    return [_move(env.staff, "/stock/adjust/", delta=-1, reason="damaged")], []


def r_transfer(env):
    stock_of("a", 3)
    location("back")
    return [_move(env.staff, "/stock/transfer/", from_location="main", to_location="back", quantity=2)], []


def r_movements(env):
    empty = env.staff.get(V1 + "/movements/")
    stock_of("a", 3)
    key = orders.attempt_key()
    orders.reserve(key, [orders.line(0, "a", 1)])
    orders.commit(key)
    return [env.staff.get(V1 + "/movements/?limit=2")], [empty]


RECIPES = {
    ("GET", V1 + "/availability/", 200): r_availability,
    ("GET", V1 + "/locations/", 200): r_locations,
    ("POST", V1 + "/locations/", 201): r_location_create,
    ("PATCH", V1 + "/locations/{code}/", 200): r_location_patch,
    ("GET", V1 + "/stock/", 200): r_stock_list,
    ("PATCH", V1 + "/stock/{item_id}/", 200): r_stock_patch,
    ("POST", V1 + "/stock/receive/", 200): r_receive,
    ("POST", V1 + "/stock/adjust/", 200): r_adjust,
    ("POST", V1 + "/stock/transfer/", 200): r_transfer,
    ("GET", V1 + "/movements/", 200): r_movements,
}


class Env:
    def __init__(self, anon, staff):
        self.anon, self.staff = anon, staff


def test_every_declared_operation_has_a_recipe():
    declared = {(m, p, c) for m, p, c, _ in OPERATIONS}
    assert declared == set(RECIPES), {
        "missing": sorted(declared - set(RECIPES)),
        "stale": sorted(set(RECIPES) - declared),
    }


@pytest.mark.parametrize("method,path,code,body_schema", OPERATIONS,
                         ids=[f"{m} {p} {c}" for m, p, c, _ in OPERATIONS])
def test_wire_matches_the_declared_body(method, path, code, body_schema, api_client, staff_client):
    populated, empty = RECIPES[(method, path, code)](Env(api_client, staff_client))
    validator = _validator(body_schema)
    for resp in populated + empty:
        assert resp.status_code == code, (method, path, resp.status_code, resp.content)
        errors = sorted(validator.iter_errors(resp.json()), key=str)
        assert not errors, [e.message for e in errors[:5]]
    for resp in populated:
        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            assert data["items"], f"{method} {path}: populated 'items' came back empty"
    for resp in empty:
        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            assert not data["items"], f"{method} {path}: the empty state is not empty"


def test_the_gate_is_not_blind(staff_client):
    """Inversion: a body validated against a wrong schema must fail."""
    body = _move(staff_client, "/stock/receive/", quantity=1).json()
    assert list(jsonschema.Draft202012Validator({"type": "string"}).iter_errors(body))
    broken = dict(body, on_hand="1")
    schema = SCHEMA["paths"][V1 + "/stock/receive/"]["post"]["responses"]["200"][
        "content"]["application/json"]["schema"]
    assert list(_validator(schema).iter_errors(broken))
