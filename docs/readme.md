## What this is

Stock for a Stapel shop — per location, with checkout holds and an
append-only movement ledger — over the same **opaque purchasables** orders
sells (`purchasable_type`, `purchasable_key`). Inventory never imports the
module that owns a product. Installed next to `stapel-orders` it becomes a
checkout hook: a checkout reserves stock, the placed order commits it, a
failed checkout or a cancel releases it. Without orders its staff API,
availability Function and facts work on their own.

## Quick start

```python
INSTALLED_APPS = [
    # ...
    "stapel_inventory",
]

# urls.py
path("inventory/api/", include("stapel_inventory.urls"))   # -> /inventory/api/v1/...

# settings.py — wire the orders checkout hook (the scaffold merges this for you
# once stapel-tools learns companion presets; until then paste it)
from stapel_inventory import preset as inventory_preset

STAPEL_ORDERS = {
    "CHECKOUT_HOOKS": {
        **inventory_preset.SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"],
    },
}

# hold expiry (every minute) and ledger reconciliation (hourly); Celery optional
from stapel_inventory.beat import get_inventory_beat_schedule
CELERY_BEAT_SCHEDULE = {**get_inventory_beat_schedule()}
```

Load stock and ask for it:

```bash
python manage.py inventory_import stock.csv --dry-run   # purchasable_type,purchasable_key,quantity[,location][,low_stock_threshold]
python manage.py inventory_import stock.csv
```

```python
from stapel_core.comm import call

call("inventory.availability", {"purchasable_type": "listing", "keys": ["42", "43"]})
# -> {"items": {"42": {"tracked": true, "available": 3, "in_stock": true, ...}, ...}}
```

## Concepts

- **Location** — where stock sits. One default location (`main`) is
  created on migrate. `ALLOCATION="single_default"` sells only from it;
  `"priority"` sells from every active location, highest priority first,
  splitting a line when it must.
- **Stock item** — one purchasable at one location. `on_hand` is a cache of
  the ledger; `available = on_hand − active, unexpired holds`.
- **Ledger** — every change is a `StockMovement` (receipt, adjust, hold,
  commit, release, transfer, restock) written in the same transaction under
  the item's row lock; rows are never edited or deleted. The beat proves
  `on_hand = Σ movements` hourly and repairs a drifted cache from the ledger.
- **Hold** — a checkout's claim, keyed on the checkout attempt key, with a
  TTL (`HOLD_TTL_SECONDS`, 30 min). Reserve locks the stock rows in
  ascending id order and re-counts under the lock, all-or-nothing across
  lines: two shoppers racing for the last unit end as one order and one
  `error.409.insufficient_stock`. Replays are idempotent; a released key
  reserves anew. A crashed checkout's hold is released by the beat.
- **Oversell** — `deny` (default) refuses; `backorder` holds the remainder
  and lets stock go negative. A purchasable with no stock record is not
  stock-managed and always sells, unless `TRACK_UNKNOWN="deny"`.
- **Availability** — `inventory.availability` answers numbers to other
  modules; `GET availability/` answers visitors what `PUBLIC_AVAILABILITY`
  allows (`few_left` by default: in stock / a few left / out / backorder).
- **Facts** — `inventory.stock.low`, `.out`, `.back_in_stock` fire once per
  transition, never per sale; `.oversold` when an expired hold's order
  still had to be decremented.
- **GDPR** — erasure pseudonymizes the shopper on holds and the staff
  member on the ledger; the numbers stay.
