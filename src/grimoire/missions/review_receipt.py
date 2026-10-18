"""Reçu de revue lié à l'empreinte de l'arbre, et vote structuré du panel (W3-01).

Une tâche V1 au vert passe en ``needs_verification`` (``task dispatch``) : la
classe exige un regard. Rien ne disait ce regard : en profil ``starter``, le
gate ``review_to_accepted`` est consultatif, et ``grimoire task done`` fermait
la tâche sans que personne l'ait relue. Ce module rend la relecture opposable.

- **Le reçu** (``_grimoire-output/reviews/<tâche>/review-receipt.json``) porte
  ``verdict`` (``READY`` ou ``NOT-READY``), ``findings[]`` (chacun avec son
  ``evidence_cmd``), ``reviewer_id``, ``model``, ``tree_fingerprint`` (calculée
  par :func:`~grimoire.core.standard_checks.tree_fingerprint.compute_tree_fingerprint`)
  et le hash du dernier pack de preuve.
- **Le gate de clôture** (``needs_verification → closed``, via
  :meth:`grimoire.missions.service.TaskService.gate`) recalcule l'empreinte *à
  chaque appel*, sans cache : un écart rend ``review.receipt_stale``.
- **Un finding sans commande exécutable reste une suggestion** : il ne fonde
  pas un ``NOT-READY`` (``review.finding_ungrounded``).

Pas de gel par hook. **Limite écrite** : entre le reçu et le gate, une
modification passe sans être vue — le gate ne la voit qu'à la clôture, et
c'est pourquoi il recalcule toujours. L'empreinte ignore l'état que le kit
réécrit lui-même (:data:`RECEIPT_FINGERPRINT_EXCLUDES`) ; le contenu de
``evidence_cmd`` n'est pas exécuté par le gate (W3-03 porte l'approbation des
commandes). Le reçu rend la falsification plus coûteuse, pas impossible : un
agent qui écrit lui-même un ``READY`` frais passe — ``reviewer_id`` et
``model`` disent qui l'a signé, rien ne l'atteste.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import atomic_write
from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint
from grimoire.core.standard_generation import STANDARD_DIR, normalize_task_id
from grimoire.core.standard_profile_manifest import read_profile
from grimoire.missions.gates import GateRefusal

__all__ = [
    "INCONCLUSIVE",
    "PANEL_CLASSES",
    "RECEIPT_FINGERPRINT_EXCLUDES",
    "VERDICTS",
    "PanelTally",
    "ReviewFinding",
    "ReviewReceipt",
    "ReviewReceiptError",
    "check_receipt",
    "evidence_pack_digest",
    "read_receipt",
    "receipt_fingerprint",
    "receipt_path",
    "receipt_required",
    "tally_panel",
    "write_receipt",
]

RECEIPT_DIR = Path("_grimoire-output/reviews")
RECEIPT_NAME = "review-receipt.json"
RECEIPT_SCHEMA = "grimoire-review-receipt/v1"

READY = "READY"
NOT_READY = "NOT-READY"
VERDICTS: tuple[str, ...] = (READY, NOT_READY)

MISSING = "review.receipt_missing"
STALE = "review.receipt_stale"
UNGROUNDED = "review.finding_ungrounded"
INVALID = "review.receipt_invalid"
REFUSED = "review.not_ready"

#: Ce que le kit réécrit lui-même entre le reçu et le gate : le journal des
#: tâches (``_grimoire-runtime-output``, refus et sessions compris) et le board
#: qu'il en projette. ``_grimoire-output`` (le reçu lui-même) est déjà exclu.
RECEIPT_FINGERPRINT_EXCLUDES: tuple[str, ...] = (
    "_grimoire-runtime-output",
    (STANDARD_DIR / "task-board.yaml").as_posix(),
)

#: Profils où même une tâche V0 doit être relue avant de fermer.
_GOVERNED_PROFILES = frozenset({"governed", "production"})
_EVIDENCE_LEDGER = Path("_grimoire-runtime-output/evidence")


class ReviewReceiptError(GrimoireMissionError):
    """Un reçu refusé à l'écriture ; *code* est celui que le gate rendrait."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code} — {message}")


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """Un constat du relecteur ; sans ``evidence_cmd`` exécutable, une suggestion."""

    summary: str
    evidence_cmd: str = ""
    severity: str = ""

    @property
    def grounded(self) -> bool:
        """Le constat porte-t-il une commande qu'on peut lancer pour le vérifier ?"""
        try:
            return bool(shlex.split(self.evidence_cmd))
        except ValueError:  # silent-ok — guillemets non fermés : pas une commande, une suggestion
            return False

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "evidence_cmd": self.evidence_cmd, "severity": self.severity,
                "grounded": self.grounded}

    @classmethod
    def from_dict(cls, data: Any) -> ReviewFinding:
        if not isinstance(data, Mapping) or not isinstance(data.get("summary"), str):
            raise ValueError("finding sans `summary` texte")
        return cls(str(data["summary"]), str(data.get("evidence_cmd") or ""), str(data.get("severity") or ""))


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    """Ce que le relecteur a signé, et l'arbre sur lequel il l'a signé."""

    task_id: str
    verdict: str
    reviewer_id: str
    model: str
    tree_fingerprint: str
    evidence_pack_sha256: str | None
    written_at: str
    findings: tuple[ReviewFinding, ...] = field(default=())

    @property
    def grounded_findings(self) -> tuple[ReviewFinding, ...]:
        return tuple(f for f in self.findings if f.grounded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "$schema": RECEIPT_SCHEMA,
            "task_id": self.task_id,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "reviewer_id": self.reviewer_id,
            "model": self.model,
            "tree_fingerprint": self.tree_fingerprint,
            "evidence_pack_sha256": self.evidence_pack_sha256,
            "written_at": self.written_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ReviewReceipt:
        if not isinstance(data, Mapping):
            raise ValueError("le reçu n'est pas un objet JSON")
        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"verdict {verdict!r} — attendu {' ou '.join(VERDICTS)}")
        fingerprint = data.get("tree_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("`tree_fingerprint` absent")
        findings = data.get("findings") or []
        if not isinstance(findings, list):
            raise ValueError("`findings` n'est pas une liste")
        pack = data.get("evidence_pack_sha256")
        return cls(
            task_id=str(data.get("task_id") or ""),
            verdict=str(verdict),
            reviewer_id=str(data.get("reviewer_id") or ""),
            model=str(data.get("model") or ""),
            tree_fingerprint=fingerprint,
            evidence_pack_sha256=pack if isinstance(pack, str) else None,
            written_at=str(data.get("written_at") or ""),
            findings=tuple(ReviewFinding.from_dict(f) for f in findings),
        )


def receipt_path(root: Path, task_id: str) -> Path:
    return root / RECEIPT_DIR / normalize_task_id(task_id) / RECEIPT_NAME


def receipt_fingerprint(root: Path) -> str:
    """L'empreinte que le reçu porte et que le gate recalcule — la même fonction, à chaque appel."""
    return compute_tree_fingerprint(root, extra_excludes=RECEIPT_FINGERPRINT_EXCLUDES)


def evidence_pack_digest(root: Path, task_id: str) -> str | None:
    """sha256 du dernier pack de preuve de *task_id* (JSON canonique) ; ``None`` sans pack."""
    from grimoire.evidence import EvidenceService

    packs = EvidenceService(root / _EVIDENCE_LEDGER).list_packs(task_id)
    if not packs:
        return None
    canonical = json.dumps(packs[-1].to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ungrounded_message(receipt_findings: Sequence[ReviewFinding]) -> str:
    suggestions = len(receipt_findings)
    return (
        f"NOT-READY sans constat vérifiable : {suggestions} finding(s), aucun avec `evidence_cmd` — "
        "un constat sans commande à lancer reste une suggestion"
    )


def write_receipt(
    root: Path,
    task_id: str,
    verdict: str,
    *,
    findings: Sequence[ReviewFinding] = (),
    reviewer_id: str,
    model: str = "",
) -> ReviewReceipt:
    """Écrit le reçu, empreinte et hash du pack calculés maintenant ; rend ce qui a été écrit.

    Refuse (:class:`ReviewReceiptError`) un verdict inconnu, un relecteur
    anonyme, et un ``NOT-READY`` dont aucun constat n'a de commande.
    """
    root = root.resolve()
    if verdict not in VERDICTS:
        raise ReviewReceiptError(INVALID, f"verdict {verdict!r} — attendu {' ou '.join(VERDICTS)}")
    if not reviewer_id.strip():
        raise ReviewReceiptError(INVALID, "`reviewer_id` requis : un reçu dit qui a relu")
    findings = tuple(findings)
    if verdict == NOT_READY and not any(f.grounded for f in findings):
        raise ReviewReceiptError(UNGROUNDED, _ungrounded_message(findings))
    path = receipt_path(root, task_id)
    receipt = ReviewReceipt(
        task_id=task_id,
        verdict=verdict,
        reviewer_id=reviewer_id.strip(),
        model=model.strip(),
        tree_fingerprint=receipt_fingerprint(root),
        evidence_pack_sha256=evidence_pack_digest(root, task_id),
        written_at=datetime.now(UTC).isoformat(),
        findings=findings,
    )
    atomic_write(path, json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return receipt


def read_receipt(root: Path, task_id: str) -> ReviewReceipt | None:
    """Le reçu de *task_id* ; ``None`` s'il n'existe pas, :class:`ValueError` s'il ne se lit pas."""
    path = receipt_path(root, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    return ReviewReceipt.from_dict(data)


def receipt_required(root: Path, task: Any) -> bool:
    """V1 et V2 exigent un reçu ; une V0 seulement en profil gouverné (``governed``, ``production``)."""
    from grimoire.missions.verifiability import Verifiability, classify

    if classify(task) is not Verifiability.V0:
        return True
    try:
        profile = read_profile(root / STANDARD_DIR / "standard-profile.yaml")
    except Exception:  # silent-ok — manifeste illisible : fermé, on exige le reçu
        return True
    return profile in _GOVERNED_PROFILES


def check_receipt(root: Path, task: Any) -> GateRefusal | None:
    """Le refus du gate de clôture pour *task*, ou ``None`` : rien n'est exigé, ou le reçu tient.

    L'empreinte est recalculée ici, à chaque appel — jamais lue d'un cache.
    """
    root = root.resolve()
    task_id = str(getattr(task, "id", "") or "")
    if not receipt_required(root, task):
        return None
    try:
        path = receipt_path(root, task_id)
        receipt = read_receipt(root, task_id)
    except ValueError as exc:
        return GateRefusal(INVALID, f"reçu de revue illisible — {exc}",
                           "réécrire le reçu : grimoire task review <tâche> --verdict READY|NOT-READY")
    rel = path.relative_to(root).as_posix()
    if receipt is None:
        return GateRefusal(MISSING, "aucun reçu de revue : la tâche exige une relecture avant de fermer",
                           f"faire relire, puis : grimoire task review {task_id} --verdict READY (attendu : {rel})")
    if receipt.task_id and receipt.task_id != task_id:
        return GateRefusal(INVALID, f"le reçu porte la tâche {receipt.task_id!r}, pas {task_id!r}",
                           f"réécrire {rel}")
    current = receipt_fingerprint(root)
    if current != receipt.tree_fingerprint:
        return GateRefusal(STALE, "l'arbre a changé depuis le reçu : la revue porte sur un autre code",
                           f"refaire relire l'état présent, puis : grimoire task review {task_id}")
    if receipt.evidence_pack_sha256 != evidence_pack_digest(root, task_id):
        return GateRefusal(STALE, "le pack de preuve a changé depuis le reçu",
                           f"refaire relire avec le pack présent, puis : grimoire task review {task_id}")
    if receipt.verdict == NOT_READY:
        grounded = receipt.grounded_findings
        if not grounded:
            return GateRefusal(UNGROUNDED, _ungrounded_message(receipt.findings),
                               "donner la commande qui montre chaque constat, ou rendre READY")
        first = grounded[0]
        return GateRefusal(REFUSED, f"relecture NOT-READY ({len(grounded)} constat(s)) : {first.summary}",
                           f"corriger, vérifier par `{first.evidence_cmd}`, puis faire relire")
    return None


# ── Panel à verdict structuré ─────────────────────────────────────────────

#: Classe d'un ``verify-panel`` : V1 = majorité des votes exprimés, V2 = unanimité.
PANEL_CLASSES: tuple[str, ...] = ("V1", "V2")
#: Trop d'abstentions pour décider : ni vert ni rouge.
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class PanelTally:
    """Le dépouillement d'un panel : ``green``, ``red``, ``inconclusive`` ou ``invalid``."""

    verdict: str
    approve: int
    reject: int
    abstain: int
    fingerprints: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "approve": self.approve, "reject": self.reject,
                "abstain": self.abstain, "fingerprints": list(self.fingerprints), "reason": self.reason}


def tally_panel(votes: Sequence[Mapping[str, Any] | None], panel_class: str) -> PanelTally:
    """Dépouille les sorties des k vérificateurs (``None`` : pas de sortie).

    Un vote sans ``verdict`` structuré (``READY``/``NOT-READY``) est une
    abstention — jamais un oui par défaut. Moins d'une majorité de k votes
    exprimés : ``inconclusive``. Des ``tree_fingerprint`` divergentes parmi les
    votants : ``invalid`` (ils n'ont pas relu le même arbre). Sinon V1 exige la
    majorité stricte des votes exprimés, V2 leur unanimité ; toute autre classe
    est lue comme V2.
    """
    k = len(votes)
    approve = reject = 0
    prints: set[str] = set()
    for vote in votes:
        verdict = vote.get("verdict") if isinstance(vote, Mapping) else None
        if verdict not in VERDICTS:
            continue
        assert isinstance(vote, Mapping)
        if verdict == READY:
            approve += 1
        else:
            reject += 1
        fingerprint = vote.get("tree_fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            prints.add(fingerprint)
    expressed = approve + reject
    abstain = k - expressed
    fingerprints = tuple(sorted(prints))
    if len(fingerprints) > 1:
        return PanelTally("invalid", approve, reject, abstain, fingerprints,
                          f"{len(fingerprints)} empreintes d'arbre différentes parmi les votants")
    if expressed * 2 <= k:
        return PanelTally(INCONCLUSIVE, approve, reject, abstain, fingerprints,
                          f"{abstain} abstention(s) sur {k} : pas de majorité de votes exprimés")
    if panel_class == "V1":
        green = approve * 2 > expressed
        rule = "majorité des votes exprimés (V1)"
    else:
        green = reject == 0
        rule = "unanimité des votes exprimés (V2)"
    return PanelTally("green" if green else "red", approve, reject, abstain, fingerprints,
                      f"{approve} pour, {reject} contre, {abstain} abstention(s) — {rule}")
