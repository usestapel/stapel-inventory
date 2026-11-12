"""A fake orders caller: sends exactly the checkout hook payloads of the
orders contract (SPEC-BRICKS-WAVE §2 + the orders contract deltas 10-18)
through ``stapel_core.comm.call``, with schema validation on.

Payloads:
- reserve: the adjuster request + ``attempt_key``;
- commit: ``{attempt_key, token | null, order_id, user_id | null}``;
- release: ``{attempt_key, token | null, order_id | null, reason}``.

The hook names come from the shipped settings fragment, so a test that
drives this caller also proves the fragment points at real Functions.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from stapel_core.comm import call

from stapel_inventory.preset import SETTINGS_DEFAULTS

HOOK = SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]["inventory"]


def attempt_key() -> str:
    return str(uuid.uuid4())


def line(index, key, quantity, *, ptype="listing", price="10.00"):
    unit = Decimal(price)
    return {
        "index": index,
        "purchasable_type": ptype,
        "purchasable_key": key,
        "quantity": quantity,
        "unit_price": f"{unit:.2f}",
        "line_total": f"{unit * quantity:.2f}",
        "attrs": {},
    }


def reserve_payload(key, lines, *, user_id="7", context=None):
    subtotal = sum(Decimal(entry["line_total"]) for entry in lines)
    return {
        "currency": "USD",
        "subtotal": f"{subtotal:.2f}",
        "user_id": user_id,
        "lines": lines,
        "adjustments_so_far": [],
        "context": context or {},
        "attempt_key": key,
    }


def reserve(key, lines, **kwargs):
    return call(HOOK["reserve"], reserve_payload(key, lines, **kwargs))


def commit(key, token=None, *, order_id="101", user_id="7"):
    return call(HOOK["commit"], {"attempt_key": key, "token": token, "order_id": order_id,
                                 "user_id": user_id})


def release(key, token=None, *, order_id=None, reason="checkout_failed"):
    return call(HOOK["release"], {"attempt_key": key, "token": token, "order_id": order_id,
                                  "reason": reason})
