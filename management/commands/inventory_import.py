from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Import stock from a CSV (purchasable_type, purchasable_key, quantity[, location]"
            "[, low_stock_threshold]). Mode 'set' counts stock, 'receive' adds it.")

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--mode", choices=("set", "receive"), default="set")
        parser.add_argument("--dry-run", action="store_true", help="Show the plan, write nothing.")

    def handle(self, *args, path, mode, dry_run, **options):
        from stapel_inventory.services import importer

        with open(path, newline="", encoding="utf-8") as handle:
            planned = importer.plan(handle, mode=mode)
        for error in planned.errors:
            self.stderr.write(error)
        if planned.errors:
            raise CommandError(f"{len(planned.errors)} bad row(s); nothing imported")
        verb = "would change" if dry_run else "changed"
        if not dry_run:
            importer.apply(planned, mode=mode)
        self.stdout.write(f"{len(planned.rows)} row(s) read, {planned.changes} {verb} stock")
