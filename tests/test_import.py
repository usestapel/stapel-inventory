"""CSV import: one plan for dry run and apply; a bad file applies nothing."""
import io

import pytest
from django.core.management import CommandError, call_command

from stapel_inventory.models import MovementKind, StockItem, StockMovement
from stapel_inventory.services import importer

from .factories import location, stock_of

pytestmark = pytest.mark.django_db

CSV = (
    "purchasable_type,purchasable_key,quantity,location,low_stock_threshold\n"
    "listing,a,10,,2\n"
    "listing,b,4,back,\n"
)


def test_dry_run_and_apply_count_the_same(tmp_path, capsys):
    location("back")
    stock_of("a", 10)
    path = tmp_path / "stock.csv"
    path.write_text(CSV)
    call_command("inventory_import", str(path), "--dry-run")
    dry = capsys.readouterr().out
    assert dry == "2 row(s) read, 1 would change stock\n"
    assert not StockItem.objects.filter(purchasable_key="b").exists()
    call_command("inventory_import", str(path))
    assert capsys.readouterr().out == "2 row(s) read, 1 changed stock\n"
    assert StockItem.objects.get(purchasable_key="b").on_hand == 4
    assert StockItem.objects.get(purchasable_key="a").low_stock_threshold == 2


def test_set_mode_is_a_count_receive_mode_adds():
    stock_of("a", 3)
    planned = importer.plan(io.StringIO("purchasable_type,purchasable_key,quantity\nlisting,a,5\n"))
    importer.apply(planned)
    assert StockItem.objects.get().on_hand == 5
    assert StockMovement.objects.filter(kind=MovementKind.ADJUST).get().on_hand_delta == 2
    planned = importer.plan(io.StringIO("purchasable_type,purchasable_key,quantity\nlisting,a,5\n"),
                            mode="receive")
    importer.apply(planned, mode="receive")
    assert StockItem.objects.get().on_hand == 10


def test_one_bad_row_applies_nothing(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("purchasable_type,purchasable_key,quantity,location\n"
                    "listing,a,5,\nlisting,b,-1,\nlisting,c,1,nowhere\n")
    with pytest.raises(CommandError):
        call_command("inventory_import", str(path))
    assert not StockItem.objects.exists()
    planned = importer.plan(path.open())
    assert len(planned.errors) == 2


def test_missing_column():
    planned = importer.plan(io.StringIO("purchasable_key,quantity\na,1\n"))
    assert planned.errors == ["missing column(s): purchasable_type"]
