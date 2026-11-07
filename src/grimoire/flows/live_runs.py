"""Runs de flow encore en vol, lus pour les hooks de compaction (W2-05, FLOW-3).

Module hors de ``grimoire.hosts.decisions`` : une décision ne doit importer que
son propre module (issue #419, ``tests/unit/test_hook_cost.py``). Le moteur est
donc importé à l'appel, pas au chargement.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grimoire.flows.engine import FlowEngine

#: Miroir des chemins que ``cmd_flow.py`` passe à ``FlowEngine`` (même motif que
#: ``tools/flow_runs.py`` : jamais réimporté depuis le CLI pour une simple lecture).
_FLOWS_DIR = Path("_grimoire-runtime-output") / "flows"
_KERNEL_DIR = Path("_grimoire-runtime-output") / "runtime"

#: Un run dans l'un de ces statuts n'attend plus rien : il n'est pas réinjecté.
_SETTLED_RUN_STATUSES = frozenset({"aborted", "refused", "completed", "verified"})

#: Plafond des lignes détaillées : au-delà, une seule ligne de comptage renvoie
#: vers ``grimoire flow list`` (issue #1151). La Forge relit la capsule à chaque
#: tour ; sans plafond, chaque run non soldé s'y ajoute indéfiniment.
_MAX_DETAILED_LINES = 3


def _run_created_at(engine: FlowEngine, run_id: str) -> str:
    """L'horodatage de lancement du run, ou ``""`` si sa méta est illisible.

    La méta d'un run est écrite une fois au lancement et jamais réécrite : cet
    horodatage dit la récence du run sans lire le kernel. Une méta illisible ne
    retire pas la ligne du run — elle le classe simplement après les runs datés
    (l'ISO-8601 UTC du moteur se trie lexicographiquement).
    """
    try:
        return engine.run_meta(run_id).created_at
    except Exception:
        return ""


def _run_line(engine: FlowEngine, run_id: str) -> str | None:
    """La ligne d'un run, ou ``None`` s'il n'attend plus rien ou si le kernel l'a oublié.

    ``status`` et ``blueprint_drift`` sont isolés : une dérive impossible à évaluer
    (blueprint supprimé ou illisible) ne fait jamais perdre le ``run_id`` ni le nœud.
    """
    if not engine.knows_run(run_id):
        return None  # métadonnée orpheline : plus rien de vivant à réinjecter
    try:
        view = engine.status(run_id, include_contract=False)
    except Exception as exc:
        return f"[Grimoire — flow actif] run {run_id} : état non évaluable — {type(exc).__name__}: {exc}"
    if view.status in _SETTLED_RUN_STATUSES:
        return None
    line = (
        f"[Grimoire — flow actif] run {view.run_id} (blueprint {view.blueprint_id}), "
        f"nœud courant {view.current_node or 'aucun'}, statut {view.status}"
    )
    try:
        if engine.blueprint_drift(run_id):
            line += " — dérive de blueprint constatée depuis le lancement"
    except Exception as exc:
        line += f" — dérive non évaluable ({type(exc).__name__}: {exc})"
    return line


def active_flow_lines(project_root: Path) -> list[str]:
    """Une ligne par run de flow encore en vol.

    La capsule de compaction ne sait rien des flows : après une compaction, le
    ``run_id`` et le nœud courant ne sont plus dans la fenêtre, et seul le
    kernel les garde. Lecture seule : sans répertoire de flows, aucun run ne
    peut exister, et rien n'est créé (le constructeur de ``FlowEngine`` crée ce
    répertoire).

    Un run est jugé seul (comme ``tools/flow_runs.py``) : un run illisible donne
    sa propre ligne « non évaluable » avec son ``run_id``, sans effacer les autres.
    Une métadonnée dont le kernel n'a plus l'instance est ignorée, comme un run
    nettoyé : sans run vivant la capsule reste identique au caractère près. Seule
    la construction du moteur ou le listage en échec remplace toute la liste.

    Au-delà de ``_MAX_DETAILED_LINES`` runs en vol, seules les lignes des runs
    les plus récents (``created_at`` de leur méta) sont rendues, puis une ligne
    ``+ N autres, grimoire flow list`` compte les runs tus (issue #1151). À ce
    plafond ou en dessous, l'ordre et le texte restent ceux d'avant.
    """
    flows_dir = project_root / _FLOWS_DIR
    if not flows_dir.is_dir():
        return []
    from grimoire.flows.engine import FlowEngine

    try:
        engine = FlowEngine(kernel_root=project_root / _KERNEL_DIR, flows_root=flows_dir, project_root=project_root)
        run_ids = engine.list_run_ids()
    except Exception as exc:
        return [f"[Grimoire — flow actif] état non évaluable — {type(exc).__name__}: {exc}"]
    active: list[tuple[str, str]] = []
    for run_id in run_ids:
        try:
            line = _run_line(engine, run_id)
        except Exception as exc:  # knows_run lui-même en échec : le run reste signalé
            line = f"[Grimoire — flow actif] run {run_id} : état non évaluable — {type(exc).__name__}: {exc}"
        if line is not None:
            active.append((_run_created_at(engine, run_id), line))
    if len(active) <= _MAX_DETAILED_LINES:
        return [line for _, line in active]
    active.sort(key=lambda item: item[0], reverse=True)
    hidden = len(active) - _MAX_DETAILED_LINES
    return [*(line for _, line in active[:_MAX_DETAILED_LINES]), f"+ {hidden} autres, grimoire flow list"]
