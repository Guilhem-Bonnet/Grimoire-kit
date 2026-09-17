"""``grimoire standard gate run-tests`` : exécute la commande de test connue du projet
et enregistre le verdict (issue #582 lot B).

Extrait de :mod:`grimoire.core.agentic_standard` dans son propre module —
pas ajouté à ce fichier — pour respecter le ratchet de taille
(``scripts/check-code-ratchet.py``, R2 : ``agentic_standard.py`` est
grandfathered à 1916 lignes et ne peut plus grossir). :func:`grimoire.core.
standard_checks.controls._verify_acceptance_record` lit le fichier que cette
fonction écrit (:func:`grimoire.core.standard_checks.controls.
acceptance_test_run_relpath`) sans dépendre de ce module au niveau import —
seul le chemin est partagé.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.execution_needs import resolve_need
from grimoire.core.standard_checks.controls import acceptance_test_run_relpath
from grimoire.core.standard_generation import normalize_task_id

__all__ = ["AcceptanceTestRunResult", "record_acceptance_test_run"]


@dataclass(frozen=True, slots=True)
class AcceptanceTestRunResult:
    """Le verdict d'un run de test réel enregistré pour une tâche (issue #582 lot B).

    ``command`` vaut ``""`` quand aucune commande de test n'est connue pour le
    projet (``ok`` est alors ``False``, sans avoir rien exécuté) — l'appelant
    (CLI ``grimoire standard gate run-tests``) décide comment le signaler ;
    ``_verify_acceptance_record`` sait déjà distinguer ce cas de « exécuté et
    rouge » via ``acceptance.no_test_command_detected``.
    """

    task_id: str
    command: str
    ok: bool
    exit_code: int | None
    output_excerpt: str
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "output_excerpt": self.output_excerpt,
            "path": str(self.path),
        }


def record_acceptance_test_run(project_root: Path, *, task_id: str = "bootstrap") -> AcceptanceTestRunResult:
    """Exécute la commande de test connue du projet et enregistre le verdict (issue #582 lot B).

    Point d'entrée de ``grimoire standard gate run-tests``. Ne duplique pas
    d'exécuteur : ``_run_checks`` (:mod:`grimoire.missions.dispatch`) est le
    même primitif que ``flows.dispatch_executor`` utilise déjà pour une
    ``AcceptanceEvidence(kind="test")`` (issue #428) — seul le point d'appel
    change ici, le chemin déclaratif (``standard verify``/``gate check``) qui
    n'exécutait jusqu'à ce lot jamais rien lui-même (voir
    ``docs/bench/diagnostic-surcout-kit-2026-09-17.md`` §2).

    La commande à exécuter vient de :func:`grimoire.core.execution_needs.resolve_need`
    (``needs.commands.test-runner`` déclaré dans ``project-context.yaml``, ou
    détection par marqueur : ``pyproject.toml``, ``package.json``,
    ``Cargo.toml``, ``go.mod``) — jamais inventée ici. Sans commande connue,
    rend un résultat ``ok=False``/``command=""`` sans rien exécuter ni écrire
    de fichier : l'appelant (CLI, ou un futur exécuteur) décide comment le
    signaler, et ``_verify_acceptance_record`` distingue déjà ce cas
    (``acceptance.no_test_command_detected``) d'un run réellement rouge.
    """
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    result_path = root / acceptance_test_run_relpath(normalized_task_id)
    need = resolve_need("test-runner", root)
    if not need.resolved or need.command is None:
        return AcceptanceTestRunResult(
            task_id=normalized_task_id,
            command="",
            ok=False,
            exit_code=None,
            output_excerpt="Aucune commande de test connue pour ce projet.",
            path=result_path,
        )
    # Importés ici, pas au sommet du module : `grimoire.missions.dispatch`
    # entraîne `providers.audit` (`urllib.request`/`ssl`, ~60 ms mesurés au
    # profilage), un coût que chaque `standard verify`/`gate check` paierait
    # au chargement si l'import restait au niveau module (même convention
    # que `cli.cmd_task` pour ce même symbole) ; `agentic_standard` est
    # importé ici pour éviter tout risque de cycle au chargement du module.
    from grimoire.core.agentic_standard import _append_runtime_event, _selected_profile
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint
    from grimoire.missions.dispatch import _run_checks

    # Calculée APRÈS avoir exécuté la commande (revue de la PR #585, point 1) :
    # l'état de référence d'un run est celui qu'il laisse derrière lui, pas
    # celui d'avant. Les caches d'outillage qu'une commande de test régénère
    # (`.pytest_cache`, `__pycache__`, `.coverage`…) sont de toute façon
    # exclus de l'empreinte (`tree_fingerprint._EXCLUDED_DIRS`) — sans quoi,
    # sur un projet sans `.gitignore` adapté, le run se serait périmé dès son
    # propre enregistrement.
    (check,) = _run_checks((need.command,), project_root=root)
    tree_fingerprint = compute_tree_fingerprint(root)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": normalized_task_id,
        "command": need.command,
        "ok": check.ok,
        "exit_code": check.exit_code,
        "output_excerpt": check.output_excerpt,
        "recorded_at": datetime.now(UTC).isoformat(),
        "tree_fingerprint": tree_fingerprint,
    }
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    profile = _selected_profile(root, None)
    _append_runtime_event(
        root,
        event_type="acceptance.test_run",
        task_id=normalized_task_id,
        profile=profile.id,
        details={"ok": check.ok, "command": need.command, "exit_code": check.exit_code},
    )
    return AcceptanceTestRunResult(
        task_id=normalized_task_id,
        command=need.command,
        ok=check.ok,
        exit_code=check.exit_code,
        output_excerpt=check.output_excerpt,
        path=result_path,
    )
