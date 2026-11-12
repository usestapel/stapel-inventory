"""stapel-inventory capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli


def main(argv=None):
    from stapel_inventory._codegen import _configure

    _configure()
    from stapel_inventory.conf import DEFAULTS
    from stapel_inventory.urls import GATE_REGISTRY

    # CTO-facing axes, all behavioural (no endpoint is unmounted):
    # ALLOCATION (which locations sell), OVERSELL (deny | backorder),
    # TRACK_UNKNOWN (untracked items pass or are refused), PUBLIC_AVAILABILITY
    # (what visitors see), RESTOCK_ON_CANCEL and MAX_AVAILABILITY_KEYS (the
    # batch limit a client must page by).
    axes = {"ALLOCATION", "OVERSELL", "TRACK_UNKNOWN", "PUBLIC_AVAILABILITY", "RESTOCK_ON_CANCEL",
            "MAX_AVAILABILITY_KEYS"}
    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/inventory/api/v1",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in axes,
        axis_group=axis_group_rules(
            exact={
                "ALLOCATION": "inventory.allocation",
                "OVERSELL": "inventory.checkout",
                "TRACK_UNKNOWN": "inventory.checkout",
                "RESTOCK_ON_CANCEL": "inventory.checkout",
                "PUBLIC_AVAILABILITY": "inventory.storefront",
                "MAX_AVAILABILITY_KEYS": "inventory.storefront",
            }
        ),
        prog="stapel-inventory-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
