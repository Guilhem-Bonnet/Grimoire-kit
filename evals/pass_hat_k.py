"""pass^k (audit B13, docs/evals-protocol.md amendement A3).

Portée depuis ``grimoire.evals.schemas`` (paquet retiré par la consolidation
flows/evals, cf. ``feat(flows): grimoire flow run|status|resume|abort`` — le
harness ``EvalCase``/``EvalHarness`` a été remplacé par le pipeline
``collect.py``/``aggregate.py`` à base de dicts). Seul ce calcul restait
consommé (par ``evals/aggregate.py``) ; le reste du module source
(``EvalCase``, ``EvalHarness``, ``EvalCategory``, ...) n'avait plus
d'appelant nulle part dans le dépôt et n'a pas été reporté.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PassHatK:
    """pass^k over a task suite's repeated runs (τ-bench, τ²-bench convention:
    consistency across repetitions, not just best-of-k like pass@k).
    """

    k: int
    tasks_evaluated: int
    tasks_passed: int
    #: Task ids with fewer than ``k`` executed repetitions — excluded from
    #: both numerator and denominator, reported so a thin sample is visible
    #: rather than silently folded into a rate.
    tasks_insufficient: tuple[str, ...]
    #: Per-task verdict: True (passed all k), False (failed at least one),
    #: None (insufficient data — see `tasks_insufficient`).
    per_task: dict[str, bool | None]

    @property
    def value(self) -> float | None:
        return round(self.tasks_passed / self.tasks_evaluated, 4) if self.tasks_evaluated else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "tasks_evaluated": self.tasks_evaluated,
            "tasks_passed": self.tasks_passed,
            "tasks_insufficient": list(self.tasks_insufficient),
            "pass_hat_k": self.value,
            "per_task": dict(self.per_task),
        }


def pass_hat_k(results_by_task: dict[str, list[bool | None]], k: int) -> PassHatK:
    """pass^k: the fraction of tasks that succeeded on every one of their
    executed repetitions, among tasks that reached ``k`` executed reps.

    ``results_by_task`` maps a task id to one entry per repetition that was
    at least attempted: ``True``/``False`` once judged, or ``None`` when the
    repetition ran but has no verdict yet (unjudged). A repetition that
    never ran at all (budget stop, infra incident) simply has no entry —
    absence, not ``None``. Either way, "un run non exécuté n'est ni succès
    ni échec" (audit B13): it contributes to neither the pass count nor the
    fail count, and a task short of ``k`` *executed and judged* reps cannot
    be scored at all — it lands in ``tasks_insufficient`` instead of being
    silently counted as a pass or a fail.

    This is τ-bench/τ²-bench's pass^k (consistency across repeated runs of
    the *same* task), not pass@k (best-of-k, a different metric the audit
    explicitly distinguishes it from).
    """
    if k < 1:
        msg = f"k doit être ≥ 1, reçu {k}"
        raise ValueError(msg)
    passed = 0
    evaluated = 0
    insufficient: list[str] = []
    per_task: dict[str, bool | None] = {}
    for task_id, outcomes in results_by_task.items():
        judged = [o for o in outcomes if o is not None]
        if len(judged) < k:
            insufficient.append(task_id)
            per_task[task_id] = None
            continue
        evaluated += 1
        ok = all(judged)
        per_task[task_id] = ok
        if ok:
            passed += 1
    return PassHatK(
        k=k,
        tasks_evaluated=evaluated,
        tasks_passed=passed,
        tasks_insufficient=tuple(sorted(insufficient)),
        per_task=per_task,
    )
