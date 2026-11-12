"""La couche de données de « Flotte en direct » (issue #1193, lot A).

`GET /api/workspace/fleet/live?project=<slug>&window=<minutes>` rend ce qu'une
session Claude Code fait, à qui elle appartient et avec quel modèle — sans rien
dessiner et sans rien écrire. Les lots B (constellation et couloirs), C
(mini-terminaux) et D (écarts) liront cette charge utile.

Cinq sources, toutes déjà existantes :

- **journaux de session** de l'hôte (``claude_projects_dir``), dossier du projet
  et dossiers de ses worktrees (``…-git-grimoire-worktrees-…``,
  ``…--claude-worktrees-…``). Chaque ``<session>.jsonl`` est une session. Un
  appel API vaut un ``message.id`` (règle de ``traces/transcript_usage``) : les
  lignes d'un même groupe portent le même ``usage`` et ne s'additionnent pas ;
- **sous-agents** : ``<session>/subagents/agent-<id>.jsonl`` plus sa méta, dont
  ``agentType``, ``model`` (demandé), ``pickedEffortModel`` (choisi) et
  ``finished``. Le dossier du parent donne le lien ; ``toolUseId`` relie au tour
  du parent ;
- **Mission Ledger** : ``task.session_launched`` (rôle et tâche d'une session de
  worker), ``task.review_launched`` et ``orchestrator.session_launched``
  (empreinte SHA-256 du ``session_id``), ``claim.session_id`` (tâche tenue),
  ``task.merged`` / ``task.merge_requested`` et la PR de la tâche ;
- **vivacité** : :func:`grimoire.missions.liveness.liveness`, la seule
  définition de « vivante » du kit ;
- **fournisseur d'un modèle** : le registre ``llm-provider-registry.yaml`` du
  projet. Un modèle absent du registre vaut ``inconnu``, jamais une
  supposition.

Lecture incrémentale : l'état d'un journal est gardé par
``(chemin, taille, mtime_ns)``. Un fichier dont la signature n'a pas changé
n'est pas ouvert ; un fichier qui a grandi est relu depuis son dernier décalage.
Seuls les fichiers modifiés dans la fenêtre demandée sont ouverts.

Rien n'est inventé : un coût reste ``None`` quand le tarif est inconnu (même
règle que ``traces/transcript_usage``), et un champ que la source ne porte pas
reste ``None``. Un extrait d'outil est tronqué, sans ANSI
(:func:`grimoire.core.ansi.strip_ansi`), secrets masqués
(:func:`grimoire.memory.validation.redact_secrets`).
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import re
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.ansi import strip_ansi
from grimoire.memory.validation import redact_secrets
from grimoire.missions.lease import parse_instant
from grimoire.missions.liveness import DEFAULT_ACTIVE_WINDOW, TERMINE, VIVANT, liveness
from grimoire.missions.session_link import resume_command
from grimoire.tools.project_registry import load_registry
from grimoire.tools.workspace_work import claude_projects_dir, model_tier

__all__ = [
    "DEFAULT_WINDOW_MINUTES",
    "MAX_WINDOW_MINUTES",
    "SCHEMA_VERSION",
    "TAIL_CALLS",
    "fleet_live_route",
    "fleet_live_view",
    "reset_cache",
    "stats",
]

#: Fenêtre par défaut de la vue, en minutes (issue #1193).
DEFAULT_WINDOW_MINUTES = 240
#: Plafond dur : une fenêtre de requête ne peut pas dépasser une journée.
MAX_WINDOW_MINUTES = 1440
#: Les 20 derniers appels d'outil au plus, par session.
TAIL_CALLS = 20
#: Longueur d'une commande Bash résumée, et d'un extrait de sortie d'outil.
SUMMARY_CHARS = 160
EXCERPT_CHARS = 240
#: Au-delà, les marqueurs et changements de modèle les plus anciens sont oubliés.
_MAX_MARKERS = 200
_MAX_CHANGES = 200

SCHEMA_VERSION = "grimoire-workspace-fleet-live/v1"

#: Ligne d'erreur de l'hôte, pas un appel API (même garde que ``transcript_usage``).
_SYNTHETIC_MODEL = "<synthetic>"
#: Entrypoints d'une session interactive : sans lancement au ledger, elle est humaine.
_HUMAN_ENTRYPOINTS = frozenset({"cli", "claude-vscode", "vscode", "desktop", "claude-desktop"})
#: Marqueurs de dossier de worktree dans le slug Claude Code.
_WORKTREE_MARKERS = (".git-grimoire-worktrees-", "--claude-worktrees-")
#: Une PR liée dans la description ou une consigne.
_PR_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)", re.IGNORECASE)
_HAS_GLOB = re.compile(r"[*?[]")
#: Outils qui posent une question à l'humain : un marqueur du fil, pas un appel ordinaire.
_QUESTION_TOOLS = frozenset({"AskUserQuestion", "ask_user_question"})
#: Ordre d'affichage des états de vivacité (les vivantes d'abord).
_LIVENESS_ORDER = {"active": 0, "waiting": 1, "silent": 2, "ended": 3}

#: Registre des modèles d'un projet, pour le fournisseur et le palier déclaré.
_REGISTRY_RELPATH = Path("_grimoire") / "standard" / "llm-provider-registry.yaml"


# ── Petits utilitaires ──────────────────────────────────────────────────────


def _text(value: Any) -> str:
    """Une chaîne du journal, encodable en UTF-8 strict (substitut isolé neutralisé)."""
    return value.encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else ""


def _count(value: Any) -> int:
    """Un compteur de tokens valide : entier non négatif, jamais un booléen, sinon ``0``."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _clip(text: str, limit: int) -> str:
    return text[:limit]


def _clean(text: str) -> str:
    """ANSI retiré puis secrets masqués — avant troncature, pour ne pas couper un jeton en deux."""
    redacted, _labels = redact_secrets(strip_ansi(text))
    return redacted


def _summary(tool: str, payload: Any) -> str:
    """Le résumé d'un appel d'outil : la commande Bash, sinon le premier champ parlant."""
    if not isinstance(payload, dict):
        return ""
    lowered = tool.lower()
    if lowered == "bash":
        raw = payload.get("command")
    else:
        raw = next(
            (
                payload.get(key)
                for key in ("description", "file_path", "path", "pattern", "query", "url", "prompt", "question")
                if payload.get(key)
            ),
            "",
        )
    return _clip(_clean(_text(raw)), SUMMARY_CHARS)


def _excerpt(content: Any) -> str:
    """L'extrait d'un ``tool_result`` : texte seulement, ANSI retiré, secrets masqués, tronqué."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [_text(block.get("text")) for block in content if isinstance(block, dict) and block.get("text")]
        text = " ".join(part for part in parts if part)
    else:
        text = ""
    return _clip(_clean(text), EXCERPT_CHARS)


def _parse(value: Any) -> datetime | None:
    return parse_instant(_text(value))


def _same_path(raw: str, root: Path) -> bool:
    """Vrai si *raw* désigne déjà la racine du projet (pas un worktree)."""
    try:
        return Path(raw).resolve() == root
    except (OSError, ValueError):
        return False


# ── Rangs de modèle (upgrade / downgrade / switch) ──────────────────────────


def _model_rank(model: str) -> tuple[str, int | None]:
    """``(famille, rang)`` d'un modèle — ``rang`` inconnu vaut ``None`` (donc incomparable)."""
    if not model:
        return "", None
    lowered = model.lower()
    tier = model_tier(model)
    if tier is not None:
        return "claude", ("haiku", "sonnet", "opus").index(tier)
    if "deepseek" in lowered:
        if "flash" in lowered:
            return "deepseek", 0
        if "pro" in lowered:
            return "deepseek", 1
        return "deepseek", None
    return lowered, None


def _change_kind(previous: str, served: str) -> str:
    """``upgrade``, ``downgrade`` ou ``switch`` entre deux modèles servis."""
    family_a, rank_a = _model_rank(previous)
    family_b, rank_b = _model_rank(served)
    if not family_a or family_a != family_b or rank_a is None or rank_b is None or rank_a == rank_b:
        return "switch"
    return "upgrade" if rank_b > rank_a else "downgrade"


# ── État incrémental d'un journal ───────────────────────────────────────────


@dataclass(slots=True)
class _Call:
    message_id: str
    at: str
    model: str
    requested: str
    effort: str
    origin: str
    counters: tuple[int, int, int, int]


@dataclass(slots=True)
class _Tool:
    id: str
    at: str
    tool: str
    summary: str
    status: str = "running"
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at or None,
            "tool": self.tool or None,
            "summary": self.summary or None,
            "status": self.status,
            "excerpt": self.excerpt or None,
        }


@dataclass(slots=True)
class _UsageGroup:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


@dataclass(slots=True)
class _FileState:
    """Ce qu'une lecture incrémentale du journal a retenu, et jusqu'où elle a lu."""

    signature: tuple[int, int] | None = None
    file_id: tuple[int, int] | None = None
    offset: int = 0
    session_id: str = ""
    entrypoint: str = ""
    cwd: str = ""
    branch: str = ""
    started_at: str = ""
    last_at: str = ""
    calls: dict[str, _Call] = field(default_factory=dict)
    current_model: str = ""
    requested_model: str = ""
    effort: str = ""
    turn_count: int = 0
    origins: dict[str, int] = field(default_factory=dict)
    usage_by_model: dict[str, _UsageGroup] = field(default_factory=dict)
    lanes: list[dict[str, Any]] = field(default_factory=list)
    lane: dict[str, Any] | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    markers: list[dict[str, Any]] = field(default_factory=list)
    tools: deque[_Tool] = field(default_factory=lambda: deque(maxlen=TAIL_CALLS))
    open_tools: dict[str, _Tool] = field(default_factory=dict)
    seen_tools: set[str] = field(default_factory=set)
    pr: dict[str, Any] | None = None


@dataclass(slots=True)
class _MetaState:
    """La méta d'un sous-agent, lue par signature."""

    signature: tuple[int, int] | None = None
    agent_type: str = ""
    description: str = ""
    model: str = ""
    picked: str = ""
    spawn_depth: int | None = None
    request_shape: str = ""
    request_non_interactive: bool | None = None
    finished: bool = False
    tool_use_id: str = ""


@dataclass(slots=True)
class _Root:
    slug: str
    name: str
    path: Path


@dataclass(slots=True)
class _Registry:
    """Le registre du projet : modèle → ``(fournisseur, palier déclaré)``."""

    exact: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    patterns: list[tuple[str, str, str | None]] = field(default_factory=list)

    def lookup(self, model: str) -> tuple[str, str | None]:
        if not model:
            return "inconnu", None
        found = self.exact.get(model)
        if found is not None:
            return found
        lowered = model.lower()
        for pattern, provider, tier in self.patterns:
            if fnmatch.fnmatchcase(lowered, pattern):
                return provider, tier
        return "inconnu", None


# ── Cache (un par processus, comme l'index des traces) ──────────────────────

_LOCK = threading.RLock()
_FILES: dict[str, _FileState] = {}
_METAS: dict[str, _MetaState] = {}
_STATS = {"opens": 0, "lines": 0}


def stats() -> dict[str, int]:
    """Compteurs de lecture : journaux et métas ouverts, lignes complètes repliées.

    ``opens`` est cumulatif ; c'est ce que le test de lecture chaude mesure
    (une deuxième lecture sans changement de fichier n'ouvre rien).
    """
    with _LOCK:
        return dict(_STATS)


def reset_cache() -> None:
    """Oublie l'état incrémental et remet les compteurs à zéro (tests, redémarrage)."""
    with _LOCK:
        _FILES.clear()
        _METAS.clear()
        _STATS["opens"] = 0
        _STATS["lines"] = 0


def _reset_state(state: _FileState) -> None:
    """Le journal a été remplacé ou raccourci : on repart de zéro, sans changer d'objet."""
    state.signature = None
    state.file_id = None
    state.offset = 0
    state.session_id = ""
    state.entrypoint = ""
    state.cwd = ""
    state.branch = ""
    state.started_at = ""
    state.last_at = ""
    state.calls.clear()
    state.current_model = ""
    state.requested_model = ""
    state.effort = ""
    state.turn_count = 0
    state.origins.clear()
    state.usage_by_model.clear()
    state.lanes.clear()
    state.lane = None
    state.changes.clear()
    state.markers.clear()
    state.tools.clear()
    state.open_tools.clear()
    state.seen_tools.clear()
    state.pr = None


def _add_usage(state: _FileState, model: str, counters: tuple[int, int, int, int]) -> None:
    group = state.usage_by_model.setdefault(model or "?", _UsageGroup())
    group.input_tokens += counters[0]
    group.output_tokens += counters[1]
    group.cache_read += counters[2]
    group.cache_creation += counters[3]


def _note_lane(state: _FileState, call: _Call) -> None:
    lane = state.lane
    if lane is None or lane["model"] != call.model or lane["effort"] != call.effort:
        if lane is not None:
            state.lanes.append(lane)
        state.lane = {
            "from": call.at or None,
            "to": call.at or None,
            "model": call.model or None,
            "effort": call.effort or None,
        }
    else:
        lane["to"] = call.at or lane["to"]


def _note_change(state: _FileState, at: str, previous: str, served: str, kind: str, turn: int) -> None:
    state.changes.append({"at": at or None, "from": previous, "to": served, "kind": kind, "turn": turn})
    if len(state.changes) > _MAX_CHANGES:
        del state.changes[: len(state.changes) - _MAX_CHANGES]
    _note_marker(state, at, "model_change", f"{previous} → {served} ({kind})")


def _note_marker(state: _FileState, at: str, kind: str, label: str) -> None:
    state.markers.append({"at": at or None, "kind": kind, "label": label or None})
    if len(state.markers) > _MAX_MARKERS:
        del state.markers[: len(state.markers) - _MAX_MARKERS]


def _record_call(state: _FileState, call: _Call) -> None:
    """Un appel vu pour la première fois : changement de modèle, lane, tour et usage."""
    turn = state.turn_count + 1
    requested, served = call.requested, call.model
    if requested and served and requested != served:
        _note_change(state, call.at, requested, served, "fallback", turn)
    elif state.current_model and served and served != state.current_model:
        _note_change(state, call.at, state.current_model, served, _change_kind(state.current_model, served), turn)
    state.turn_count = turn
    if call.origin:
        state.origins[call.origin] = state.origins.get(call.origin, 0) + 1
    if served:
        state.current_model = served
    if requested:
        state.requested_model = requested
    if call.effort:
        state.effort = call.effort
    _note_lane(state, call)
    _add_usage(state, served, call.counters)


def _note_meta(state: _FileState, line: dict[str, Any]) -> None:
    session_id = _text(line.get("sessionId"))
    if session_id and not state.session_id:
        state.session_id = session_id
    entrypoint = _text(line.get("entrypoint"))
    if entrypoint and not state.entrypoint:
        state.entrypoint = entrypoint
    cwd = _text(line.get("cwd"))
    if cwd:
        state.cwd = cwd
    branch = _text(line.get("gitBranch"))
    if branch:
        state.branch = branch
    # Les journaux s'écrivent dans l'ordre : la première ligne donne le début
    # de la session, la dernière son dernier signe.
    stamp = _text(line.get("timestamp"))
    if stamp:
        if not state.started_at:
            state.started_at = stamp
        state.last_at = stamp


def _counters(usage: Any) -> tuple[int, int, int, int] | None:
    """Les compteurs d'un ``usage``, ou ``None`` s'ils ne sont pas exploitables."""
    if not isinstance(usage, dict):
        return None
    raw_in = usage.get("input_tokens")
    raw_out = usage.get("output_tokens")
    if (
        not isinstance(raw_in, int)
        or isinstance(raw_in, bool)
        or not isinstance(raw_out, int)
        or isinstance(raw_out, bool)
    ):
        return None
    if raw_in < 0 or raw_out < 0:
        return None
    return (
        raw_in,
        raw_out,
        _count(usage.get("cache_read_input_tokens", 0)),
        _count(usage.get("cache_creation_input_tokens", 0)),
    )


def _grow(call: _Call, counters: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    """Porte les compteurs du groupe à leur maximum ; rend le delta à ajouter à l'usage."""
    if counters is None:
        return (0, 0, 0, 0)
    previous = call.counters
    merged = (
        max(previous[0], counters[0]),
        max(previous[1], counters[1]),
        max(previous[2], counters[2]),
        max(previous[3], counters[3]),
    )
    call.counters = merged
    return (merged[0] - previous[0], merged[1] - previous[1], merged[2] - previous[2], merged[3] - previous[3])


def _fold_assistant(state: _FileState, line: dict[str, Any]) -> None:
    message = line.get("message")
    if not isinstance(message, dict):
        return
    model = _text(message.get("model"))
    if model == _SYNTHETIC_MODEL:
        return
    message_id = _text(message.get("id") or line.get("uuid"))
    if not message_id:
        return
    counters = _counters(message.get("usage"))
    requested = _text(line.get("requestedModel"))
    effort = _text(line.get("effort"))
    origin = _text(line.get("turnOrigin"))
    stamp = _text(line.get("timestamp"))

    call = state.calls.get(message_id)
    if call is None:
        call = _Call(message_id, stamp, model, requested, effort, origin, counters or (0, 0, 0, 0))
        state.calls[message_id] = call
        _record_call(state, call)
    else:
        delta = _grow(call, counters)
        if any(delta):
            _add_usage(state, call.model, delta)
        if not call.model and model:
            call.model = model
        if not call.requested and requested:
            call.requested = requested
        if not call.effort and effort:
            call.effort = effort

    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_id = _text(block.get("id"))
        if tool_id and tool_id in state.seen_tools:
            continue
        name = _text(block.get("name")) or "outil"
        tool = _Tool(id=tool_id, at=stamp, tool=name, summary=_summary(name, block.get("input")))
        state.tools.append(tool)
        if name in _QUESTION_TOOLS:
            _note_marker(state, stamp, "question", tool.summary or name)
        if tool_id:
            state.seen_tools.add(tool_id)
            state.open_tools[tool_id] = tool


def _fold_user(state: _FileState, line: dict[str, Any]) -> None:
    message = line.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    stamp = _text(line.get("timestamp"))
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_id = _text(block.get("tool_use_id"))
        tool = state.open_tools.pop(tool_id, None) if tool_id else None
        if tool is None:
            continue
        failed = block.get("is_error") is True
        tool.status = "error" if failed else "ok"
        tool.excerpt = _excerpt(block.get("content"))
        if failed:
            _note_marker(state, tool.at or stamp, "error", tool.excerpt or tool.tool)


def _fold_line(state: _FileState, raw: bytes) -> None:
    """Replie une ligne complète du journal ; une ligne illisible est ignorée, jamais complétée."""
    try:
        line = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return
    if not isinstance(line, dict):
        return
    _STATS["lines"] += 1
    _note_meta(state, line)
    if line.get("isCompactSummary") is True:
        _note_marker(state, _text(line.get("timestamp")), "compaction", "contexte compacté")
    kind = line.get("type")
    if kind == "assistant":
        _fold_assistant(state, line)
    elif kind == "user":
        _fold_user(state, line)
    elif kind == "pr-link":
        number = line.get("prNumber")
        url = _text(line.get("prUrl"))
        if (isinstance(number, int) and not isinstance(number, bool) and number > 0) or url:
            state.pr = {
                "number": number if isinstance(number, int) and not isinstance(number, bool) else None,
                "url": url or None,
            }


def _consume(state: _FileState, path: Path) -> None:
    """Consomme les octets complets ajoutés depuis le dernier décalage.

    La signature ``(taille, mtime_ns)`` se lit par ``stat`` : un fichier
    inchangé n'est jamais ouvert, il n'est même pas touché en lecture.
    """
    try:
        stat = path.stat()
    except OSError:
        return
    signature = (stat.st_size, stat.st_mtime_ns)
    file_id = (stat.st_dev, stat.st_ino)
    if state.signature == signature and state.file_id == file_id:
        return
    if state.file_id is not None and (state.file_id != file_id or stat.st_size < state.offset):
        _reset_state(state)
    _STATS["opens"] += 1
    with path.open("rb") as handle:
        handle.seek(state.offset)
        data = handle.read()
    cut = data.rfind(b"\n")
    if cut >= 0:
        for raw in data[:cut].split(b"\n"):
            if raw.strip():
                _fold_line(state, raw)
        state.offset += cut + 1
    state.signature = signature
    state.file_id = file_id


def _state_for(path: Path) -> _FileState:
    key = str(path)
    state = _FILES.get(key)
    if state is None:
        state = _FileState()
        _FILES[key] = state
    _consume(state, path)
    return state


def _meta_for(path: Path) -> _MetaState:
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return _MetaState()
    signature = (stat.st_size, stat.st_mtime_ns)
    meta = _METAS.get(key)
    if meta is not None and meta.signature == signature:
        return meta
    meta = _MetaState(signature=signature)
    try:
        _STATS["opens"] += 1
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return meta
    if not isinstance(data, dict):
        return meta
    meta.agent_type = _text(data.get("agentType"))
    meta.description = _text(data.get("description"))
    meta.model = _text(data.get("model"))
    meta.picked = _text(data.get("pickedEffortModel"))
    depth = data.get("spawnDepth")
    meta.spawn_depth = depth if isinstance(depth, int) and not isinstance(depth, bool) else None
    meta.request_shape = _text(data.get("requestShape"))
    non_interactive = data.get("requestNonInteractive")
    meta.request_non_interactive = non_interactive if isinstance(non_interactive, bool) else None
    meta.finished = data.get("finished") is True
    meta.tool_use_id = _text(data.get("toolUseId"))
    _METAS[key] = meta
    return meta


# ── Ledger : rôle, tâche, PR ────────────────────────────────────────────────


class _Ledger:
    """Ce que le Mission Ledger d'un projet dit des sessions, lu une fois par vue."""

    def __init__(self, root: Path) -> None:
        self.tasks: dict[str, Any] = {}
        self.launched: dict[str, tuple[str, dict[str, Any]]] = {}
        self.digests: dict[str, tuple[str, str]] = {}
        self.claims: dict[str, str] = {}
        self.prs: dict[str, int] = {}
        self.extra_prs: dict[str, tuple[int | None, str | None]] = {}
        self._repo: str | None = None
        self._repo_read = False
        self._root = root
        self._load()

    def _load(self) -> None:
        from grimoire.missions.service import TaskService

        with contextlib.suppress(Exception):
            service = TaskService(self._root)
            if not service.has_ledger:
                return
            self.tasks = {task.id: task for task in service.list_tasks()}
            for event in service.ledger.list_events():
                payload = event.payload if isinstance(event.payload, dict) else {}
                if event.event_type == "task.session_launched":
                    session_id = _text(payload.get("session_id"))
                    if session_id:
                        self.launched[session_id] = (event.entity_id, payload)
                elif event.event_type in ("task.review_launched", "orchestrator.session_launched"):
                    digest = _text(payload.get("session_sha256"))
                    if digest:
                        role = "reviewer" if event.event_type == "task.review_launched" else "orchestrator"
                        self.digests[digest] = (role, event.entity_id)
                elif event.event_type in ("task.merged", "task.merged_externally", "task.merge_requested"):
                    number = payload.get("pr")
                    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                        self.prs[event.entity_id] = number
        for task_id, task in self.tasks.items():
            claim = getattr(task, "claim", None)
            session_id = _text(getattr(claim, "session_id", "")) if claim is not None else ""
            if session_id:
                self.claims[session_id] = task_id
            extra = getattr(task, "extra", None)
            if isinstance(extra, dict):
                number, url = _extra_pr(extra)
                if number or url:
                    self.extra_prs[task_id] = (number, url)

    def role_and_task(self, session_id: str) -> tuple[str | None, Any]:
        launched = self.launched.get(session_id)
        if launched is not None:
            return "worker", self.tasks.get(launched[0])
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        known = self.digests.get(digest)
        if known is not None:
            return known[0], self.tasks.get(known[1])
        claimed = self.claims.get(session_id)
        return None, self.tasks.get(claimed) if claimed else None

    def launch_payload(self, task_id: str | None) -> dict[str, Any]:
        if not task_id:
            return {}
        for tid, payload in self.launched.values():
            if tid == task_id:
                return payload
        return {}

    def _repository(self) -> str:
        if self._repo_read:
            return self._repo or ""
        self._repo_read = True
        with contextlib.suppress(Exception):
            from grimoire.missions.ci_slots import ci_repository

            self._repo = ci_repository(self._root, self._root)
        return self._repo or ""

    def pr_of(self, task: Any) -> dict[str, Any] | None:
        if task is None:
            return None
        task_id = task.id
        number, url = self.extra_prs.get(task_id, (None, None))
        if number is None and url is None:
            number = self.prs.get(task_id)
        if number is None and url is None:
            number, url = _description_pr(task)
        if number is None and url is None:
            return None
        if url is None and number is not None:
            repository = self._repository()
            if repository:
                url = f"https://github.com/{repository}/pull/{number}"
        return {"number": number, "url": url}


def _extra_pr(extra: dict[str, Any]) -> tuple[int | None, str | None]:
    """``(numéro, url)`` lus dans ``task.extra``, sans deviner ce qui n'y est pas."""
    number: int | None = None
    url: str | None = None
    for key in ("pr", "pull_request", "pr_url"):
        value = extra.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            number = value
        elif isinstance(value, str) and value.strip():
            if value.strip().isdigit():
                number = int(value.strip())
            elif value.startswith(("http://", "https://")):
                url = value.strip()
    return number, url


def _description_pr(task: Any) -> tuple[int | None, str | None]:
    texts = [str(getattr(task, "description", "") or "")]
    for directive in getattr(task, "directives", ()) or ():
        texts.append(str(getattr(directive, "text", "") or ""))
    for text in texts:
        matches = list(_PR_URL_RE.finditer(text))
        if matches:
            last = matches[-1]
            return int(last.group(2)), last.group(0) if last.group(0).startswith("http") else f"https://{last.group(0)}"
    return None, None


# ── Registre des modèles ────────────────────────────────────────────────────


def _registry_for(root: Path) -> _Registry:
    registry = _Registry()
    with contextlib.suppress(Exception):
        from grimoire.providers.registry import read_registry

        for provider in read_registry(root):
            declarations: list[tuple[str, str | None]] = [(spec.id, spec.tier) for spec in provider.models]
            declarations += [(model, None) for model in provider.default_models]
            for model_id, tier in declarations:
                key = model_id.strip()
                if not key:
                    continue
                if _HAS_GLOB.search(key):
                    registry.patterns.append((key.lower(), provider.id, tier))
                elif key not in registry.exact:
                    registry.exact[key] = (provider.id, tier)
    return registry


# ── Assemblage d'une session ────────────────────────────────────────────────


def _session_cost(state: _FileState, pricing: dict[str, Any]) -> float | None:
    """Coût des appels de la session ; ``None`` dès qu'un modèle n'est pas tarifé.

    Un modèle dont aucun token n'a été compté ne rend pas le total inconnu : il
    n'a rien coûté à chiffrer. Zéro appel, en revanche, vaut ``None`` — la vue
    ne prétend pas que la session a coûté 0, elle n'en sait rien.
    """
    if not state.usage_by_model:
        return None
    from grimoire.traces.transcript_usage import UsageCall, call_cost_usd

    total = 0.0
    for model, group in state.usage_by_model.items():
        if not (group.input_tokens or group.output_tokens or group.cache_read or group.cache_creation):
            continue
        call = UsageCall(
            message_id="",
            model=model,
            input_tokens=group.input_tokens,
            output_tokens=group.output_tokens,
            cache_read_tokens=group.cache_read,
            cache_creation_tokens=group.cache_creation,
            # L'agrégat ne garde que le total des écritures ; sans la répartition
            # 5 min / 1 h, leur coût reste inconnu plutôt que supposé.
            cache_write_5m_tokens=None,
            cache_write_1h_tokens=None,
            timestamp="",
            agent="",
            session_id="",
        )
        cost = call_cost_usd(call, pricing.get(model))
        if cost is None:
            return None
        total += cost
    return round(total, 6)


def _liveness_value(live: Any, *, waiting: bool) -> str:
    """Les quatre états de la maquette, projetés depuis la vivacité du kit."""
    if live.state == TERMINE:
        return "ended"
    if waiting:
        return "waiting"
    return "active" if live.state == VIVANT else "silent"


def _assemble(
    state: _FileState,
    *,
    session_id: str,
    root: _Root,
    path: Path,
    meta: _MetaState | None,
    ledger: _Ledger,
    registry: _Registry,
    pricing: dict[str, Any],
    now: datetime,
    probe: Mapping[str, Mapping[str, Any]],
    parent_id: str | None = None,
) -> dict[str, Any]:
    role, task = ledger.role_and_task(session_id)
    if role is None:
        if meta is not None:
            role = "subagent"
        elif state.entrypoint in _HUMAN_ENTRYPOINTS:
            role = "human"
        else:
            role = "other"
    launch = ledger.launch_payload(getattr(task, "id", None))
    entry = probe.get(session_id) or {}
    status = entry.get("status") if isinstance(entry, Mapping) else None
    live = liveness(
        now=now,
        journal_at=_parse(state.last_at),
        ended=bool(meta and meta.finished),
        probe_status=str(status) if status else None,
        active_window=DEFAULT_ACTIVE_WINDOW,
    )
    model = state.current_model or (meta.model if meta is not None else "") or _text(launch.get("model"))
    requested = state.requested_model or (meta.model if meta is not None else "") or _text(launch.get("model")) or None
    provider, tier = registry.lookup(model) if model else ("inconnu", None)
    pr = state.pr or ledger.pr_of(task)
    worktree = state.cwd or None
    if worktree and _same_path(worktree, root.path):
        worktree = None
    lanes = [dict(lane) for lane in state.lanes]
    if state.lane is not None:
        lanes.append(dict(state.lane))
    return {
        "id": session_id,
        "project": root.slug,
        "role": role,
        "parent_id": parent_id,
        "task_id": getattr(task, "id", None),
        "pr": pr,
        "worktree": worktree,
        "branch": state.branch or _text(launch.get("branch")) or None,
        "entrypoint": state.entrypoint or None,
        "started_at": state.started_at or None,
        "last_at": state.last_at or None,
        "liveness": _liveness_value(live, waiting=status == "waiting"),
        "model": {
            "current": model or None,
            "requested": requested,
            "picked": (meta.picked or None) if meta is not None else None,
            "provider": provider,
            "tier": tier,
            "effort": state.effort or None,
        },
        "model_changes": [dict(change) for change in state.changes],
        "turns": {"count": state.turn_count, "by_origin": dict(state.origins)},
        "usage": {
            "input": sum(group.input_tokens for group in state.usage_by_model.values()),
            "output": sum(group.output_tokens for group in state.usage_by_model.values()),
            "cache_read": sum(group.cache_read for group in state.usage_by_model.values()),
            "cost_usd": _session_cost(state, pricing),
        },
        "lanes": lanes,
        "markers": [dict(marker) for marker in state.markers],
        "tail": [tool.to_dict() for tool in state.tools],
        "subagents": [],
        "links": {
            "task": f"/workspace/index.html#executer?task={task.id}" if task is not None else None,
            "pr": pr["url"] if pr is not None else None,
            "resume": (resume_command("claude-code-cli", session_id) or None) if meta is None else None,
            "transcript": str(path),
        },
    }


# ── Découverte des journaux ─────────────────────────────────────────────────


def _slug_for(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _roots(project_root: Path, project: str | None) -> list[_Root]:
    """Les projets à lire : tout le registre pour ``project=*``, la cible sinon."""
    if project == "*":
        roots: list[_Root] = []
        for entry in load_registry():
            raw = str(entry.get("path", "") or "")
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_dir():
                continue
            with contextlib.suppress(OSError):
                resolved = candidate.resolve()
                roots.append(
                    _Root(
                        str(entry.get("slug", "") or resolved.name),
                        str(entry.get("name", "") or "") or resolved.name,
                        resolved,
                    )
                )
        return roots
    served = project_root.resolve()
    entry = next(
        (item for item in load_registry() if str(item.get("path", "") or "") and _same_path(str(item["path"]), served)),
        {},
    )
    slug = str(entry.get("slug", "") or "") or served.name
    return [_Root(slug, str(entry.get("name", "") or "") or slug, served)]


def _project_dirs(base: Path, root: _Root, listing: list[Path]) -> list[Path]:
    """Le dossier du projet et ceux de ses worktrees (préfixe du slug + marqueur)."""
    slug = _slug_for(root.path)
    found = [base / slug]
    found += [
        directory
        for directory in listing
        if directory.name.startswith(f"{slug}-") and any(marker in directory.name for marker in _WORKTREE_MARKERS)
    ]
    return [directory for directory in found if directory.is_dir()]


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# ── Entrée publique ─────────────────────────────────────────────────────────


def fleet_live_route(project_root: Path, query: Mapping[str, list[str]]) -> Any:
    """``GET /api/workspace/fleet/live[?project=<slug>&window=<minutes>]`` — le handler de la route.

    Lecture seule. ``window`` borne l'âge des journaux ouverts (défaut
    :data:`DEFAULT_WINDOW_MINUTES`, plafond :data:`MAX_WINDOW_MINUTES`) ; une
    valeur non numérique ou non positive est une demande invalide
    (``ValueError``, traduite en 400 par la route). ``project=*`` couvre tous
    les projets enrôlés au registre du cockpit.

    Le module vit hors de ``workspace_routes`` (cliquet de taille) ; la sonde
    d'hôte, elle, reste dans ``workspace_routes`` pour être mémorisée avec
    celle de la vue Sessions — import tardif, pas de cycle.
    """
    from grimoire.tools.workspace_routes import _host_entries

    windows = query.get("window") or []
    raw = windows[0] if windows else None
    if raw in (None, ""):
        window = DEFAULT_WINDOW_MINUTES
    else:
        try:
            window = int(raw)
        except ValueError as exc:
            raise ValueError(f"`window` doit être un nombre de minutes : {raw!r}") from exc
        if window <= 0:
            raise ValueError("`window` doit être strictement positif (minutes)")
    projects = query.get("project") or []
    project = projects[0] if projects else None
    return fleet_live_view(project_root, project=project, window_minutes=window, probe=_host_entries())


def fleet_live_view(
    project_root: Path,
    *,
    project: str | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    now: datetime | None = None,
    probe: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/fleet/live`` — lecture seule, rien d'écrit.

    *project* ``"*"`` couvre tous les projets enrôlés ; sinon la racine reçue
    est la cible. *window_minutes* borne l'âge des journaux ouverts (défaut
    :data:`DEFAULT_WINDOW_MINUTES`, plafond :data:`MAX_WINDOW_MINUTES`).
    *probe* (facultatif) : les entrées de ``claude agents --json``
    (``{session_id: {status, …}}``) — sans elle, le journal de session tranche
    seul, ce qui reste une preuve.
    """
    if isinstance(window_minutes, bool) or not isinstance(window_minutes, int) or window_minutes <= 0:
        raise ValueError(f"fenêtre invalide : {window_minutes!r} (minutes entières positives attendues)")
    window = min(window_minutes, MAX_WINDOW_MINUTES)
    moment = now or datetime.now(UTC)
    cutoff = (moment - timedelta(minutes=window)).timestamp()
    probes = probe or {}

    with _LOCK:
        roots = _roots(project_root, project)
        base = claude_projects_dir()
        listing = [entry for entry in base.iterdir() if entry.is_dir()] if base.is_dir() else []

        owner: dict[Path, _Root] = {}
        for root in roots:
            for directory in _project_dirs(base, root, listing):
                previous = owner.get(directory)
                if previous is None or len(root.slug) > len(previous.slug):
                    owner[directory] = root

        sessions: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        per_root: dict[str, tuple[_Root, list[Path]]] = {}
        for directory, root in owner.items():
            per_root.setdefault(str(root.path), (root, []))[1].append(directory)
        for root, directories in per_root.values():
            ledger = _Ledger(root.path)
            registry = _registry_for(root.path)
            pricing = _pricing_for(root.path)
            for directory in directories:
                for journal in sorted(directory.glob("*.jsonl")):
                    modified = _mtime(journal)
                    if modified is None or modified < cutoff:
                        continue
                    session_id = journal.stem
                    state = _state_for(journal)
                    payload = _assemble(
                        state,
                        session_id=session_id,
                        root=root,
                        path=journal,
                        meta=None,
                        ledger=ledger,
                        registry=registry,
                        pricing=pricing,
                        now=moment,
                        probe=probes,
                    )
                    sessions.append(payload)
                    _attach_subagents(
                        sessions,
                        edges,
                        parent=payload,
                        parent_state=state,
                        directory=directory / session_id,
                        root=root,
                        ledger=ledger,
                        registry=registry,
                        pricing=pricing,
                        now=moment,
                        cutoff=cutoff,
                        probe=probes,
                    )

        # Les vivantes d'abord, puis les silencieuses et les terminées ; à état égal,
        # le dernier signe le plus récent d'abord (tri stable en deux passes).
        sessions.sort(key=lambda row: row["last_at"] or "", reverse=True)
        sessions.sort(key=lambda row: _LIVENESS_ORDER.get(row["liveness"], 9))
        totals = _totals(sessions)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generated_at": moment.isoformat(),
            "window_minutes": window,
            "projects": [root.slug for root in roots],
            "sessions": sessions,
            "edges": edges,
            "totals": totals,
        }


def _pricing_for(root: Path) -> dict[str, Any]:
    with contextlib.suppress(Exception):
        from grimoire.providers.registry import read_model_pricing

        return read_model_pricing(root)
    return {}


def _attach_subagents(
    sessions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    parent: dict[str, Any],
    parent_state: _FileState,
    directory: Path,
    root: _Root,
    ledger: _Ledger,
    registry: _Registry,
    pricing: dict[str, Any],
    now: datetime,
    cutoff: float,
    probe: Mapping[str, Mapping[str, Any]],
) -> None:
    """Ajoute les sous-agents du dossier ``<session>/subagents`` et les arêtes ``spawned``."""
    subdir = directory / "subagents"
    if not subdir.is_dir():
        return
    for journal in sorted(subdir.glob("agent-*.jsonl")):
        meta_path = journal.with_suffix(".meta.json")
        journal_mtime = _mtime(journal)
        meta_mtime = _mtime(meta_path)
        if (journal_mtime is None or journal_mtime < cutoff) and (meta_mtime is None or meta_mtime < cutoff):
            continue
        meta = _meta_for(meta_path)
        agent_id = journal.stem.removeprefix("agent-")
        state = _state_for(journal)
        payload = _assemble(
            state,
            session_id=agent_id,
            root=root,
            path=journal,
            meta=meta,
            parent_id=parent["id"],
            ledger=ledger,
            registry=registry,
            pricing=pricing,
            now=now,
            probe=probe,
        )
        payload["role"] = "subagent"
        parent["subagents"].append(agent_id)
        sessions.append(payload)
        edges.append({"from": parent["id"], "to": agent_id, "kind": "spawned"})


def _totals(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, int] = {}
    for session in sessions:
        provider = session["model"]["provider"]
        if provider:
            by_provider[provider] = by_provider.get(provider, 0) + 1
    costs = [session["usage"]["cost_usd"] for session in sessions if session["turns"]["count"]]
    cost_usd = (
        round(sum(cost for cost in costs if cost is not None), 6)
        if costs and all(cost is not None for cost in costs)
        else None
    )
    return {
        "live": sum(1 for session in sessions if session["liveness"] in ("active", "waiting")),
        "subagents": sum(1 for session in sessions if session["role"] == "subagent"),
        "by_provider": by_provider,
        "cost_usd": cost_usd,
    }
