"""comm Functions provided by stapel-inventory (schemas in ``schemas/functions/``).

- ``inventory.availability`` — exact availability for a keyed batch of one
  purchasable type (internal: numbers, policy, per location). The public
  endpoint applies the ``PUBLIC_AVAILABILITY`` disclosure policy on top.
- ``inventory.checkout.reserve`` / ``.commit`` / ``.release`` — the orders
  checkout hooks (``STAPEL_ORDERS["CHECKOUT_HOOKS"]``, registered by
  ``preset.SETTINGS_DEFAULTS``). Each answers ``{ok, token, errors}``.

Every schema carries the output shape under ``$defs.response``; the suite
validates answers against it.
"""
from stapel_core.comm import function

FN_AVAILABILITY = "inventory.availability"
FN_RESERVE = "inventory.checkout.reserve"
FN_COMMIT = "inventory.checkout.commit"
FN_RELEASE = "inventory.checkout.release"


@function(FN_AVAILABILITY)
def availability(payload):
    from .conf import inventory_settings
    from .services.stock import availability as compute

    keys = [str(k) for k in payload.get("keys") or []]
    limit = int(inventory_settings.MAX_AVAILABILITY_KEYS)
    if len(keys) > limit:
        raise ValueError(f"inventory.availability answers at most {limit} keys")
    return {"items": compute(str(payload["purchasable_type"]), keys, payload.get("location") or None)}


@function(FN_RESERVE)
def checkout_reserve(payload):
    from .services.holds import reserve

    return reserve(payload)


@function(FN_COMMIT)
def checkout_commit(payload):
    from .services.holds import commit

    return commit(payload)


@function(FN_RELEASE)
def checkout_release(payload):
    from .services.holds import release

    return release(payload)
