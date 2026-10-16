"""Runs de flow encore en vol, lus pour les hooks de compaction (W2-05, FLOW-3).

Module hors de ``grimoire.hosts.decisions`` : une décision ne doit importer que
son propre module (issue #419, ``tests/unit/test_hook_cost.py``). Le moteur est
donc importé à l'appel, pas au chargement.
"""

from __future__ import annotations

from pathlib import Path

#: Miroir des chemins que ``cmd_flow.py`` passe à ``FlowEngine`` (même motif que
#: ``tools/flow_runs.py`` : jamais réimporté depuis le CLI pour une simple lecture).
_FLOWS_DIR = Path("_grimoire-runtime-output") / "flows"
_KERNEL_DIR = Path("_grimoire-runtime-output") / "runtime"

#: Un run dans l'un de ces statuts n'attend plus rien : il n'est pas réinjecté.
_SETTLED_RUN_STATUSES = frozenset({"aborted", "refused", "completed", "verified"})


def active_flow_lines(project_root: Path) -> list[str]:
    """Une ligne par run de flow encore en vol.

    La capsule de compaction ne sait rien des flows : après une compaction, le
    ``run_id`` et le nœud courant ne sont plus dans la fenêtre, et seul le
    kernel les garde. Lecture seule : sans répertoire de flows, aucun run ne
    peut exister, et rien n'est créé (le constructeur de ``FlowEngine`` crée ce
    répertoire). Un état illisible donne une ligne « non évaluable », jamais une
    absence silencieuse.
    """
    flows_dir = project_root / _FLOWS_DIR
    if not flows_dir.is_dir():
        return []
    from grimoire.flows.engine import FlowEngine

    try:
        engine = FlowEngine(kernel_root=project_root / _KERNEL_DIR, flows_root=flows_dir, project_root=project_root)
        lines: list[str] = []
        for run_id in engine.list_run_ids():
            view = engine.status(run_id, include_contract=False)
            if view.status in _SETTLED_RUN_STATUSES:
                continue
            line = (
                f"[Grimoire — flow actif] run {view.run_id} (blueprint {view.blueprint_id}), "
                f"nœud courant {view.current_node or 'aucun'}, statut {view.status}"
            )
            if engine.blueprint_drift(run_id):
                line += " — dérive de blueprint constatée depuis le lancement"
            lines.append(line)
    except Exception as exc:
        return [f"[Grimoire — flow actif] état non évaluable — {type(exc).__name__}: {exc}"]
    return lines
