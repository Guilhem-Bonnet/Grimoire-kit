"""Plancher de profil committé et abaissement de session expirant (W2-07).

Avant ce module, le profil du standard n'avait qu'une source : le champ
``profile:`` de ``standard-profile.yaml``. Le baisser d'un cran relâchait en
une écriture tous les seuils qui en dépendent (clôture refusée, garde
d'autorité, profil de risque des règles), et la seule défense était une
question posée par :mod:`.tool_policy` sur l'outil ``Write``/``Edit`` : une
réponse « oui » abaissait le profil pour toujours, et un abaissement que la
question ne voit pas (script, ``git checkout``, édition hors hôte) passait.

Deux axes, indépendants :

- **Le plancher.** ``_grimoire/standard/profile-floor.yaml`` (``floor: governed``),
  committé avec le projet (:func:`grimoire.core.standard_state.profile_floor_id`).
  Un profil déclaré sous le plancher est ignoré : le plancher s'applique et
  chaque ``Stop`` ou ``UserPromptSubmit`` qui le subit trace un
  ``policy.floor_applied`` dans le journal de garde de la tâche (compté, pas
  répété ligne à ligne).
- **L'abaissement de session.** L'humain tape, comme message entier,
  ``Lower Profile <profil>: <raison>``. La phrase n'est reçue que par
  ``UserPromptSubmit`` d'un hôte nommé, hors ``GRIMOIRE_UNATTENDED=1`` (même
  origine ``user`` que W2-01 et W2-06). La ligne ``policy.profile_lowered`` est
  écrite avant tout effet ; si l'écriture échoue, rien n'est abaissé. Elle ne
  vaut que pour le ``session_id`` qui l'a reçue, pendant :data:`LOWERING_TTL`
  au plus : la session suivante retrouve le plancher. Le plancher n'est pas
  modifié, jamais.

Le journal ``<runs>/profile-lowerings.authority.jsonl`` porte le suffixe que
:mod:`.authority_guard` protège : Write, Edit ou Bash qui l'écrit sont refusés
quel que soit le profil.

Limite (principe 7) : un agent qui exécute du code arbitraire peut écrire ce
journal par une voie que la garde de chemin ne prévoit pas, et un hôte qui
change de ``session_id`` à la reprise perd l'abaissement (le plancher revient :
c'est le côté sûr). La trace rend l'abaissement visible et coûteux à falsifier,
pas impossible.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.standard_generation import PROFILE_LADDER, RUNS_DIR
from grimoire.core.standard_state import (
    active_profile_id,
    declared_profile_id,
    profile_below_floor,
    profile_floor_id,
)
from grimoire.hosts.decisions._shared import HookInput
from grimoire.hosts.events import HookEvent

__all__ = [
    "FLOOR_APPLIED_EVENT",
    "LOWERED_EVENT",
    "LOWERING_FILENAME",
    "LOWERING_TTL",
    "lowering_relpath",
    "record_lowering_request",
    "session_profile",
]

FLOOR_APPLIED_EVENT = "policy.floor_applied"
LOWERED_EVENT = "policy.profile_lowered"
#: Suffixe ``.authority.jsonl`` : celui que :data:`.authority_guard.AUTHORITY_SUFFIX`
#: protège (recopié pour que ``UserPromptSubmit`` n'importe pas la garde ; un test l'épingle).
LOWERING_FILENAME = "profile-lowerings.authority.jsonl"
#: Même une session qui dure : l'abaissement ne survit pas à ce délai.
LOWERING_TTL = timedelta(hours=12)

#: La phrase, message entier : un texte collé qui la cite au milieu ne change rien.
_PHRASE = re.compile(r"\ALower Profile ([A-Za-z0-9_-]+)\s*:\s*(\S.*)\Z", re.DOTALL)
_REASON_MAX = 240
_LIMIT = (
    "Limite : un agent qui exécute du code arbitraire peut écrire ce journal par une voie non prévue ; "
    "la trace rend l'abaissement visible et coûteux à falsifier, pas impossible."
)


def lowering_relpath() -> Path:
    return RUNS_DIR / LOWERING_FILENAME


def _now() -> datetime:
    return datetime.now(UTC)


def _read_lowerings(project_root: Path) -> list[dict[str, Any]]:
    """Les lignes ``policy.profile_lowered`` ; ``[]`` si le journal est absent ou illisible (aucun abaissement)."""
    try:
        raw = (project_root / lowering_relpath()).read_text(encoding="utf-8", errors="replace")
    except OSError:  # silent-ok — illisible = aucun abaissement : le plancher reste (fail-closed)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:  # silent-ok — ligne coupée : ignorée, elle n'abaisse rien
            continue
        if isinstance(data, dict) and data.get("type") == LOWERED_EVENT:
            out.append(data)
    return out


def _alive(entry: dict[str, Any], now: datetime) -> bool:
    try:
        granted = datetime.fromisoformat(str(entry.get("ts", "")))
    except ValueError:  # silent-ok — horodatage illisible : l'abaissement n'est pas daté, donc pas valable
        return False
    if granted.tzinfo is None:
        return False
    return granted <= now < granted + LOWERING_TTL


def _session_lowering(hook: HookInput) -> str:
    """Le profil auquel *hook*.session_id a été abaissé, ``""`` sans abaissement valable."""
    if not hook.session_id.strip():
        return ""
    now = _now()
    for entry in reversed(_read_lowerings(hook.project_root)):
        if entry.get("session_id") == hook.session_id and _alive(entry, now):
            profile = entry.get("profile")
            return profile if isinstance(profile, str) and profile in PROFILE_LADDER else ""
    return ""


def _trace_floor_applied(hook: HookInput, declared: str, floor: str) -> None:
    """``policy.floor_applied`` dans le journal de garde de la tâche active. Best-effort, jamais bloquant."""
    try:
        from grimoire.core.standard_checks.evidence_journal import append_guard_event
        from grimoire.core.standard_state import resolve_active_task

        task_id = resolve_active_task(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id).task_id
        append_guard_event(
            hook.project_root,
            task_id,
            {
                "ts": _now().isoformat(),
                "type": FLOOR_APPLIED_EVENT,
                "declared": declared,
                "floor": floor,
                "session_id": hook.session_id,
            },
        )
    except Exception:  # silent-ok — la trace est un témoin ; la décision (plancher appliqué) est déjà prise
        return


def session_profile(hook: HookInput, *, journal: bool = True) -> str:
    """Le profil qui s'applique à la session de *hook* : plancher compris, abaissement de session compris.

    *journal* ``False`` pour un appel à chaque outil (``PreToolUse``) : le
    plancher s'applique alors sans écrire de ligne. Ne lève jamais.
    """
    root = hook.project_root
    lowered = _session_lowering(hook)
    if lowered:
        return lowered
    effective = active_profile_id(root)
    if journal:
        declared = declared_profile_id(root)
        floor = profile_floor_id(root)
        if profile_below_floor(declared, floor):
            _trace_floor_applied(hook, declared, floor)
    return effective


def _append(project_root: Path, entry: dict[str, Any]) -> None:
    """Ajoute *entry* sous verrou. Lève en cas d'échec : l'appelant n'abaisse rien sans la ligne."""
    from grimoire.core.fsio import locked

    path = project_root / lowering_relpath()
    with locked(path), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _refused(why: str) -> tuple[str, dict[str, Any]]:
    return f"[Grimoire] Abaissement refusé : {why}. Le profil ne change pas.", {"profile_lowering": {"status": "refused"}}


def record_lowering_request(hook: HookInput) -> tuple[str, dict[str, Any]]:
    """``UserPromptSubmit`` : journalise ``policy.profile_lowered`` si le prompt est la phrase.

    Rend ``(contexte, détail)`` ; ``("", {})`` quand le prompt n'est pas la phrase.
    """
    match = _PHRASE.match(hook.prompt.strip())
    if hook.event is not HookEvent.USER_PROMPT_SUBMIT or match is None:
        return "", {}
    from grimoire.hosts.decisions.human_turns import ORIGIN_USER, turn_origin

    target, raw_reason = match.groups()
    if turn_origin(hook) != ORIGIN_USER or not hook.session_id.strip():
        return _refused("la phrase ne vient pas d'un tour humain transmis par un hôte nommé (ou GRIMOIRE_UNATTENDED=1)")
    if target not in PROFILE_LADDER:
        return _refused(f"profil inconnu {target!r} (attendu : {', '.join(PROFILE_LADDER)})")
    current = _session_lowering(hook) or active_profile_id(hook.project_root)
    if PROFILE_LADDER.index(target) >= (PROFILE_LADDER.index(current) if current in PROFILE_LADDER else -1):
        return _refused(f"« {target} » n'est pas plus léger que le profil actuel « {current} »")
    entry = {
        "type": LOWERED_EVENT,
        "lowering_id": f"LOW-{uuid.uuid4().hex[:12]}",
        "session_id": hook.session_id,
        "from": current,
        "profile": target,
        "floor": profile_floor_id(hook.project_root),
        "reason": " ".join(raw_reason.split())[:_REASON_MAX],
        "prompt_sha256": hashlib.sha256(hook.prompt.encode("utf-8", "surrogatepass")).hexdigest(),
        "host": hook.host,
        "ts": _now().isoformat(),
    }
    try:
        _append(hook.project_root, entry)
    except Exception as exc:
        return _refused(f"le journal {lowering_relpath().as_posix()} n'a pas pu être écrit ({type(exc).__name__})")
    context = (
        f"[Grimoire] Profil abaissé de « {current} » à « {target} » pour cette session seulement "
        f"({entry['lowering_id']}, raison : {entry['reason']}). Il disparaît avec la session ou après "
        f"{int(LOWERING_TTL.total_seconds() // 3600)} h ; le plancher committé n'est pas modifié. {_LIMIT}"
    )
    return context, {
        "profile_lowering": {"status": "granted", "lowering_id": entry["lowering_id"], "profile": target}
    }
