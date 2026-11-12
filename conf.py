"""Settings namespace for stapel-inventory.

All configuration is read through ``inventory_settings`` (lazily, at call
time). Resolution order per key: ``settings.STAPEL_INVENTORY`` dict -> flat
Django setting of the same name -> environment variable -> default below.

``ALLOCATION`` is a short name from the allocation registry
(``single_default``, ``priority``, plus whatever a deployment registers) or
a dotted path to a strategy; the environment may choose among the short
names only (``env_enum``).
"""
from stapel_core.conf import AppSettings

#: Accepted values of the plain-string axes (validated by checks E002-E004).
OVERSELL_POLICIES = ("deny", "backorder")
TRACK_UNKNOWN_MODES = ("allow", "deny")
PUBLIC_AVAILABILITY_MODES = ("exact", "in_stock", "few_left")

#: AppSettings-shaped literal dict: a top-level DEFAULTS lets the
#: capabilities.json emitter introspect axis keys/kinds.
DEFAULTS = {
    # The location stock lands in when a request names none; created on
    # post_migrate and on first use.
    "DEFAULT_LOCATION_CODE": "main",
    # Axis: how a reserve picks locations. "single_default": only the default
    # location sells. "priority": active locations by priority, a line may
    # split. A registered short name or a dotted path to a strategy.
    "ALLOCATION": "single_default",
    # Axis: "deny" refuses a reserve beyond available stock; "backorder"
    # holds the remainder and lets on_hand go negative on commit. A stock
    # item may override it (StockItem.oversell_policy).
    "OVERSELL": "deny",
    # Axis: "allow" = a purchasable with no stock item is not stock-managed
    # and passes checkout; "deny" = it is refused as not tracked.
    "TRACK_UNKNOWN": "allow",
    # How long a checkout hold keeps stock before the beat releases it. Keep
    # it above orders' CHECKOUT_HOOK_ORPHAN_SECONDS (900): orders releases a
    # crashed checkout itself first, the TTL is the second net.
    "HOLD_TTL_SECONDS": 1800,
    # available <= this (and > 0) is "low" (a stock item may override it).
    "LOW_STOCK_THRESHOLD": 5,
    # Axis: what the public availability endpoint discloses. "exact":
    # numbers; "in_stock": in/out only; "few_left": in/out plus a few-left
    # state at or below FEW_LEFT_THRESHOLD, never a number.
    "PUBLIC_AVAILABILITY": "few_left",
    "FEW_LEFT_THRESHOLD": 5,
    # Releasing a committed hold (an order cancelled) puts the units back.
    "RESTOCK_ON_CANCEL": True,
    # The reconciler rewrites a drifted on_hand cache from the ledger (it
    # always logs the drift); False = report only.
    "RECONCILE_REPAIR": True,
    # Fallback for an orders without checkout hooks: decrement stock on
    # orders.order.placed, idempotent on the order id. Never together with
    # the hook (check E006).
    "DECREMENT_ON_ORDER_PLACED": False,
    # The public endpoint and the Function answer at most this many keys.
    "MAX_AVAILABILITY_KEYS": 100,
}

inventory_settings = AppSettings(
    "STAPEL_INVENTORY",
    defaults=DEFAULTS,
    resolvers={"ALLOCATION": "stapel_inventory.allocation.resolve_allocation"},
    env_enum={"ALLOCATION": "stapel_inventory.allocation.allocation_names"},
)

__all__ = [
    "DEFAULTS",
    "OVERSELL_POLICIES",
    "PUBLIC_AVAILABILITY_MODES",
    "TRACK_UNKNOWN_MODES",
    "inventory_settings",
]
