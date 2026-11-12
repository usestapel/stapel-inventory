"""GDPR for stapel-inventory — the ledger rule: the person goes, the numbers stay.

Two columns name a person: ``StockHold.user_id`` (the shopper of a checkout)
and ``StockMovement.actor_id`` (the staff member who received, adjusted or
transferred). Erasure of an ``account`` replaces both with the fleet
pseudonym (``stapel_core.gdpr.pseudonymize``); quantities, kinds and
references stay, so ``on_hand = Σ movements`` survives an erasure.
"""
from __future__ import annotations

from stapel_core.gdpr import GDPRProvider


class InventoryGDPRProvider(GDPRProvider):
    section = "inventory"

    def export(self, user_id) -> dict:
        from .models import StockHold, StockMovement

        user_id = str(user_id)
        return {
            "holds": [
                {
                    "token": str(h.token),
                    "status": h.status,
                    "reference": h.reference,
                    "created_at": h.created_at.isoformat(),
                    "lines": [
                        {"purchasable_type": line.stock_item.purchasable_type,
                         "purchasable_key": line.stock_item.purchasable_key,
                         "quantity": line.quantity}
                        for line in h.lines.select_related("stock_item")
                    ],
                }
                for h in StockHold.objects.filter(user_id=user_id)
            ],
            "movements": [
                {
                    "kind": m.kind,
                    "purchasable_type": m.stock_item.purchasable_type,
                    "purchasable_key": m.stock_item.purchasable_key,
                    "on_hand_delta": m.on_hand_delta,
                    "reason": m.reason,
                    "created_at": m.created_at.isoformat(),
                }
                for m in StockMovement.objects.filter(actor_id=user_id).select_related("stock_item")
            ],
        }

    def delete(self, user_id) -> None:
        erase_subject("account", user_id)

    #: After erasure the rows hold numbers and a pseudonym: that IS the
    #: anonymisation, so one operation answers both names.
    anonymize = delete


OWNER = InventoryGDPRProvider.section
SUBJECT_TYPES = ("account",)


def erase_subject(subject_type, subject_key, workspace_id=None):
    """Pseudonymize one account's ids. Idempotent; counts what it touched."""
    from django.db import transaction
    from stapel_core.gdpr.owners import pseudonymize

    from .models import StockHold, StockMovement

    if subject_type != "account" or not subject_key:
        return None
    user_id = str(subject_key)
    pseudonym = pseudonymize(user_id)
    with transaction.atomic():
        holds = StockHold.objects.filter(user_id=user_id).update(user_id=pseudonym)
        movements = StockMovement.objects.reassign_actor(user_id, pseudonym)
    return {"holds": holds, "movements": movements}
