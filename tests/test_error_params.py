"""Every raised error's params are declared in ``docs/errors.json``.

A refusal must send every ``{placeholder}`` of its template (else the text
cannot render) and nothing that ``docs/errors.json`` does not declare
(placeholders + ``errors.ERROR_PARAMS``). The gate walks every raise site of
the package source: ``InventoryError(status, KEY, {..}, field=..)``,
``StapelValidationError(KEY, {..})`` and the checkout hook's
``_refusal(KEY, field, **params)``. A ``field`` (positional or keyword)
counts as a sent param: HTTP refusals put it in ``params`` and orders adds a
hook refusal's ``field`` to its envelope's params. It cannot see params
built dynamically: a non-literal params argument fails the gate.
"""
import ast
import json
from pathlib import Path

import pytest
from stapel_core.i18n.domains import params_of

from stapel_inventory import errors

ROOT = Path(__file__).resolve().parent.parent


def _sources():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in ("tests", ".venv", "migrations", "build") or rel.name.startswith("_codegen"):
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_keys(node, where):
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return set()
    assert isinstance(node, ast.Dict), f"{where}: params must be a literal dict"
    assert all(isinstance(k, ast.Constant) for k in node.keys), f"{where}: non-literal param name"
    return {k.value for k in node.keys}


def raise_sites():
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            where = f"{rel}:{node.lineno}"
            kwargs = {k.arg: k.value for k in node.keywords}
            if name == "InventoryError" and len(node.args) >= 2:
                key, params = node.args[1], node.args[2] if len(node.args) > 2 else kwargs.get("params")
                sent = _literal_keys(params, where) | ({"field"} if "field" in kwargs else set())
            elif name == "StapelValidationError" and node.args:
                key = node.args[0]
                sent = _literal_keys(node.args[1] if len(node.args) > 1 else None, where)
            elif name == "_refusal" and node.args:
                key = node.args[0]
                sent = {k for k in kwargs if k and k != "field"}
                if len(node.args) > 1 or "field" in kwargs:
                    sent.add("field")
            else:
                continue
            key_name = _name(key)
            if key_name and key_name.startswith("ERR_") and hasattr(errors, key_name):
                yield where, getattr(errors, key_name), sent


SITES = list(raise_sites())


def test_the_gate_sees_raise_sites():
    assert len(SITES) >= 15, f"checked {len(SITES)} raise sites"


@pytest.mark.parametrize("where,key,sent", SITES, ids=[s[0] for s in SITES])
def test_sent_params_are_declared(where, key, sent):
    placeholders = set(params_of(errors.STAPEL_INVENTORY_ERRORS[key]))
    declared = placeholders | set(errors.ERROR_PARAMS.get(key, ()))
    assert placeholders <= sent, f"{where}: {key} misses {sorted(placeholders - sent)}"
    assert sent <= declared, f"{where}: {key} sends undeclared {sorted(sent - declared)}"


def test_errors_json_declares_the_extra_params():
    declared = {e["code"]: e["params"] for e in json.loads((ROOT / "docs" / "errors.json").read_text())}
    for key, template in errors.STAPEL_INVENTORY_ERRORS.items():
        expected = list(dict.fromkeys([*params_of(template), *errors.ERROR_PARAMS.get(key, ())]))
        assert declared[key] == expected, key
