"""The housekeeping schedule — shipped by the library.

Wire it in the settings module itself::

    from stapel_inventory.beat import get_inventory_beat_schedule

    CELERY_BEAT_SCHEDULE = {**get_inventory_beat_schedule(), ...}

Two passes: hold expiry (every minute — a crashed checkout gives its stock
back within TTL + 60 s) and the ledger reconciliation (hourly). Imports
settings and nothing else, so it is safe from a settings module. Celery is
optional: ``manage.py inventory_housekeeping`` runs the same passes from
any scheduler.
"""
from __future__ import annotations

EXPIRE_TASK_NAME = "stapel_inventory.beat.expire_holds"
RECONCILE_TASK_NAME = "stapel_inventory.beat.reconcile_stock"

EXPIRE_BEAT_KEY = "inventory-expire-holds"
RECONCILE_BEAT_KEY = "inventory-reconcile-stock"

EXPIRE_INTERVAL_SECONDS = 60
RECONCILE_INTERVAL_SECONDS = 60 * 60


def expire_holds() -> int:
    """Release active holds past their TTL. Returns how many expired."""
    from .services.holds import expire_due

    return expire_due()


def reconcile_stock() -> int:
    """Check (and by default repair) the on_hand cache against the ledger."""
    from .services.reconcile import reconcile

    found = reconcile()
    return len(found["on_hand"]) + len(found["held"])


def get_inventory_beat_schedule() -> dict:
    """Beat entries for the two passes. Add to ``CELERY_BEAT_SCHEDULE``."""
    return {
        EXPIRE_BEAT_KEY: {"task": EXPIRE_TASK_NAME, "schedule": float(EXPIRE_INTERVAL_SECONDS)},
        RECONCILE_BEAT_KEY: {"task": RECONCILE_TASK_NAME, "schedule": float(RECONCILE_INTERVAL_SECONDS)},
    }


try:  # pragma: no cover - exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    expire_holds = shared_task(name=EXPIRE_TASK_NAME)(expire_holds)
    reconcile_stock = shared_task(name=RECONCILE_TASK_NAME)(reconcile_stock)


__all__ = [
    "EXPIRE_BEAT_KEY",
    "EXPIRE_TASK_NAME",
    "RECONCILE_BEAT_KEY",
    "RECONCILE_TASK_NAME",
    "expire_holds",
    "get_inventory_beat_schedule",
    "reconcile_stock",
]
