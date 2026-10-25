"""Le jugement READY scellé au journal d'autorité : ce que la fusion et l'acceptation sous mandat comparent (#876, lot 4).

Le reçu de revue (``review-receipt.json``) vit dans le worktree de la tâche, que le worker écrit : ses champs se
recalculent avec les fonctions publiques du kit, et l'empreinte d'arbre qu'il porte ignore des chemins entiers
(``node_modules``, ``__pycache__``, ``.venv``…). Ce module scelle au journal d'autorité (``~/.grimoire/control/``,
chaîné par hash, refusé en écriture aux outils d'agent) ce qui a été relu, en objets git :

- **au lancement du relecteur** (:func:`seal_review_launch`) : la tête de ``task/<id>`` qu'il va relire ;
- **au jugement READY** (:func:`seal_judgment`) : la même tête — une branche qui a bougé pendant la relecture n'est pas
  scellée —, son arbre git (``<tête>^{tree}``), un worktree propre et sur cette tête, l'empreinte des octets du reçu et
  du dernier pack (qui doivent être ceux que le reçu déclare) ;
- **à chaque réalignement du merger** (:func:`seal_realignment`) : ``from`` → ``to`` et l'arbre git produit.

:func:`verify` compare une tête (celle de la PR pour le merger, celle de la branche sinon) à cette chaîne : la tête
scellée avec son arbre, ou un descendant noté par un réalignement du merger. Un dépôt sans git retombe sur l'empreinte
de contenu du reçu. Chaque git passe par :mod:`grimoire.core.safe_git`.

**Limites** : le journal vit sous le même utilisateur que les sessions ; la garde de zone refuse les écritures d'agent
par les outils et les voies shell reconnues, pas un script arbitraire, qui pourrait forger un sceau.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.missions.review_receipt import (
    RECEIPT_FINGERPRINT_EXCLUDES,
    evidence_pack_digest,
    receipt_fingerprint,
    receipt_path,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "LAUNCH_ENTRY",
    "REALIGN_ENTRY",
    "SEAL_ENTRY",
    "branch_head",
    "receipt_digest",
    "seal_judgment",
    "seal_realignment",
    "seal_review_launch",
    "tree_of",
    "verify",
]

LAUNCH_ENTRY = "review_launch"
SEAL_ENTRY = "review_judgment"
REALIGN_ENTRY = "merge_realignment"
_BRANCH_PREFIX = "task/"


def receipt_digest(root: Path, task_id: str) -> str:
    """sha256 des octets du fichier reçu ; ``""`` s'il n'existe pas ou ne se lit pas."""
    try:
        return hashlib.sha256(receipt_path(root, task_id).read_bytes()).hexdigest()
    except OSError:
        return ""


def _rev(cwd: Path, ref: str) -> str:
    from grimoire.core.safe_git import run

    done = run(("rev-parse", "--verify", "--quiet", ref), cwd=cwd, timeout=30)
    return str(done.stdout).strip() if done.returncode == 0 else ""


def branch_head(project_root: Path, task_id: str) -> str:
    """Le SHA de ``task/<id>`` dans le dépôt du projet ; ``""`` sans dépôt ou sans branche."""
    return _rev(project_root, f"refs/heads/{_BRANCH_PREFIX}{task_id}^{{commit}}")


def tree_of(project_root: Path, commit: str) -> str:
    """L'arbre git de *commit* : ce qui serait fusionné, chemins ignorés par l'empreinte compris."""
    return _rev(project_root, f"{commit}^{{tree}}") if commit else ""


def _store(project_root: Path) -> Any:
    from grimoire.missions.mandate import MandateStore

    return MandateStore(project_root)


def seal_review_launch(project_root: Path, task_id: str, session_sha256: str, head: str) -> None:
    """Note au journal d'autorité la tête que le relecteur lancé va relire."""
    store = _store(project_root)
    store.journal.append({
        "type": LAUNCH_ENTRY, "project": store.project, "task_id": task_id, "session_sha256": session_sha256,
        "head": head, "ts": datetime.now(UTC).isoformat(),
    })


def _launch_head(project_root: Path, task_id: str, session_sha256: str) -> str | None:
    store = _store(project_root)
    found = [
        e for e in store.journal.read()
        if e.get("type") == LAUNCH_ENTRY and e.get("project") == store.project and e.get("task_id") == task_id
        and e.get("session_sha256") == session_sha256
    ]
    return str(found[-1].get("head", "")) if found else None


def _dirty(proofs_root: Path) -> str:
    from grimoire.core.safe_git import run

    excludes = [f":(exclude){p}" for p in ("_grimoire-output", *RECEIPT_FINGERPRINT_EXCLUDES)]
    done = run(("status", "--porcelain", "--untracked-files=all", "--", ".", *excludes), cwd=proofs_root)
    if done.returncode != 0:
        return f"état git du worktree illisible ({str(done.stderr).strip()[:160]})"
    lines = str(done.stdout).splitlines()
    return f"modifications non commitées ({', '.join(ln[3:] for ln in lines[:5])})" if lines else ""


def seal_judgment(
    project_root: Path, task_id: str, review_id: str, proofs_root: Path, receipt: ReviewReceipt, session_sha256: str = "",
) -> dict[str, Any]:
    """Scelle le READY jugé ; lève :class:`ValueError` si ce qui a été relu n'est pas ce qui est là."""
    tree, pack = receipt_fingerprint(proofs_root), evidence_pack_digest(proofs_root, task_id)
    if tree != receipt.tree_fingerprint:
        raise ValueError("l'arbre présent n'est pas celui que le reçu déclare")
    if pack != receipt.evidence_pack_sha256:
        raise ValueError("le pack présent n'est pas celui que le reçu déclare")
    head = branch_head(project_root, task_id)
    if head:
        launched = _launch_head(project_root, task_id, session_sha256)
        if launched is None:
            raise ValueError("aucune tête scellée au lancement de cette relecture")
        if launched != head:
            raise ValueError(f"la branche a bougé pendant la relecture ({launched[:12]} → {head[:12]})")
        if _rev(proofs_root, "HEAD^{commit}") != head:
            raise ValueError(f"le worktree n'est pas sur la tête de {_BRANCH_PREFIX}{task_id}")
        if dirty := _dirty(proofs_root):
            raise ValueError(f"{dirty} : la relecture ne porte pas sur la tête")
    store = _store(project_root)
    entry: dict[str, Any] = store.journal.append({
        "type": SEAL_ENTRY, "project": store.project, "task_id": task_id, "review": review_id,
        "head": head, "tree": tree_of(project_root, head), "tree_fingerprint": tree, "evidence_pack_sha256": pack or "",
        "receipt_sha256": receipt_digest(proofs_root, task_id), "reviewer_session": receipt.reviewer_session,
        "ts": datetime.now(UTC).isoformat(),
    })
    return entry


def seal_realignment(project_root: Path, task_id: str, review_id: str, source: str, target: str, tree: str) -> None:
    """Note au journal d'autorité un réalignement fait par le merger : ``source`` → ``target``, arbre git produit."""
    store = _store(project_root)
    store.journal.append({
        "type": REALIGN_ENTRY, "project": store.project, "task_id": task_id, "review": review_id, "from": source,
        "to": target, "tree": tree, "ts": datetime.now(UTC).isoformat(),
    })


def _sealed(project_root: Path, task_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    store = _store(project_root)
    mine = [e for e in store.journal.read() if e.get("project") == store.project and e.get("task_id") == task_id]
    seals = [i for i, e in enumerate(mine) if e.get("type") == SEAL_ENTRY]
    if not seals:
        return None, []
    seal = mine[seals[-1]]
    realigns = [e for e in mine[seals[-1] + 1:] if e.get("type") == REALIGN_ENTRY and e.get("review") == seal.get("review")]
    return seal, realigns


def reachable(project_root: Path, task_id: str) -> set[tuple[str, str]]:
    """Les ``(tête, arbre)`` que le dernier sceau couvre : la tête relue, puis les réalignements du merger qui en partent."""
    seal, realigns = _sealed(project_root, task_id)
    if seal is None or not seal.get("head"):
        return set()
    found = {(str(seal["head"]), str(seal.get("tree", "")))}
    heads = {str(seal["head"])}
    for step in realigns:
        if str(step.get("from")) in heads:
            heads.add(str(step.get("to")))
            found.add((str(step.get("to")), str(step.get("tree", ""))))
    return found


def verify(project_root: Path, task: MissionTask, proofs_root: Path, *, review_id: str = "", head: str | None = None) -> str:
    """``""`` si *head* (défaut : la tête de ``task/<id>``) est couverte par le dernier READY scellé ; sinon la raison.

    Fermé : un journal illisible, un sceau absent ou d'une autre relecture que *review_id* refusent ; le reçu et le pack
    doivent être, octet pour octet, ceux du sceau.
    """
    try:
        seal, _realigns = _sealed(project_root, task.id)
        covered = reachable(project_root, task.id)
    except Exception as exc:  # une garde qui ne lit pas son journal refuse
        return f"journal d'autorité illisible ({type(exc).__name__}) : jugement READY non vérifiable"
    if seal is None:
        return "aucun jugement READY scellé au journal d'autorité pour cette tâche"
    if review_id and seal.get("review") != review_id:
        return "le dernier jugement scellé n'est pas celui de cette relecture"
    if receipt_digest(proofs_root, task.id) != seal.get("receipt_sha256"):
        return "reçu réécrit depuis le jugement READY scellé"
    if (evidence_pack_digest(proofs_root, task.id) or "") != seal.get("evidence_pack_sha256"):
        return "le pack de preuve a changé depuis le jugement READY scellé"
    if not seal.get("head"):  # dépôt sans git : l'empreinte de contenu du reçu, faute d'objets
        if receipt_fingerprint(proofs_root) != seal.get("tree_fingerprint"):
            return "l'arbre a changé depuis le jugement READY scellé"
        return ""
    target = head if head is not None else branch_head(project_root, task.id)
    if target and (target, tree_of(project_root, target)) in covered:
        return ""
    return "la tête n'est pas celle du jugement READY scellé, ni un réalignement noté par le merger"
