"""Chemin attendu et remède en une commande pour chaque artefact de gate (issue #582 lot G1).

Le banc à trois bras du 2026-09-17 (``docs/bench/diagnostic-surcout-kit-2026-09-17.md``,
puis ``analyse-tours-kit-gov``) a mesuré que le bras gouverné passait une
médiane de 8 tours (jusqu'à 20) à lire le code source installé du kit
(``site-packages/grimoire/{cli,core,missions}/*.py``) après un
``FAIL missing context_bundle`` qui ne disait ni où créer le fichier ni quoi
faire. Sur 21 runs analysés, 21 ont ouvert le source. Ce module est la seule
table qui relie une clé de gate (``context_bundle``, ``task_envelope``…) au
chemin conventionnel du standard et à la commande qui le produit — partagée
par :func:`grimoire.core.agentic_standard.check_evidence_gates` (message du
check), par la CLI ``gate check``/``verify`` (rendu texte) et par le résumé du
hook Stop (:mod:`grimoire.hosts.decisions.gate_summary`), pour qu'aucun de ces
trois rendus ne puisse redevenir muet indépendamment des autres.

Volontairement sans import lourd : ``agentic_standard`` l'importe au niveau
module, et ce module ne doit jamais remonter vers lui.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_generation import (
    CONTEXT_DIR,
    DECISION_DIR,
    EVIDENCE_DIR,
    SCORE_DIR,
    STANDARD_DIR,
)

__all__ = [
    "GATE_ARTIFACT_KEYS",
    "PROFILE_LEVEL_KEYS",
    "TASK_LEVEL_KEYS",
    "gate_artifact_relpath",
    "missing_artifact_message",
    "remedy_command",
    "remedy_for_relpath",
]

#: Artefacts que le profil (pas la tâche) possède : ``standard init`` les écrit.
PROFILE_LEVEL_KEYS: frozenset[str] = frozenset({"task_board", "memory_policy"})
#: Artefacts par tâche : ``standard task scaffold`` les crée s'ils manquent.
TASK_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "task_envelope",
        "evidence_pack",
        "claim_ledger",
        "acceptance_record",
        "context_bundle",
        "decision_trace",
    }
)
#: Toutes les clés que ``check_evidence_gates`` peut déclarer manquantes.
GATE_ARTIFACT_KEYS: tuple[str, ...] = (
    "task_board",
    "memory_policy",
    "task_envelope",
    "evidence_pack",
    "claim_ledger",
    "acceptance_record",
    "context_bundle",
    "decision_trace",
    "compliance_score",
)


def gate_artifact_relpath(key: str, task_id: str) -> Path:
    """Chemin conventionnel (relatif à la racine du projet) de l'artefact *key* pour *task_id*.

    Mêmes conventions que ``generation_targets.task_runtime`` de
    ``framework/agentic-standard/profile-map.yaml`` et que les ``*_ref`` que
    :func:`grimoire.missions.board._task_entry` projette sur le board — un
    test (``test_scaffold_paths_match_the_profile_map``) garde les deux
    sources alignées.
    """
    if key == "task_board":
        return STANDARD_DIR / "task-board.yaml"
    if key == "memory_policy":
        return STANDARD_DIR / "memory-policy.yaml"
    if key == "task_envelope":
        return EVIDENCE_DIR / task_id / "task-envelope.md"
    if key == "evidence_pack":
        return EVIDENCE_DIR / task_id / "evidence-pack.md"
    if key == "claim_ledger":
        return EVIDENCE_DIR / task_id / "claim-ledger.md"
    if key == "acceptance_record":
        return EVIDENCE_DIR / task_id / "acceptance-record.md"
    if key == "context_bundle":
        return CONTEXT_DIR / task_id / "context-bundle.yaml"
    if key == "decision_trace":
        return DECISION_DIR / task_id / "decision-trace.yaml"
    if key == "compliance_score":
        return SCORE_DIR / task_id / "compliance-score.yaml"
    msg = f"Unknown gate artifact key: {key!r}. Known: {', '.join(GATE_ARTIFACT_KEYS)}"
    raise ValueError(msg)


def remedy_command(key: str, *, root: Path, task_id: str, profile_id: str) -> str:
    """La commande shell, copiable telle quelle, qui produit l'artefact *key*.

    *root* est cité en absolu : la commande reste juste quel que soit le
    répertoire courant de l'agent qui la copie — un chemin relatif au moment
    du message n'est plus le bon deux ``cd`` plus tard.
    """
    quoted_root = _shell_quote(str(root))
    if key in PROFILE_LEVEL_KEYS:
        if key == "task_board" and (root / "_grimoire-runtime-output" / "ledger" / "events.jsonl").is_file():
            return f"grimoire task board export {quoted_root}"
        return f"grimoire standard init {quoted_root} --profile {profile_id}"
    if key == "compliance_score":
        return f"grimoire standard score {quoted_root} --task-id {task_id}"
    if key in TASK_LEVEL_KEYS:
        return f"grimoire standard task scaffold {quoted_root} --task-id {task_id}"
    msg = f"Unknown gate artifact key: {key!r}. Known: {', '.join(GATE_ARTIFACT_KEYS)}"
    raise ValueError(msg)


def remedy_for_relpath(relpath: Path, *, root: Path, task_id: str, profile_id: str) -> str:
    """Le remède d'un chemin que ``verify`` déclare manquant (il liste des chemins, pas des clés).

    Un artefact par tâche (``_grimoire-output/evidence|context|decisions/<tâche>/``)
    se scaffolde ; le score de conformité se calcule ; tout le reste appartient
    au profil et vient de ``standard init``.
    """
    parts = relpath.parts
    if len(parts) >= 2 and parts[0] == "_grimoire-output":
        if parts[1] == SCORE_DIR.name and relpath.name == "compliance-score.yaml":
            return remedy_command("compliance_score", root=root, task_id=task_id, profile_id=profile_id)
        if parts[1] in {EVIDENCE_DIR.name, CONTEXT_DIR.name, DECISION_DIR.name}:
            return remedy_command("task_envelope", root=root, task_id=task_id, profile_id=profile_id)
    return remedy_command("memory_policy", root=root, task_id=task_id, profile_id=profile_id)


def missing_artifact_message(key: str, *, root: Path, task_id: str, profile_id: str) -> str:
    """Le message complet d'un artefact de gate manquant : clé, chemin attendu, remède."""
    relpath = gate_artifact_relpath(key, task_id)
    remedy = remedy_command(key, root=root, task_id=task_id, profile_id=profile_id)
    return f"Artefact de gate manquant : {key} — attendu à {relpath.as_posix()} ; remède : {remedy}"


def _shell_quote(value: str) -> str:
    """Guillemets simples POSIX seulement si nécessaire — un chemin sans espace reste lisible."""
    if value and all(ch.isalnum() or ch in "/._-+:@%" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
