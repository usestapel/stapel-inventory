"""Settings fragment that wires stapel-inventory into its neighbours.

Plain data, importable without Django settings. Merge it under the
project's own values (an explicit key wins):

    from stapel_inventory import preset

    STAPEL_ORDERS = {
        **preset.SETTINGS_DEFAULTS["STAPEL_ORDERS"],
        **STAPEL_ORDERS,   # the project's own keys, if any
    }
    STAPEL_ORDERS["CHECKOUT_HOOKS"] = {
        **preset.SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"],
        **STAPEL_ORDERS.get("CHECKOUT_HOOKS", {}),
    }

The stapel-tools scaffold merges ``SETTINGS_DEFAULTS`` two levels deep
(``STAPEL_<MOD>`` -> key -> registry entry), so this fragment and another
companion's (promotions, delivery) land side by side in ``CHECKOUT_HOOKS``.
Without orders installed the fragment is inert: nobody reads
``STAPEL_ORDERS``.
"""

#: Hook order: inventory 50 (stock first), promotions 100, delivery 200 —
#: explicit, never a tie broken by name. Orders reserves in ascending order
#: and releases in reverse, so a cart without stock is refused before any
#: promotion use or delivery intent is held.
HOOK_ORDER = 50

#: The orders checkout hook entry (contract: SPEC-BRICKS-WAVE §2 + the
#: orders contract deltas).
CHECKOUT_HOOK = {
    "reserve": "inventory.checkout.reserve",
    "commit": "inventory.checkout.commit",
    "release": "inventory.checkout.release",
    "order": HOOK_ORDER,
}

SETTINGS_DEFAULTS = {
    "STAPEL_ORDERS": {
        "CHECKOUT_HOOKS": {
            "inventory": CHECKOUT_HOOK,
        },
    },
}

INSTALLED_APPS = ["stapel_inventory"]

URL_INCLUDES = [
    ("inventory/api/", "stapel_inventory.urls"),
]

__all__ = ["CHECKOUT_HOOK", "INSTALLED_APPS", "SETTINGS_DEFAULTS", "URL_INCLUDES"]
