# SPEC — stapel-inventory 0.1.0 (stock per location, holds, movement ledger)

Status: **decided, built locally, not public** (brigade IN, brick wave
2026-11-12). Queue: `tasks/studio-wave-2026-10-15/SPEC-BRICKS-WAVE-2026-11-12.md`
§1 row `stapel-inventory`, the orders checkout contract §2, gates §3. House
style: `SPEC-STAPEL-ORDERS.md` applies verbatim. Consumers: the orders
checkout hooks (`STAPEL_ORDERS["CHECKOUT_HOOKS"]`), storefront pages
(`inventory.availability` + the public endpoint), staff tools (the staff
API, admin, CSV import), and the React pair (next slice).

## 1. Scope

| In | Out (0.1.0) |
|---|---|
| Locations (one default auto-created), stock per `(purchasable_type, purchasable_key, location)` | purchasing, suppliers, purchase orders |
| Append-only movement ledger; `on_hand` is a cache of it | lots, batches, expiry dates, serial numbers |
| Holds with TTL, keyed on the checkout `attempt_key` | multi-unit-of-measure, kits/bundles |
| Checkout hook: reserve / commit / release (orders §2) | valuation (cost, FIFO) and accounting |
| Allocation across locations by a strategy (`single_default`, `priority`) | shipping from a location (stapel-delivery's job) |
| Oversell policy axis `deny` / `backorder`, per-item override | a back-in-stock subscription UI (the fact is emitted; notifications are a host's subscriber) |
| Availability Function + public endpoint with a disclosure policy | |
| Staff API (receive, adjust with reason, transfer, thresholds), CSV import | |
| Low-stock / out-of-stock / back-in-stock / oversold facts, deduplicated | |
| Beat: expire holds, reconcile cache vs ledger | |

A purchasable is the opaque pair orders uses (`purchasable_type` ≤ 32,
`purchasable_key` ≤ 255). Inventory never imports a sibling and never reads
a sibling's table.

## 2. Models

- **`Location`** `code` (slug ≤ 32, unique), `name` ≤ 120, `priority` (int,
  lower first), `is_default` (at most one, partial unique constraint),
  `active`. The default location (`DEFAULT_LOCATION_CODE`, `"main"`) is
  created on `post_migrate` and lazily on first use (get-or-create under the
  unique constraint, so a race ends in one row).
- **`StockItem`** per `(purchasable_type, purchasable_key, location)`
  (unique). `on_hand` (int, cache), `low_stock_threshold` (null = the
  `LOW_STOCK_THRESHOLD` setting), `oversell_policy` (`""` = the `OVERSELL`
  setting, `deny`, `backorder`), `alert_state` (`ok` / `low` / `out`, the
  dedupe memory of the facts), `updated_at`.
- **`StockMovement`** — append-only (`save()` of an existing row, `delete()`,
  and queryset `update()`/`delete()` raise). `kind` ∈ `receipt, adjust, hold,
  commit, release, transfer, restock`; `on_hand_delta`, `held_delta`
  (signed ints), `on_hand_after` (snapshot), `reason` ≤ 200, `ref_type` ≤ 32 /
  `ref_key` ≤ 128 (`hold`/`<id>`, `transfer`/`<uuid>`, `order`/`<id>`),
  `actor_id` ≤ 64 (`str(user.pk)` or `"system"`), `created_at`.
- **`StockHold`** `token` (uuid, unique), `attempt_key` ≤ 128 (unique — the
  idempotency key), `user_id` ≤ 64 (nullable; null for a guest), `actor_id`
  ≤ 64 (nullable; the acting account, a guest's own id too — the hold's
  owner), `status` ∈ `active,
  committed, released, expired, returned`, `expires_at`, `reference` ≤ 64
  (the order id on commit), `release_reason` ≤ 32. **`StockHoldLine`** `hold`,
  `stock_item`, `line_index`, `quantity`, `backordered` (the part beyond
  stock under `backorder`).

`user_id`/`actor_id` are `CharField(64)`, never FKs (a pseudonymized id must
fit).

## 3. Invariants (the point of this brick)

1. **`on_hand = Σ on_hand_delta`** of the item's movements, always: every
   write to `on_hand` writes its movement in the same transaction under the
   item's row lock. The reconciler (beat, hourly) proves it and repairs the
   cache from the ledger (`RECONCILE_REPAIR`, default on), logging ERROR per
   drift — the ledger is the source of truth, the cache is derived.
2. **`held = Σ held_delta` = Σ quantity of the item's `active` hold lines.**
   Reported by the reconciler, never auto-repaired.
3. **`available = on_hand − Σ quantity of active, unexpired hold lines`.** An
   expired hold stops counting at `expires_at`, before the beat releases it,
   so a dead checkout never blocks stock longer than the TTL.
4. **No oversell under `deny`.** A reserve locks every candidate `StockItem`
   with `SELECT … FOR UPDATE` **in ascending id order** (one deterministic
   order for every path: reserve, commit, release, expire, receive, adjust,
   transfer — items first, then the hold row), re-counts `available` after
   the lock, and is all-or-nothing across lines. On Postgres two reserves
   for the last unit end as one hold and one refusal; opposite line orders
   cannot deadlock.
5. **Idempotency on `attempt_key`.** Replaying reserve with the same key and
   the same lines returns the same token; a key whose hold was released or
   expired re-reserves (orders keeps the key across a refused submit);
   commit and release are no-ops on replay.

## 4. Checkout hook (orders contract §2 + the orders contract deltas 10-19)

Registered by the settings fragment `stapel_inventory.preset.SETTINGS_DEFAULTS`
(stapel-tools merges `SETTINGS_DEFAULTS` for composites only today — a
hand-wired host pastes it; upstream ask T-IN1):

```python
"STAPEL_ORDERS": {"CHECKOUT_HOOKS": {"inventory": {
    "reserve": "inventory.checkout.reserve",
    "commit": "inventory.checkout.commit",
    "release": "inventory.checkout.release",
    "order": 50,
}}}
```

- `inventory.checkout.reserve` — payload = the adjuster request
  (`currency, subtotal, user_id (null for a guest), actor_id? (the acting
  account, a guest's own id too; recorded on the hold, `user_id` when
  absent; a commit fills a blank one), lines[{index,
  purchasable_type, purchasable_key, quantity, unit_price, line_total,
  attrs}], adjustments_so_far, context`) + `attempt_key` (lowercase UUID) →
  `{ok, token, errors}`. Lines of the same purchasable are summed. Refusals
  are `{code, field, params}` with `code` an `error.409.<slug>` key (orders
  refuses the checkout with that status): `error.409.insufficient_stock`
  (`field` `lines.<index>.quantity`, params `index, purchasable_type,
  purchasable_key, requested, available`), `error.409.stock_not_tracked`
  (only under `TRACK_UNKNOWN="deny"`). A live hold with the same lines
  answers the same token; a released or expired one reserves anew.
- `inventory.checkout.commit` — `{attempt_key, token | null, order_id,
  user_id | null}` → `{ok, token, errors}`; runs after the order commits and
  is retried by orders until `ok`. Decrements `on_hand` and consumes the
  hold. A hold the beat already expired is committed anyway (the order is
  the fact) and, if that drives a `deny` item below zero,
  `inventory.stock.oversold` is emitted. A hold released (or expired) and
  then reached by a `cancelled` release stays as it is (the cancel overtook
  the retried commit). An
  unknown key answers `ok: true` with an ERROR log line — there are no lines
  to decrement, and `ok: false` would make orders retry forever.
- `inventory.checkout.release` — `{attempt_key, token | null, order_id |
  null, reason: checkout_failed | cancelled | orphaned}` → `{ok, token,
  errors}`. An active hold is released; a committed hold released with
  `reason: cancelled` is **restocked** (a `restock` movement) when
  `RESTOCK_ON_CANCEL` (default on). **Only `cancelled` undoes a commit:**
  `orphaned`/`checkout_failed` (or a payload without a reason) on a
  committed hold are a no-op answering `ok: true`; a `cancelled` release on
  a released/expired hold is recorded in `release_reason` so a late commit
  stays a no-op; an unknown key answers `ok: true`.
  `tests/test_release_semantics.py` is the full (state, event) table.

The token is a convenience: every hook acts on `attempt_key` alone. Schemas
allow additional properties on the hook payloads (orders may grow them).

Untracked purchasables (no `StockItem` anywhere) pass through: no hold line,
never blocked, unless `TRACK_UNKNOWN="deny"`.

## 5. Allocation and oversell

`ALLOCATION` (resolver key: short name or dotted path; env picks short names):
- `single_default` (default) — only the default location's stock sells.
- `priority` — active locations by `(priority, id)`; a line may split across
  locations; all-or-nothing across the whole reserve.

`OVERSELL` `deny` (default) / `backorder` (the remainder is held as
`backordered` on the first candidate item and drives `on_hand` negative on
commit). Per-item `oversell_policy` overrides; for a purchasable the policy
of its default-location item (else its first candidate) applies.

## 6. API (`/inventory/api/v1/`, SimpleRouter-free plain paths)

| Method/path | Who | Request → Response |
|---|---|---|
| `GET availability/?purchasable_type=&keys=` | public | → `AvailabilityResponse {items: [AvailabilityItem {purchasable_type, purchasable_key, state, quantity}]}`; `state` ∈ `in_stock, few_left, out_of_stock, backorder, untracked`; `quantity` only under `PUBLIC_AVAILABILITY="exact"`; ≤ `MAX_AVAILABILITY_KEYS` keys. **Exactly one item per distinct key asked, in the order asked** (duplicates collapse): a key with no stock item reads `untracked` (sellable, not stock-managed) — or `out_of_stock` under `TRACK_UNKNOWN="deny"`, where checkout would refuse it |
| `GET locations/` | staff | anchor page (`anchor`, `limit`, `direction`; creation order) → `LocationPage` |
| `POST locations/` | staff | `LocationRequest` → `LocationResponse` |
| `PATCH locations/{code}/` | staff | `PatchedLocationUpdateRequest` → `LocationResponse` |
| `GET stock/` | staff | `?purchasable_type&purchasable_key&location&alert_state` → `StockItemPage` (anchor) |
| `GET stock/{id}/` | staff | → `StockItemResponse` (404 `stock_item_not_found`) |
| `PATCH stock/{id}/` | staff | `PatchedStockItemUpdateRequest {low_stock_threshold, oversell_policy}` → `StockItemResponse` |
| `POST stock/receive/` | staff | `StockReceiveRequest {purchasable_type, purchasable_key, location?, quantity, reason?}` → `StockItemResponse` |
| `POST stock/adjust/` | staff | `StockAdjustRequest {…, delta | on_hand, reason}` → `StockItemResponse` |
| `POST stock/transfer/` | staff | `StockTransferRequest {…, from_location, to_location, quantity, reason?}` → `StockTransferResponse {source, target}` |
| `GET movements/` | staff | `?stock_item&purchasable_type&purchasable_key&kind` → `StockMovementPage` (anchor, `-id`) |

`PUBLIC_AVAILABILITY`: `exact` (numbers), `in_stock` (boolean states only:
`in_stock`/`out_of_stock`/`backorder`/`untracked`), `few_left` (default; adds
`few_left` when `available ≤ FEW_LEFT_THRESHOLD`, no numbers).

## 7. Settings (`STAPEL_INVENTORY`, CONFIG.MD is the registry)

`DEFAULT_LOCATION_CODE "main"`, `ALLOCATION "single_default"`, `OVERSELL
"deny"`, `TRACK_UNKNOWN "allow"`, `HOLD_TTL_SECONDS 1800` (above orders' 900 s orphan sweep),
`LOW_STOCK_THRESHOLD 5`, `FEW_LEFT_THRESHOLD 5`, `PUBLIC_AVAILABILITY
"few_left"`, `RESTOCK_ON_CANCEL True`, `RECONCILE_REPAIR True`,
`DECREMENT_ON_ORDER_PLACED False`, `MAX_AVAILABILITY_KEYS 100`.

## 8. Functions, facts

- Functions: `inventory.availability {purchasable_type, keys, location?}` →
  `{items: {key: {tracked, on_hand, held, available, in_stock, policy,
  low_stock, locations[{code, on_hand, available}]}}}` (exact numbers —
  internal); the three checkout hooks.
- Facts (emitted in the mutating transaction, key = stock item id):
  `inventory.stock.low`, `inventory.stock.out`,
  `inventory.stock.back_in_stock`, `inventory.stock.oversold`. Dedupe: a fact
  fires on the transition of `alert_state` only (ok → low, * → out,
  out/low → ok), never on each write.
- Consumes (switch, default off): `orders.order.placed` →
  decrement per line, idempotent on `order_id` — the fallback for an orders
  without checkout hooks (the old ask L2), mutually exclusive with the hook
  (check E006 when both are on).

## 9. Checks

E001 unknown `ALLOCATION`; E002 `OVERSELL`; E003 `PUBLIC_AVAILABILITY`; E004
`TRACK_UNKNOWN`; E005 non-positive `HOLD_TTL_SECONDS`; E006 hook registered
and `DECREMENT_ON_ORDER_PLACED` on (double decrement); W001 orders present
(its `orders.order.status` Function in process, or a `STAPEL_ORDERS` dict)
but no inventory hook registered; W002 hold TTL not above orders'
`CHECKOUT_HOOK_ORPHAN_SECONDS`. Without orders every orders check is silent.

## 10. Gates

Suite on 3.12 and 3.14; `make contract`; `stapel-verify`; schema/migration
lint; `emit_check`; checks with and without orders' settings; migration from
zero; GDPR (export + erase of `StockHold.user_id` / `.actor_id`, `StockMovement.actor_id`);
concurrency (`tests/test_concurrency.py`): N threads × real transactions on a
locking database — SQLite file database in `IMMEDIATE` mode locally (writers
serialize on the database lock), Postgres row locks with
`STAPEL_TEST_DATABASE_URL` (the CI `postgres` job; also run locally against a
throwaway container); no oversell, idempotent replay, crash between
reserve and commit released by the beat, ledger/cache reconciliation, and
two inversions (a count before the lock; no `FOR UPDATE` on Postgres) that
must oversell.
