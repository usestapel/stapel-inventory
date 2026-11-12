"""Service layer of stapel-inventory: stock, holds, facts, reconciliation.

Views, comm Functions and the beat call these; nothing here knows about
HTTP. A refusal is an :class:`InventoryError` carrying the status, the
error key and its params — the view layer renders it as the fleet error
envelope, the checkout hooks as ``{code, field, params}``.

Lock order, the same on every path: the stock items a call touches, in
ascending id, then the hold row. Two calls that need the same items wait
for each other instead of deadlocking.
"""
from __future__ import annotations


class InventoryError(Exception):
    """A domain refusal with its HTTP status and i18n error key."""

    def __init__(self, status: int, key: str, params: dict | None = None, field: str | None = None):
        self.status = status
        self.key = key
        self.params = params or {}
        #: The request field the refusal is about; HTTP answers carry it as ``params.field``.
        self.field = field
        super().__init__(key)

    @property
    def envelope_params(self) -> dict:
        return {**self.params, "field": self.field} if self.field else dict(self.params)


__all__ = ["InventoryError"]
