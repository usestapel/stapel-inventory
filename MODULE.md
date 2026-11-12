# stapel-inventory — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it
> without forking, and what not to do. Kept in the same change as any seam.
> Design spec: `docs/SPEC.md`. See also README.md and CHANGELOG.md.

## What this module provides

- **Stock per location** for opaque purchasables (`purchasable_type` ≤ 32,
  `purchasable_key` ≤ 255 — the orders pair): `Location` (one default,
  created on migrate), `StockItem` with an `on_hand` cache.
- **An append-only ledger** (`StockMovement`): `on_hand = Σ on_hand_delta`,
  `Σ held_delta = Σ active hold lines`; the hourly reconciler proves both.
- **Checkout holds** (`StockHold` + lines, TTL, keyed on the orders
  `attempt_key`) and the **orders checkout hook**: reserve / commit /
  release, all-or-nothing, row locks in ascending id order, idempotent.
- **Availability**: `inventory.availability` (exact, internal) and a public
  endpoint with a disclosure policy.
- **Staff API**: locations, receive, adjust (reason required), transfer,
  thresholds and per-item oversell policy, the ledger; CSV import command.
- **Facts**: low / out / back-in-stock (once per transition), oversold.

## Extension points (fork-free)

### Settings — `STAPEL_INVENTORY` namespace (`conf.py`, registry: CONFIG.MD)

| Key | Default | Kind |
|---|---|---|
| `ALLOCATION` | `"single_default"` | REPLACE: short name (`single_default`, `priority`, `register_allocation` names) or dotted path to an `AllocationStrategy` |
| `OVERSELL` | `"deny"` | axis `deny` / `backorder`; per item `StockItem.oversell_policy` |
| `TRACK_UNKNOWN` | `"allow"` | axis `allow` / `deny` |
| `PUBLIC_AVAILABILITY` | `"few_left"` | axis `few_left` / `exact` / `in_stock` |
| `RESTOCK_ON_CANCEL` | `True` | axis |
| `HOLD_TTL_SECONDS` | `1800` | must exceed orders' `CHECKOUT_HOOK_ORPHAN_SECONDS` (W002) |
| `LOW_STOCK_THRESHOLD` / `FEW_LEFT_THRESHOLD` | `5` / `5` | per item `low_stock_threshold` |
| `DEFAULT_LOCATION_CODE` | `"main"` | |
| `RECONCILE_REPAIR` | `True` | False = report only |
| `DECREMENT_ON_ORDER_PLACED` | `False` | hookless-orders fallback; never with the hook (E006) |
| `MAX_AVAILABILITY_KEYS` | `100` | |

### Allocation strategies (`allocation.py`)

Subclass `AllocationStrategy`, implement `eligible(candidates)` (which
`Candidate`s sell, in take order); `allocate()` and `available()` come with
it. Register with `register_allocation("name", factory)` from your
`AppConfig.ready()` or name it by dotted path. A strategy is pure — it never
touches the database; the service has already locked and counted.

### Orders wiring (`preset.py`)

`SETTINGS_DEFAULTS["STAPEL_ORDERS"]["CHECKOUT_HOOKS"]["inventory"]` =
`{reserve, commit, release, order: 50}` (hooks reserve in an explicit
order: inventory 50 — stock first —, promotions 100, delivery 200; release
runs in reverse). The stapel-tools scaffold merges
`SETTINGS_DEFAULTS` for composites only today; a host pastes it (README
quick start) until the companion merge lands.

### Presenters (`STAPEL_SWAP`)

| Key | Default | Builds |
|---|---|---|
| `STAPEL_INVENTORY_AVAILABILITY_PRESENTER` | `presenters.AvailabilityPresenter` | the public availability answer (applies the disclosure policy) |
| `STAPEL_INVENTORY_STOCK_PRESENTER` | `presenters.StockPresenter` | stock items, locations, transfers, ledger rows |

### Serializer seams (`views.py`)

Views inherit `StapelAPIView`; subclass, set `request_serializer_class` /
`response_serializer_class`, remount.

| View | Request | Response |
|---|---|---|
| `AvailabilityView` (public) | query `purchasable_type`, `keys` | `AvailabilityResponseSerializer` |
| `LocationListView` | `LocationRequestSerializer` | `LocationResponseSerializer` |
| `LocationDetailView` | `LocationUpdateRequestSerializer` | `LocationResponseSerializer` |
| `StockListView` | query filters | `StockItemPageSerializer` |
| `StockDetailView` | `StockItemUpdateRequestSerializer` | `StockItemResponseSerializer` |
| `StockReceiveView` / `StockAdjustView` | `StockReceiveRequestSerializer` / `StockAdjustRequestSerializer` | `StockItemResponseSerializer` |
| `StockTransferView` | `StockTransferRequestSerializer` | `StockTransferResponseSerializer` |
| `MovementListView` | query filters | `StockMovementPageSerializer` |

### Comm surface

| Kind | Name | Payload | Schema |
|---|---|---|---|
| Function (provides) | `inventory.availability` | `{purchasable_type, keys, location?}` → `{items: {key: {tracked, on_hand, held, available, in_stock, policy, low_stock, locations}}}` | `schemas/functions/` |
| Function (provides) | `inventory.checkout.reserve` | adjuster request + `attempt_key` → `{ok, token, errors}` | `schemas/functions/` |
| Function (provides) | `inventory.checkout.commit` | `{attempt_key, token?, order_id, user_id?}` → `{ok, token, errors}` | `schemas/functions/` |
| Function (provides) | `inventory.checkout.release` | `{attempt_key, token?, order_id?, reason}` → `{ok, token, errors}` | `schemas/functions/` |
| Action (emits) | `inventory.stock.low` / `.out` / `.back_in_stock` | `{stock_item_id, purchasable_type, purchasable_key, location, on_hand, available, threshold}` | `schemas/emits/` |
| Action (emits) | `inventory.stock.oversold` | the above + `{oversold, reference}` | `schemas/emits/` |
| Action (consumes) | `user.merged` | `{from_user_id, into_user_id}`; moves holds by `user_id` and `actor_id` | `schemas/consumes/` |
| Action (consumes) | `orders.order.placed` | only under `DECREMENT_ON_ORDER_PLACED`; validated in the handler, never registered (it would replace the emitter's schema) | `schemas/consumes/` |
| Action (consumes) | `user.deleted`, `gdpr.erasure.requested` | via `register_gdpr_owner("inventory", ["account"], …)` | core |

Every Function schema carries its output under `$defs.response`.

### Beat and commands

`beat.get_inventory_beat_schedule()` — `expire_holds` (60 s),
`reconcile_stock` (hourly). `manage.py inventory_housekeeping
[--report-only]`, `manage.py inventory_import <csv> [--mode set|receive]
[--dry-run]`.

### Checks

E001–E006, W001–W002 (see `checks.py`). Orders is detected without an import:
its `orders.order.status` Function in process, or a `STAPEL_ORDERS` dict.

## Invariants an extension must keep

- Change `on_hand` only through `services.stock.write_movement`, holding the
  item's row lock (`services.stock.lock_items`) inside one transaction.
- Lock order: stock items by ascending id, then the hold row. Never lock a
  hold before its items.
- Count `available` (`held_by_item`) after the lock, never before.
- The ledger is append-only (`save()` of an existing row, `delete()`, bulk
  `update()`/`delete()` raise); a correction is a new `adjust` movement.

## Anti-patterns

- **Never import a sibling module** (`stapel_orders`, `stapel_listings`, …);
  `tests/test_architecture.py` greps the package.
- **Never write `StockItem.on_hand` directly** — the reconciler will log it
  and overwrite it from the ledger.
- **Don't fork to change behavior** — strategies, presenters, settings and
  serializer seams cover it; anything else is an upstream contribution.
