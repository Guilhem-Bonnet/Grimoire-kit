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


def test_la_capacite_n_analyse_le_registre_qu_une_fois(
    project_with_task: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le plan d'admission relit le registre à chaque routage de tâche (64 fois sur un vrai poste). Le projet de test
    n'a pas de politique de routage : on fait lire le registre huit fois par le plan, comme le routeur le ferait."""
    from grimoire.tools import workspace_capacity
    from grimoire.tools.workspace_capacity import capacity_view

    real_project, _task_id = project_with_task
    real_build_plan = workspace_capacity.build_plan

    def build_plan_lisant_le_registre(*args: Any, **kwargs: Any) -> Any:
        for _ in range(8):
            registry.read_registry(real_project)
            registry.read_default_fallback_chain(real_project)
        return real_build_plan(*args, **kwargs)

    monkeypatch.setattr(workspace_capacity, "build_plan", build_plan_lisant_le_registre)
    path = real_project / registry.REGISTRY_FILE
    assert path.is_file()
    old = time.time() - 3600
    os.utime(path, (old, old))
    file_memo.clear()
    parses = _count_parses(monkeypatch)

    first = capacity_view(real_project)
    assert len(parses) == 1, f"{len(parses)} analyses du registre dans un seul appel de capacité, une attendue"
    capacity_view(real_project)
    assert len(parses) == 1, "le registre inchangé a été relu par l'appel suivant"
    assert first["wip"] is not None


def test_une_sous_vue_qui_leve_porte_son_erreur_sans_casser_les_autres(
    real_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(root: Path) -> dict[str, Any]:
        raise RuntimeError("registre cassé")

    monkeypatch.setattr(workspace_sheet, "_capacity", boom)
    result = wa.sheet_view(real_project, ("capacity", "name"))
    assert result["capacity"] == {"error": "registre cassé"}
    assert "name" in result and "error" not in (result["name"] or {})


@pytest.mark.parametrize("section", workspace_sheet.SECTION_NAMES)
def test_chaque_section_se_demande_seule(real_project: Path, section: str) -> None:
    assert list(wa.sheet_view(real_project, section)) == [section]


def test_la_route_refuse_une_section_inconnue_en_valeur_error(real_project: Path) -> None:
    """Le transport code ``ValueError`` en 400."""
    with pytest.raises(ValueError, match="nope"):
        workspace_routes.workspace_get(real_project, "/api/workspace/sheet", {"sections": ["nope"]})


def test_le_nom_du_projet_vient_du_registre_du_portefeuille(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.tools import project_registry

    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setattr(
        project_registry,
        "load_registry",
        lambda: [
            {"slug": "vide"},  # sans chemin : ignorée
            {"slug": "ailleurs", "name": "Ailleurs", "path": str(tmp_path / "autre")},
            {"slug": "mine", "path": str(mine)},  # sans nom : le slug fait foi
        ],
    )
    assert workspace_sheet._project_name(mine.resolve()) == "mine"

    monkeypatch.setattr(project_registry, "load_registry", lambda: [{"slug": "ailleurs", "path": str(tmp_path / "x")}])
    assert workspace_sheet._project_name(mine.resolve()) is None


def test_un_chemin_du_registre_illisible_est_saute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools import project_registry

    class _Broken:
        def __init__(self, raw: str) -> None:
            self.raw = raw

        def resolve(self) -> Path:
            if self.raw == "boom":
                raise OSError("disque")
            return Path(self.raw)

    monkeypatch.setattr(workspace_sheet, "Path", _Broken)
    monkeypatch.setattr(
        project_registry, "load_registry", lambda: [{"slug": "casse", "path": "boom"}, {"slug": "bon", "name": "Bon", "path": "/ici"}]
    )
    assert workspace_sheet._project_name(Path("/ici")) == "Bon"  # type: ignore[arg-type]


def test_le_registre_absent_vide_ou_invalide(tmp_path: Path) -> None:
    assert registry.read_registry(tmp_path) == ()  # absent : zéro fournisseur, pas une erreur
    path = tmp_path / registry.REGISTRY_FILE
    path.parent.mkdir(parents=True)

    path.write_text("", encoding="utf-8")
    assert registry.read_registry(tmp_path) == ()  # vide : idem

    path.write_text("- une\n- liste\n", encoding="utf-8")
    with pytest.raises(registry.ProviderRegistryError, match="table YAML"):
        registry.read_registry(tmp_path)

    path.write_text("a: [non fermé\n", encoding="utf-8")
    with pytest.raises(registry.ProviderRegistryError, match="illisible"):
        registry.read_registry(tmp_path)

    path.write_bytes(b"\xff\xfe\x00 pas de l'utf-8")
    with pytest.raises(registry.ProviderRegistryError, match="illisible"):
        registry.read_registry(tmp_path)


def test_une_erreur_de_registre_n_est_jamais_gardee_en_memoire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registry_project(tmp_path)
    _count_parses(monkeypatch)
    path = root / registry.REGISTRY_FILE
    path.write_text("- liste\n", encoding="utf-8")
    with file_memo.view_scope():
        with pytest.raises(registry.ProviderRegistryError):
            registry.read_registry(root)
        path.write_text(_REGISTRY, encoding="utf-8")
        assert [p.id for p in registry.read_registry(root)] == ["p1"]
