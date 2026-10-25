"""Le jugement READY scellé au journal d'autorité : ce que la fusion et l'acceptation sous mandat comparent (#876, lot 4).

Le reçu de revue (``review-receipt.json``) vit dans le worktree de la tâche, que le worker écrit : ses champs
(``written_at``, ``tree_fingerprint``, ``evidence_pack_sha256``) se recalculent avec les fonctions publiques du kit. Un
worker qui modifie le code, puis réécrit le reçu avec les nouvelles empreintes, passait pour relu. Ici, au moment où le
relecteur du tick juge un READY, le kit **scelle** au journal d'autorité (``~/.grimoire/control/``, chaîné par hash,
refusé en écriture aux outils d'agent) ce qui a été jugé :

- ``head`` : le SHA de la tête du worktree ;
- ``tree_fingerprint`` et ``evidence_pack_sha256`` : l'arbre et le dernier pack *tels qu'ils sont* au jugement (et le
  jugement exige qu'ils soient ceux que le reçu déclare) ;
- ``receipt_sha256`` : l'empreinte du fichier reçu lui-même.

Le merger y ajoute chaque réalignement qu'il fait lui-même (``from`` → ``to``, et l'empreinte obtenue). :func:`verify`
compare l'état présent à ces valeurs, jamais au fichier seul : le reçu n'a pas changé d'un octet, le pack non plus, et la
tête est la tête scellée avec son arbre, ou un descendant noté par un réalignement du merger avec l'arbre noté.

**Limites** : le journal vit sous le même utilisateur que les sessions ; la garde de zone (``authority_guard``) refuse
les écritures d'agent par les outils et les voies shell reconnues, pas un script arbitraire (voir :mod:`.mandate`).
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.missions.review_receipt import evidence_pack_digest, receipt_fingerprint, receipt_path

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.review_receipt import ReviewReceipt
    from grimoire.missions.schemas import MissionTask

__all__ = ["REALIGN_ENTRY", "SEAL_ENTRY", "head_of", "receipt_digest", "seal_judgment", "seal_realignment", "verify"]

SEAL_ENTRY = "review_judgment"
REALIGN_ENTRY = "merge_realignment"


def receipt_digest(root: Path, task_id: str) -> str:
    """sha256 des octets du fichier reçu ; ``""`` s'il n'existe pas ou ne se lit pas."""
    try:
        return hashlib.sha256(receipt_path(root, task_id).read_bytes()).hexdigest()
    except OSError:
        return ""


def head_of(root: Path) -> str:
    """Le SHA de ``HEAD`` dans *root* ; ``""`` hors d'un dépôt git ou si git ne répond pas."""
    from grimoire.missions.task_worktree import _env

    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, check=False, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _store(project_root: Path) -> Any:
    from grimoire.missions.mandate import MandateStore

    return MandateStore(project_root)


def seal_judgment(project_root: Path, task_id: str, review_id: str, proofs_root: Path, receipt: ReviewReceipt) -> dict[str, Any]:
    """Scelle le READY jugé ; lève :class:`ValueError` si l'arbre ou le pack ne sont pas ceux que le reçu déclare."""
    tree, pack = receipt_fingerprint(proofs_root), evidence_pack_digest(proofs_root, task_id)
    if tree != receipt.tree_fingerprint:
        raise ValueError("l'arbre présent n'est pas celui que le reçu déclare")
    if pack != receipt.evidence_pack_sha256:
        raise ValueError("le pack présent n'est pas celui que le reçu déclare")
    store = _store(project_root)
    entry: dict[str, Any] = store.journal.append({
        "type": SEAL_ENTRY, "project": store.project, "task_id": task_id, "review": review_id,
        "head": head_of(proofs_root), "tree_fingerprint": tree, "evidence_pack_sha256": pack or "",
        "receipt_sha256": receipt_digest(proofs_root, task_id), "reviewer_session": receipt.reviewer_session,
        "ts": datetime.now(UTC).isoformat(),
    })
    return entry


def seal_realignment(project_root: Path, task_id: str, review_id: str, source: str, target: str, tree: str) -> None:
    """Note au journal d'autorité un réalignement fait par le merger : ``source`` → ``target``, arbre obtenu."""
    store = _store(project_root)
    store.journal.append({
        "type": REALIGN_ENTRY, "project": store.project, "task_id": task_id, "review": review_id, "from": source,
        "to": target, "tree_fingerprint": tree, "ts": datetime.now(UTC).isoformat(),
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


def verify(project_root: Path, task: MissionTask, proofs_root: Path, *, review_id: str = "") -> str:
    """``""`` si l'état présent est celui du dernier READY scellé (ou d'un réalignement noté) ; sinon la raison.

    Fermé : un journal illisible, un sceau absent ou d'une autre relecture que *review_id* refusent.
    """
    try:
        seal, realigns = _sealed(project_root, task.id)
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
    head, tree = head_of(proofs_root), receipt_fingerprint(proofs_root)
    reachable = {(str(seal.get("head")), str(seal.get("tree_fingerprint")))}
    heads = {str(seal.get("head"))}
    for step in realigns:  # seuls les réalignements du merger, chaînés depuis la tête scellée
        if str(step.get("from")) in heads:
            heads.add(str(step.get("to")))
            reachable.add((str(step.get("to")), str(step.get("tree_fingerprint"))))
    if (head, tree) in reachable:
        return ""
    return "la tête ou l'arbre ont changé depuis le jugement READY scellé (autrement que par un réalignement du merger)"
