"""Single-module Django settings shared by the test suite and contract emission.

One copy, so the harness that emits ``docs/schema.json`` and the tests that
prove it can never drift in INSTALLED_APPS or comm config. The production
``REST_FRAMEWORK`` (exception handler, schema class) is used by both: the
error envelope the tests assert is the one a host serves.

The test database is the point of this brick's suite: the concurrency
proofs need a database that LOCKS.

- ``STAPEL_TEST_DATABASE_URL`` set (the CI ``postgres`` job): that server,
  where ``SELECT … FOR UPDATE`` takes real row locks.
- unset: a SQLite *file* in ``IMMEDIATE`` transaction mode, so every
  ``atomic()`` block starts by taking the database write lock and concurrent
  writers serialize on it (``:memory:`` would give every thread its own
  database and two writers would never meet).
"""
from __future__ import annotations

import os
import tempfile

#: One file per test process (pytest-django creates and drops it).
SQLITE_TEST_FILE = os.path.join(tempfile.gettempdir(), f"stapel_inventory_test_{os.getpid()}.sqlite3")


def _server_url() -> str:
    return os.environ.get("STAPEL_TEST_DATABASE_URL", "")  # noqa: CFG001 - test harness switch, not a knob


def test_database(*, contract: bool = False) -> dict:
    if contract:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    if _server_url():
        from stapel_core.testing import test_database as server_database

        return server_database()
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": SQLITE_TEST_FILE,
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 30,
            "init_command": "PRAGMA journal_mode=WAL;",
        },
        "TEST": {"NAME": SQLITE_TEST_FILE},
    }


def settings_kwargs(
    *,
    root_urlconf: str = "stapel_inventory.tests.urls",
    contract: bool = False,
) -> dict:
    rest_framework = {
        "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
        "DEFAULT_SCHEMA_CLASS": "stapel_core.django.openapi.schemas.PermissionAwareAutoSchema",
        "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
    }
    if contract:
        rest_framework.update({
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
            ],
            "DEFAULT_PERMISSION_CLASSES": [
                "stapel_core.django.api.permissions.IsServiceRequest",
                "stapel_core.django.api.permissions.IsSuperUser",
            ],
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ],
        })
    return dict(
        SECRET_KEY="test-secret-key-not-for-production",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.admin",
            "django.contrib.messages",
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_inventory",
        ],
        AUTH_USER_MODEL="users.User",
        DATABASES={"default": test_database(contract=contract)},
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
        ROOT_URLCONF=root_urlconf,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
        STAPEL_COMM={
            "OUTBOX_ENABLED": False,
            "ACTION_TRANSPORT": "inprocess",
            "VALIDATE_SCHEMAS": True,
        },
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]},
        }],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        # The users app is created unmigrated on SQLite (fast); on a server
        # `migrate --run-syncdb` would create it before auth_group exists, so
        # the real chain runs there (billing's precedent).
        **({} if _server_url() else {"MIGRATION_MODULES": {"users": None}}),
        REST_FRAMEWORK=rest_framework,
    )


#: Forced on drf-spectacular's singleton so a single-module instance derives
#: the same operation ids as a multi-module aggregate.
CODEGEN_SCHEMA_PATH_PREFIX = "/"


def enum_name_overrides() -> dict:
    """Stable enum component names (stapel-schema-lint SCH004)."""
    from stapel_inventory.models import AlertState, MovementKind, OversellPolicy
    from stapel_inventory.serializers import AVAILABILITY_STATES

    return {
        "AvailabilityStateEnum": [(s, s) for s in AVAILABILITY_STATES],
        "AlertStateEnum": AlertState,
        "MovementKindEnum": MovementKind,
        "OversellPolicyEnum": OversellPolicy,
    }
