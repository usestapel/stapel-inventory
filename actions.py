"""Action subscriptions of stapel-inventory.

- ``orders.order.placed`` — only under ``DECREMENT_ON_ORDER_PLACED`` (an
  orders without checkout hooks): each line decrements stock, idempotent on
  ``order_id``. The schema in ``schemas/consumes/`` is checked here, not
  registered on the bus: registering it would replace the emitter's own,
  stricter schema in a process that runs orders too.
- ``user.merged`` (stapel-auth) — a guest folded into an account on sign-in:
  its holds (and any ledger rows naming it) follow the survivor. Idempotent.
- ``user.deleted`` / ``gdpr.erasure.requested`` are answered by
  ``register_gdpr_owner`` (``apps.py``).
"""
import json
import logging
from pathlib import Path

from stapel_core.comm import on_action

logger = logging.getLogger(__name__)

_CONSUMES = Path(__file__).resolve().parent / "schemas" / "consumes"


def _schema(name: str) -> dict:
    return json.loads((_CONSUMES / f"{name}.json").read_text(encoding="utf-8"))


@on_action("orders.order.placed")
def handle_order_placed(event):
    """Decrement stock for a placed order (the hookless fallback)."""
    import jsonschema

    from .conf import inventory_settings
    from .services.holds import decrement_for_order

    if not inventory_settings.DECREMENT_ON_ORDER_PLACED:
        return
    payload = event.payload or {}
    try:
        jsonschema.validate(payload, _schema("orders.order.placed"))
    except jsonschema.ValidationError as exc:
        logger.error("orders.order.placed %s not understood: %s", event.event_id, exc.message)
        return
    moved = decrement_for_order(str(payload["order_id"]), payload["lines"])
    if moved:
        logger.info("orders.order.placed %s: %s unit(s) decremented", payload["order_id"], moved)


@on_action("user.merged", schema=_schema("user.merged"))
def handle_user_merged(event):
    """Re-parent the guest's holds and ledger rows to the surviving account."""
    from django.db import transaction

    from .models import StockHold, StockMovement

    payload = event.payload or {}
    from_user_id = str(payload.get("from_user_id") or "")
    into_user_id = str(payload.get("into_user_id") or "")
    if not from_user_id or not into_user_id or from_user_id == into_user_id:
        return
    with transaction.atomic():
        holds = StockHold.objects.filter(user_id=from_user_id).update(user_id=into_user_id)
        movements = StockMovement.objects.reassign_actor(from_user_id, into_user_id)
    if holds or movements:
        logger.info("user.merged %s -> %s: %s hold(s), %s movement(s)", from_user_id, into_user_id,
                    holds, movements)
