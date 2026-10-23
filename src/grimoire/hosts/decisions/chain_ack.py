"""Explication humaine d'une rupture du journal de preuve, sans rien réécrire (CHAIN-ACK).

``evidence.chain_broken`` disait « le journal n'est plus une preuve d'inventaire tant
que la rupture n'est pas expliquée » sans qu'aucun mécanisme ne permette de
l'expliquer : l'erreur était permanente, ``standard verify`` et le gate ``Stop``
restaient rouges pour toujours, et une session sans tâche (repli ``bootstrap``) était
bloquée à chaque fin de tour. Cas réel : une ligne sans ``prev`` écrite par le hook
d'une version antérieure du kit pendant ``pip install -U``, entre des lignes chaînées,
la ligne suivante prolongeant la ligne non chaînée.

Ce module ne touche jamais au journal ni à son ancre : rechaîner ou retirer la ligne
serait falsifier la preuve. Il porte un **acquittement** nominatif, à côté du journal.

- **Seul un humain explique.** L'origine humaine ne s'atteste pas depuis une CLI : un
  agent lance la CLI par Bash. La seule voie du kit qui prouve un tour humain est la
  phrase tapée, reçue par ``UserPromptSubmit`` (même mécanisme que ``Override Gate``,
  :mod:`.gate_override`) : ``Explain Chain <task_id> line <n>: <raison>``, message
  entier, d'un hôte nommé, hors ``GRIMOIRE_UNATTENDED=1``. ``grimoire evidence chain
  explain`` n'écrit donc rien : il diagnostique la ligne et imprime la phrase à taper.
  Contrairement à ``Override Gate``, aucun claim n'est exigé : la rupture du repli
  ``bootstrap`` doit pouvoir s'expliquer.
- **Lié à ce qui est expliqué.** L'acquittement porte l'empreinte de la ligne non
  chaînée et de ses deux voisines physiques. Une ligne modifiée, une autre rupture ou une
  ligne ajoutée à côté ne sont pas couvertes. Seul le cas ``unchained_inserted`` (la chaîne
  continue autour) est explicable ; toute autre rupture reste une erreur (fail-closed).
- **Durable, nominatif.** Pas d'expiration ni de consommation : l'empreinte tient lieu de
  validité. Il cite l'auteur (utilisateur du poste, hôte, session), la raison et la date.
- **Jamais masqué.** ``standard verify`` liste la rupture en avertissement
  ``evidence.chain_break_explained`` ; elle ne disparaît pas.

Le fichier ``evidence-chain-acks.authority.jsonl`` porte le suffixe que
:mod:`.authority_guard` protège : Write, Edit ou Bash qui l'écrivent sont refusés quel que
soit le profil.

Limite (principe 7) : un agent qui exécute du code arbitraire peut écrire ce fichier par une
voie que la garde de chemin ne prévoit pas ; l'acquittement rend l'explication visible et
coûteuse à falsifier, pas impossible. Il n'atteste pas non plus que la ligne est innocente :
un écrivain sans chaînage et une ligne ajoutée à la main sont indiscernables, seule la
décision de l'humain les distingue.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.hosts.decisions._shared import HookInput
from grimoire.hosts.events import HookEvent

if TYPE_CHECKING:
    from grimoire.core.standard_checks.evidence_chain import InsertedLine

__all__ = [
    "ACKS_FILENAME",
    "ACK_EVENT",
    "acks_relpath",
    "covering_ack",
    "explain_phrase",
    "read_acks",
    "record_chain_ack_request",
]

ACK_EVENT = "evidence.chain_ack"
#: Suffixe ``.authority.jsonl`` : celui que :data:`.authority_guard.AUTHORITY_SUFFIX` protège
#: (recopié pour que ``UserPromptSubmit`` n'importe pas la garde ; un test l'épingle).
ACKS_FILENAME = "evidence-chain-acks.authority.jsonl"
_PHRASE = re.compile(r"\AExplain Chain ([^\s:]+) line (\d+)\s*:\s*(\S.*)\Z", re.DOTALL)
_REASON_MAX = 240
_LIMIT = (
    "Limite : un agent qui exécute du code arbitraire peut écrire ce fichier par une voie non prévue ; "
    "l'acquittement rend l'explication visible et coûteuse à falsifier, pas impossible."
)


def acks_relpath(task_id: str) -> Path:
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath

    return evidence_log_relpath(task_id).with_name(ACKS_FILENAME)


def explain_phrase(task_id: str, line: int, reason: str = "<raison>") -> str:
    """La phrase que l'humain tape pour expliquer la ligne *line* du journal de *task_id*."""
    return f"Explain Chain {task_id} line {line}: {reason}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def read_acks(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Les acquittements de *task_id* ; ``[]`` si le fichier est absent ou illisible (rien n'est expliqué)."""
    try:
        raw = (project_root / acks_relpath(task_id)).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # silent-ok — illisible = rien d'expliqué : la rupture reste une erreur (fail-closed)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:  # silent-ok — ligne coupée : ignorée, elle n'explique rien
            continue
        if isinstance(data, dict) and data.get("type") == ACK_EVENT:
            out.append(data)
    return out


def covering_ack(acks: list[dict[str, Any]], task_id: str, inserted: InsertedLine) -> dict[str, Any] | None:
    """Le dernier acquittement qui couvre exactement *inserted* (ligne et empreintes des trois lignes), sinon ``None``."""
    for ack in reversed(acks):
        if (
            ack.get("task_id") == task_id
            and ack.get("kind") == "unchained_inserted"
            and ack.get("line") == inserted.line
            and ack.get("line_sha256") == inserted.sha256
            and ack.get("prev_sha256") == inserted.prev_sha256
            and ack.get("next_sha256") == inserted.next_sha256
        ):
            return ack
    return None


def _refused(why: str) -> tuple[str, dict[str, Any]]:
    return f"[Grimoire] Explication refusée : {why}. La rupture reste une erreur.", {"chain_ack": {"status": "refused"}}


def _author(hook: HookInput) -> dict[str, str]:
    try:
        user = getpass.getuser()
    except Exception:  # silent-ok — nom d'utilisateur indisponible : l'hôte et la session nomment encore l'auteur
        user = ""
    return {"user": user, "host": hook.host, "session_id": hook.session_id}


def record_chain_ack_request(hook: HookInput) -> tuple[str, dict[str, Any]]:
    """``UserPromptSubmit`` : journalise l'acquittement si le prompt est la phrase d'un tour humain.

    Rend ``(contexte, détail)`` ; ``("", {})`` quand le prompt n'est pas la phrase.
    """
    match = _PHRASE.match(hook.prompt.strip())
    if hook.event is not HookEvent.USER_PROMPT_SUBMIT or match is None:
        return "", {}
    from grimoire.hosts.decisions.human_turns import ORIGIN_USER, turn_origin

    raw_task, raw_line, raw_reason = match.groups()
    if turn_origin(hook) != ORIGIN_USER or not hook.session_id.strip():
        return _refused("la phrase ne vient pas d'un tour humain transmis par un hôte nommé (ou GRIMOIRE_UNATTENDED=1)")
    from grimoire.core.standard_checks.evidence_chain import analyze_chain
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath
    from grimoire.core.standard_generation import normalize_task_id

    try:
        task_id = normalize_task_id(raw_task)
    except ValueError:
        return _refused(f"identifiant de tâche invalide {raw_task!r}")
    line_no = int(raw_line)
    report = analyze_chain(hook.project_root / evidence_log_relpath(task_id))
    target = next((item for item in report.inserted if item.line == line_no), None)
    if report.status != "broken" or report.kind != "unchained_inserted" or target is None:
        return _refused(
            f"la ligne {line_no} du journal de {task_id} n'est pas une ligne sans `prev` intercalée dans une chaîne qui "
            f"continue autour (diagnostic : {report.kind or report.status}). Seul ce cas s'explique"
        )
    entry = {
        "type": ACK_EVENT,
        "ack_id": f"ACK-{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "kind": report.kind,
        "line": target.line,
        "line_sha256": target.sha256,
        "prev_sha256": target.prev_sha256,
        "next_sha256": target.next_sha256,
        "reason": " ".join(raw_reason.split())[:_REASON_MAX],
        "author": _author(hook),
        "prompt_sha256": hashlib.sha256(hook.prompt.encode("utf-8", "surrogatepass")).hexdigest(),
        "ts": _now(),
    }
    try:
        _append(hook.project_root, task_id, entry)
    except Exception as exc:
        return _refused(f"{acks_relpath(task_id).as_posix()} n'a pas pu être écrit ({type(exc).__name__})")
    context = (
        f"[Grimoire] Rupture expliquée {entry['ack_id']} pour {task_id}, ligne {target.line} (raison : {entry['reason']}). "
        "`standard verify` la passe en avertissement `evidence.chain_break_explained` et la liste toujours ; "
        f"le journal n'est pas modifié. {_LIMIT}"
    )
    return context, {"chain_ack": {"status": "recorded", "ack_id": entry["ack_id"], "task_id": task_id, "line": target.line}}


def _append(project_root: Path, task_id: str, entry: dict[str, Any]) -> None:
    """Ajoute *entry* sous verrou. Lève en cas d'échec : l'appelant n'accorde rien sans la ligne."""
    from grimoire.core.fsio import locked

    path = project_root / acks_relpath(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path), path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
