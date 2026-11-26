"""Le pilotage de l'orchestrateur depuis le cockpit : pause, reprise, disjoncteur, et l'état qui les explique.

Jusqu'ici, la pause (``drain`` ou ``hold``), la reprise et le disjoncteur ne se réglaient qu'en ligne de commande
(``grimoire orchestrate pause|resume|breaker``). Ce module en fait des gestes du cockpit, sans rien ajouter aux
règles : il appelle ``write_pause``, ``clear_pause``, ``trip`` et ``reset`` tels qu'ils sont.

**Lecture** (``GET pilotage``) : l'état d'un projet, ou de tous (``scope=portfolio``). Que des lectures de fichiers
(drapeaux de pause, disjoncteur, unité du minuteur, politique) ; la seule lecture du ledger, le compte des
lancements du jour, est gardée 10 s. Aucun sous-processus : le minuteur est « posé » si son fichier existe, le kit
ne vérifie pas ici qu'il est activé, et il ne consigne pas le dernier tick (la réponse le dit : ``None``).

**Gestes** (``POST pilotage/...``), tous des gestes d'humain, dans cet ordre de refus :

1. le projet est nommé et le corps le confirme (``refuse_foreign_body``) ;
2. un texte issu d'une dictée est refusé (seul le clic tranche) ;
3. la clé de contrôle (``~/.grimoire/control``) est exigée, comme pour le pilote automatique : le jeton de session
   servi sur la loopback ne suffit pas. Absente ou fausse : 403, rien n'est écrit ;
4. une pause déjà en place n'est jamais écrasée sans ``replace: true`` (le cockpit l'envoie après avoir nommé l'auteur) ;
5. l'événement ``pilotage`` est consigné au journal d'autorité, chaîné par hash. Un geste qui arrête (pause, hold,
   ouverture du disjoncteur) ne se bloque pas sur un journal illisible : il s'exécute et dit ``journaled: false``. Un
   geste qui rend l'autonomie (reprise, fermeture) exige un journal lisible avant d'agir.

Aucun outil MCP ni commande de la Console n'expose ces gestes (``tests/unit/tools/test_workspace_pilotage.py``).
Les réglages des rôles (``wip``, plafonds, activation) et la cadence du minuteur ne sont que LUS ici.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

__all__ = ["COCKPIT_ACTOR", "GET_ROUTES", "POST_ROUTES", "authority_view", "clear_cache", "pilotage_state"]

PREFIX = "/api/workspace/"
#: Ce que le cockpit inscrit comme ``actor`` du drapeau qu'il pose : ce qui distingue sa pause de celle d'autrui.
COCKPIT_ACTOR = "cockpit"
_USAGE_RULE_PREFIX = "usage-rule:"
_REASON_MAX = 200
_COUNT_TTL_S = 10.0
_COUNT_MAX_ENTRIES = 256
_INTERVAL_LINE = re.compile(r"^OnUnitActiveSec=(\d+)min\s*$", re.MULTILINE)
DICTATION_REFUSAL = "une pause ou une reprise ne se dicte pas : elle se confirme par un clic à l'écran"

#: ``(racine, jour) → (instant, nombre)`` : le compte des lancements du jour, seule lecture du ledger.
_COUNTS: dict[tuple[str, str], tuple[float, int | None]] = {}


def clear_cache() -> None:
    """Oublie les comptes gardés (tests)."""
    _COUNTS.clear()


# ── Lecture ────────────────────────────────────────────────────────────────────


def _author(flag: Any) -> str:
    """Qui a posé la pause, en clair : la règle d'usage, le cockpit, une session d'agent, ou l'acteur noté."""
    if flag.unreadable:
        return "un drapeau illisible (traité comme une pause)"
    actor = flag.actor or ""
    if actor.startswith(_USAGE_RULE_PREFIX):
        return f"la règle d'usage {actor[len(_USAGE_RULE_PREFIX):]}"
    if actor == COCKPIT_ACTOR:
        return "le cockpit"
    if flag.origin == "agent":
        return f"une session d'agent ({actor})" if actor else "une session d'agent"
    return f"{actor} (ligne de commande)" if actor else "auteur non noté"


def _clean(value: object, limit: int = _REASON_MAX) -> str:
    """Un texte sur une ligne, sans contrôle ni caractère invisible, borné : tout ce qu'un agent a pu écrire passe ici."""
    from grimoire.missions.requirement_gate import safe_text

    text = " ".join(safe_text(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _flag_view(flag: Any) -> dict[str, Any]:
    foreign = (flag.actor or "") != COCKPIT_ACTOR
    rule = (flag.actor or "").startswith(_USAGE_RULE_PREFIX)
    author = _clean(_author(flag), 80)
    notice = f"Pause posée par {author} levée."
    if rule:
        notice += " La règle peut la reposer si son seuil est encore dépassé."
    raw = flag.to_dict()
    # L'identifiant de session de l'agent ne sort pas ; l'interface n'a besoin que de savoir qu'il y en a une.
    raw["has_session"] = bool(raw.pop("session", ""))
    raw["actor"], raw["reason"], raw["unreadable"] = _clean(raw["actor"], 80), _clean(raw["reason"]), _clean(raw["unreadable"])
    return {**raw, "author": author, "foreign": foreign, "usage_rule": rule, "lift_notice": notice}


def _breaker_view(root: Path) -> dict[str, Any]:
    from grimoire.missions.breaker import read_breaker
    from grimoire.missions.orchestrator_policy import DEFAULT_BREAKER_THRESHOLD, load_orchestrator_policy

    state = read_breaker(root)
    read = load_orchestrator_policy(root)
    threshold = read.policy.breaker_threshold if read.policy is not None else DEFAULT_BREAKER_THRESHOLD
    return {
        "open": state.open, "failures": state.failures, "threshold": threshold, "reason": _clean(state.reason),
        "source": _clean(state.source, 40), "opened_at": state.opened_at, "transients": state.transients,
        "history": [{str(k): _clean(v) for k, v in h.items()} for h in state.history[-5:]],
        "describe": _clean(state.describe(), 400),
    }


def _timer_view(root: Path) -> dict[str, Any]:
    """Le minuteur du tick : posé ou non, sa période. Le kit ne consigne pas le dernier tick : ``None``."""
    from grimoire.missions.timer import _unit_name, unit_dir

    path = unit_dir() / f"{_unit_name(root)}.timer"
    interval: int | None = None
    installed = path.is_file()
    if installed:
        try:
            found = _INTERVAL_LINE.search(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            found = None
        interval = int(found.group(1)) if found else None
    return {
        "installed": installed, "interval_minutes": interval, "last_tick": None, "last_result": None,
        "note": "le kit ne consigne pas le dernier tick ; l'activation du minuteur n'est pas vérifiée ici",
    }


def _launched_today(root: Path, now: datetime) -> int | None:
    """Les lancements de worker du jour (UTC), lus au ledger et gardés 10 s ; ``None`` : inconnu."""
    day = now.astimezone(UTC).date().isoformat()
    key = (str(root), day)
    hit = _COUNTS.get(key)
    if hit is not None and time.monotonic() - hit[0] < _COUNT_TTL_S:
        return hit[1]
    count: int | None
    try:
        from grimoire.missions.launcher import LAUNCH_FAILED_EVENT, LAUNCHED_EVENT
        from grimoire.missions.service import TaskService

        service = TaskService(root)
        count = 0 if not service.has_ledger else sum(
            1 for e in service.ledger.list_events()
            if e.event_type in (LAUNCHED_EVENT, LAUNCH_FAILED_EVENT) and e.created_at[:10] == day
        )
    except Exception:  # un ledger illisible se dit « inconnu », jamais un zéro inventé
        count = None
    if len(_COUNTS) >= _COUNT_MAX_ENTRIES:
        _COUNTS.clear()
    _COUNTS[key] = (time.monotonic(), count)
    return count


def _roles_view(root: Path, now: datetime) -> dict[str, Any]:
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy

    read = load_orchestrator_policy(root)
    if read.policy is None:
        return {"error": read.error, "source": read.source, "roles": {}}
    policy = read.policy
    worker, reviewer, merger = policy.worker, policy.reviewer, policy.merger
    arbiter, orchestrator = policy.arbiter, policy.orchestrator
    roles = {
        "worker": {"enabled": worker.enabled, "wip": worker.wip, "sessions_per_day": worker.sessions_per_day,
                   "sessions_today": _launched_today(root, now)},
        "reviewer": {"enabled": reviewer.enabled, "wip": reviewer.wip, "sessions_per_day": None, "sessions_today": None},
        "merger": {"enabled": merger.enabled, "wip": None, "sessions_per_day": None, "sessions_today": None},
        "arbiter": {"enabled": arbiter.enabled, "wip": None, "sessions_per_day": arbiter.sessions_per_day,
                    "sessions_today": None},
        "orchestrator": {"enabled": orchestrator.enabled, "wip": orchestrator.wip,
                         "sessions_per_day": orchestrator.sessions_per_day, "sessions_today": None},
    }
    return {"error": "", "source": read.source, "roles": roles}


def _sentence(pause: dict[str, Any], breaker: dict[str, Any]) -> tuple[str, str]:
    """``(état, phrase)`` : ``arret`` (hold), ``pause`` (drain), ``disjoncteur``, ``actif``."""
    flags = [f for f in (pause["machine"], pause["project"]) if f is not None]
    parts: list[str] = []
    mode = pause["mode"]
    if flags:
        flag = flags[0]
        where = "toute la machine" if flag["scope"] == "global" else "ce projet"
        what = "Arrêté net" if mode == "hold" else "En pause (les sessions en cours finissent)"
        parts.append(f"{what} pour {where}, posée par {flag['author']}" + (f" : {flag['reason']}" if flag["reason"] else ""))
    if breaker["open"]:
        parts.append(f"Disjoncteur ouvert : {breaker['reason']}")
    if not parts:
        return "actif", "Actif : l'orchestrateur admet normalement."
    state = "arret" if mode == "hold" else "pause" if flags else "disjoncteur"
    return state, " · ".join(parts)


def pilotage_state(
    project_root: Path, *, now: datetime | None = None, control_home: Path | None = None, roles: bool = True
) -> dict[str, Any]:
    """L'état de pilotage d'un projet. Lecture pure ; ``roles=False`` saute le ledger (lignes de la Flotte)."""
    from grimoire.missions.pause import SCOPE_GLOBAL, SCOPE_PROJECT, home_root, read_pause
    from grimoire.tools.project_registry import slug_for_path

    home = home_root(project_root)
    moment = now or datetime.now(UTC)
    state = read_pause(home, control_home=control_home)
    flags = {f.scope: f for f in state.flags}
    machine, mine = flags.get(SCOPE_GLOBAL), flags.get(SCOPE_PROJECT)
    pause = {
        "project": _flag_view(mine) if mine is not None else None,
        "machine": _flag_view(machine) if machine is not None else None,
        "mode": state.mode_for(None), "active": state.active,
    }
    breaker = _breaker_view(home)
    kind, sentence = _sentence(pause, breaker)
    view: dict[str, Any] = {
        "project": slug_for_path(home) or "", "state": kind, "sentence": sentence, "pause": pause,
        "breaker": breaker, "timer": _timer_view(home), "ts": moment.isoformat(timespec="seconds"),
    }
    if roles:
        view["roles"] = _roles_view(home, moment)
    return view


class _ReadOnceJournal:
    """Le journal d'autorité lu UNE fois (chaîne et tête vérifiées) puis partagé entre les lignes de la vue globale.

    Seul ``read`` est mis en cache — y compris un échec, relancé tel quel à chaque ligne : le journal est partagé par
    tous les projets de la machine, le relire par ligne coûterait N lectures complètes à chaque rafraîchissement.
    Les marqueurs de révocation restent lus au fichier (``__getattr__``). Lecture seule : aucune ligne n'est écrite.
    """

    def __init__(self) -> None:
        from grimoire.missions.mandate import AuthorityJournal

        self._real = AuthorityJournal()
        self._entries: list[dict[str, Any]] | None = None
        self._error: Exception | None = None
        self.reads = 0

    def read(self) -> list[dict[str, Any]]:
        if self._entries is None and self._error is None:
            self.reads += 1
            try:
                self._entries = self._real.read()
            except Exception as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        return list(self._entries or [])

    def append(self, *_a: Any, **_k: Any) -> Any:
        raise PermissionError("lecture seule : un GET n'écrit jamais dans le journal d'autorité")

    def mark_revoked(self, *_a: Any, **_k: Any) -> Any:
        raise PermissionError("lecture seule : un GET n'écrit jamais dans le journal d'autorité")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


_UNREADABLE = "lecture du journal d'autorité impossible"


def authority_view(root: Path, journal: Any = None) -> dict[str, Any]:
    """Le pilote automatique et le mandat d'un projet, pour une ligne de la vue globale. LECTURE SEULE, vraiment.

    Rien de ce que le mandat porte (gestes, types, plafond) ne sort : l'interface a besoin de savoir s'il est actif,
    jusqu'à quand, et pourquoi il ne l'est pas (un code, jamais le texte d'un motif ni un chemin). Un journal
    illisible se dit (``error``), jamais un « coupé » inventé. Le magasin du mandat révoque d'ordinaire à l'échéance
    (``state()`` écrit) : ici l'état est calculé SANS révocation automatique et le journal refuse toute écriture ; un
    mandat échu s'affiche « expired » sans que le GET n'inscrive quoi que ce soit.
    """
    from grimoire.missions import mandate as md
    from grimoire.missions.autopilot import AutopilotStore

    try:
        store = AutopilotStore(root, cast("Any", journal if journal is not None else _ReadOnceJournal()))
        mandates = store.mandates
        mandates.state = lambda now=None: mandates._state(now or md._now(), auto_revoke=False)  # type: ignore[method-assign]
        mandate = mandates.state()
        pilot = store.state()
    except Exception as exc:
        return {"error": f"{_UNREADABLE} ({type(exc).__name__})", "autopilot": None, "mandate": None}
    if mandate.reason == md.JOURNAL_UNREADABLE or pilot.reason.startswith("journal d'autorité illisible"):
        return {"error": _UNREADABLE, "autopilot": None, "mandate": None}
    return {
        "error": "",
        "autopilot": {"active": pilot.active, "since": pilot.since},
        "mandate": {
            "active": mandate.active, "until": mandate.mandate.until if mandate.mandate else "",
            "reason": _clean(mandate.reason.split(":", 1)[0], 40),
        },
    }


def portfolio_view(*, control_home: Path | None = None) -> dict[str, Any]:
    """Une ligne par projet du registre (sans le ledger) et la pause de la machine."""
    from grimoire.missions.pause import SCOPE_GLOBAL, global_pause_path, read_flag
    from grimoire.tools.project_registry import load_registry

    path = global_pause_path(control_home)
    machine = read_flag(path, SCOPE_GLOBAL) if path is not None else None
    rows: list[dict[str, Any]] = []
    journal = _ReadOnceJournal()  # une seule lecture du journal d'autorité pour toutes les lignes
    for entry in load_registry():
        root = Path(str(entry.get("path", "")))
        if not root.is_dir():
            continue
        row = pilotage_state(root, control_home=control_home, roles=False)
        rows.append({
            "slug": str(entry.get("slug", "")), "name": str(entry.get("name") or entry.get("slug", "")), **row,
            "authority": authority_view(root, journal),
        })
    return {"machine": _flag_view(machine) if machine is not None else None, "projects": rows}


def _get(project_root: Path, query: dict[str, list[str]]) -> Any:
    """``GET /api/workspace/pilotage[?scope=portfolio]`` — lecture seule."""
    if (query.get("scope") or [""])[0] == "portfolio":
        return portfolio_view()
    return pilotage_state(project_root)


# ── Gestes ─────────────────────────────────────────────────────────────────────


def _gate(project_root: Path, body: dict[str, Any], what: str) -> None:
    """Les refus communs, dans l'ordre : projet confirmé, pas de dictée, clé de contrôle (403, rien d'écrit)."""
    from grimoire.missions.mandate import verify_control_key
    from grimoire.tools.project_registry import refuse_foreign_body
    from grimoire.voice.dictation import sent_as_dictation

    refuse_foreign_body(project_root, body)
    if sent_as_dictation(project_root, body):
        raise ValueError(DICTATION_REFUSAL)
    if not verify_control_key(body.get("control_key")):
        raise PermissionError(f"clé de contrôle absente ou invalide : {what} est un geste d'humain")


def _journal_readable() -> bool:
    from grimoire.missions.mandate import AuthorityJournal

    try:
        AuthorityJournal().read()
    except Exception:
        return False
    return True


def _journal(project_root: Path, gesture: str, **fields: Any) -> bool:
    """L'événement gouverné ``pilotage`` au journal d'autorité ; ``False`` si le journal refuse.

    Un geste de machine (``scope: machine``) ne porte pas de projet : le projet qui l'a ancré est ``via_project``.
    """
    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.pause import home_root
    from grimoire.missions.schemas import ORIGIN_HUMAN

    anchor = str(home_root(project_root))
    where = {"project": "", "via_project": anchor} if fields.get("scope") == "machine" else {"project": anchor}
    try:
        AuthorityJournal().append({
            "type": "pilotage", **where, "gesture": gesture, "by": ORIGIN_HUMAN,
            "via": "cockpit", "ts": datetime.now(UTC).isoformat(), **fields,
        })
    except Exception:  # un journal rompu ne doit pas empêcher d'ARRÊTER ; l'appelant le dit
        return False
    return True


def _need_readable_journal(command: str) -> None:
    """Rendre l'autonomie exige un journal lisible. La ligne de commande ne passe pas par lui : elle reste possible."""
    if not _journal_readable():
        raise ValueError(
            "journal d'autorité illisible : rendre l'autonomie est refusé ici tant qu'il n'est pas réparé ; "
            f"`{command}` en ligne de commande ne dépend pas de ce journal et reste possible"
        )


def _reason(body: dict[str, Any]) -> str:
    return _clean(body.get("reason") or "")


def _described(flag: Any) -> dict[str, Any]:
    """Ce qu'une pause était, pour l'événement : auteur, origine, mode, motif, date (jamais l'identifiant de session)."""
    view = _flag_view(flag)
    return {
        "author": view["author"], "actor": view["actor"], "origin": _clean(flag.origin, 20), "mode": flag.mode,
        "reason": view["reason"], "since": flag.since, "had_session": view["has_session"],
    }


def _stops_at_least(existing: Any, mode: str) -> bool:
    """Vrai si poser *mode* arrête au moins autant que *existing* : rien avant, une pause plus étroite, ou un durcissement."""
    if existing is None or existing.tasks:
        return True
    return mode == "hold" and existing.mode != "hold"


def _refuse_if_paused(existing: Any, body: dict[str, Any]) -> None:
    """Une pause en place n'est jamais écrasée sans ``replace: true`` : on dit qui l'a posée et pourquoi."""
    if existing is not None and body.get("replace") is not True:
        view = _flag_view(existing)
        why = f" ({view['reason']})" if view["reason"] else ""
        raise ValueError(
            f"une pause {existing.mode} est déjà en place, posée par {view['author']}{why} : "
            "« Reprendre » la lève ; la remplacer demande un geste explicite (replace)"
        )


def _outcome(project_root: Path, journaled: bool, **extra: Any) -> dict[str, Any]:
    return {**extra, "journaled": journaled, "pilotage": pilotage_state(project_root)}


def _pause_at(project_root: Path, body: dict[str, Any], scope: str) -> Any:
    from grimoire.missions.pause import (
        DRAIN,
        HOLD,
        MODES,
        PauseError,
        global_pause_path,
        home_root,
        project_pause_path,
        read_flag,
        write_pause,
    )
    from grimoire.missions.schemas import ORIGIN_HUMAN

    machine = scope == "global"
    _gate(project_root, body, "pauser toute la machine" if machine else "pauser un projet")
    mode = body.get("mode")
    if mode not in MODES:
        raise ValueError(f"`mode` requis : {DRAIN} (laisser finir) ou {HOLD} (arrêter net)")
    if machine and body.get("confirm") is not True:
        raise ValueError("pause de toute la machine : `confirm: true` requis (l'interface nomme ce qui sera arrêté)")
    path = global_pause_path() if machine else project_pause_path(project_root)
    existing = read_flag(path, scope) if path is not None else None
    _refuse_if_paused(existing, body)
    if not _stops_at_least(existing, str(mode)) and not _journal_readable():
        raise ValueError(
            "journal d'autorité illisible : sans lui, seul un geste qui arrête au moins autant qu'avant passe "
            "(poser une pause, durcir, ouvrir le disjoncteur) ; remplacer ou affaiblir une pause est refusé ici, "
            "`grimoire orchestrate pause` / `resume` en ligne de commande ne dépendent pas de ce journal"
        )
    reason = _reason(body)
    try:
        flag = write_pause(
            home_root(project_root), mode=str(mode), scope=scope, actor=COCKPIT_ACTOR, reason=reason,
            origin=ORIGIN_HUMAN,
        )
    except PauseError as exc:
        raise ValueError(str(exc)) from exc
    if flag.session and path is not None:
        # `write_pause` garde la session du drapeau remplacé : un drapeau humain ne porte aucune session d'agent.
        # Le drapeau ne quitte jamais l'état « en pause » pendant la réécriture (même fichier, écriture atomique).
        from grimoire.core.fsio import atomic_write
        from grimoire.missions.pause import SCHEMA

        payload = {
            "schema": SCHEMA, "mode": flag.mode, "tasks": list(flag.tasks), "since": flag.since, "actor": flag.actor,
            "reason": flag.reason, "origin": flag.origin, "session": "",
        }
        atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    extra = {"replaced": _described(existing)} if existing is not None else {}
    journaled = _journal(
        project_root, "pause", scope="machine" if machine else "project", mode=flag.mode, reason=reason, **extra
    )
    return _outcome(project_root, journaled, paused=_flag_view(flag))


def pause_project(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/pause`` — « Mettre en pause » (``drain``) ou « Arrêter net » (``hold``) pour ce projet."""
    return _pause_at(project_root, body, "project")


def pause_machine(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/machine/pause`` — la pause de TOUS les projets de la machine, avec ``confirm: true``."""
    return _pause_at(project_root, body, "global")


def _resume_at(project_root: Path, body: dict[str, Any], scope: str) -> Any:
    from grimoire.missions.pause import PauseError, clear_pause, home_root
    from grimoire.missions.schemas import ORIGIN_HUMAN

    _gate(project_root, body, "reprendre")
    _need_readable_journal("grimoire orchestrate resume" + (" --global" if scope == "global" else ""))
    try:
        lifted = clear_pause(home_root(project_root), scope=scope, origin=ORIGIN_HUMAN)
    except PauseError as exc:
        raise ValueError(str(exc)) from exc
    if lifted is None:
        return _outcome(project_root, True, lifted=None, notice="Aucune pause à lever.")
    view = _flag_view(lifted)
    journaled = _journal(
        project_root, "resume", scope="machine" if scope == "global" else "project", mode=lifted.mode,
        lifted=_described(lifted),
    )
    return _outcome(project_root, journaled, lifted=view, notice=view["lift_notice"])


def resume_project(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/resume`` — « Reprendre » : lève la pause du projet, quel qu'en soit l'auteur, et le dit."""
    return _resume_at(project_root, body, "project")


def resume_machine(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/machine/resume`` — lève la pause de toute la machine."""
    return _resume_at(project_root, body, "global")


def breaker_reset(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/breaker/reset`` — « Refermer » : le geste humain que le disjoncteur ouvert attend."""
    from grimoire.missions.breaker import read_breaker, reset
    from grimoire.missions.pause import home_root

    _gate(project_root, body, "refermer le disjoncteur")
    home = home_root(project_root)
    before = read_breaker(home)
    if not before.open:
        raise ValueError("le disjoncteur est déjà fermé")
    _need_readable_journal("grimoire orchestrate breaker reset")
    reset(home, actor=COCKPIT_ACTOR)
    journaled = _journal(project_root, "breaker_reset", was_reason=before.reason, was_source=before.source)
    return _outcome(project_root, journaled, closed=True, was=before.reason)


def breaker_trip(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST pilotage/breaker/trip`` — « Ouvrir » : plus aucun lancement jusqu'à « Refermer »."""
    from grimoire.missions.breaker import read_breaker, trip
    from grimoire.missions.pause import home_root

    _gate(project_root, body, "ouvrir le disjoncteur")
    home = home_root(project_root)
    reason = _reason(body)
    if not reason:
        raise ValueError("`reason` requis : pourquoi on ouvre le disjoncteur")
    if read_breaker(home).open:
        raise ValueError("le disjoncteur est déjà ouvert")
    trip(home, reason, source="manual")
    journaled = _journal(project_root, "breaker_trip", reason=reason)
    return _outcome(project_root, journaled, opened=True)


GET_ROUTES = {f"{PREFIX}pilotage": _get}

POST_ROUTES = {
    f"{PREFIX}pilotage/pause": pause_project,
    f"{PREFIX}pilotage/resume": resume_project,
    f"{PREFIX}pilotage/machine/pause": pause_machine,
    f"{PREFIX}pilotage/machine/resume": resume_machine,
    f"{PREFIX}pilotage/breaker/reset": breaker_reset,
    f"{PREFIX}pilotage/breaker/trip": breaker_trip,
}
