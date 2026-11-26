"""``GET /api/workspace/sheet`` — la fiche Piloter, en une réponse (#548) ou par sections (#1243).

Sorti de :mod:`grimoire.tools.workspace_api` (qui touche son plafond de taille) : même charge, même parallélisme.

``?sections=a,b`` ne calcule QUE les sous-vues nommées et rend ``{a: …, b: …}`` ; sans ``sections`` la réponse
est celle d'avant, clé ``doctor`` comprise. Sur un poste réel (journal des traces de 97 Mo), la fiche entière
coûtait ~4 s à froid parce que trois de ses sous-vues — agents, propositions (toutes deux lisent le journal des
traces) et capacité (registre de fournisseurs) — retenaient les six autres : la page demande donc d'abord les
sous-vues rapides (:data:`FAST_SECTIONS`) et peint, puis chaque sous-vue lente séparément. La réponse complète
reste la réunion exacte des réponses par section (test d'égalité).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

__all__ = ["FAST_SECTIONS", "SECTION_NAMES", "SLOW_SECTIONS", "parse_sections", "sheet_view"]

#: Ce qui se calcule sans le journal des traces ni le registre de fournisseurs : de quoi peindre la fiche.
FAST_SECTIONS: tuple[str, ...] = ("health", "memory", "setupRun", "upgradeRuns", "flowRuns", "name")
#: Ce qui retient la fiche à froid : agents et propositions (journal des traces), capacité (registre).
SLOW_SECTIONS: tuple[str, ...] = ("agents", "proposals", "capacity")
SECTION_NAMES: tuple[str, ...] = (*FAST_SECTIONS, *SLOW_SECTIONS)


def parse_sections(raw: str | None) -> tuple[str, ...] | None:
    """Les sections demandées par ``?sections=a,b`` ; ``None`` (absent ou vide) veut dire « toute la fiche ».

    Un nom inconnu est refusé (``ValueError``, un 400) plutôt que ignoré : une faute de frappe rendrait sinon
    une fiche muette que rien ne distingue d'une sous-vue vide.
    """
    if raw is None or not raw.strip():
        return None
    names = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    unknown = [name for name in names if name not in SECTION_NAMES]
    if unknown:
        msg = f"section(s) de fiche inconnue(s) : {', '.join(unknown)} (attendu : {', '.join(SECTION_NAMES)})"
        raise ValueError(msg)
    return names


def _project_name(root: Path) -> str | None:
    """Nom d'affichage du projet, depuis le registre du portefeuille (#548).

    Évite un huitième appel réseau séparé (``api.projects()``) que la fiche Piloter du cockpit faisait juste
    pour ce nom. ``None`` quand le projet n'est pas (ou plus) enregistré : l'atelier mono-projet, qui connaît
    déjà son propre slug via ``/api/status``, ou un projet retiré du registre entre deux lectures.
    """
    from grimoire.tools.project_registry import load_registry

    for entry in load_registry():
        raw_path = str(entry.get("path", ""))
        if not raw_path:
            continue
        try:
            if Path(raw_path).resolve() == root:
                return str(entry.get("name") or entry.get("slug") or "") or None
        except OSError:
            continue
    return None


def _capacity(root: Path) -> dict[str, Any]:
    """La sous-vue Capacité de la fiche (P71) : la même lecture que ``GET /api/workspace/capacity``."""
    from grimoire.tools.workspace_capacity import capacity_view

    return capacity_view(root)


def _tasks(root: Path) -> dict[str, Callable[[], Any]]:
    from grimoire.tools import workspace_api
    from grimoire.tools.flow_runs import flow_runs_view
    from grimoire.tools.memory_link import memory_link_status
    from grimoire.tools.project_health import project_health
    from grimoire.tools.project_setup import read_setup_run

    return {
        "health": lambda: project_health(root),
        "memory": lambda: memory_link_status(root, probe=False),
        "agents": lambda: workspace_api.agents_view(root),
        "proposals": lambda: workspace_api.proposals_view(root),
        "setupRun": lambda: read_setup_run(root),
        "upgradeRuns": lambda: flow_runs_view(root, blueprint_id="project-upgrade"),
        # Même lecture que `GET /api/workspace/flows/runs` (la liste d'Observer) :
        # le KPI de la fiche en dérive, jamais du nombre de blueprints présents.
        "flowRuns": lambda: flow_runs_view(root),
        "name": lambda: _project_name(root),
        # Capacité (P71) : même lecture que `GET /api/workspace/capacity`.
        "capacity": lambda: _capacity(root),
    }


def sheet_view(project_root: Path, sections: Iterable[str] | None = None) -> dict[str, Any]:
    """Toute la fiche Piloter, ou seulement les *sections* nommées, calculées en parallèle (#548, #1243).

    Calculées en parallèle (même principe que :func:`grimoire.tools.project_health.fleet_status`) pour borner
    le temps total à la plus lente des sous-vues plutôt qu'à leur somme ; chacune passe par son propre cache
    (:mod:`grimoire.tools.view_cache`). ``doctor`` volontairement absent des sous-vues : mesuré (#548) à ~350 ms
    par appel contre ~120 ms pour ``health`` — l'onglet Problèmes (``ctx.api.doctor()``) le rend à la demande ;
    la fiche entière porte ``"doctor": None`` pour ne rien changer au contrat. Best-effort partout : une
    sous-vue qui lève ne casse jamais la fiche, elle porte son erreur dans son propre champ.
    """
    root = project_root.resolve()
    available = _tasks(root)
    wanted = None if sections is None else tuple(sections)
    tasks = available if wanted is None else {name: available[name] for name in wanted}
    results: dict[str, Any] = {"doctor": None} if wanted is None else {}
    with ThreadPoolExecutor(max_workers=max(len(tasks), 1)) as pool:
        futures = {key: pool.submit(fn) for key, fn in tasks.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:  # une sous-vue cassée ne casse jamais la fiche
                results[key] = {"error": str(exc)}
    return results
