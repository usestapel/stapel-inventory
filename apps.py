from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _ensure_default_location(sender, **kwargs):
    from .services.stock import default_location

    default_location()


class InventoryConfig(AppConfig):
    name = "stapel_inventory"
    label = "inventory"
    verbose_name = "Inventory"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import actions  # noqa: F401
        from . import checks  # noqa: F401
        from . import errors  # noqa: F401
        from . import flows  # noqa: F401
        from . import functions  # noqa: F401

        from stapel_core.gdpr import gdpr_registry, register_gdpr_owner

        from .gdpr import OWNER, SUBJECT_TYPES, InventoryGDPRProvider, erase_subject

        if not any(p.section == OWNER for p in gdpr_registry.providers):
            gdpr_registry.register(InventoryGDPRProvider())
        register_gdpr_owner(OWNER, SUBJECT_TYPES, erase_subject)
        post_migrate.connect(_ensure_default_location, sender=self,
                             dispatch_uid="stapel_inventory.default_location")
