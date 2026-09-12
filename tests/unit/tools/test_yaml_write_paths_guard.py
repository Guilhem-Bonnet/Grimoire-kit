"""Static guard for grimoire-kit#430: no module may pair the *safe*
``load_yaml()`` loader with ``save_yaml()`` without also importing
``load_yaml_roundtrip()``.

``load_yaml()`` (ruamel ``typ="safe"``) returns a bare dict with no comment,
quote-style or flow/block metadata. Feeding that into ``save_yaml()`` to
rewrite an existing file has nothing left to round-trip, so every comment in
that file is silently discarded — exactly the bug reported in #430
(``grimoire upgrade`` stripping every comment from ``project-context.yaml``).

This is a *static* companion to the runtime guard in ``save_yaml()`` itself
(which refuses a plain ``dict``/``list`` on the ruamel backend): the runtime
guard catches it the moment the code path executes; this test catches it at
review time, by inspecting every module's imports with :mod:`ast` rather
than executing anything.

A module that only *reads* with ``load_yaml()`` (validation, lookups, etc.)
is unaffected — this only fires when the very same module also imports
``save_yaml``, i.e. it round-trips a file it read.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "grimoire"
_COMMON_MODULE = "grimoire.tools._common"


def _imported_common_names(tree: ast.Module) -> set[str]:
    """Names imported from ``grimoire.tools._common`` in *tree*, by their
    original (pre-``as``) name — ``load_yaml``, ``save_yaml``,
    ``load_yaml_roundtrip``, etc."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _COMMON_MODULE:
            names.update(alias.name for alias in node.names)
    return names


def _iter_source_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_src_root_is_found() -> None:
    """Sanity check the path math above before trusting an empty scan."""
    assert _SRC_ROOT.is_dir(), f"expected {_SRC_ROOT} to exist"
    assert (_SRC_ROOT / "tools" / "_common.py").is_file()


def test_no_module_pairs_safe_load_yaml_with_save_yaml_unguarded() -> None:
    offenders: list[str] = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_common_names(tree)
        if "save_yaml" in imported and "load_yaml" in imported and "load_yaml_roundtrip" not in imported:
            offenders.append(str(path.relative_to(_SRC_ROOT.parent.parent)))

    assert not offenders, (
        "these modules import both the safe load_yaml() and save_yaml() from "
        f"{_COMMON_MODULE} without load_yaml_roundtrip(): {offenders}. "
        "Rewriting a file loaded with load_yaml() silently strips every "
        "comment in it (grimoire-kit#430) — load it with "
        "load_yaml_roundtrip() instead before mutating and saving it."
    )


def test_common_module_itself_still_exports_all_three_helpers() -> None:
    """Guards the guard: if load_yaml_roundtrip() gets renamed without
    updating this test, fail loudly here instead of silently scanning for a
    name nothing imports anymore."""
    tree = ast.parse((_SRC_ROOT / "tools" / "_common.py").read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {"load_yaml", "load_yaml_roundtrip", "save_yaml"} <= defined
