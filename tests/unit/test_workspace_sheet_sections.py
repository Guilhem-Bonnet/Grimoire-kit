"""La fiche Piloter par sections (#1243) : même charge que la fiche entière, un coût compté, jamais un chrono."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import file_memo
from grimoire.providers import registry
from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_routes, workspace_sheet


def test_la_fiche_entiere_est_exactement_la_reunion_des_sections(real_project: Path) -> None:
    """Rapide + lent, demandés séparément, redonnent la charge de la route complète (``doctor`` : ``None``)."""
    full = wa.sheet_view(real_project)
    parts: dict[str, Any] = {}
    for group in (workspace_sheet.FAST_SECTIONS, ("agents",), ("proposals",), ("capacity",)):
        parts.update(wa.sheet_view(real_project, group))

    assert set(workspace_sheet.SECTION_NAMES) | {"doctor"} == set(full)
    assert full["doctor"] is None
    for key in workspace_sheet.SECTION_NAMES:
        if key == "capacity":
            continue  # dépend de l'horloge (heure de l'instantané) : comparée par sa forme ci-dessous
        assert parts[key] == full[key], f"la section {key} diffère de la fiche entière"
    assert parts["capacity"].keys() == full["capacity"].keys()


def test_la_route_rend_la_fiche_entiere_sans_sections_et_la_section_demandee_avec(real_project: Path) -> None:
    whole = workspace_routes.workspace_get(real_project, "/api/workspace/sheet", {})
    assert set(whole) == set(workspace_sheet.SECTION_NAMES) | {"doctor"}
    only = workspace_routes.workspace_get(real_project, "/api/workspace/sheet", {"sections": ["agents"]})
    assert list(only) == ["agents"]
    assert only["agents"] == whole["agents"]


def test_une_section_demandee_ne_calcule_que_celle_la(real_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(wa, "agents_view", lambda root: calls.append("agents") or {"agents": []})
    monkeypatch.setattr(wa, "proposals_view", lambda root: calls.append("proposals") or {"proposals": []})
    monkeypatch.setattr(workspace_sheet, "_capacity", lambda root: calls.append("capacity") or {})

    result = wa.sheet_view(real_project, "health,memory")
    assert list(result) == ["health", "memory"]
    assert calls == [], f"des sous-vues non demandées ont été calculées : {calls}"

    wa.sheet_view(real_project, ["agents"])
    assert calls == ["agents"]


def test_une_section_inconnue_est_refusee(real_project: Path) -> None:
    with pytest.raises(ValueError, match="inconnue"):
        wa.sheet_view(real_project, "health,nope")
    assert workspace_sheet.parse_sections("") is None
    assert workspace_sheet.parse_sections(" health , health,name ") == ("health", "name")


_REGISTRY = "providers:\n  - id: p1\n    enabled: true\n    provider_type: anthropic\nrouting:\n  default_data_class: public\n"


def _registry_project(tmp_path: Path) -> Path:
    path = tmp_path / registry.REGISTRY_FILE
    path.parent.mkdir(parents=True)
    path.write_text(_REGISTRY, encoding="utf-8")
    old = time.time() - 3600  # assez ancien pour que la mémoire le garde (file_memo.COARSE_CLOCK_NS)
    os.utime(path, (old, old))
    return tmp_path


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    parses: list[int] = []
    real = registry._yaml
    # `ctime` d'un fichier qu'on vient d'écrire est récent, qu'on ait reculé sa date ou non : sans ce délai nul la
    # mémoire (qui refuse de garder un fichier tout juste écrit) ne garderait rien.
    monkeypatch.setattr(file_memo, "COARSE_CLOCK_NS", 0)

    def counting() -> Any:
        parses.append(1)
        return real()

    monkeypatch.setattr(registry, "_yaml", counting)
    return parses


def test_le_registre_n_est_analyse_qu_une_fois_par_changement_dans_une_lecture_d_affichage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registry_project(tmp_path)
    file_memo.clear()
    parses = _count_parses(monkeypatch)

    with file_memo.view_scope():
        for _ in range(10):
            registry.read_registry(root)
            registry.read_default_fallback_chain(root)
            registry.read_default_data_class(root)
    assert len(parses) == 1, f"{len(parses)} analyses du registre pour 30 lectures, une seule attendue"

    # Le fichier change : la lecture suivante le voit.
    path = root / registry.REGISTRY_FILE
    path.write_text(_REGISTRY.replace("public", "internal"), encoding="utf-8")
    old = time.time() - 1800
    os.utime(path, (old, old))
    with file_memo.view_scope():
        assert registry.read_default_data_class(root) == "internal"
    assert len(parses) == 2


def test_hors_lecture_d_affichage_le_registre_est_relu_a_chaque_appel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les décisions (routage réel) ne lisent jamais la mémoire : chaque appel relit le fichier."""
    root = _registry_project(tmp_path)
    file_memo.clear()
    parses = _count_parses(monkeypatch)
    for _ in range(3):
        registry.read_registry(root)
    assert len(parses) == 3


def test_la_capacite_n_analyse_le_registre_qu_une_fois(real_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools.workspace_capacity import capacity_view

    path = real_project / registry.REGISTRY_FILE
    assert path.is_file()
    old = time.time() - 3600
    os.utime(path, (old, old))
    file_memo.clear()
    parses = _count_parses(monkeypatch)

    first = capacity_view(real_project)
    assert len(parses) <= 1, f"{len(parses)} analyses du registre dans un seul appel de capacité"
    capacity_view(real_project)
    assert len(parses) <= 1, "le registre inchangé a été relu par l'appel suivant"
    assert first["wip"] is not None
