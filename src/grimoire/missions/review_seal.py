"""Le jugement READY scellé au journal d'autorité : ce que la fusion et l'acceptation sous mandat comparent (#876, lot 4).

Le reçu de revue (``review-receipt.json``) vit dans le worktree de la tâche, que le worker écrit : ses champs se
recalculent avec les fonctions publiques du kit, et l'empreinte d'arbre qu'il porte ignore des chemins entiers
(``node_modules``, ``__pycache__``, ``.venv``…). Ce module scelle au journal d'autorité (``~/.grimoire/control/``,
chaîné par hash, refusé en écriture aux outils d'agent) ce qui a été relu, en objets git :

- **à l'octroi d'un mandat** (:func:`pin_project_repository`, #994) : le dépôt GitHub et l'URL de ``origin`` du projet,
  ce que la relecture d'une tâche faite à la main compare (:func:`project_repository_pin`, sans repli sur le dépôt
  figé pour une tâche du tick) ;
- **au lancement du relecteur** (:func:`seal_review_launch`) : la tête de ``task/<id>`` qu'il va relire — pour une tâche
  faite à la main, aussi la PR, sa base, son dépôt et le worktree de vérification (:func:`launch_entry`) ;
- **au jugement READY** (:func:`seal_judgment`) : la même tête — une branche qui a bougé pendant la relecture n'est pas
  scellée —, son arbre git (``<tête>^{tree}``), un worktree propre et sur cette tête, l'empreinte des octets du reçu et
  du dernier pack (qui doivent être ceux que le reçu déclare) ;
- **à chaque réalignement du merger** (:func:`seal_realignment`) : ``from`` → ``to`` et l'arbre git produit.

Pour la double relecture (:mod:`.dual_review`, ``protected_gate: dual_review``), le journal porte aussi ce que la
décision de fusion compare, et que le ledger d'événements ne peut pas porter (un worker l'écrit) :

- le lancement de chaque relecteur garde le **modèle que le tick a lancé** (``model``) et, pour une seconde relecture,
  le sceau du premier READY qu'elle double (``second_of``) ; le jugement READY les recopie de ce lancement, jamais du
  reçu que la session écrit, avec son verdict et la session du relecteur ;
- le lancement de chaque worker en ``dual_review`` (:func:`seal_worker_launch`) : son modèle et l'empreinte de sa session ;
- la demande de seconde relecture du merger (:func:`request_second_review`) et son annulation par un NOT-READY
  (:func:`void_second_review`).

:func:`verify` compare une tête (celle de la PR pour le merger, celle de la branche sinon) à cette chaîne : la tête
scellée avec son arbre, ou un descendant noté par un réalignement du merger. Un dépôt sans git retombe sur l'empreinte
de contenu du reçu. Dans le tick, ses git sont neutralisés par :mod:`grimoire.core.safe_git`.

**Limites** : le journal vit sous le même utilisateur que les sessions ; la garde de zone refuse les écritures d'agent
par les outils et les voies shell reconnues, pas un script arbitraire, qui pourrait forger un sceau.
"""

from __future__ import annotations

import hashlib
import subprocess
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
    "DUAL_REQUEST_ENTRY",
    "DUAL_VOID_ENTRY",
    "LAUNCH_ENTRY",
    "PROJECT_PIN_ENTRY",
    "REALIGN_ENTRY",
    "REPOSITORY_ENTRY",
    "SEAL_ENTRY",
    "WORKER_ENTRY",
    "branch_head",
    "covered_by",
    "freeze_repository",
    "frozen_repository",
    "launch_entry",
    "pin_project_repository",
    "project_repository_pin",
    "receipt_digest",
    "request_second_review",
    "seal_judgment",
    "seal_realignment",
    "seal_review_launch",
    "seal_worker_launch",
    "task_entries",
    "tree_of",
    "verify",
    "void_second_review",
]

LAUNCH_ENTRY = "review_launch"
REPOSITORY_ENTRY = "task_repository"
SEAL_ENTRY = "review_judgment"
REALIGN_ENTRY = "merge_realignment"
#: Double relecture : le lancement d'un worker (modèle, empreinte de session), scellé par le lanceur.
WORKER_ENTRY = "worker_launch"
#: Double relecture : le merger demande la seconde relecture d'un premier READY (``first_seq`` : son sceau).
DUAL_REQUEST_ENTRY = "dual_review_request"
#: Double relecture : un NOT-READY a annulé la demande ; ce premier READY ne fonde plus aucune paire.
DUAL_VOID_ENTRY = "dual_review_void"
_READY = "READY"
#: #994 : le dépôt du projet figé à l'octroi d'un mandat, avant toute relecture d'une tâche faite à la main.
PROJECT_PIN_ENTRY = "project_repository"
_BRANCH_PREFIX = "task/"


def receipt_digest(root: Path, task_id: str) -> str:
    """sha256 des octets du fichier reçu ; ``""`` s'il n'existe pas ou ne se lit pas."""
    try:
        return hashlib.sha256(receipt_path(root, task_id).read_bytes()).hexdigest()
    except OSError:
        return ""


def _git(cwd: Path, *args: str) -> tuple[int, str, str]:
    from grimoire.core.safe_git import git_env

    try:
        done = subprocess.run(  # arguments fixes, sans shell ; dans un tick durci, l'environnement neutralise la config
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False, env=git_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _rev(cwd: Path, ref: str) -> str:
    code, out, _err = _git(cwd, "rev-parse", "--verify", "--quiet", ref)
    return out.strip() if code == 0 else ""


def branch_head(project_root: Path, task_id: str) -> str:
    """Le SHA de ``task/<id>`` dans le dépôt du projet ; ``""`` sans dépôt ou sans branche."""
    return _rev(project_root, f"refs/heads/{_BRANCH_PREFIX}{task_id}^{{commit}}")


def tree_of(project_root: Path, commit: str) -> str:
    """L'arbre git de *commit* : ce qui serait fusionné, chemins ignorés par l'empreinte compris."""
    return _rev(project_root, f"{commit}^{{tree}}") if commit else ""


def _store(project_root: Path) -> Any:
    from grimoire.missions.mandate import MandateStore

    return MandateStore(project_root)


def freeze_repository(project_root: Path, task_id: str) -> dict[str, str]:
    """Fige au journal d'autorité, **avant** que le worker tourne, le dépôt GitHub de la tâche et l'URL de ``origin``.

    Le worker écrit ensuite la configuration du dépôt : ce que le merger compare et passe à ``gh -R`` vient d'ici.
    Un dépôt qu'on ne sait pas résoudre est figé vide : le merger refusera (échec fermé), le lancement suit son cours.
    """
    from grimoire.missions.ci_slots import ci_repository

    try:
        repo = ci_repository(project_root, project_root)
    except Exception:  # pas de remote GitHub reconnaissable : rien ne sera fusionné sous mandat
        repo = ""
    _code, url, _err = _git(project_root, "remote", "get-url", "origin")
    store = _store(project_root)
    entry = {"type": REPOSITORY_ENTRY, "project": store.project, "task_id": task_id, "repo": repo,
             "origin_url": url.strip(), "ts": datetime.now(UTC).isoformat()}
    store.journal.append(entry)
    return {"repo": repo, "origin_url": url.strip()}


def frozen_repository(project_root: Path, task_id: str) -> dict[str, str] | None:
    """``{repo, origin_url}`` figés au lancement de la tâche, ou ``None``."""
    store = _store(project_root)
    found = [
        e for e in store.journal.read()
        if e.get("type") == REPOSITORY_ENTRY and e.get("project") == store.project and e.get("task_id") == task_id
    ]
    if not found:
        return None
    return {"repo": str(found[-1].get("repo") or ""), "origin_url": str(found[-1].get("origin_url") or "")}


def _current_repository(project_root: Path) -> dict[str, str]:
    from grimoire.missions.ci_slots import ci_repository

    try:
        repo = ci_repository(project_root, project_root)
    except Exception:  # pas de remote GitHub reconnaissable
        repo = ""
    _code, url, _err = _git(project_root, "remote", "get-url", "origin")
    return {"repo": repo, "origin_url": url.strip()}


def pin_project_repository(project_root: Path, mandate_id: str) -> dict[str, str]:
    """#994 : fige au journal d'autorité, à l'octroi du mandat *mandate_id*, le dépôt GitHub et l'URL de ``origin``.

    Un dépôt qu'on ne sait pas résoudre est figé vide : aucune tâche faite à la main ne sera relue sous ce mandat.
    """
    found = _current_repository(project_root)
    store = _store(project_root)
    store.journal.append({"type": PROJECT_PIN_ENTRY, "project": store.project, "mandate_id": mandate_id, **found,
                          "ts": datetime.now(UTC).isoformat()})
    return found


def project_repository_pin(project_root: Path, mandate_id: str = "") -> tuple[dict[str, str] | None, dict[str, str]]:
    """``(pin, courant)`` : le dépôt figé à l'octroi du mandat *mandate_id*, et celui que le projet déclare maintenant.

    Le pin est le dernier posé à l'octroi de *mandate_id* (de n'importe quel mandat du projet si *mandate_id* est vide).
    Jamais un repli sur le dépôt figé pour une tâche du tick : un pin absent, ou dont le dépôt ou l'URL de ``origin``
    sont vides, rend ``None`` — rien n'est relu à la main, il faut réoctroyer le mandat.
    """
    store = _store(project_root)
    pins = [e for e in store.journal.read() if e.get("type") == PROJECT_PIN_ENTRY and e.get("project") == store.project
            and (not mandate_id or e.get("mandate_id") == mandate_id)]
    last = pins[-1] if pins else {}
    repo, url = str(last.get("repo") or "").strip(), str(last.get("origin_url") or "").strip()
    return ({"repo": repo, "origin_url": url} if repo and url else None), _current_repository(project_root)


def seal_review_launch(
    project_root: Path, task_id: str, session_sha256: str, head: str, target: dict[str, Any] | None = None,
    *, model: str = "", second_of: int | None = None,
) -> None:
    """Note au journal d'autorité la tête que le relecteur lancé va relire ; *target* (#994) : la PR d'une tâche faite à
    la main (``pr``, ``base``, ``branch``, ``repo``, ``origin_url``, ``cwd``), que le jugement et le merger comparent.

    *model* : le modèle que le tick vient de passer à la session (jamais celui qu'elle déclare) ; *second_of* : le
    ``seq`` du sceau du premier READY dont cette session est la seconde relecture (:mod:`.dual_review`).
    """
    store = _store(project_root)
    store.journal.append({
        "type": LAUNCH_ENTRY, "project": store.project, "task_id": task_id, "session_sha256": session_sha256,
        "head": head, **(target or {}), **({"model": model} if model else {}),
        **({"second_of": second_of} if second_of is not None else {}), "ts": datetime.now(UTC).isoformat(),
    })


def seal_worker_launch(
    project_root: Path, task_id: str, session_sha256: str, model: str, provider: str = "", *, relaunch: bool = False,
) -> None:
    """Note au journal d'autorité le lancement d'un worker : le modèle que le tick lui a donné et l'empreinte de sa
    session. C'est ce que la double relecture compare (:mod:`.dual_review`) : jamais le ledger d'événements.

    *relaunch* : une session neuve repartie après une reprise ratée. Son modèle est celui du lancement noté au
    ledger : il s'ajoute à ceux que la seconde relecture écarte, mais ne remplace jamais le lancement d'origine —
    sans lancement d'origine scellé, aucune double relecture ne tient.
    """
    store = _store(project_root)
    store.journal.append({
        "type": WORKER_ENTRY, "project": store.project, "task_id": task_id, "session_sha256": session_sha256,
        "model": model, "provider": provider, **({"relaunch": True} if relaunch else {}),
        "ts": datetime.now(UTC).isoformat(),
    })


def request_second_review(project_root: Path, task_id: str, first_seq: int, head: str, pr: int) -> bool:
    """Le merger demande la seconde relecture du premier READY scellé à *first_seq* ; ``True`` si la demande vient
    d'être inscrite, ``False`` si elle l'était déjà. La demande ne porte aucun modèle : le relecteur le choisit au
    lancement (:func:`~grimoire.missions.dual_review.choose_second_reviewer`)."""
    store = _store(project_root)
    asked = any(
        e.get("type") == DUAL_REQUEST_ENTRY and e.get("project") == store.project and e.get("task_id") == task_id
        and e.get("first_seq") == first_seq for e in store.journal.read()
    )
    if asked:
        return False
    store.journal.append({
        "type": DUAL_REQUEST_ENTRY, "project": store.project, "task_id": task_id, "first_seq": first_seq, "head": head,
        "pr": pr, "ts": datetime.now(UTC).isoformat(),
    })
    return True


def void_second_review(project_root: Path, task_id: str, reason: str) -> int | None:
    """Un NOT-READY annule la seconde relecture demandée : le premier READY concerné ne fonde plus aucune paire, les
    deux relectures sont à refaire. Rend le ``seq`` du premier sceau annulé, ou ``None`` s'il n'y avait rien à annuler."""
    mine = task_entries(project_root, task_id)
    done = {e.get("first_seq") for e in mine if e.get("type") == DUAL_VOID_ENTRY}
    asked = [e.get("first_seq") for e in mine if e.get("type") == DUAL_REQUEST_ENTRY and e.get("first_seq") not in done]
    if not asked:
        return None
    store = _store(project_root)
    first_seq = asked[-1]
    store.journal.append({
        "type": DUAL_VOID_ENTRY, "project": store.project, "task_id": task_id, "first_seq": first_seq,
        "reason": reason[:200], "ts": datetime.now(UTC).isoformat(),
    })
    return first_seq if isinstance(first_seq, int) else None


def launch_entry(project_root: Path, task_id: str, session_sha256: str) -> dict[str, Any] | None:
    """La dernière entrée de lancement de la relecture d'empreinte *session_sha256*, telle que le journal la garde."""
    store = _store(project_root)
    found = [
        e for e in store.journal.read()
        if e.get("type") == LAUNCH_ENTRY and e.get("project") == store.project and e.get("task_id") == task_id
        and e.get("session_sha256") == session_sha256
    ]
    return dict(found[-1]) if found else None


def _launch_head(project_root: Path, task_id: str, session_sha256: str) -> str | None:
    found = launch_entry(project_root, task_id, session_sha256)
    return str(found.get("head", "")) if found is not None else None


def _dirty(proofs_root: Path) -> str:
    excludes = [f":(exclude){p}" for p in ("_grimoire-output", *RECEIPT_FINGERPRINT_EXCLUDES)]
    code, out, err = _git(proofs_root, "status", "--porcelain", "--untracked-files=all", "--", ".", *excludes)
    if code != 0:
        return f"état git du worktree illisible ({err.strip()[:160]})"
    lines = out.splitlines()
    return f"modifications non commitées ({', '.join(ln[3:] for ln in lines[:5])})" if lines else ""


def seal_judgment(
    project_root: Path, task_id: str, review_id: str, proofs_root: Path, receipt: ReviewReceipt, session_sha256: str = "",
    *, head: str | None = None,
) -> dict[str, Any]:
    """Scelle le READY jugé ; lève :class:`ValueError` si ce qui a été relu n'est pas ce qui est là.

    *head* (#994) : la tête relue quand ce n'est pas ``task/<id>`` — celle de la PR d'une tâche faite à la main, dans
    un worktree détaché ; elle doit être celle scellée au lancement et celle du worktree, comme une branche.
    """
    tree, pack = receipt_fingerprint(proofs_root), evidence_pack_digest(proofs_root, task_id)
    if tree != receipt.tree_fingerprint:
        raise ValueError("l'arbre présent n'est pas celui que le reçu déclare")
    if pack != receipt.evidence_pack_sha256:
        raise ValueError("le pack présent n'est pas celui que le reçu déclare")
    explicit = head is not None
    head = head if head is not None else branch_head(project_root, task_id)
    if explicit and not head:
        raise ValueError("aucune tête de PR notée au lancement de cette relecture : un sceau sans tête ne couvre rien")
    if not head and _rev(proofs_root, "HEAD^{commit}"):
        raise ValueError(f"aucune branche {_BRANCH_PREFIX}{task_id} dans un worktree git : un sceau sans tête ne couvre rien")
    sealed_launch: dict[str, Any] = {}
    if head:
        found = launch_entry(project_root, task_id, session_sha256)
        if found is None:
            raise ValueError("aucune tête scellée au lancement de cette relecture")
        sealed_launch, launched = found, str(found.get("head", ""))
        if launched != head:
            raise ValueError(f"la branche a bougé pendant la relecture ({launched[:12]} → {head[:12]})")
        if _rev(proofs_root, "HEAD^{commit}") != head:
            what = "la tête relue de la PR" if explicit else f"la tête de {_BRANCH_PREFIX}{task_id}"
            raise ValueError(f"le worktree n'est pas sur {what}")
        if dirty := _dirty(proofs_root):
            raise ValueError(f"{dirty} : la relecture ne porte pas sur la tête")
    store = _store(project_root)
    # Double relecture : ce que le tick a décidé au lancement de cette session (son modèle, le premier READY qu'elle
    # double) est recopié du lancement scellé, jamais du reçu que la session écrit.
    decided: dict[str, Any] = {k: sealed_launch[k] for k in ("model", "second_of") if k in sealed_launch}
    if "seq" in sealed_launch:
        decided["launch_seq"] = sealed_launch["seq"]
    entry: dict[str, Any] = store.journal.append({
        "type": SEAL_ENTRY, "project": store.project, "task_id": task_id, "review": review_id,
        "head": head, "tree": tree_of(project_root, head), "tree_fingerprint": tree, "evidence_pack_sha256": pack or "",
        "receipt_sha256": receipt_digest(proofs_root, task_id), "reviewer_session": receipt.reviewer_session,
        "verdict": _READY, **decided, "ts": datetime.now(UTC).isoformat(),
    })
    return entry


def seal_realignment(project_root: Path, task_id: str, review_id: str, source: str, target: str, tree: str) -> None:
    """Note au journal d'autorité un réalignement fait par le merger : ``source`` → ``target``, arbre git produit."""
    store = _store(project_root)
    store.journal.append({
        "type": REALIGN_ENTRY, "project": store.project, "task_id": task_id, "review": review_id, "from": source,
        "to": target, "tree": tree, "ts": datetime.now(UTC).isoformat(),
    })


def task_entries(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Les entrées du journal d'autorité de cette tâche, dans l'ordre où elles ont été écrites."""
    store = _store(project_root)
    return [e for e in store.journal.read() if e.get("project") == store.project and e.get("task_id") == task_id]


def _sealed(project_root: Path, task_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    mine = task_entries(project_root, task_id)
    seals = [i for i, e in enumerate(mine) if e.get("type") == SEAL_ENTRY]
    if not seals:
        return None, []
    seal = mine[seals[-1]]
    realigns = [e for e in mine[seals[-1] + 1:] if e.get("type") == REALIGN_ENTRY and e.get("review") == seal.get("review")]
    return seal, realigns


def covered_by(mine: list[dict[str, Any]], index: int, seal: dict[str, Any]) -> set[tuple[str, str]]:
    """Les ``(tête, arbre)`` couverts par le sceau à *index* : sa tête, puis ses réalignements notés après lui."""
    if not seal.get("head"):
        return set()
    found = {(str(seal["head"]), str(seal.get("tree", "")))}
    heads = {str(seal["head"])}
    for step in mine[index + 1:]:
        if (step.get("type") == REALIGN_ENTRY and step.get("review") == seal.get("review")
                and str(step.get("from")) in heads):
            heads.add(str(step.get("to")))
            found.add((str(step.get("to")), str(step.get("tree", ""))))
    return found


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
    if not seal.get("head") and head is not None:
        return "le jugement scellé ne porte aucune tête : il ne couvre pas la tête demandée"
    if not seal.get("head"):  # dépôt sans git : l'empreinte de contenu du reçu, faute d'objets
        if receipt_fingerprint(proofs_root) != seal.get("tree_fingerprint"):
            return "l'arbre a changé depuis le jugement READY scellé"
        return ""
    target = head if head is not None else branch_head(project_root, task.id)
    if target and (target, tree_of(project_root, target)) in covered:
        return ""
    return "la tête n'est pas celle du jugement READY scellé, ni un réalignement noté par le merger"
