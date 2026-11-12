"""Package-level public API (PEP 562 lazy exports) and import hygiene."""
import os
import subprocess
import sys

import stapel_inventory


class TestLazyExports:
    def test_all_declares_public_api(self):
        assert stapel_inventory.__all__ == [
            "inventory_settings",
        ]

    def test_settings_resolve(self):
        from stapel_inventory.conf import inventory_settings

        assert stapel_inventory.inventory_settings is inventory_settings

    def test_unknown_attribute_raises(self):
        try:
            stapel_inventory.nonexistent_export
        except AttributeError as exc:
            assert "nonexistent_export" in str(exc)
        else:
            raise AssertionError("expected AttributeError")


class TestImportWithoutDjangoSettings:
    def test_package_import_is_django_free(self):
        """`import stapel_inventory` must not import Django nor require settings."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = (
            "import sys\n"
            "import stapel_inventory\n"
            'polluted = [m for m in sys.modules if m == "django" or m.startswith("django.")]\n'
            'assert not polluted, f"django imported at package import time: {polluted}"\n'
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(sys.executable),
        )
        assert result.returncode == 0, result.stderr


class TestPreset:
    def test_fragment_is_django_free_and_names_the_hook_functions(self):
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = (
            "import sys\n"
            "from stapel_inventory.preset import SETTINGS_DEFAULTS\n"
            "hook = SETTINGS_DEFAULTS['STAPEL_ORDERS']['CHECKOUT_HOOKS']['inventory']\n"
            "assert set(hook) == {'reserve', 'commit', 'release', 'order'}\n"
            "assert not [m for m in sys.modules if m.startswith('django')]\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                env=env, cwd=os.path.dirname(sys.executable))
        assert result.returncode == 0, result.stderr

    def test_hook_functions_are_registered(self):
        from stapel_core.comm.registry import function_registry

        from stapel_inventory.preset import CHECKOUT_HOOK

        for name in ("reserve", "commit", "release"):
            assert function_registry.get(CHECKOUT_HOOK[name])
