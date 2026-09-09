"""The web data resolver and the generator must agree on what is per-project."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _generator_layers() -> set[str]:
    src = (ROOT / "scripts" / "gen-site-data.py").read_text(encoding="utf-8")
    body = src.split("def build_project(")[1].split("\ndef ")[0]
    return set(re.findall(r'_write\(out_dir,\s*"([^"]+\.json)"', body))


def _resolver_layers() -> set[str]:
    src = (ROOT / "web" / "atelier-nav.js").read_text(encoding="utf-8")
    block = src.split("const PROJECT_SCOPED = new Set([")[1].split("]);")[0]
    return set(re.findall(r"'([^']+\.json)'", block))


def test_project_scoped_layers_match_the_generator() -> None:
    """A drift here serves one project's data under another project's name."""
    assert _resolver_layers() == _generator_layers()


def test_read_version_tolerates_a_non_kit_project(tmp_path: Path) -> None:
    """A governed project is not grimoire-kit: no __version__.py, no version.txt."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gsd", ROOT / "scripts" / "gen-site-data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._read_version(tmp_path) == "unknown"
    assert mod._list_archetypes(tmp_path) == []
    assert mod._count_tools(tmp_path) == 0

    (tmp_path / "version.txt").write_text("9.9.9\n", encoding="utf-8")
    assert mod._read_version(tmp_path) == "9.9.9"


def test_routes_module_does_not_import_its_hub() -> None:
    """forge_routes est une feuille : un cycle d'import y est une régression."""
    src = (ROOT / "src" / "grimoire" / "tools" / "forge_routes.py").read_text(encoding="utf-8")
    assert "forge_server" not in src.replace("grimoire.tools.forge_server` pour", "")


def test_registry_entries_with_a_dead_path_are_skipped_and_counted(tmp_path: Path, capsys) -> None:
    """#340 : un chemin disparu ne doit plus déclencher `build_project`.

    Avant le correctif, `main()` appelait `build_project` pour chaque entrée du
    registre sans vérifier que le dossier existe encore — le coût mesuré d'un
    registre pollué (~276 chemins morts) était de l'ordre de la minute. Le test
    porte sur le comportement observable : le nombre d'entrées ignorées est
    nommé, et seul le projet vivant produit des données.
    """
    import importlib.util
    import json
    import subprocess

    spec = importlib.util.spec_from_file_location("gsd", ROOT / "scripts" / "gen-site-data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    live = tmp_path / "vivant"
    live.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(live), check=False, capture_output=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=str(live), check=False, capture_output=True)

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            [
                {"name": "vivant", "path": str(live), "slug": "vivant"},
                {"name": "fantome", "path": str(tmp_path / "disparu"), "slug": "fantome"},
            ]
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "data"

    rc = mod.main(["--registry", str(registry), "--out-dir", str(out_dir)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "1 entrée(s) ignorée(s), chemin disparu" in captured.out
    index = json.loads((out_dir / "projects.json").read_text(encoding="utf-8"))
    assert [p["slug"] for p in index["projects"]] == ["vivant"]
    assert not (out_dir / "projects" / "fantome").exists()


def test_blueprint_path_refuses_traversal(tmp_path: Path) -> None:
    """L'identifiant vient d'une URL : il ne doit jamais sortir du dossier."""
    import pytest

    from grimoire.tools.forge_server import ForgeAPI

    api = ForgeAPI(tmp_path, tmp_path, None)
    for bad in ("../../etc/passwd", "a/../../b", "..", "a b", ""):
        with pytest.raises(ValueError, match="invalide"):
            api._blueprint_path(bad)
    assert api._blueprint_path("ok-1").name == "ok-1.blueprint.json"
