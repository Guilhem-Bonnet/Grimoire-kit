"""Premier lancement (P45) : « Lancer » exécute le plan du guide, étape par étape.

Le guide de P36 dit ce qui serait fait ; ce module le fait, **uniquement** par
les actions typées de P40 (:mod:`grimoire.tools.workspace_actions` : plan puis
apply, jamais une commande libre), puis vérifie le résultat.

Étapes d'un lancement (selon le profil mémoire choisi) :

* ``stack`` — ``stack.start`` de la pile du profil (graphe, complet), sautable :
  on peut démarrer ses services soi-même ;
* ``compose`` — ``memory.compose`` : le bloc ``memory:`` de
  ``project-context.yaml``, restaurable ;
* ``verify.memory`` — aller-retour mémoire réel : écrire un souvenir de test,
  le retrouver, le retirer, et relire pour s'assurer qu'il est parti ;
* ``verify.doctor`` — ``grimoire doctor``, sautable (un avertissement n'est pas
  une panne).

Un échec est traduit (ce qui s'est passé, le remède) ; ``retry`` rejoue
l'étape, ``skip`` saute une étape non bloquante, ``rollback`` défait ce qui a été
appliqué, en commençant par la dernière. L'avancement est écrit à chaque
transition dans ``_grimoire-runtime-output/setup/launch.json`` : une étape que
le rechargement a trouvée « en cours » est dite interrompue et reprise (replanifiée)
là où le lancement s'était arrêté.

Un projet sans ``project-context.yaml`` (S0) n'a pas d'action typée d'initialisation
au catalogue de P40 : l'étape ``compose`` est bloquée et dit la commande à copier.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.tools import workspace_actions

__all__ = ["SCHEMA_VERSION", "VERBS", "LaunchEnv", "launch_state", "launch_verb"]

SCHEMA_VERSION = "grimoire-first-launch/v1"
_STATE = ("_grimoire-runtime-output", "setup", "launch.json")

PENDING, RUNNING, DONE, FAILED, SKIPPED, ROLLED_BACK = (
    "pending",
    "running",
    "done",
    "failed",
    "skipped",
    "rolled_back",
)
#: Statuts qui ne bloquent plus la suite du lancement.
_SETTLED = frozenset({DONE, SKIPPED, ROLLED_BACK})

VERBS = ("start", "next", "retry", "skip", "rollback", "reset")

#: Pile du kit que démarre un profil (les autres n'en demandent aucune).
_PROFILE_STACK = {"graphe": "weaviate-neo4j", "complet": "weaviate-neo4j"}

#: Sondes du souvenir de test : étiquette et préfixe, pour le retrouver et le reconnaître.
_PROBE_TAG = "grimoire-setup-check"


@dataclass
class LaunchEnv:
    """Ce que le lancement demande au système, remplaçable en test."""

    actions: workspace_actions.ActionEnv = field(default_factory=workspace_actions.ActionEnv)
    manager: Callable[[Path], Any] | None = None
    doctor: Callable[[Path], dict[str, Any]] | None = None
    guide: Callable[[Path], dict[str, Any]] | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


# ── Persistance ────────────────────────────────────────────────────────────────


def _state_path(project_root: Path) -> Path:
    return project_root.resolve().joinpath(*_STATE)


def _read(project_root: Path) -> dict[str, Any] | None:
    path = _state_path(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"avancement illisible ({path.name}) : {exc} — « reset » le repart de zéro") from exc
    return data if isinstance(data, dict) and isinstance(data.get("steps"), list) else None


def _write(project_root: Path, state: dict[str, Any], env: LaunchEnv) -> None:
    state["updated"] = env.now().isoformat()
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── Traduction des échecs ──────────────────────────────────────────────────────

#: (fragment du refus ou de l'erreur, ce qui s'est passé, remède) — premier qui correspond.
_TRANSLATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "project-context.yaml absent",
        "Ce dossier n'est pas encore équipé : il n'a pas de project-context.yaml.",
        "Lance `grimoire init . -y` dans un terminal (le guide ne l'exécute pas), puis relance.",
    ),
    (
        "docker",
        "Docker ne répond pas, la pile mémoire ne peut pas démarrer.",
        "Lance Docker Desktop ou le service docker, puis « Réessayer » — ou « Sauter » et démarre les services toi-même.",
    ),
    ("pile du catalogue", "La pile demandée n'est pas au catalogue du kit.", "Choisis un autre profil mémoire."),
    (
        "compose d'un autre projet",
        "Un conteneur de cette pile appartient à un autre projet : il n'est pas touché.",
        "Utilise la pile de l'autre projet, ou choisis un profil sans pile.",
    ),
    ("expiré", "Le plan a expiré avant d'être appliqué.", "« Réessayer » en demande un nouveau."),
    (
        "l'état a changé",
        "Le projet a changé entre le plan et son application.",
        "« Réessayer » replanifie sur l'état actuel.",
    ),
    (
        "journal",
        "Le journal des actions n'est pas inscriptible : sans journal, rien ne s'applique.",
        "Vérifie les droits d'écriture sur _grimoire-runtime-output/ puis « Réessayer ».",
    ),
    (
        "Weaviate",
        "Un service mémoire n'a pas pu être configuré.",
        "Vérifie qu'il répond sur localhost, puis « Réessayer ».",
    ),
)


def translate_failure(raw: str) -> dict[str, str]:
    """Le refus brut d'une action devient une phrase et un remède ; le brut reste dit à part."""
    lowered = raw.lower()
    for fragment, message, remedy in _TRANSLATIONS:
        if fragment.lower() in lowered:
            return {"message": message, "remedy": remedy, "raw": raw}
    return {"message": "L'étape a échoué.", "remedy": "Lis le détail, corrige, puis « Réessayer ».", "raw": raw}


# ── Étapes ─────────────────────────────────────────────────────────────────────


def _step(
    step_id: str, label: str, kind: str, *, skippable: bool, action: str = "", params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": step_id,
        "label": label,
        "kind": kind,
        "action": action,
        "params": params or {},
        "skippable": skippable,
        "status": PENDING,
        "run_id": "",
        "error": None,
        "detail": "",
        "findings": [],
        "started": "",
        "finished": "",
    }


def _build_steps(profile: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    stack = _PROFILE_STACK.get(str(profile["id"]))
    if stack and profile.get("start"):
        steps.append(
            _step(
                "stack",
                "Démarrer les services mémoire",
                "action",
                skippable=True,
                action="stack.start",
                params={"stack": stack},
            )
        )
    steps.append(
        _step(
            "compose",
            f"Composer la mémoire (profil {profile['label']})",
            "action",
            skippable=False,
            action="memory.compose",
            params={"profile": profile["id"]},
        )
    )
    steps.append(_step("verify.memory", "Vérifier la mémoire (écrire, retrouver, retirer)", "verify", skippable=False))
    steps.append(_step("verify.doctor", "Lancer doctor", "verify", skippable=True))
    return steps


def _view(project_root: Path, state: dict[str, Any] | None) -> dict[str, Any]:
    """La charge utile : l'état persisté, avec les étapes interrompues et la suite possible."""
    if state is None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "exists": False,
            "steps": [],
            "finished": False,
            "projectRoot": str(project_root),
        }
    steps = state["steps"]
    interrupted = next((s["id"] for s in steps if s["status"] == RUNNING), None)
    current = next((s["id"] for s in steps if s["status"] not in _SETTLED), None)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "exists": True,
        "projectRoot": str(project_root),
        "profile": state["profile"],
        "steps": steps,
        "updated": state.get("updated", ""),
        "current": current,
        "interrupted": interrupted,
        "finished": current is None,
        "canRollback": any(s["status"] == DONE and s["kind"] == "action" for s in steps),
    }


def launch_state(project_root: Path) -> dict[str, Any]:
    """``GET setup/launch`` — l'avancement enregistré, ou « pas de lancement »."""
    return _view(project_root, _read(project_root))


# ── Exécution d'une étape ──────────────────────────────────────────────────────


def _run_action(project_root: Path, step: dict[str, Any], env: LaunchEnv) -> None:
    """plan puis apply d'une action typée ; le résultat est écrit dans *step*."""
    action = step["action"]
    plan = workspace_actions.plan_action(project_root, action, {"params": step["params"]}, env=env.actions)
    blockers = [str(b) for b in plan.get("blockers") or []]
    if blockers:
        if any("rien à changer" in b for b in blockers):
            step.update(status=DONE, detail="Déjà dans l'état voulu : rien à changer.", run_id="", error=None)
            return
        step.update(status=FAILED, error=translate_failure("; ".join(blockers)))
        return
    applied = workspace_actions.apply_action(
        project_root,
        action,
        {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]},
        env=env.actions,
    )
    run_id = str(applied.get("run_id") or plan.get("plan_id") or "")
    if applied.get("ok") is True:
        step.update(
            status=DONE, run_id=run_id, error=None, detail="; ".join(str(e) for e in applied.get("effects") or [])
        )
        return
    step.update(status=FAILED, run_id=run_id, error=translate_failure(str(applied.get("raison") or "refus sans motif")))


def _default_manager(project_root: Path) -> Any:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    config = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
    return MemoryManager.from_config(config, project_root=project_root)


def _finding(finding_id: str, label: str, state: str, detail: str, remedy: str = "") -> dict[str, str]:
    return {"id": finding_id, "label": label, "state": state, "detail": detail, "remedy": remedy}


def memory_roundtrip(project_root: Path, env: LaunchEnv) -> list[dict[str, str]]:
    """Écrire un souvenir de test, le retrouver, le retirer — sur le vrai backend du projet.

    Chaque temps est une ligne ; un écart porte son remède. Le souvenir de test est
    retiré même si la recherche l'a manqué, et relu pour confirmer qu'il est parti.
    """
    findings: list[dict[str, str]] = []
    try:
        manager = (env.manager or _default_manager)(project_root)
    except Exception as exc:  # backend absent, extra manquant, service muet
        findings.append(
            _finding(
                "open",
                "Ouvrir la mémoire",
                "bad",
                f"{type(exc).__name__} : {exc}",
                "Lance `grimoire memory status` pour voir quel backend manque, puis `grimoire memory up`.",
            )
        )
        return findings
    marker = f"verification-premier-lancement-{uuid.uuid4().hex[:12]}"
    entry_id = ""
    try:
        entry = manager.store(
            f"Souvenir de test du premier lancement {marker} (retiré aussitôt).",
            tags=(_PROBE_TAG,),
            actor="cockpit-first-launch",
            reason="vérification du premier lancement",
        )
        entry_id = str(entry.id)
        findings.append(_finding("write", "Écrire", "ok", f"souvenir {entry_id} écrit"))
    except Exception as exc:
        findings.append(
            _finding(
                "write",
                "Écrire",
                "bad",
                f"{type(exc).__name__} : {exc}",
                "La mémoire refuse l'écriture : `grimoire memory status` dit pourquoi (service arrêté, extra manquant).",
            )
        )
        return findings
    try:
        hits = manager.search(marker, limit=20)
        if any(str(getattr(h, "id", "")) == entry_id for h in hits):
            findings.append(_finding("find", "Retrouver", "ok", "retrouvé par la recherche"))
        elif manager.recall(entry_id) is not None:
            findings.append(
                _finding(
                    "find",
                    "Retrouver",
                    "warn",
                    "relu par identifiant, mais la recherche ne le remonte pas",
                    "L'index de recherche est en retard ou vide : `grimoire memory reindex`, ou installe le moteur d'embedding du profil.",
                )
            )
        else:
            findings.append(
                _finding(
                    "find",
                    "Retrouver",
                    "bad",
                    "écrit, mais introuvable ensuite",
                    "Le backend n'a pas gardé l'écriture : `grimoire memory status` et `grimoire doctor .`.",
                )
            )
    except Exception as exc:
        findings.append(
            _finding(
                "find",
                "Retrouver",
                "bad",
                f"{type(exc).__name__} : {exc}",
                "La recherche échoue : `grimoire memory status` dit quel service répond.",
            )
        )
    try:
        removed = manager.delete(entry_id, actor="cockpit-first-launch", reason="retrait du souvenir de test")
        gone = manager.recall(entry_id) is None
        if removed and gone:
            findings.append(_finding("remove", "Retirer", "ok", "souvenir de test retiré, relu absent"))
        else:
            findings.append(
                _finding(
                    "remove",
                    "Retirer",
                    "bad",
                    f"souvenir {entry_id} encore présent après le retrait",
                    f"Retire-le à la main : `grimoire memory delete {entry_id}`.",
                )
            )
    except Exception as exc:
        findings.append(
            _finding(
                "remove",
                "Retirer",
                "bad",
                f"{type(exc).__name__} : {exc} — souvenir {entry_id} resté",
                f"Retire-le à la main : `grimoire memory delete {entry_id}`.",
            )
        )
    return findings


def doctor_findings(project_root: Path, env: LaunchEnv) -> list[dict[str, str]]:
    """``grimoire doctor`` : ok, ou les lignes qui s'écartent avec la commande de réparation."""
    from grimoire.tools import workspace_exec

    view = (env.doctor or (lambda root: workspace_exec.doctor_view(root, probe=True)))(project_root)
    if view.get("timed_out"):
        return [
            _finding(
                "doctor",
                "doctor",
                "bad",
                "doctor n'a pas répondu à temps",
                "Relance `grimoire doctor .` dans un terminal.",
            )
        ]
    lines = [str(line) for line in view.get("lines") or []]
    bad = [line for line in lines if any(mark in line for mark in ("✗", "FAIL", "ERROR", "✘"))]
    warn = [line for line in lines if line not in bad and any(mark in line for mark in ("⚠", "WARN"))]
    if view.get("ok") and not bad:
        detail = (
            f"doctor est passé ({len(warn)} avertissement{'s' if len(warn) > 1 else ''})"
            if warn
            else "doctor est passé"
        )
        return [
            _finding(
                "doctor",
                "doctor",
                "warn" if warn else "ok",
                detail,
                "Voir les avertissements : `grimoire doctor .`" if warn else "",
            )
        ]
    shown = (bad or lines)[:6]
    return [
        _finding(
            "doctor",
            "doctor",
            "bad",
            " · ".join(s.strip() for s in shown) or f"code {view.get('code')}",
            "Répare ce que doctor sait réparer : `grimoire doctor . --fix`.",
        )
    ]


def _run_verify(project_root: Path, step: dict[str, Any], env: LaunchEnv) -> None:
    findings = (
        memory_roundtrip(project_root, env) if step["id"] == "verify.memory" else doctor_findings(project_root, env)
    )
    step["findings"] = findings
    gaps = [f for f in findings if f["state"] == "bad"]
    soft = [f for f in findings if f["state"] == "warn"]
    if gaps:
        step.update(
            status=FAILED,
            error={
                "message": "Écart constaté : " + " ; ".join(f"{f['label']} — {f['detail']}" for f in gaps),
                "remedy": " ".join(f["remedy"] for f in gaps if f["remedy"]),
                "raw": "",
            },
        )
    else:
        step.update(status=DONE, error=None, detail="; ".join(f["detail"] for f in findings + soft if f["detail"]))


def _execute(project_root: Path, state: dict[str, Any], step: dict[str, Any], env: LaunchEnv) -> None:
    step.update(status=RUNNING, started=env.now().isoformat(), finished="", error=None)
    _write(project_root, state, env)  # écrit avant d'agir : un arrêt ici se relit « interrompu »
    try:
        if step["kind"] == "action":
            _run_action(project_root, step, env)
        else:
            _run_verify(project_root, step, env)
    except Exception as exc:
        step.update(status=FAILED, error=translate_failure(f"{type(exc).__name__} : {exc}"))
    step["finished"] = env.now().isoformat()
    _write(project_root, state, env)


# ── Verbes ─────────────────────────────────────────────────────────────────────


def _find(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in state["steps"]:
        if step["id"] == step_id:
            return step
    raise ValueError(f"étape inconnue : {step_id!r}")


def _start(project_root: Path, body: dict[str, Any], env: LaunchEnv) -> dict[str, Any]:
    from grimoire.tools import workspace_setup_guide

    profile_id = str(body.get("profile") or "")
    guide = (env.guide or workspace_setup_guide.setup_guide)(project_root)
    profile = next((p for p in guide["profiles"] if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError(f"profil mémoire inconnu : {profile_id!r}")
    if profile.get("possible") is False:
        raise ValueError(f"profil {profile_id} impossible ici : {'; '.join(profile.get('reasons') or [])}")
    existing = _read(project_root)
    if existing is not None and any(s["status"] not in (PENDING, *_SETTLED) for s in existing["steps"]):
        raise ValueError("un lancement est en cours : « reset » l'abandonne, « next » le reprend")
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "profile": {"id": profile["id"], "label": profile["label"]},
        "created": env.now().isoformat(),
        "steps": _build_steps(profile),
    }
    _write(project_root, state, env)
    return state


def _next(project_root: Path, state: dict[str, Any], env: LaunchEnv) -> None:
    """Exécute l'étape courante : la première non réglée. Un échec ou une étape en cours l'arrête là."""
    step = next((s for s in state["steps"] if s["status"] not in _SETTLED), None)
    if step is None:
        return
    if step["status"] == FAILED:
        raise ValueError(f"l'étape « {step['id']} » a échoué : réessaie ou saute-la, sinon la suite reste arrêtée")
    # Une étape trouvée « en cours » est celle que le rechargement a interrompue : on la replanifie.
    _execute(project_root, state, step, env)


def _rollback(project_root: Path, state: dict[str, Any], body: dict[str, Any], env: LaunchEnv) -> None:
    wanted = str(body.get("step") or "")
    targets = [_find(state, wanted)] if wanted else list(reversed(state["steps"]))
    for step in targets:
        if step["status"] != DONE or step["kind"] != "action" or not step["run_id"]:
            if wanted:
                raise ValueError(f"l'étape « {step['id']} » n'a rien d'appliqué à défaire")
            continue
        result = workspace_actions.rollback_action(
            project_root, step["action"], {"run_id": step["run_id"]}, env=env.actions
        )
        if result.get("ok") is False:
            step.update(error=translate_failure(str(result.get("raison") or "retour arrière refusé")))
            _write(project_root, state, env)
            raise ValueError(step["error"]["message"] + " " + step["error"]["remedy"])
        step.update(status=ROLLED_BACK, error=None, detail="annulé : l'état d'avant est restauré")
        _write(project_root, state, env)


def launch_verb(project_root: Path, body: dict[str, Any], *, env: LaunchEnv | None = None) -> dict[str, Any]:
    """``POST setup/launch`` — ``{verb, profile?, step?}`` ; rend l'avancement après le verbe."""
    env = env or LaunchEnv()
    verb = str(body.get("verb") or "")
    if verb not in VERBS:
        raise ValueError(f"verbe inconnu : {verb!r} (attendu : {', '.join(VERBS)})")
    if verb == "reset":
        _state_path(project_root).unlink(missing_ok=True)
        return launch_state(project_root)
    if verb == "start":
        return _view(project_root, _start(project_root, body, env))
    state = _read(project_root)
    if state is None:
        raise ValueError("aucun lancement en cours : « start » avec un profil")
    if verb == "next":
        _next(project_root, state, env)
    elif verb == "retry":
        step = _find(state, str(body.get("step") or ""))
        if step["status"] not in (FAILED, RUNNING):
            raise ValueError(f"l'étape « {step['id']} » n'est pas en échec : rien à rejouer")
        _execute(project_root, state, step, env)
    elif verb == "skip":
        step = _find(state, str(body.get("step") or ""))
        if not step["skippable"]:
            raise ValueError(f"l'étape « {step['id']} » est bloquante : elle ne se saute pas")
        if step["status"] in _SETTLED:
            raise ValueError(f"l'étape « {step['id']} » est déjà réglée")
        step.update(status=SKIPPED, error=None, detail="sautée : à faire toi-même")
        _write(project_root, state, env)
    elif verb == "rollback":
        _rollback(project_root, state, body, env)
    return _view(project_root, state)
