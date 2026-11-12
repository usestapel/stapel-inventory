# Changelog

All notable changes to stapel-inventory are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [0.1.0] - unreleased

### Added
- Locations (a default one created on migrate), stock per
  `(purchasable_type, purchasable_key, location)` with an `on_hand` cache, an
  append-only movement ledger (receipt, adjust, hold, commit, release,
  transfer, restock) and checkout holds with a TTL.
- The orders checkout hook: `inventory.checkout.reserve` / `.commit` /
  `.release`, all-or-nothing across lines, row locks in ascending id order,
  idempotent on the checkout attempt key; only a `cancelled` release undoes
  a commit (restock), other reasons answer ok and change nothing, and a
  commit after a cancel is a no-op; registered by
  `stapel_inventory.preset.SETTINGS_DEFAULTS`.
- Allocation strategies `single_default` and `priority`
  (`register_allocation` for more); oversell policy `deny` / `backorder` with a
  per-item override; untracked purchasables pass through unless
  `TRACK_UNKNOWN="deny"`.
- `inventory.availability` Function and the public `GET availability/`
  endpoint with the `PUBLIC_AVAILABILITY` disclosure policy.
- Staff API: locations, stock list, receive, adjust with a reason, transfer,
  thresholds, the ledger; `inventory_import` CSV command (dry run).
- Facts `inventory.stock.low`, `.out`, `.back_in_stock` (once per
  transition) and `.oversold`.
- Beat: hold expiry and ledger/cache reconciliation;
  `inventory_housekeeping` command.
- System checks E001–E006, W001–W002; GDPR owner (pseudonymizes holds and
  ledger actors); `user.merged` re-parent.
- Concurrency suite with real threads on a locking database (SQLite
  `IMMEDIATE` locally, Postgres row locks in the CI `postgres` job).
