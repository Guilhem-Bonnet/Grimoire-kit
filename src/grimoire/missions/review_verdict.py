"""Le verdict structuré du relecteur : lu, validé par du code, porté au pack de preuve puis au reçu (#876, lot 3).

Le relecteur rend un fichier JSON (schéma ``grimoire-review-verdict/v1``) :

.. code-block:: json

    {
      "verdict": "READY",
      "criteria": [
        {"index": 1, "status": "met", "evidence_cmd": "pytest tests/unit/test_x.py -q", "note": ""},
        {"index": 2, "status": "met", "evidence_cmd": "", "note": "diff relu : la route rend 404"}
      ],
      "findings": [{"summary": "…", "evidence_cmd": "…", "severity": "minor"}]
    }

``grimoire task review <tâche> --criteria <fichier>`` le passe à :func:`record_review`, qui :

1. valide le verdict contre les critères de la tâche (:func:`~.review_receipt.validate_criteria`), puis la session
   qui le rend : jamais celle du worker, et sous mandat une session inscrite relecteur de la tâche — rien n'est
   écrit sur un refus ;
2. **verrou 8** — ajoute un pack de preuve : les éléments du dernier pack (les commandes lancées par
   ``task verify``) plus un élément par critère jugé ; les critères ``met`` y sont couverts *par le verdict*,
   plus par une sous-chaîne trouvée dans un résumé. Le verdict de ce pack reprend les contrôles de commandes du
   dernier verdict (une relecture ne rend pas vert un check rouge) et celui de la relecture (un ``NOT-READY`` le
   rend rouge) ;
3. écrit le reçu sur ce pack (son empreinte d'arbre et le hash du pack), avec la session du relecteur ;
4. note ``task.review_recorded`` au ledger : verdict, critères tenus et non tenus, session, modèle.

**Limite** : le statut ``met`` d'un critère qui n'est pas mécanique reste un jugement — le code vérifie qu'il est
complet, cohérent avec le verdict et adossé à une preuve citée, pas que cette preuve est juste. Le reviewer est une
autre session (verrou 7), c'est ce qui rend le jugement opposable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile, VerdictResult, VerificationCheck
from grimoire.evidence.service import EvidenceService
from grimoire.missions.review_receipt import (
    CRITERION_MET,
    CRITERION_UNMET,
    INVALID,
    READY,
    UNGROUNDED,
    VERDICTS,
    CriterionVerdict,
    ReviewFinding,
    ReviewReceipt,
    ReviewReceiptError,
    validate_criteria,
    write_receipt,
)
from grimoire.missions.verifiability import Verifiability, classify

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "REVIEW_RECORDED_EVENT",
    "SELF_REVIEW",
    "VERDICT_SCHEMA",
    "StructuredVerdict",
    "load_verdict",
    "parse_verdict",
    "record_review",
    "require_reviewer",
]

VERDICT_SCHEMA = "grimoire-review-verdict/v1"
REVIEW_RECORDED_EVENT = "task.review_recorded"
_EVIDENCE_LEDGER = Path("_grimoire-runtime-output/evidence")
#: Les contrôles du dernier verdict que le pack de relecture reprend : ceux des commandes de ``task verify``.
_COMMAND_CHECK_PREFIX = "command-"
_REVIEW_ITEM_PREFIX = "review-"
SELF_REVIEW = "review.self_review"


@dataclass(frozen=True, slots=True)
class StructuredVerdict:
    verdict: str
    criteria: tuple[CriterionVerdict, ...]
    findings: tuple[ReviewFinding, ...] = ()


def parse_verdict(data: Any) -> StructuredVerdict:
    """Le verdict lu d'un objet JSON ; :class:`ReviewReceiptError` (``review.receipt_invalid``) s'il est mal formé."""
    if not isinstance(data, Mapping):
        raise ReviewReceiptError(INVALID, "le verdict n'est pas un objet JSON")
    schema = data.get("$schema")
    if schema not in (None, VERDICT_SCHEMA):
        raise ReviewReceiptError(INVALID, f"schéma {schema!r} — attendu {VERDICT_SCHEMA}")
    verdict = str(data.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        raise ReviewReceiptError(INVALID, f"verdict {verdict!r} — attendu {' ou '.join(VERDICTS)}")
    criteria, findings = data.get("criteria"), data.get("findings") or []
    if not isinstance(criteria, list) or not criteria:
        raise ReviewReceiptError(INVALID, "`criteria` absent ou vide : un statut par critère d'acceptation")
    if not isinstance(findings, list):
        raise ReviewReceiptError(INVALID, "`findings` n'est pas une liste")
    try:
        return StructuredVerdict(
            verdict,
            tuple(CriterionVerdict.from_dict(c) for c in criteria),
            tuple(ReviewFinding.from_dict(f) for f in findings),
        )
    except ValueError as exc:
        raise ReviewReceiptError(INVALID, str(exc)) from exc


def load_verdict(path: Path) -> StructuredVerdict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewReceiptError(INVALID, f"verdict illisible ({path}) : {type(exc).__name__}: {exc}") from exc
    return parse_verdict(data)


def _digest(session: str) -> str:
    return hashlib.sha256(session.encode("utf-8")).hexdigest() if session else ""


def require_reviewer(service: TaskService, task: MissionTask, reviewer_id: str, session: str) -> None:
    """Avant toute écriture : la session qui rend le verdict n'est pas celle du worker et, sous mandat, est inscrite.

    Sans cette garde, le worker écrivait lui-même un pack où ses critères étaient couverts, que le tick jugeait forgé
    ensuite sans pouvoir l'effacer. Hors mandat, seule la session du worker est refusée : un humain relit qui il veut.
    """
    from grimoire.missions.launcher import launched_sessions
    from grimoire.missions.mandate import MandateRefusedError, MandateStore

    launch = launched_sessions(service).get(task.id, {})
    workers = {str(launch.get("session_id") or ""), task.claim.session_id if task.claim else ""} - {""}
    if session and session in workers:
        raise ReviewReceiptError(SELF_REVIEW, f"la session {session!r} est celle du worker de {task.id} : un autre relit")
    store = MandateStore(service.home_root)
    if not store.state().active:
        return
    try:
        store.check_receipt_provenance(task, SimpleNamespace(reviewer_id=reviewer_id, reviewer_session=session))
    except MandateRefusedError as exc:
        raise ReviewReceiptError(SELF_REVIEW if exc.code == "receipt_self_review" else INVALID, str(exc)) from exc


def _criterion_item(c: CriterionVerdict) -> EvidenceItem:
    text = json.dumps(c.to_dict(), ensure_ascii=False, sort_keys=True)
    return EvidenceItem.from_text(
        f"{_REVIEW_ITEM_PREFIX}{c.index:02d}", EvidenceKind.REPORT, text, uri=f"review:criterion-{c.index}",
        summary=f"relecture — critère {c.index} {c.status}" + (f" : {c.evidence_cmd}" if c.evidence_cmd else ""),
    )


def record_review(
    service: TaskService,
    task: MissionTask,
    verdict: StructuredVerdict,
    *,
    reviewer_id: str,
    model: str = "",
    reviewer_session: str = "",
    human: bool = False,
) -> ReviewReceipt:
    """Valide *verdict*, ajoute le pack de relecture, écrit le reçu et le note au ledger ; rend le reçu.

    *human* : le canal d'appel est humain (CLI interactive) — la garde de session (:func:`require_reviewer`) ne
    s'applique pas, un humain relit sous mandat comme hors mandat.
    """
    acceptance = tuple(task.acceptance)
    judged = validate_criteria(acceptance, verdict.criteria, verdict.verdict)
    if verdict.verdict != READY and not any(f.grounded for f in verdict.findings) and not any(
        c.status == CRITERION_UNMET and c.grounded for c in judged
    ):
        # Le même refus que write_receipt, mais avant le pack : un refus n'écrit rien.
        raise ReviewReceiptError(UNGROUNDED, "NOT-READY sans constat ni critère montré par une commande")
    if not human:
        require_reviewer(service, task, reviewer_id, reviewer_session)
    root = service.project_root
    evidence = EvidenceService(root / _EVIDENCE_LEDGER)
    previous = evidence.list_packs(task.id)
    last = evidence.get_latest_verdict(task.id)
    # Les éléments du dernier pack, sans ceux d'une relecture précédente : un cycle ne recopie pas l'ancien jugement.
    kept = tuple(i for i in (previous[-1].items if previous else ()) if not i.id.startswith(_REVIEW_ITEM_PREFIX))
    items = [*kept, *(_criterion_item(c) for c in judged)]
    met = tuple(c.criterion for c in judged if c.status == CRITERION_MET)
    covered_acceptance = () if classify(task) is Verifiability.V0 else acceptance
    pack = evidence.create_pack(task.id, EvidenceProfile.LIGHT, items, acceptance=covered_acceptance, reviewed=met)
    commands = tuple(c for c in (last.checks if last is not None else ()) if c.id.startswith(_COMMAND_CHECK_PREFIX))
    # Le verdict du pack suit la relecture : un NOT-READY ne laisse pas un verdict vert derrière lui.
    judgment = VerificationCheck(
        id="review-verdict",
        result=VerdictResult.PASSED if verdict.verdict == READY else VerdictResult.FAILED,
        reason="" if verdict.verdict == READY else "relecture NOT-READY",
    )
    pack_verdict = evidence.verify(pack, extra_checks=(*commands, judgment))
    receipt = write_receipt(
        root, task.id, verdict.verdict, findings=verdict.findings, reviewer_id=reviewer_id, model=model,
        reviewer_session=reviewer_session, criteria=judged, acceptance=acceptance,
    )
    service.ledger.append_event(
        REVIEW_RECORDED_EVENT, task.id, "task", reviewer_id,
        {
            "task_id": task.id,
            "verdict": receipt.verdict,
            "met": [c.index for c in judged if c.status == CRITERION_MET],
            "not_met": [c.index for c in judged if c.status != CRITERION_MET],
            "reviewer_session_sha256": _digest(receipt.reviewer_session),
            "model": receipt.model,
            "evidence_pack": pack.id,
            "pack_verdict": pack_verdict.verdict.value,
        },
    )
    return receipt
