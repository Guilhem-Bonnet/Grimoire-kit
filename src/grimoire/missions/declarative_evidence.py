"""``grimoire task verify --evidence <fichier>…`` — la preuve déclarative d'une tâche sans commande (KAN-FRICTIONS).

Constat du 2026-10-19 (Forge, kit 3.63.0) : ``task move --to needs_verification`` exige un pack
de preuve (gate ``evidence_pack``) et ``task verify`` refuse toute tâche qui ne déclare aucune
commande (« rien à vérifier mécaniquement »). Une tâche V2 dont la preuve est faite de fichiers
(captures, rapports, comptes rendus de revue) n'avait aucun chemin vers la revue.

Le chemin retenu : ``task verify`` avec ``--evidence``, pour une tâche **sans** ``checks``
seulement. Chaque fichier cité doit exister dans le projet (chemin résolu sous la racine, fichier
ordinaire) ; il entre au pack avec son empreinte SHA-256 et sa taille, sous l'URI
``file:<chemin relatif>``. Le pack et son verdict le disent déclaratif :

- le verdict est ``skipped``, jamais ``passed`` : aucune commande n'a été lancée, le contenu des
  fichiers n'est pas jugé. Son ``created_by`` vaut :data:`DECLARATIVE_VERIFIER` ;
- le contrôle ``declarative-only`` le répète, et la décision ne ferme rien (``close_task`` faux) ;
- la couverture oppose tous les critères d'acceptation de la tâche (aucune dispense V0).

Le pack ouvre la revue (``in_progress → review`` ne demande qu'un pack) ; la clôture reste
refusée par ``review_gate`` (« dernier verdict : skipped »), comme pour toute tâche sans verdict
mécanique vert : c'est un humain qui tranche une preuve déclarative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from grimoire.evidence.schemas import (
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceProfile,
    VerdictDecision,
    VerdictResult,
    VerificationCheck,
    VerificationVerdict,
)
from grimoire.evidence.service import EvidenceService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grimoire.missions.schemas import MissionTask

__all__ = ["DECLARATIVE_VERIFIER", "DeclarativeEvidenceError", "DeclarativeOutcome", "declare_evidence"]

#: Le ``created_by`` d'un verdict déclaratif : jamais celui du vérificateur mécanique.
DECLARATIVE_VERIFIER = "declarative"

_SCREENSHOT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_LOG = frozenset({".log", ".txt", ".jsonl"})
_DIFF = frozenset({".diff", ".patch"})


class DeclarativeEvidenceError(ValueError):
    """Une preuve déclarative refusée : fichier absent, hors du projet, ou tâche qui déclare des commandes."""


@dataclass(frozen=True, slots=True)
class DeclarativeOutcome:
    pack: EvidencePack
    verdict: VerificationVerdict
    #: ``(chemin relatif, sha256, octets)`` par fichier, dans l'ordre cité.
    files: tuple[tuple[str, str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "declarative": True,
            "pack": self.pack.to_dict(),
            "verdict": self.verdict.to_dict(),
            "files": [{"path": p, "sha256": h, "bytes": n} for p, h, n in self.files],
        }


def _kind(rel: str) -> EvidenceKind:
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix in _SCREENSHOT:
        return EvidenceKind.SCREENSHOT
    if suffix in _LOG:
        return EvidenceKind.LOG
    if suffix in _DIFF:
        return EvidenceKind.DIFF
    return EvidenceKind.DOC if suffix in (".md", ".rst", ".html", ".pdf") else EvidenceKind.REPORT


def _project_file(root: Path, raw: str) -> tuple[str, Path]:
    """``(chemin relatif POSIX, chemin résolu)`` d'un fichier du projet ; lève sinon (fail-closed)."""
    if not raw.strip():
        raise DeclarativeEvidenceError("--evidence : chemin vide")
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise DeclarativeEvidenceError(f"--evidence {raw} : hors du projet ({root.as_posix()})") from exc
    if not resolved.is_file():
        raise DeclarativeEvidenceError(f"--evidence {raw} : aucun fichier ordinaire à ce chemin")
    return rel.as_posix(), resolved


def declare_evidence(
    task: MissionTask, project_root: Path, evidence_root: Path, paths: Sequence[str]
) -> DeclarativeOutcome:
    """Écrit le pack déclaratif de *task* (fichiers *paths*) et son verdict ``skipped`` ; rend les deux.

    Rien n'est écrit si un seul chemin est refusé, ni si la tâche déclare des commandes
    (``grimoire task verify`` sans ``--evidence`` les lance : une preuve mécanique ne se
    remplace pas par une déclaration).
    """
    if task.checks:
        raise DeclarativeEvidenceError(
            f"{task.id} déclare {len(task.checks)} commande(s) : `grimoire task verify {task.id}` les lance ; "
            "une preuve déclarative ne remplace pas une vérification mécanique"
        )
    if not paths:
        raise DeclarativeEvidenceError("--evidence : au moins un fichier de preuve")
    root = project_root.resolve()
    seen: dict[str, Path] = {}
    for raw in paths:
        rel, resolved = _project_file(root, raw)
        seen.setdefault(rel, resolved)
    files: list[tuple[str, str, int]] = []
    items: list[EvidenceItem] = []
    for index, (rel, resolved) in enumerate(seen.items(), start=1):
        data = resolved.read_bytes()
        digest = sha256(data).hexdigest()
        files.append((rel, digest, len(data)))
        summary = (
            f"preuve déclarative : {rel} présent ({len(data)} octets, sha256 {digest[:12]}) — "
            "contenu non vérifié mécaniquement"
        )
        items.append(EvidenceItem(f"file-{index:02d}", _kind(rel), f"file:{rel}", f"sha256-{digest}", summary))
    service = EvidenceService(evidence_root)
    pack = service.create_pack(task.id, EvidenceProfile.LIGHT, items, acceptance=task.acceptance)
    checks = (
        VerificationCheck("files-present", VerdictResult.PASSED, ""),
        VerificationCheck(
            "declarative-only", VerdictResult.SKIPPED,
            f"preuve déclarative : {len(files)} fichier(s) présents et hachés, aucune commande lancée — "
            "une relecture humaine tranche",
        ),
    )
    seq = len(service.list_verdicts(task.id)) + 1
    verdict = VerificationVerdict(
        id=f"ver-{task.id}-{seq:03d}", task_id=task.id, evidence_pack_id=pack.id, verdict=VerdictResult.SKIPPED,
        profile=pack.profile, checks=checks, decision=VerdictDecision(False, False, False),
        created_at=datetime.now(UTC).isoformat(), created_by=DECLARATIVE_VERIFIER,
    )
    service.record_verdict(verdict)
    return DeclarativeOutcome(pack, verdict, tuple(files))
