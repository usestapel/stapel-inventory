"""Django system checks for stapel-inventory.

- E001  ``ALLOCATION`` names no known strategy.
- E002  ``OVERSELL`` is not ``deny`` / ``backorder``.
- E003  ``PUBLIC_AVAILABILITY`` is not ``exact`` / ``in_stock`` / ``few_left``.
- E004  ``TRACK_UNKNOWN`` is not ``allow`` / ``deny``.
- E005  ``HOLD_TTL_SECONDS`` is not a positive number of seconds.
- E006  the orders checkout hook is registered AND
        ``DECREMENT_ON_ORDER_PLACED`` is on: every order would decrement twice.
- W001  orders is present but no ``STAPEL_ORDERS["CHECKOUT_HOOKS"]`` entry
        names the inventory hooks: checkout never reserves stock.
- W002  the hold TTL is not longer than orders' orphan sweep
        (``CHECKOUT_HOOK_ORPHAN_SECONDS``, default 900).

Orders is never imported. It counts as present when its Function
``orders.order.status`` is provided in this process or the project declares
a ``STAPEL_ORDERS`` settings dict (an orders running in another service is
configured here only through that dict). Without it every orders-related
check is silent.
"""
from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

ORDERS_PROBE_FUNCTION = "orders.order.status"
ORDERS_ORPHAN_DEFAULT = 900


def orders_present() -> bool:
    from stapel_core.comm.registry import function_registry

    if getattr(settings, "STAPEL_ORDERS", None) is not None:
        return True
    return ORDERS_PROBE_FUNCTION in function_registry.names()


def _hook_registered() -> bool:
    from .functions import FN_RESERVE

    hooks = (getattr(settings, "STAPEL_ORDERS", None) or {}).get("CHECKOUT_HOOKS") or {}
    return any(isinstance(e, dict) and e.get("reserve") == FN_RESERVE for e in hooks.values())


@checks.register(checks.Tags.compatibility)
def check_axes(app_configs, **kwargs):
    from .conf import (
        OVERSELL_POLICIES,
        PUBLIC_AVAILABILITY_MODES,
        TRACK_UNKNOWN_MODES,
        inventory_settings,
    )

    findings = []
    try:
        inventory_settings.ALLOCATION
    except ImproperlyConfigured as exc:
        findings.append(checks.Error(str(exc), id="stapel_inventory.E001"))
    for key, allowed, check_id in (
        ("OVERSELL", OVERSELL_POLICIES, "E002"),
        ("PUBLIC_AVAILABILITY", PUBLIC_AVAILABILITY_MODES, "E003"),
        ("TRACK_UNKNOWN", TRACK_UNKNOWN_MODES, "E004"),
    ):
        value = getattr(inventory_settings, key)
        if value not in allowed:
            findings.append(checks.Error(
                f"STAPEL_INVENTORY[{key!r}] = {value!r}; expected one of {', '.join(allowed)}.",
                id=f"stapel_inventory.{check_id}",
            ))
    try:
        ttl = int(inventory_settings.HOLD_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = 0
    if ttl <= 0:
        findings.append(checks.Error(
            "STAPEL_INVENTORY['HOLD_TTL_SECONDS'] must be a positive number of seconds.",
            id="stapel_inventory.E005",
        ))
    return findings


@checks.register(checks.Tags.compatibility)
def check_orders_wiring(app_configs, **kwargs):
    from .conf import inventory_settings

    registered = _hook_registered()
    findings = []
    if registered and inventory_settings.DECREMENT_ON_ORDER_PLACED:
        findings.append(checks.Error(
            "The inventory checkout hook is registered in STAPEL_ORDERS['CHECKOUT_HOOKS'] and "
            "STAPEL_INVENTORY['DECREMENT_ON_ORDER_PLACED'] is on: every order would be "
            "decremented twice.",
            hint="Keep the hook; DECREMENT_ON_ORDER_PLACED is for an orders without hooks.",
            id="stapel_inventory.E006",
        ))
    if not orders_present():
        return findings
    if not registered and not inventory_settings.DECREMENT_ON_ORDER_PLACED:
        findings.append(checks.Warning(
            "stapel-orders is present but STAPEL_ORDERS['CHECKOUT_HOOKS'] names no inventory "
            "hook: checkout never reserves or decrements stock.",
            hint="Merge stapel_inventory.preset.SETTINGS_DEFAULTS['STAPEL_ORDERS'] into your "
                 "settings (see the preset module docstring).",
            id="stapel_inventory.W001",
        ))
    if registered:
        orders = getattr(settings, "STAPEL_ORDERS", None) or {}
        orphan = int(orders.get("CHECKOUT_HOOK_ORPHAN_SECONDS") or ORDERS_ORPHAN_DEFAULT)
        try:
            ttl = int(inventory_settings.HOLD_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = 0
        if 0 < ttl <= orphan:
            findings.append(checks.Warning(
                f"STAPEL_INVENTORY['HOLD_TTL_SECONDS'] = {ttl} is not longer than orders' "
                f"orphan sweep ({orphan} s): a slow checkout loses its hold before orders "
                "gives up on it.",
                id="stapel_inventory.W002",
            ))
    return findings
