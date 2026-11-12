"""System checks, each with the case that must fire and the one that must not.

"With and without orders": orders is present when its probe Function is
provided in process or STAPEL_ORDERS is declared. The provided case is
simulated by registering a stand-in Function (orders is not a dependency of
this suite).
"""
import pytest
from django.core import checks

from stapel_inventory import checks as inv_checks
from stapel_inventory.preset import SETTINGS_DEFAULTS


def ids(findings):
    return [f.id for f in findings]


@pytest.fixture
def orders_installed():
    from stapel_core.comm.registry import function_registry

    function_registry.register(inv_checks.ORDERS_PROBE_FUNCTION, lambda payload: {})
    try:
        yield
    finally:
        function_registry._providers.pop(inv_checks.ORDERS_PROBE_FUNCTION, None)


class TestAxes:
    def test_defaults_are_silent(self):
        assert inv_checks.check_axes(None) == []

    @pytest.mark.parametrize("key,value,check_id", [
        ("ALLOCATION", "nearest", "stapel_inventory.E001"),
        ("OVERSELL", "maybe", "stapel_inventory.E002"),
        ("PUBLIC_AVAILABILITY", "all", "stapel_inventory.E003"),
        ("TRACK_UNKNOWN", "ignore", "stapel_inventory.E004"),
        ("HOLD_TTL_SECONDS", 0, "stapel_inventory.E005"),
    ])
    def test_each_bad_value_fires(self, settings, key, value, check_id):
        settings.STAPEL_INVENTORY = {key: value}
        assert ids(inv_checks.check_axes(None)) == [check_id]


class TestWithoutOrders:
    def test_silent(self):
        assert inv_checks.check_orders_wiring(None) == []

    def test_a_declared_orders_settings_dict_counts_as_present(self, settings):
        settings.STAPEL_ORDERS = {}
        assert ids(inv_checks.check_orders_wiring(None)) == ["stapel_inventory.W001"]

    def test_double_decrement_is_e006_even_without_orders(self, settings):
        settings.STAPEL_ORDERS = SETTINGS_DEFAULTS["STAPEL_ORDERS"]
        settings.STAPEL_INVENTORY = {"DECREMENT_ON_ORDER_PLACED": True}
        assert ids(inv_checks.check_orders_wiring(None)) == ["stapel_inventory.E006"]


class TestWithOrders:
    def test_unwired_is_w001(self, orders_installed):
        findings = inv_checks.check_orders_wiring(None)
        assert ids(findings) == ["stapel_inventory.W001"] and isinstance(findings[0], checks.Warning)

    def test_the_shipped_fragment_wires_it(self, orders_installed, settings):
        settings.STAPEL_ORDERS = SETTINGS_DEFAULTS["STAPEL_ORDERS"]
        assert inv_checks.check_orders_wiring(None) == []

    def test_the_fallback_switch_also_counts_as_wired(self, orders_installed, settings):
        settings.STAPEL_INVENTORY = {"DECREMENT_ON_ORDER_PLACED": True}
        assert inv_checks.check_orders_wiring(None) == []

    def test_short_ttl_is_w002(self, orders_installed, settings):
        settings.STAPEL_ORDERS = SETTINGS_DEFAULTS["STAPEL_ORDERS"]
        settings.STAPEL_INVENTORY = {"HOLD_TTL_SECONDS": 600}
        assert ids(inv_checks.check_orders_wiring(None)) == ["stapel_inventory.W002"]


@pytest.mark.django_db
def test_run_checks_has_no_inventory_error():
    from django.core.checks import run_checks

    errors = [f for f in run_checks() if f.id and f.id.startswith("stapel_inventory") and f.is_serious()]
    assert errors == []
