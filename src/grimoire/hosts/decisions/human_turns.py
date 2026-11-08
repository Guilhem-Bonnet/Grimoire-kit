"""Journal des tours humains : la trace qu'un humain a parlé dans une session (W2-01).

``UserPromptSubmit`` est le seul événement d'hôte qui suit un message tapé
par l'utilisateur. Avant ce module, il nourrissait la mémoire de session
(:mod:`.session_memory`) et le contexte de tâche, mais aucun enregistrement
d'autorité : un checkpoint de flow ne pouvait pas distinguer « l'humain a
approuvé » de « l'agent a écrit ``approve`` dans un fichier JSON ».

Chaque tour devient une ligne ``{session_id, prompt_sha256, ts, origin}``
dans ``_grimoire-output/.runs/human-turns.jsonl``. Le texte n'est jamais
stocké, seulement son empreinte. :mod:`grimoire.flows.attestation` relit ce
journal pour classer une décision de checkpoint ``attested`` ou
``unattested``.

Deux vecteurs de falsification, et ce qui les ferme :

- écrire dans ce fichier (Write, Edit, redirection shell, ``python -c``) :
  :mod:`.authority_guard` le refuse dans ``PreToolUse``, quel que soit le
  profil ;
- produire un tour depuis une autre session (``claude -p "approve"``) : ce
  tour porte un autre ``session_id`` et n'ouvre donc pas le checkpoint
  présenté par la session parente.

Limite assumée : un agent qui exécute du code arbitraire peut contourner une
garde de chemin par une voie qu'elle ne prévoit pas. L'attestation rend la
falsification plus coûteuse et visible, pas impossible.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.hosts.events import HookEvent

if TYPE_CHECKING:
    from grimoire.hosts.decisions._shared import HookInput

__all__ = [
    "HOST_HUMAN_ORIGIN",
    "ORIGIN_HOST",
    "ORIGIN_USER",
    "TURNS_FILENAME",
    "TURNS_RELPATH",
    "UNATTENDED_ENV",
    "HumanTurn",
    "host_turn_origin",
    "is_unattended",
    "load_turns",
    "record_human_turn",
    "turn_origin",
    "turns_path",
]

TURNS_RELPATH = Path("_grimoire-output") / ".runs" / "human-turns.jsonl"
TURNS_FILENAME = TURNS_RELPATH.name
#: ``GRIMOIRE_UNATTENDED=1`` déclare une exécution sans humain (CI, pilote) :
#: aucun tour enregistré sous ce drapeau n'atteste quoi que ce soit.
UNATTENDED_ENV = "GRIMOIRE_UNATTENDED"
#: Tour tapé par l'utilisateur, transmis par un hôte identifié.
ORIGIN_USER = "user"
#: Tout le reste : hôte inconnu (``grimoire-hook`` sans ``--host``), prompt
#: vide, exécution déclarée sans humain. Journalisé, jamais attestant.
ORIGIN_HOST = "host"
#: ``turnOrigin`` que Claude Code écrit au journal de session pour un message tapé par l'humain (#992).
#: Les autres valeurs relevées (2.1.319) : ``scheduled`` (réveil, ``/loop``), ``sdk`` (``claude -p``) ; une
#: notification de tâche de fond n'a pas de ``turnOrigin`` mais ``origin.kind: task-notification``.
HOST_HUMAN_ORIGIN = "human"

#: Au-delà, le journal est réduit à ses :data:`_KEEP_LINES` dernières lignes.
#: Un tour ne sert qu'au checkpoint présenté avant lui : l'historique ancien
#: n'atteste plus rien.
_MAX_BYTES = 256 * 1024
_KEEP_LINES = 512


@dataclass(frozen=True, slots=True)
class HumanTurn:
    """Un tour journalisé — jamais le texte, seulement son empreinte."""

    session_id: str
    prompt_sha256: str
    ts: str
    origin: str
    #: ``prompt_id`` de l'hôte et son journal de session (#992) : ``""`` pour un hôte qui ne les donne pas.
    prompt_id: str = ""
    transcript_path: str = ""


def turns_path(project_root: Path) -> Path:
    return project_root / TURNS_RELPATH


def is_unattended(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return str(environ.get(UNATTENDED_ENV, "")).strip() == "1"


def turn_origin(hook: HookInput) -> str:
    """``user`` seulement pour un ``UserPromptSubmit`` non vide d'un hôte nommé, hors exécution sans humain."""
    if hook.event is not HookEvent.USER_PROMPT_SUBMIT:
        return ORIGIN_HOST
    if not hook.host or not hook.prompt.strip() or is_unattended():
        return ORIGIN_HOST
    return ORIGIN_USER


def record_human_turn(hook: HookInput) -> bool:
    """Ajoute le tour de *hook* au journal. ``True`` si la ligne est écrite.

    Best-effort comme :func:`.session_memory.record_user_message` : un journal
    illisible ou un verrou indisponible ne fait jamais échouer le prompt. Un
    tour perdu se paie par une décision ``unattested``, jamais par une
    décision attestée à tort.
    """
    try:
        if not hook.session_id.strip():
            return False
        from grimoire.core.fsio import atomic_write, locked

        digest = hashlib.sha256(hook.prompt.encode("utf-8", "surrogatepass")).hexdigest()
        entry = {
            "session_id": hook.session_id,
            "prompt_sha256": digest,
            "ts": datetime.now(UTC).isoformat(),
            "origin": turn_origin(hook),
        }
        # #992 : l'entrée du hook ne dit pas qui a écrit le prompt, et le journal de session n'existe pas encore à ce
        # moment. L'identifiant du prompt et le chemin du journal sont notés : l'attestation y lira l'origine plus tard.
        prompt_id = str(hook.raw.get("prompt_id") or "")
        transcript = str(hook.raw.get("transcript_path") or "")
        if prompt_id and transcript:
            entry["prompt_id"] = prompt_id
            entry["transcript_path"] = transcript
        path = turns_path(hook.project_root)
        with locked(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
            if path.stat().st_size > _MAX_BYTES:
                kept = path.read_text(encoding="utf-8").splitlines()[-_KEEP_LINES:]
                atomic_write(path, "\n".join(kept) + "\n")
        return True
    except Exception:
        return False


def load_turns(project_root: Path) -> list[HumanTurn] | None:
    """Les tours journalisés, ou ``None`` si le journal n'existe pas (aucun hook de prompt n'a jamais écrit).

    Une ligne illisible est ignorée, jamais réparée : ce journal n'est écrit
    que par le hook.
    """
    try:
        raw = turns_path(project_root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return []
    turns: list[HumanTurn] = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        turns.append(
            HumanTurn(
                session_id=str(data.get("session_id", "")),
                prompt_sha256=str(data.get("prompt_sha256", "")),
                ts=str(data.get("ts", "")),
                origin=str(data.get("origin", "")),
                prompt_id=str(data.get("prompt_id", "")),
                transcript_path=str(data.get("transcript_path", "")),
            )
        )
    return turns


def host_turn_origin(turn: HumanTurn) -> str:
    """L'origine que l'hôte a écrite pour ce tour dans son journal de session (#992) ; ``""`` si non vérifiable ici.

    ``""`` : le tour ne porte pas de ``prompt_id`` (autre hôte, ancien journal) — l'appelant garde alors la règle
    d'avant. Sinon, la première entrée ``user`` du journal portant ce ``promptId`` décide : son ``turnOrigin``, à
    défaut ``origin.kind`` ; introuvable ou illisible, ``"unknown"``, qui n'atteste jamais.
    """
    if not turn.prompt_id:
        return ""
    needle = turn.prompt_id
    try:
        with Path(turn.transcript_path).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if needle not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("promptId") != needle:
                    continue
                origin = entry.get("turnOrigin")
                if not origin and isinstance(entry.get("origin"), dict):
                    origin = entry["origin"].get("kind")
                return str(origin or "unknown")
    except OSError:
        return "unknown"
    return "unknown"
