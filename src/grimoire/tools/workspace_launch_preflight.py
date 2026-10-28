"""Préflight d'assignation (P47 lot 1) — tout savoir AVANT de lancer un worker, sans rien lancer.

``GET /api/workspace/tasks/<id>/launch-plan`` rend le plan d'assignation d'une
session worker sur une tâche, en lecture seule stricte (ADR-008, phase plan) :
le modèle recommandé avec son palier et sa classe de vérifiabilité, le worktree
et la branche que le lanceur créerait, les vérifications avant lancement
(conflit de réservation, plafond de sessions, admission, dépendances, quota,
dossier approuvé par Claude Code, ``gh`` authentifié, PR ouverte sur les mêmes
fichiers), et la commande équivalente à copier — jamais exécutée ici.

Tout vient des mêmes sources que le tick réel : :func:`grimoire.missions.
admission.build_plan` (le plan à blanc de ``grimoire queue plan``),
:func:`grimoire.missions.reservation.find_conflict` (la règle unique W1-07),
:mod:`grimoire.missions.launcher` (plafonds et composition de la commande) et
:func:`grimoire.missions.dispatch_advice.dispatch_advice` (classe → modèle).
Aucune logique n'est recopiée ; aucune écriture, pas même un événement. Une
donnée illisible ou une sonde muette rend un verdict ``inconnu`` — jamais une
estimation (doctrine d'ancrage des affirmations).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_generation import normalize_task_id
from grimoire.missions import launcher
from grimoire.missions.admission import (
    Admit,
    Capacity,
    Defer,
    HostReading,
    LedgerView,
    QueuePlan,
    RouteRef,
    build_plan,
    load_capacity,
    read_host,
)
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.launcher import Exec, default_exec
from grimoire.missions.ledger_rules import unclosed_blockers
from grimoire.missions.orchestrator_policy import PolicyRead, WorkerRole, load_orchestrator_policy
from grimoire.missions.pause import read_pause
from grimoire.missions.reservation import _HOLDING, find_conflict  # la même règle que le claim_guard (W1-07)
from grimoire.missions.schemas import MissionTask, TaskClaim
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import TASK_BRANCH_PREFIX, find_task_worktree
from grimoire.tools.workspace_write_guard import state_fingerprint

__all__ = ["Preflight", "launch_preflight", "launch_preflight_full", "plan_fingerprint"]

#: Le ``host_id`` que le lanceur pose sur le claim du worker (``launcher._HOST``).
_HOST = "claude-code-cli"

_OK, _WARN, _BLOQUANT, _INCONNU = "ok", "warn", "bloquant", "inconnu"
#: Les deux verdicts qui empêchent ``pret`` : un refus constaté, ou l'impossibilité de savoir.
_NOT_READY = (_BLOQUANT, _INCONNU)


def _verif(vid: str, libelle: str, verdict: str, detail: str) -> dict[str, str]:
    return {"id": vid, "libelle": libelle, "verdict": verdict, "detail": detail}


# ── Vérifications ────────────────────────────────────────────────────────────


def _conflit(tasks: dict[str, MissionTask], task: MissionTask, files: tuple[str, ...]) -> dict[str, str]:
    """Conflit de réservation : les fichiers que le claim du worker réserverait contre les claims tenus.

    *files* vide : rien n'est réservé (le lanceur réclame sans fichier), donc
    aucun conflit possible côté claim — mais si d'autres tâches tiennent des
    réservations, le chevauchement réel n'est pas évaluable : ``inconnu``,
    jamais « pas de conflit » deviné. L'expiration fine d'un claim déclaré est
    jugée par :func:`find_conflict`, la même règle que le ``claim_guard``.
    """
    libelle = "Conflit de réservation"
    if files:
        claim = TaskClaim.new(actor_id=launcher.WORKER_ACTOR, host_id=_HOST, exclusive_files=files)
        conflict = find_conflict(tasks, task, claim)
        if conflict is not None:
            return _verif("conflit_reservation", libelle, _BLOQUANT,
                          f"fichier {conflict.file} {conflict.reason} — {conflict.remedy}")
        return _verif("conflit_reservation", libelle, _OK, f"{len(files)} fichier(s) exclusif(s) libres")
    reserved = [o for o in tasks.values()
                if o.id != task.id and o.status in _HOLDING and o.claim is not None and o.claim.exclusive_files]
    if reserved:
        holders = ", ".join(sorted(o.id for o in reserved)[:3])
        return _verif("conflit_reservation", libelle, _INCONNU,
                      f"la tâche ne déclare aucun fichier et {holders} tien(nen)t des réservations : "
                      "chevauchement non évaluable")
    return _verif("conflit_reservation", libelle, _OK,
                  "aucun fichier déclaré et aucune réservation tenue : rien à croiser")


def _actives(service: TaskService, tasks: dict[str, MissionTask]) -> int:
    """Les sessions de worker qui travaillent — la même règle que ``WorkerLauncher._active``."""
    return len([tid for tid in launcher.active_worker_ids(service) if tid in tasks])


def _launched_today(service: TaskService, now: datetime) -> int:
    """Les lancements du jour (UTC), réussis ou non — même compte que ``WorkerLauncher._launched_today``."""
    today = now.isoformat()[:10]
    return sum(
        1 for e in service.ledger.list_events()
        if e.event_type in (launcher.LAUNCHED_EVENT, launcher.LAUNCH_FAILED_EVENT) and e.created_at[:10] == today
    )


def _plafond(read: PolicyRead, service: TaskService, tasks: dict[str, MissionTask], now: datetime) -> dict[str, str]:
    """Plafond de sessions : WIP du rôle worker et lancements du jour, contre la politique d'orchestration."""
    libelle = "Plafond de sessions"
    if read.policy is None:
        return _verif("plafond_sessions", libelle, _INCONNU, read.error)
    role = read.policy.worker
    active = _actives(service, tasks)
    today = _launched_today(service, now)
    compte = f"{active}/{role.wip} session(s) actives · {today}/{role.sessions_per_day} lancement(s) aujourd'hui"
    if active >= role.wip:
        return _verif("plafond_sessions", libelle, _BLOQUANT, f"WIP du worker atteint — {compte}")
    if today >= role.sessions_per_day:
        return _verif("plafond_sessions", libelle, _BLOQUANT, f"plafond de lancements du jour atteint — {compte}")
    if not role.enabled:
        return _verif("plafond_sessions", libelle, _WARN,
                      f"rôle worker désactivé (orchestration-policy.yaml) : le tick ne lancerait rien — {compte}")
    return _verif("plafond_sessions", libelle, _OK, compte)


def _admission(plan: QueuePlan, task: MissionTask) -> dict[str, str]:
    """Admission : le verdict que ``grimoire queue plan`` rendrait maintenant pour cette tâche."""
    libelle = "Admission"
    tid = task.id
    if any(a.task_id == tid for a in plan.admitted):
        return _verif("admission", libelle, _OK, "serait admise maintenant (Admit)")
    for d in plan.deferred:
        if d.task_id == tid:
            retry = f" — retenter vers {d.retry_at}" if d.retry_at and d.retry_at != "unknown" else ""
            verdict = _INCONNU if d.reason == "unknown" else _BLOQUANT
            return _verif("admission", libelle, verdict, f"Defer({d.reason}) : {d.detail}{retry}")
    for excluded, reason in plan.excluded:
        if excluded == tid:
            return _verif("admission", libelle, _BLOQUANT, f"exclue de la file : {reason}")
    return _verif("admission", libelle, _BLOQUANT,
                  f"tâche non candidate à l'admission (statut {task.status.value}, seule une tâche ready entre en file)")


def _dependances(tasks: dict[str, MissionTask], task: MissionTask) -> dict[str, str]:
    """Dépendances ``blocks`` ouvertes : la tâche est-elle réclamable (même règle que ``build_plan``) ?"""
    libelle = "Dépendances ouvertes"
    blockers = unclosed_blockers(tasks, task)
    if blockers:
        return _verif("dependances", libelle, _BLOQUANT, f"dépend de {'; '.join(blockers)}")
    return _verif("dependances", libelle, _OK, "aucune dépendance bloquante ouverte")


def _quota(capacity: Capacity, view: LedgerView) -> dict[str, str]:
    """Quota de tokens du jour : mode, consommé, plafond — la même lecture que la vue Capacité (P71)."""
    libelle = "Quota de tokens du jour"
    if err := capacity.error_for("quota"):
        return _verif("quota", libelle, _INCONNU, f"quota illisible : {err}")
    used = view.tokens
    if capacity.unlimited:
        return _verif("quota", libelle, _OK, f"mode illimité — >= {used.tokens or 0} token(s) consommés, aucun plafond")
    plafond = int(capacity.tokens_per_day)
    if used.tokens is None:
        return _verif("quota", libelle, _INCONNU, f"consommation du jour inconnue sous un quota fini ({plafond} tokens)")
    if used.tokens >= plafond:
        return _verif("quota", libelle, _BLOQUANT, f"quota du jour atteint (>= {used.tokens}/{plafond} tokens)")
    return _verif("quota", libelle, _OK, f"mode fini — >= {used.tokens}/{plafond} tokens consommés")


def _dossier_approuve(repo: Path, config: Path) -> dict[str, str]:
    """Dossier approuvé par Claude Code : sans la confiance, ``claude --bg`` se bloque en silence.

    Piège vérifié (2026-10-17) : une session de fond lancée dans un dépôt que
    Claude Code n'a jamais approuvé reste bloquée sur « Workspace not
    trusted » sans que rien ne le dise. La confiance se lit dans
    ``~/.claude.json`` (``projects.<chemin>.hasTrustDialogAccepted``).
    """
    libelle = "Dossier approuvé par Claude Code"
    remede = f"ouvre une session Claude Code dans {repo} et accepte la confiance du dossier"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _verif("dossier_approuve", libelle, _BLOQUANT,
                      f"aucun {config.name} sur ce poste : rien n'est approuvé — {remede}")
    except (OSError, ValueError) as exc:
        return _verif("dossier_approuve", libelle, _INCONNU,
                      f"{config} illisible ({exc}) : confiance non vérifiable")
    projects = data.get("projects") if isinstance(data, dict) else None
    entry = projects.get(str(repo)) if isinstance(projects, dict) else None
    if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
        return _verif("dossier_approuve", libelle, _OK, f"confiance accordée à {repo}")
    return _verif("dossier_approuve", libelle, _BLOQUANT,
                  f"dossier non approuvé : une session --bg s'y bloquerait en silence — {remede}")


def _gh_app(repo: Path, exec_: Exec) -> dict[str, str]:
    """``gh`` authentifié : sans lui, le worker finirait sans pouvoir ouvrir sa PR."""
    libelle = "gh authentifié (GitHub App)"
    result = exec_(["gh", "auth", "status"], repo, None)
    if result.returncode == 0:
        return _verif("gh_app", libelle, _OK, "gh auth status répond : le worker pourra ouvrir sa PR")
    if result.returncode in (124, 126, 127):
        # Binaire absent ou sonde expirée : on ne sait pas, on ne devine pas.
        return _verif("gh_app", libelle, _INCONNU,
                      f"gh non sondable ({(result.stderr or '').strip() or f'sortie {result.returncode}'})")
    detail = (result.stderr or result.stdout).strip()[:160] or "aucun détail"
    return _verif("gh_app", libelle, _BLOQUANT,
                  f"gh non authentifié (sortie {result.returncode}) : {detail} — le worker ne pourrait pas ouvrir sa PR")


def _pr_ouverte(repo: Path, files: tuple[str, ...], exec_: Exec) -> dict[str, str]:
    """PR ouverte sur les mêmes fichiers : lancer dessus produirait deux PR en conflit."""
    libelle = "PR ouverte sur les mêmes fichiers"
    if not files:
        return _verif("pr_ouverte", libelle, _OK, "aucun fichier déclaré : rien à croiser")
    result = exec_(["gh", "pr", "list", "--state", "open", "--json", "number,title,files"], repo, None)
    if result.returncode != 0:
        return _verif("pr_ouverte", libelle, _INCONNU,
                      f"gh pr list a échoué ({result.returncode}) : chevauchement non évaluable")
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return _verif("pr_ouverte", libelle, _INCONNU, "réponse de gh illisible : chevauchement non évaluable")
    wanted = set(files)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        touched = {str(f.get("path", "")) for f in row.get("files") or [] if isinstance(f, dict)}
        hit = sorted(wanted & touched)
        if hit:
            return _verif("pr_ouverte", libelle, _BLOQUANT,
                          f"la PR ouverte #{row.get('number', '?')} touche déjà {hit[0]} — "
                          "fusionne-la d'abord, ou retire le fichier du claim")
    return _verif("pr_ouverte", libelle, _OK,
                  f"aucune PR ouverte ne touche les {len(files)} fichier(s) que le claim réserverait")


# ── Modèle, worktree, commande ───────────────────────────────────────────────


def _model_for(role: WorkerRole, route: RouteRef | None) -> tuple[str, str]:
    """``(modèle, source)`` — la même cascade que ``WorkerLauncher._model_for``, jamais recopiée ailleurs."""
    if role.model:
        return role.model, "worker.model de la politique d'orchestration"
    if route is not None and route.outcome == "route" and route.model and route.provider.lower() in {"anthropic", "claude", "claude-code"}:
        return route.model, "route du routeur (K9)"
    alias = launcher._TIER_ALIAS.get(role.tier, "")
    if alias:
        return alias, f"palier {role.tier} de la politique d'orchestration"
    return "", "aucun — ni modèle, ni route, ni palier : la session garderait son défaut"


def _route_of(plan: QueuePlan, tid: str) -> RouteRef | None:
    """La route que le plan d'admission a lue pour *tid* (``None`` hors file) — un seul appel au routeur."""
    decisions: tuple[Admit | Defer, ...] = (*plan.admitted, *plan.deferred)
    for decision in decisions:
        if decision.task_id == tid:
            return decision.route
    return None


def _modele(read: PolicyRead, route: RouteRef | None, task: MissionTask) -> tuple[dict[str, Any], str]:
    """Le bloc « modèle » de la maquette, et le modèle que la commande équivalente porterait."""
    advice = dispatch_advice(task)
    verifiability = advice["verifiability"]
    if read.policy is None:
        model = advice["recommended_model"] or ""
        source = "classe de vérifiabilité (politique d'orchestration illisible)"
        palier = advice["model_tier"]
    else:
        model, source = _model_for(read.policy.worker, route)
        palier = read.policy.worker.tier or advice["model_tier"]
    bloc = {
        "recommande": model or None,
        "palier": palier,
        "classe": verifiability["class"],
        "justification": verifiability["reason"],
        "source": source,
    }
    return bloc, model


def _worktree(service: TaskService, task_id: str) -> dict[str, Any]:
    """Worktree et branche : l'existant noté au ledger, sinon la composition de ``create_task_worktree``."""
    existing = find_task_worktree(service, task_id)
    if existing is not None:
        return {"chemin": str(existing.path), "branche": existing.branch, "existant": True, "detail": ""}
    branch = TASK_BRANCH_PREFIX + task_id
    try:
        done = subprocess.run(  # lecture seule : le chemin du dossier git commun, rien d'autre
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=service.home_root, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"chemin": None, "branche": branch, "existant": False,
                "detail": f"dépôt git illisible ({type(exc).__name__}) : chemin du worktree inconnu"}
    if done.returncode != 0 or not done.stdout.strip():
        return {"chemin": None, "branche": branch, "existant": False,
                "detail": "dépôt git illisible : chemin du worktree inconnu"}
    target = Path(done.stdout.strip()) / "grimoire" / "worktrees" / normalize_task_id(task_id)
    return {"chemin": str(target), "branche": branch, "existant": False, "detail": ""}


def _commande(read: PolicyRead, tid: str, model: str) -> dict[str, Any]:
    """La commande que ``WorkerLauncher._argv`` composerait — à copier, JAMAIS exécutée ici."""
    if read.policy is None:
        return {"argv": None, "texte": None, "raison": f"politique d'orchestration illisible : {read.error}"}
    argv = ["claude", "--bg", "--name", f"grimoire-{tid}"]
    if model:
        argv += ["--model", model]
    if read.policy.worker.permission_mode:
        argv += ["--permission-mode", read.policy.worker.permission_mode]
    argv.append(launcher.WorkerLauncher._prompt(tid))  # le même prompt que le lanceur, jamais recopié
    return {"argv": argv, "texte": shlex.join(argv), "raison": ""}


# ── Entrée ───────────────────────────────────────────────────────────────────


def plan_fingerprint(payload: Mapping[str, Any]) -> str:
    """L'empreinte du plan (ADR-008) : les faits qui engagent le lancement, jamais les compteurs.

    Entrent dans l'empreinte : la tâche (id, statut), ``pret``, le verdict de
    chaque vérification — pas son détail : les compteurs qu'il porte (tokens
    consommés, sessions actives) bougent sans changer la décision —, le modèle
    recommandé, la branche et l'existence du worktree, la commande équivalente.
    ``apply`` recalcule cette empreinte et refuse si elle diffère de celle du
    plan montré : l'état a changé entre le plan et le geste.
    """
    stable = {
        "task_id": payload["task_id"],
        "statut": payload["statut"],
        "pret": payload["pret"],
        "verifications": [[v["id"], v["verdict"]] for v in payload["verifications"]],
        "modele": payload["modele"]["recommande"],
        "branche": payload["worktree"]["branche"],
        "worktree_existant": payload["worktree"]["existant"],
        "commande": payload["commande"]["texte"],
    }
    return state_fingerprint(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Preflight:
    """Le plan rendu à la route, plus ce que l'apply (P47 lot 3) relit sans recomposer.

    ``payload`` est exactement ce que ``launch_preflight`` rend ; ``plan`` le
    plan d'admission déjà construit (son ``Admit`` part au lanceur tel quel),
    ``service``, ``policy`` et ``task`` les lectures déjà faites.
    """

    payload: dict[str, Any]
    plan: QueuePlan
    service: TaskService
    policy: PolicyRead
    task: MissionTask


def launch_preflight(
    project_root: Path,
    task_id: str,
    *,
    now: datetime | None = None,
    probe: Callable[[], HostReading | None] = read_host,
    route_of: Callable[[MissionTask], RouteRef | None] | None = None,
    files: tuple[str, ...] | None = None,
    exec_: Exec = default_exec,
    claude_config: Path | None = None,
) -> dict[str, Any]:
    """Le plan d'assignation d'une session worker sur *task_id* — lecture pure, rien n'est lancé ni écrit.

    Même plan à blanc que ``grimoire queue plan`` (routes du routeur lues,
    jamais écrites ; ``autonomous`` suit ``GRIMOIRE_UNATTENDED`` comme la vue
    Capacité P71). *probe* est la sonde de l'hôte et *route_of* la lecture de
    route du routeur (défaut : celle de ``queue_plan``), injectables comme dans
    ``capacity_view``. *files* : les fichiers que le claim du worker
    réserverait (défaut : ceux du claim courant de la tâche, s'il y en a un).
    Une tâche inconnue lève :class:`GrimoireMissionError` (404 côté transport).
    """
    return launch_preflight_full(
        project_root, task_id, now=now, probe=probe, route_of=route_of, files=files,
        exec_=exec_, claude_config=claude_config,
    ).payload


def launch_preflight_full(
    project_root: Path,
    task_id: str,
    *,
    now: datetime | None = None,
    probe: Callable[[], HostReading | None] = read_host,
    route_of: Callable[[MissionTask], RouteRef | None] | None = None,
    files: tuple[str, ...] | None = None,
    exec_: Exec = default_exec,
    claude_config: Path | None = None,
) -> Preflight:
    """Comme :func:`launch_preflight`, mais rend aussi les lectures déjà faites (:class:`Preflight`).

    C'est l'entrée de l'apply (P47 lot 3) : il compare l'empreinte du corps à
    ``payload["empreinte"]`` et passe ``plan.admitted`` au lanceur — le même
    plan que celui qui a rendu le verdict, jamais un second calcul divergent.
    """
    service = TaskService(project_root.resolve())
    if not service.has_ledger:
        raise GrimoireMissionError(f"Tâche inconnue : {task_id} (aucun ledger)")
    task = service.require(task_id)
    moment = now or datetime.now(UTC)
    capacity = load_capacity(service.home_root)
    autonomous = os.environ.get("GRIMOIRE_UNATTENDED", "").strip() == "1"
    plan = build_plan(
        service.ledger, capacity, now=moment, host=probe(),
        # La même lecture de route que `queue_plan` (K9) — jamais écrite.
        route_of=route_of if route_of is not None else service._route_ref,
        autonomous=autonomous, pause=read_pause(service.home_root),
    )
    tasks = {t.id: t for t in service.ledger.list_tasks()}
    read = load_orchestrator_policy(service.home_root)
    requested = files if files is not None else (task.claim.exclusive_files if task.claim is not None else ())
    config = claude_config if claude_config is not None else Path.home() / ".claude.json"
    verifications = [
        _conflit(tasks, task, tuple(requested)),
        _plafond(read, service, tasks, moment),
        _admission(plan, task),
        _dependances(tasks, task),
        _quota(capacity, plan.view),
        # Les contrôles préalables au lancement lui-même (critère 2 de P47) :
        # sans eux, la session se bloquerait en silence ou finirait sans PR.
        _dossier_approuve(service.home_root, config),
        _gh_app(service.home_root, exec_),
        _pr_ouverte(service.home_root, tuple(requested), exec_),
    ]
    modele, model = _modele(read, _route_of(plan, task.id), task)
    bad = [v for v in verifications if v["verdict"] in _NOT_READY]
    pret = not bad
    raison = "toutes les vérifications passent" if pret else f"{bad[0]['libelle']} : {bad[0]['detail']}"
    payload = {
        "task_id": task.id,
        "titre": task.title,
        "statut": task.status.value,
        "modele": modele,
        "worktree": _worktree(service, task.id),
        "verifications": verifications,
        "commande": _commande(read, task.id, model),
        "pret": pret,
        "raison": raison,
    }
    payload["empreinte"] = plan_fingerprint(payload)
    return Preflight(payload=payload, plan=plan, service=service, policy=read, task=task)
