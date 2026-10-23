"""Le cadrage face au ledger dans ``standard verify`` (W6-07b, ADP-8 critère 2 et ADP-9).

Deux constats, déclarés au registre des checks :

- ``cadrage.question_open`` — **erreur**, quel que soit le profil : une question bloquante posée par
  ``grimoire cadrage plan`` reste sans réponse humaine alors que son ambiguïté est toujours dans le cadrage.
  Le message nomme la question et la façon d'y répondre. Une question non bloquante n'échoue jamais ; une
  bloquante devenue sans objet (le cadrage a été corrigé) non plus : on n'exige pas de réponse à ce qui
  n'est plus demandé.
- ``cadrage.requirement_uncovered`` / ``cadrage.task_orphan`` — couverture exigences ↔ tâches.
  Avertissement, sauf en profil ``governed``/``production`` quand le cahier des charges est marqué accepté
  (``status: accepted`` dans son front matter) : même règle que ``acceptance.uncovered`` (W3-06), un
  engagement accepté ne garde pas d'exigence sans tâche.

Rien n'est vérifié sans ``_grimoire/cadrage/`` ni sans exigence identifiée (``REQ-NNN``) : un cadrage en
prose reste valide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["verify_cadrage_ledger"]

_STRICT_PROFILES = frozenset({"governed", "production"})


def verify_cadrage_ledger(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    from grimoire.core.cadrage import CADRAGE_DIR
    from grimoire.core.cadrage_requirements import cdc_accepted

    if not (root / CADRAGE_DIR).is_dir():
        return
    # Import tardif : le standard ne charge pas le ledger pour un projet qui ne cadre pas.
    from grimoire.missions.cadrage_plan import answer_command, build_plan
    from grimoire.missions.service import TaskService

    plan = build_plan(root, TaskService(root))
    if not plan.requirements.identified:
        return
    for q in plan.blocking_unresolved:
        if q.status == "open":
            remedy = f"répondez dans le cockpit (Décisions) ou : {answer_command(q.task_id, q.question_id)}"
        elif q.status == "unanswered":
            remedy = f"posée sur {q.task_id} sans réponse, plus attendue — `grimoire cadrage plan --yes` la repose"
        else:
            continue  # jamais posée : `cadrage plan` le dit ; seule une question posée fait échouer le gate
        _add_check(
            result, "cadrage.question_open", "error",
            f"Question bloquante du cadrage sans réponse : « {q.ambiguity.text} » — {remedy}.",
            path=CADRAGE_DIR,
        )
    strict = profile.id in _STRICT_PROFILES and cdc_accepted(root)
    severity = "error" if strict else "warning"
    if plan.uncovered:
        _add_check(
            result, "cadrage.requirement_uncovered", severity,
            f"{len(plan.uncovered)} exigence(s) sans tâche au ledger : {', '.join(plan.uncovered)} — "
            "`grimoire cadrage plan` les propose.",
            path=CADRAGE_DIR,
        )
    for orphan in plan.orphans:
        _add_check(
            result, "cadrage.task_orphan", severity,
            f"{orphan['task_id']} couvre {', '.join(orphan['covers'])}, absente(s) du cadrage : exigence "
            "retirée ou renommée ?",
            path=CADRAGE_DIR,
        )
