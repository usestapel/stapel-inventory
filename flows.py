"""Business flows of stapel-inventory (stapel_core.flows).

The Flow objects live here; HTTP steps are attached in ``views.py`` with
``@flow_step``. Literals are the canonical English source texts; i18n keys
are derived (``flow.<id>.title`` / ``.step.<order>.note``).
"""
from stapel_core.flows import Flow

SHOP = Flow(
    "inventory.shop",
    title="See whether an item is in stock",
    description=(
        "A visitor opens a product page or a catalogue; the page asks for the "
        "availability of the items it shows and renders in stock, a few left, "
        "out of stock or on backorder. At checkout the order holds the units "
        "under a row lock, so two shoppers racing for the last unit end as one "
        "order and one 'only 0 left'."
    ),
    actors=["Visitor", "Shopper"],
)
SHOP.action("inventory.stock.out", order=3, note="The last unit sold: staff and storefronts learn it once")

STOCK = Flow(
    "inventory.stock",
    title="Keep stock right",
    description=(
        "Staff receive deliveries, correct counts with a reason, move units "
        "between locations and set low-stock thresholds; every change is a "
        "ledger row and the shelf count is the sum of the ledger."
    ),
    actors=["Staff"],
)
STOCK.action("inventory.stock.low", order=5, note="A shelf reached its low-stock threshold")
STOCK.action("inventory.stock.back_in_stock", order=6, note="An item that was out can be sold again")
