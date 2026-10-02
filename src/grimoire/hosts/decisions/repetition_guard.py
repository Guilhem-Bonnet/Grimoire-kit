"""Répétition exacte d'un échec : signal sans modèle, jamais un blocage
(issue #668, phase 5, Refs #562 #555).

Lecture déterministe de `pi-warden/src/stuck.ts` (MIT) — `AttemptWindow` et
`exactRepeats()` — sans le juge (`ask(options.judge, ...)`) ni le reste des
signaux de ce fichier (`churn`, répétition de succès, diff LCS) : ce module
ne reprend que le seul motif que le design retenu (revue à plusieurs voix,
`_scratch/party-oss/pilotage.md`, idée A) affecte à ce lot — un échec qui se
répète **à l'identique**. Trois répétitions du même appel (outil + entrée),
avec la même sortie normalisée, ET que cette sortie signale un échec, valent
un verdict `looping` — jamais un `DENY`/`BLOCK`, jamais une consultation de
:mod:`grimoire.flows.pilot` (le lot 4.4, gelé, choisit stratégie et forme
d'équipe ; ce module ne fait que porter un signal à `Decision.detail`).

Fichier d'état : ``_grimoire-output/.runs/repetition-<session>.json``, même
forme que :mod:`grimoire.hosts.decisions.session_memory` (écriture atomique,
best-effort dans les deux sens, jamais d'exception qui remonte au hook) —
lui-même copié de :mod:`grimoire.policies.session_state` (issue #429/#422).

Empreinte d'un appel
---------------------
``call_hash`` = sha1(nom de l'outil + ``\\0`` + JSON de ``tool_input``, clés
triées) — même idée que ``stuck.ts:133`` (``key = sha1(tool + "\\0" +
JSON.stringify(input))``), sans reprendre son inventaire de commande
(``commandOf``) : les clés triées suffisent à rendre deux appels identiques
comparables, quel que soit l'ordre dans lequel un hôte sérialise
``tool_input``, sans dépendre de :mod:`.tool_facts`.

``output_hash`` = sha1 de la sortie normalisée (:func:`_normalise_output`) :
espaces aplatis, durées/horodatages/adresses hexadécimales/grands nombres et
chemins temporaires masqués — même liste que ``stuck.ts:98-105``
(``normaliseOutput``), plus les chemins temporaires (``/tmp/...``) que ce
lot ajoute explicitement (deux exécutions identiques d'un même échec créent
rarement le même fichier temporaire).

Échec
-----
Un code de sortie non nul (mêmes clés que
:func:`grimoire.hosts.decisions.evidence_trace._exit_code`, dupliquées ici
plutôt qu'importées — c'est une fonction privée d'un autre module, et la
dupliquer coûte quatre lignes) l'emporte quand il est disponible. Sinon,
repli sur un motif d'erreur textuel simple
(:data:`_ERROR_MARKERS_RE`) — volontairement grossier : un faux positif ici
ne fait qu'ajouter une entrée de plus au calcul de répétition, jamais un
refus.

Fenêtre et seuil
-----------------
Fenêtre glissante de :data:`WINDOW_SIZE` appels (12, comme
``pi-warden``/``jev-guard``) ; seuil de :data:`REPEAT_THRESHOLD` (3)
répétitions exactes (même ``call_hash`` ET même ``output_hash`` que le
dernier appel) parmi les entrées encore dans la fenêtre.

Nudge : une fois par série
----------------------------
Le contexte de recadrage n'est injecté qu'une fois par série identique
(``last_nudge_key``, persisté) — un nouvel appel ou une sortie différente
réarme automatiquement, puisque la clé de série change. Consommé par
:mod:`.evidence_trace` via ``additionalContext`` sur ``PostToolUse`` (déjà
lu par ce hook pour le rappel d'évidence — voir
:func:`grimoire.hosts.runtime.render`, branche générique) ; le verdict
lui-même va toujours dans ``Decision.detail["repetition"]``, que l'hôte
consomme le contexte ou non.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.hosts.decisions._shared import HookInput
    from grimoire.hosts.decisions.tool_facts import ToolFacts

__all__ = [
    "REPEAT_THRESHOLD",
    "WINDOW_SIZE",
    "RepetitionState",
    "RepetitionVerdict",
    "evaluate_and_record",
    "load_repetition_state",
    "repetition_path",
    "save_repetition_state",
]

_STATE_DIR = Path("_grimoire-output") / ".runs"
_SCHEMA_VERSION = 1
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: Fenêtre glissante — même taille que ``pi-warden``/``jev-guard``.
WINDOW_SIZE = 12
#: Répétitions exactes (même appel, même sortie normalisée) avant `looping`.
REPEAT_THRESHOLD = 3

#: Durées, timestamps, adresses et grands nombres changent d'une exécution à
#: l'autre sans rien dire d'un échec différent — copie de
#: ``stuck.ts:98-105`` (``normaliseOutput``).
_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|m|h|µs|us|ns)\b")
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_BIG_NUMBER_RE = re.compile(r"\d{5,}")
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?")
#: Chemins temporaires (fichiers ``.tmp``, répertoires ``/tmp``…) : deux
#: exécutions identiques du même échec ne recréent presque jamais le même
#: nom de fichier temporaire (voir ``session_memory.save_session_memory``,
#: qui en fabrique un nouveau à chaque appel via ``os.getpid()``).
_TEMP_PATH_RE = re.compile(
    r"/tmp/\S+"  # noqa: S108 — un motif à reconnaître, pas un chemin en dur
    r"|/var/folders/\S+"
    r"|\\AppData\\Local\\Temp\\\S+"
    r"|\.[A-Za-z0-9_.-]*\.\d+\.tmp\b"
)

#: Un mot d'erreur usuel — grossier et délibérément large : voir le
#: docstring du module pour pourquoi un faux positif ici est sans
#: conséquence (jamais un refus, juste une entrée de plus).
_ERROR_MARKERS_RE = re.compile(
    r"(?i)\b(?:error|exception|traceback|failed|failure|fatal|panic|denied|"
    r"cannot|no such file|not found|enoent)\b"
)


def _normalise_output(text: str) -> str:
    """La sortie d'un appel, débarrassée de ce qui varie sans rien dire d'un
    échec différent. Voir le docstring du module (``stuck.ts:98-105`` plus
    les chemins temporaires)."""
    text = _TEMP_PATH_RE.sub("#tmppath", text)
    text = _DURATION_RE.sub("#t", text)
    text = _HEX_ADDR_RE.sub("0x#", text)
    text = _BIG_NUMBER_RE.sub("#", text)
    text = _ISO_TS_RE.sub("#date", text)
    return re.sub(r"\s+", " ", text).strip()


def _call_hash(tool_name: str, tool_input: dict[str, Any]) -> str:
    """sha1(outil + entrée normalisée) — clés triées, voir le docstring du module."""
    try:
        payload = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(tool_input)
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(tool_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


def _output_hash(text: str) -> str:
    return hashlib.sha1(_normalise_output(text).encode("utf-8"), usedforsecurity=False).hexdigest()


def _exit_code(tool_response: dict[str, Any]) -> int | None:
    """Copie de :func:`grimoire.hosts.decisions.evidence_trace._exit_code`
    (fonction privée d'un autre module — dupliquée plutôt qu'importée, comme
    :mod:`.session_memory` le documente pour son propre cas)."""
    for key in ("exit_code", "exitCode", "returncode", "return_code", "code"):
        value = tool_response.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _response_text(tool_response: dict[str, Any]) -> str:
    """Copie de :func:`grimoire.hosts.decisions.session_memory._response_text`."""
    for key in ("content", "output", "result", "text", "stdout"):
        value = tool_response.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [
                item["text"]
                for item in value
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if parts:
                return "\n".join(parts)
    return ""


def _is_failure(tool_response: dict[str, Any], text: str) -> bool:
    code = _exit_code(tool_response)
    if code is not None:
        return code != 0
    return bool(_ERROR_MARKERS_RE.search(text))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_session_id(session_id: str) -> str:
    session_id = session_id.strip() or "unknown"
    if _SAFE_ID_RE.match(session_id):
        return session_id
    return "h-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def repetition_path(project_root: Path, session_id: str) -> Path:
    return project_root / _STATE_DIR / f"repetition-{_safe_session_id(session_id)}.json"


@dataclass
class RepetitionState:
    """La fenêtre glissante d'une session, et la dernière série déjà signalée."""

    session_id: str
    updated_at: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    last_nudge_key: str = ""
    schema_version: int = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "updated_at": self.updated_at,
            "entries": [dict(entry) for entry in self.entries],
            "last_nudge_key": self.last_nudge_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, session_id: str) -> RepetitionState:
        raw_entries = data.get("entries", [])
        entries = (
            [
                {
                    "tool": str(entry.get("tool", "")),
                    "call_hash": str(entry.get("call_hash", "")),
                    "output_hash": str(entry.get("output_hash", "")),
                    "failed": bool(entry.get("failed", False)),
                    "ts": str(entry.get("ts", "")),
                }
                for entry in raw_entries
                if isinstance(entry, dict)
            ]
            if isinstance(raw_entries, list)
            else []
        )
        return cls(
            session_id=session_id,
            updated_at=str(data.get("updated_at", "")),
            entries=entries[-WINDOW_SIZE:],
            last_nudge_key=str(data.get("last_nudge_key", "")),
        )

    @classmethod
    def new(cls, session_id: str) -> RepetitionState:
        return cls(session_id=session_id)


def load_repetition_state(project_root: Path, session_id: str) -> RepetitionState:
    """L'état de *session_id*, ou un état neuf — jamais d'exception.

    Même contrat que :func:`grimoire.hosts.decisions.session_memory.load_session_memory` :
    un fichier absent, illisible, corrompu ou d'une autre version rend un état
    neuf plutôt qu'une erreur qu'un hook pourrait faire échouer sur.
    """
    path = repetition_path(project_root, session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return RepetitionState.new(session_id)
    try:
        data = json.loads(raw)
    except ValueError:
        return RepetitionState.new(session_id)
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return RepetitionState.new(session_id)
    return RepetitionState.from_dict(data, session_id=session_id)


def save_repetition_state(project_root: Path, state: RepetitionState) -> None:
    """Écriture atomique (fichier temporaire + ``os.replace``), best-effort —
    voir :func:`grimoire.hosts.decisions.session_memory.save_session_memory`."""
    state.updated_at = _now_iso()
    path = repetition_path(project_root, state.session_id)
    payload = json.dumps(state.to_dict(), ensure_ascii=False)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


#: Texte de recadrage — nommé, jamais un blocage. Voir le docstring du module.
_NUDGE_TEMPLATE = (
    "[Grimoire] même appel, même échec, {repeats} fois : change d'approche ou demande de l'aide."
)


@dataclass(frozen=True, slots=True)
class RepetitionVerdict:
    """Ce que :func:`evaluate_and_record` a décidé — toujours attaché à
    ``Decision.detail["repetition"]`` par :mod:`.evidence_trace`."""

    looping: bool
    repeats: int
    nudge: str = ""
    """Non vide seulement la première fois qu'une série atteint le seuil —
    voir le docstring du module, « Nudge : une fois par série »."""

    def to_dict(self) -> dict[str, Any]:
        return {"looping": self.looping, "repeats": self.repeats}


def evaluate_and_record(hook: HookInput, facts: ToolFacts) -> RepetitionVerdict | None:
    """``PostToolUse`` : enregistre cet appel dans la fenêtre glissante de la
    session, et rend un verdict — ``None`` seulement quand il n'y a pas de
    session à qui l'attacher. Best-effort : une erreur d'I/O ou de
    sérialisation dégrade en « rien à signaler », jamais une exception qui
    remonterait au hook.
    """
    if not hook.session_id:
        return None
    try:
        text = _response_text(hook.tool_response)
        failed = _is_failure(hook.tool_response, text)
        entry = {
            "tool": hook.tool_name,
            "call_hash": _call_hash(hook.tool_name, hook.tool_input),
            "output_hash": _output_hash(text),
            "failed": failed,
            "ts": _now_iso(),
        }
        state = load_repetition_state(hook.project_root, hook.session_id)
        state.entries.append(entry)
        state.entries = state.entries[-WINDOW_SIZE:]

        repeats = sum(
            1
            for past in state.entries
            if past["call_hash"] == entry["call_hash"] and past["output_hash"] == entry["output_hash"]
        )
        looping = failed and repeats >= REPEAT_THRESHOLD
        nudge = ""
        if looping:
            series_key = f"{entry['call_hash']}:{entry['output_hash']}"
            if state.last_nudge_key != series_key:
                nudge = _NUDGE_TEMPLATE.format(repeats=repeats)
                state.last_nudge_key = series_key

        save_repetition_state(hook.project_root, state)
        return RepetitionVerdict(looping=looping, repeats=repeats, nudge=nudge)
    except Exception:
        return None
