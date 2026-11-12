from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Expire stale checkout holds and reconcile the stock cache with the ledger."

    def add_arguments(self, parser):
        parser.add_argument("--report-only", action="store_true",
                            help="Reconcile without repairing a drifted cache.")

    def handle(self, *args, **options):
        from stapel_inventory.services.holds import expire_due
        from stapel_inventory.services.reconcile import reconcile

        expired = expire_due()
        found = reconcile(repair=False if options["report_only"] else None)
        self.stdout.write(
            f"expired {expired}, on_hand drift {len(found['on_hand'])}, held drift {len(found['held'])}"
        )
