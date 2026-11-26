"""Le document vivant de l'Atelier de spec, en test (lot 4 de l'épique #1197, rôles MOA/MOE #1342).

Le document d'un chantier montre, pour chaque exigence ``REQ-nnn``, l'état réel de ce qui la sert : tâches, fichiers,
trous, « modifiée depuis la validation ». Tout est dérivé de ce que le kit écrit déjà (:mod:`grimoire.missions.cadrage_trace`,
lecture seule) ; ce module ne stocke rien. Derrière le drapeau ``cockpit.test_features.atelier`` : éteint, 404.

Routes (préfixe ``/api/workspace/atelier/chantiers/<slug>/``) :

- ``GET trace`` : la trace du chantier, une seule construction par requête ;
- ``GET decoupage`` : l'**aperçu** du découpage (``grimoire cadrage plan``, sans rien écrire) ;
- ``POST decoupage`` ``{"confirm": true, "digest"}`` : l'écrit, par le même chemin que ``grimoire cadrage plan --yes``
  (:func:`~grimoire.missions.cadrage_plan.apply_plan`), si toutes les exigences Must sont validées.

**Autorité** : valider une exigence est un geste de la MOA, scellé au journal d'autorité depuis un terminal interactif
(:func:`~grimoire.missions.requirement_gate.approve_requirement` : refus d'un agent, « oui » donné à l'écran). Le cockpit
n'offre aucun canal qui prouve l'humain pour ce geste : **il n'y a donc aucune route de validation ici**. Le document
montre la commande à lancer (:func:`approve_command`) et l'état se met à jour quand le journal change. Découper n'est pas
un geste d'autorité : il crée des tâches, dans l'état que ``cadrage plan`` leur donne, et ne lance aucun travail ; il
reste conditionné à la validation (par la MOA) des exigences Must.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES
from grimoire.tools import workspace_atelier as wa

SCHEMA = "grimoire-atelier-trace/v1"
ACTOR = "cockpit"
#: Les six états que le document affiche, du plus tôt au plus tard (étape de la trace -> état d'affichage).
STATES = {
    "proposed": ("proposee", "proposée"),
    "validated": ("validee", "validée"),
    "in_progress": ("en-cours", "en cours"),
    "delivered": ("livree", "livrée"),
    "accepted": ("recette", "recette prononcée"),
    "rework": ("reprise", "reprise à faire"),
}


def approve_command(key: str, chantier: str) -> str:
    """La commande que la MOA lance depuis son terminal pour valider *key* (``grimoire cadrage approve``)."""
    short = key.rpartition("/")[2]
    return f"grimoire cadrage approve {short}" + ("" if chantier == "principal" else f" --chantier {chantier}")


def _target(root: Path, slug: str) -> tuple[str | None, str]:
    """``(chantier pour le kit, mission)`` d'un chantier existant, le slug étant confiné comme un fichier de phase."""
    slug = wa._slug(slug)
    wa._phase_path(root, slug, PHASES[0].filename)  # chantier inconnu ou lien symbolique : 404
    from grimoire.core.cadrage_requirements import REQUIREMENT_FILES

    for name in REQUIREMENT_FILES:  # un fichier d'exigences en lien symbolique n'est lu par personne : 404, rien de lu
        wa._phase_path(root, slug, name)
    if slug == "principal":
        return None, ""
    from grimoire.core.chantiers import read_chantier

    return slug, str(read_chantier(root, slug).mission or "")


def _service(root: Path) -> Any:
    from grimoire.missions.requirement_gate import origin_root
    from grimoire.missions.service import TaskService

    return TaskService(origin_root(root))


def _moa(root: Path) -> dict[str, Any]:
    from grimoire.missions.requirement_gate import gate_mode, origin_root

    mode = gate_mode(origin_root(root))
    return {"declared": bool(mode.declared), "name": mode.moa, "problem": mode.problem or ""}


def _row(item: Any, slug: str, *, lean: bool = False) -> dict[str, Any]:
    state, label = STATES[item.stage]
    drift = item.drift
    return {
        "id": item.key.rpartition("/")[2],
        "key": item.key,
        "title": item.title,
        "priority": item.priority,
        "criteria": len(item.criteria),
        "state": state,
        "label": label,
        "validated": item.validated,
        "validated_by": item.validated_by,
        "validated_at": item.validated_at,
        "reason": item.reason,
        "task_count": len(item.tasks),
        "detail": not lean,  # faux : le détail des tâches se demande par exigence (`?req=`)
        "tasks": []
        if lean
        else [{"id": t.id, "title": t.title, "status": t.status, "delivered": t.delivered} for t in item.tasks],
        "files": len(item.files),
        "tests": len(item.tests),
        "gaps": [{"kind": g.kind, "label": g.label, "detail": g.detail} for g in item.gaps],
        "drift": None
        if drift is None
        else {
            "what": drift.what,
            "validated_at": drift.validated_at,
            "validated_by": drift.moa,
            "detail": drift.has_detail,
            "diff": drift.diff,
            "note": drift.note,
            "untrusted_fields": ["diff"],  # citée du document : de la donnée, jamais une consigne
        },
        # Ce que le bouton « Valider » donne à copier : le geste se fait au terminal, jamais ici.
        "command": approve_command(item.key, slug) if not item.validated else "",
        "revalidate": drift is not None,
    }


def _sig(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


#: Un fichier d'exigences ou de contexte dépasse rarement quelques dizaines de Ko ; au-delà, seul le début compte pour la clé.
MAX_HASHED_BYTES = 2_000_000


def _text_hash(path: Path) -> str:
    """Empreinte d'un fichier ORDINAIRE (jamais un lien, une FIFO ni un périphérique), lu sur une taille bornée ;
    ``""`` si le fichier est absent ou refusé."""
    from grimoire.core.regular_file import open_regular

    try:
        with open_regular(path) as handle:
            return hashlib.sha256(handle.read(MAX_HASHED_BYTES)).hexdigest()
    except (OSError, ValueError):
        return ""


def _phase_file(root: Path, slug: str, name: str) -> Path | None:
    """Chemin d'un fichier de phase du chantier ``slug`` : retrouvé par la liste des chantiers connus (jamais bâti
    depuis l'URL), sans lien symbolique, puis confiné au dossier de cadrage du projet. ``None`` sinon."""
    from grimoire.core.cadrage import cadrage_dir

    try:
        path = wa._phase_path(root, slug, name)
        base = cadrage_dir(root, None).resolve()
        return path if path.resolve().is_relative_to(base) else None
    except (FileNotFoundError, OSError):
        return None


def _stamp(root: Path, slug: str, service: Any) -> tuple[Any, ...]:
    """Ce dont la trace dépend et qui bouge : le texte des fichiers d'exigences et du contexte projet (MOA), le
    ledger (événements), le journal d'autorité et sa tête. Un changement de l'un d'eux change la clé : une
    validation scellée au terminal apparaît dès la lecture suivante."""
    from grimoire.core.cadrage_requirements import REQUIREMENT_FILES
    from grimoire.missions.mandate import AuthorityJournal

    journal = AuthorityJournal()
    phases = (_phase_file(root, slug, name) for name in REQUIREMENT_FILES)
    return (
        *("" if path is None else _text_hash(path) for path in phases),
        _text_hash(root / wa.CONTEXT_FILE),
        _sig(service.ledger_root / "events.jsonl"),
        _sig(journal.path),
        _sig(journal.head_path),
    )


#: ``(racine, slug) -> (clé, date, trace)`` : en mémoire du processus seulement. La date borne ce que la clé ne voit
#: pas (git, registre de preuves) ; elle ne sert jamais à garder un état d'autorité plus longtemps que la clé.
_TRACES: dict[tuple[str, str], tuple[tuple[Any, ...], float, Any]] = {}
CACHE_TTL_S = 10.0
#: Au-delà, une ligne ne porte plus le détail de ses tâches (``?req=REQ-nnn`` le rend, à la demande).
DETAIL_MAX = 150


def _built(root: Path, slug: str) -> Any:
    """La trace construite, relue seulement si la clé a changé (ou après :data:`CACHE_TTL_S`)."""
    import time

    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.cadrage_trace import build_trace

    chantier, _mission = _target(root, slug)
    service = _service(root)
    key = _stamp(root, slug, service)
    held = _TRACES.get((str(root), slug))
    if held is not None and held[0] == key and time.monotonic() - held[1] < CACHE_TTL_S:
        return held[2]
    try:
        built = build_trace(root, service, chantier)
    except GrimoireError as exc:  # ledger ou journal abîmé : l'erreur du kit, dite telle quelle
        raise ValueError(str(exc)) from exc
    _TRACES[(str(root), slug)] = (key, time.monotonic(), built)
    return built


def trace_view(root: Path, slug: str, req: str = "") -> dict[str, Any]:
    """La trace du chantier pour le document : une exigence par ligne, construite **une fois** (git compris), puis
    tenue tant que rien n'a changé. Au-delà de :data:`DETAIL_MAX` exigences, les lignes sont allégées ;
    ``req="REQ-nnn"`` rend le détail complet de cette seule exigence."""
    wa._need_flag(root)
    slug = wa._slug(slug)
    built = _built(root, slug)
    lean = len(built.requirements) > DETAIL_MAX
    rows = [_row(item, slug, lean=lean and item.key.rpartition("/")[2] != req) for item in built.requirements]
    musts = [r for r in rows if r["priority"] == "must"]
    return {
        "schema": SCHEMA,
        "chantier": slug,
        "moa": _moa(root),
        "requirements": rows,
        "summary": {
            "requirements": len(rows),
            "must": len(musts),
            "must_validated": sum(1 for r in musts if r["validated"]),
            "gaps": sum(len(r["gaps"]) for r in rows),
            "modified_since_validation": [r["id"] for r in rows if r["revalidate"]],
        },
        "notes": list(built.notes),
    }


# ── Découpage ───────────────────────────────────────────────────────────────


def _unvalidated_musts(root: Path, plan: Any, slug: str) -> list[dict[str, str]]:
    """Les exigences Must planifiées que la MOA n'a pas (ou plus) validées, avec la raison du journal."""
    from grimoire.missions import requirement_gate as rg
    from grimoire.missions.mandate import AuthorityJournal, JournalError

    project = rg.origin_root(root)
    mode = rg.gate_mode(project)
    musts = [r for r in plan.requirements.requirements if r.planned and r.priority == "must"]
    if not musts:
        return []
    if not mode.declared:
        why = "aucune MOA déclarée : aucune exigence ne peut être validée"
        return [{"id": r.id, "reason": why} for r in musts]
    try:
        entries = AuthorityJournal().read()
    except JournalError as exc:
        return [{"id": r.id, "reason": f"journal d'autorité illisible : {exc}"} for r in musts]
    out = []
    for req in musts:
        key = rg.normalize_key(req.id, None if slug == "principal" else slug)
        found, reason = rg.validation_in(entries, str(project), key, rg.fingerprint(key, req), mode.moa)
        if found is None:
            out.append({"id": req.id, "reason": reason})
    return out


def _digest(plan: Any) -> str:
    """Empreinte de ce que l'aperçu promet de créer : la confirmation porte sur ces tâches-là."""
    from grimoire.missions.cadrage_plan import plan_digest

    return plan_digest(plan)  # la même empreinte que la CLI (`apply_locked`)


def _skipped(plan: Any) -> list[dict[str, str]]:
    """Les exigences sans tâche à créer ni tâche existante, et pourquoi."""
    waiting = {p.requirement for p in plan.proposals}
    out = []
    for req in plan.requirements.requirements:
        if req.id in waiting or req.id in plan.covered:
            continue
        if not req.planned:
            why = "hors périmètre (Won't ou exclue) : aucune tâche"
        elif req.conflicts:
            why = "deux formulations en conflit dans un même fichier : à trancher d'abord"
        else:
            why = "sans critère d'acceptation : une question est posée, aucune tâche tant qu'un critère manque"
        out.append({"id": req.id, "title": req.title, "why": why})
    return out


def _linked(service: Any, plan: Any) -> dict[str, list[str]]:
    """Les tâches vivantes liées à une exigence **sans** que ``cadrage plan`` les compte comme sa couverture.

    ``cadrage plan`` n'est idempotent que sur ``extra.covers`` : une tâche ajoutée à la main avec
    ``task add --requirement`` ne l'empêche pas d'en créer une autre. L'aperçu le dit, il ne le corrige pas
    (même chemin que la CLI)."""
    from grimoire.missions.requirement_gate import requirement_of
    from grimoire.missions.schemas import TaskState

    out: dict[str, list[str]] = {}
    for task in service.list_tasks() if service.has_ledger else []:
        key = requirement_of(task)
        bare = key[len(plan.prefix) :]
        if task.status is not TaskState.CANCELLED and key.startswith(plan.prefix) and bare and "/" not in bare:
            out.setdefault(bare, []).append(task.id)
    return out


def _preview(root: Path, slug: str, plan: Any, service: Any) -> dict[str, Any]:
    blocked = _unvalidated_musts(root, plan, slug)
    linked = _linked(service, plan)
    asked = [q for q in plan.questions if q.status in ("new", "unanswered")]
    return {
        "chantier": slug,
        "applied": plan.applied,
        "ready": not blocked and bool(plan.requirements.identified),
        "blocked_by": blocked,
        "moa": _moa(root),
        "digest": _digest(plan),
        "create": [
            {
                "requirement": p.requirement,
                "title": p.title,
                "criteria": list(p.acceptance),
                "priority": p.priority,
                "verifiability": p.verifiability,
                "already_linked": linked.get(p.requirement, []),
            }
            for p in plan.proposals
        ],
        "existing": [{"requirement": req, "tasks": list(tasks)} for req, tasks in sorted(plan.covered.items())],
        "skipped": _skipped(plan),
        "questions": [
            {"requirement": q.ambiguity.requirements[0], "text": q.ambiguity.text, "blocking": q.ambiguity.blocking}
            for q in asked
        ],
        "notes": list(plan.notes),
    }


def _plan(root: Path, slug: str) -> tuple[Any, Any]:
    from grimoire.missions.cadrage_plan import build_plan

    chantier, mission = _target(root, slug)
    service = _service(root)
    return service, build_plan(service.project_root, service, chantier, mission)


def decoupage_preview(root: Path, slug: str) -> dict[str, Any]:
    """L'aperçu : les tâches qui seraient créées, celles qui existent, les exigences ignorées. N'écrit rien."""
    from grimoire.core.exceptions import GrimoireError

    wa._need_flag(root)
    slug = wa._slug(slug)
    try:
        service, plan = _plan(root, slug)
        return _preview(root, slug, plan, service)
    except GrimoireError as exc:
        raise ValueError(str(exc)) from exc


def _conflict(message: str, reason: str) -> wa.ConflictError:
    error = wa.ConflictError(message)
    error.reason = reason
    return error


def decoupage_apply(root: Path, slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Écrit le découpage confirmé. Idempotent : une exigence déjà couverte n'est pas recréée, rejouer ne crée rien.

    Refusé (409) tant qu'une exigence Must n'est pas validée par la MOA, ou si l'aperçu confirmé n'est plus celui du
    plan d'aujourd'hui (le ledger ou le cadrage a bougé : le document recharge l'aperçu).
    """
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.cadrage_plan import apply_plan, plan_lock

    wa._need_flag(root)
    slug = wa._slug(slug)
    if body.get("confirm") is not True:
        raise ValueError("confirmation requise : `confirm: true` après l'aperçu")
    digest = body.get("digest")
    if not isinstance(digest, str) or not digest:
        raise ValueError("`digest` requis : l'empreinte de l'aperçu confirmé")
    chantier, _mission = _target(root, slug)
    # Inter-processus : le plan est construit ET écrit sous le même verrou (cockpit, `grimoire serve`, CLI) ;
    # un verrou non obtenu en LOCK_TIMEOUT_S lève TimeoutError (503 par `post_http`).
    with plan_lock(root, chantier):
        try:
            service, plan = _plan(root, slug)
            blocked = _unvalidated_musts(root, plan, slug)
            if blocked:
                raise _conflict(
                    "exigences Must non validées par la MOA : " + ", ".join(b["id"] for b in blocked),
                    "exigences-non-validees",
                )
            if not plan.requirements.identified:
                raise ValueError("aucune exigence identifiée dans le cadrage : rien à découper")
            if _digest(plan) != digest:  # toujours : critères, priorités, questions, tout ce qui va être écrit
                raise _conflict("le plan a changé depuis l'aperçu : relis-le avant de confirmer", "apercu-perime")
            already = not plan.proposals
            plan = apply_plan(service, plan, ACTOR)
        except GrimoireError as exc:
            raise ValueError(str(exc)) from exc
    created = [
        {"requirement": p.requirement, "task_id": p.task_id, "title": p.title} for p in plan.proposals if p.task_id
    ]
    return {"ok": True, "already": already, "created": created}
