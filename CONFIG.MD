# CONFIG.MD — stapel-inventory

Config registry for **stapel-inventory**. One row per configuration key
the module reads, its **source** (`env` = the process environment / the
`STAPEL_INVENTORY` settings dict), what it is for, whether it is
required, and its default.

Every key is read through `inventory_settings` (`stapel_inventory.conf`,
namespace `STAPEL_INVENTORY`). Resolution order per key:
`settings.STAPEL_INVENTORY` dict → a flat Django setting of the same name →
environment variable → the default below. `ALLOCATION`, `OVERSELL`,
`TRACK_UNKNOWN`, `PUBLIC_AVAILABILITY`, `RESTOCK_ON_CANCEL` and
`MAX_AVAILABILITY_KEYS` are CTO-facing config axes. The orders wiring is not a key of this namespace: it is the
`STAPEL_ORDERS["CHECKOUT_HOOKS"]["inventory"]` entry shipped in
`stapel_inventory.preset.SETTINGS_DEFAULTS`.

## stapel-inventory

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| DEFAULT_LOCATION_CODE | env | code of the location created on migrate and used when a request names none. | no | main |
| ALLOCATION | env | axis: which locations sell — `single_default` (default location only) or `priority` (every active location by priority, lines may split), a `register_allocation` name; a dotted path only from the settings module. Unknown is check E001. | no | single_default |
| OVERSELL | env | axis: `deny` refuses a reserve beyond stock (`error.409.insufficient_stock`); `backorder` holds the remainder and lets on_hand go negative. Overridden per item by `StockItem.oversell_policy`. E002. | no | deny |
| TRACK_UNKNOWN | env | axis: `allow` = a purchasable with no stock item is not stock-managed and passes checkout; `deny` = refused with `error.409.stock_not_tracked`. E004. | no | allow |
| HOLD_TTL_SECONDS | env | how long a checkout hold keeps stock before the beat releases it; keep above orders' CHECKOUT_HOOK_ORPHAN_SECONDS (W002). Non-positive is E005. | no | 1800 |
| LOW_STOCK_THRESHOLD | env | available at or below this (and above zero) is "low" (`inventory.stock.low`); a stock item may override it. | no | 5 |
| PUBLIC_AVAILABILITY | env | axis: what `GET availability/` discloses — `few_left` (states, no numbers), `exact` (numbers too), `in_stock` (in/out only). E003. | no | few_left |
| FEW_LEFT_THRESHOLD | env | available at or below this reads `few_left` on the public endpoint. | no | 5 |
| RESTOCK_ON_CANCEL | env | axis: a `cancelled` release of a committed hold puts the units back (a `restock` movement). | no | true |
| RECONCILE_REPAIR | env | the hourly reconciler rewrites a drifted on_hand cache from the ledger (it always logs the drift at ERROR); false = report only. | no | true |
| DECREMENT_ON_ORDER_PLACED | env | fallback for an orders without checkout hooks: decrement on `orders.order.placed`, idempotent on order_id. Never together with the hook (E006). | no | false |
| MAX_AVAILABILITY_KEYS | env | keys one availability request (endpoint or Function) may ask for; over is `error.400.availability_too_many_keys`. | no | 100 |
