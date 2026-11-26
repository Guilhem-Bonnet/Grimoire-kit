"""Usage de jetons lu dans le journal de session de GitHub Copilot, pendant de :mod:`.transcript_usage`.

**Non vérifié sur un vrai Copilot.** Le format lu ici est celui des fixtures
``tests/fixtures/hosts/copilot/copilot-harness/`` (manifeste : ``local_token_usage``,
canal « journal de session (``session.shutdown.modelMetrics``) », statut ``unverified``,
version d'hôte inconnue). Aucune capture réelle n'est versée : tout chiffre rendu ici
porte ``verified: False`` et le libellé :data:`UNVERIFIED_NOTE`.

Ce que le format donne, et ce qu'il ne donne pas (règle : le reste est *inconnu*, jamais zéro) :

* ``session.shutdown.data.modelMetrics`` : par modèle, ``requests.count``, ``requests.cost`` (des
  requêtes *premium*, pas des dollars) et ``usage.{inputTokens, outputTokens, cacheReadTokens,
  cacheWriteTokens, reasoningTokens}``. C'est le seul endroit qui donne l'entrée et le cache ;
  une session sans ``session.shutdown`` (en cours, interrompue) n'a donc **ni entrée ni cache
  connus**, seulement la sortie de ses messages ;
* ``assistant.message.data.outputTokens`` : sortie par message, ``messageId`` pour ne compter
  un message qu'une fois ; ``model`` n'y figure que sur une partie des messages ;
* ``subagent.started`` / ``assistant.message.parentToolCallId`` : rattachement d'un message à son
  sous-agent ;
* ``hook.start.data.input.cwd`` : le dossier de travail (seule indication du projet) ;
* **aucun coût en dollars** : le tarif de Copilot est un forfait de requêtes premium. Le coût d'un
  appel est inconnu (``None``) sauf si le registre du projet déclare un tarif pour le modèle
  (``models[].pricing``) ; un coût inconnu n'est jamais compté zéro ;
* l'identifiant de session : ``session.start.data.sessionId`` s'il existe, sinon le nom du dossier
  de session (``<dossier>/events.jsonl``) ou le nom du fichier. Convention déduite du libellé du
  manifeste (« fichier events.jsonl du dossier de session »), pas d'une capture.

Idempotent par construction : l'identifiant d'une trace est dérivé de ``(session, rang du
``session.shutdown``, modèle)``, sans le chemin du fichier : une session n'est repliée qu'une fois,
même lue depuis une copie. Seules les sessions arrêtées (``session.shutdown`` présent) sont
repliées : une session en cours n'a pas d'entrée connue, et la replier deux fois (messages puis
arrêt) compterait deux fois. Si un journal contient plusieurs ``session.shutdown`` (reprise), le
format ne dit pas si les compteurs sont cumulés : chacun est replié séparément et le rapport le dit
(``shutdowns``).

L'emplacement des journaux n'est **pas établi** dans le dépôt : aucun défaut. Il se règle par la
variable d'environnement :data:`LOGS_ENV` ou, sinon, le réglage ``hosts.copilot_session_logs`` de
``project-context.yaml`` ; sans l'un ni l'autre, rien n'est lu (:meth:`LogsLocation.message`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.regular_file import open_regular
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TokenUsage, TraceOutcome, TraceRecord
from grimoire.traces.transcript_usage import (
    CACHE_CREATION_TAG_PREFIX,
    CACHE_READ_TAG_PREFIX,
    COPILOT_UNVERIFIED_NOTE,
    SOURCE_COPILOT_SESSION,
    SOURCE_TAG_PREFIX,
    PricingLookup,
    usage_key,
)

__all__ = [
    "LOGS_ENV",
    "MAX_JOURNAL_BYTES",
    "MAX_LINE_BYTES",
    "SOURCE_COPILOT_SESSION",
    "UNVERIFIED_NOTE",
    "CopilotFoldResult",
    "CopilotSession",
    "LogsLocation",
    "ModelUsage",
    "SubagentUsage",
    "confined_path",
    "copilot_logs_location",
    "fold_copilot_journal",
    "list_journals",
    "locate_session_journal",
    "read_copilot_journal",
    "session_task_usage",
]

#: Variable d'environnement qui désigne le dossier des journaux de session Copilot.
LOGS_ENV = "GRIMOIRE_COPILOT_LOGS_DIR"
#: Réglage de ``project-context.yaml`` (section ``hosts``) quand la variable est absente.
SETTING_KEY = "hosts.copilot_session_logs"
UNVERIFIED_NOTE = COPILOT_UNVERIFIED_NOTE
#: Un journal plus gros n'est pas lu en entier : ce qui suit la borne est inconnu, jamais deviné.
MAX_JOURNAL_BYTES = 64 << 20
MAX_LINE_BYTES = 4 << 20
COPILOT_RECIPE_ID = "grimoire.model-call.copilot-session"
PREMIUM_TAG_PREFIX = "usage.premium_requests:"
REASONING_TAG_PREFIX = "usage.reasoning:"
_MAX_JOURNALS = 2000


# ── Emplacement des journaux ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LogsLocation:
    """Où lire les journaux Copilot, ou pourquoi on n'en lit aucun."""

    path: Path | None
    #: ``env`` | ``project`` | ``none``.
    source: str
    problem: str = ""

    @property
    def usable(self) -> bool:
        return self.path is not None and not self.problem

    @property
    def message(self) -> str:
        """La ligne que ``doctor`` et ``host status`` affichent."""
        if self.problem:
            return f"journaux Copilot : {self.problem}"
        if self.path is None:
            return (
                "journaux Copilot : emplacement non configuré "
                f"(variable {LOGS_ENV} ou réglage {SETTING_KEY} de project-context.yaml ; "
                "aucun défaut, le dépôt n'établit pas où l'hôte les écrit)"
            )
        return f"journaux Copilot : {self.path} (via {'la variable ' + LOGS_ENV if self.source == 'env' else SETTING_KEY}) — {UNVERIFIED_NOTE}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.path is not None,
            "usable": self.usable,
            "path": str(self.path) if self.path is not None else None,
            "source": self.source,
            "message": self.message,
            "verified": False,
        }


def _project_setting(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    try:
        cfg = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
    except GrimoireConfigError:
        return ""
    return cfg.hosts.copilot_session_logs


def copilot_logs_location(project_root: Path | None = None, environ: Mapping[str, str] | None = None) -> LogsLocation:
    """Le dossier des journaux Copilot : variable d'environnement, puis réglage du projet, sinon rien.

    Un chemin relatif ou inexistant est un *problème* dit tel quel (jamais corrigé en silence) ; l'absence
    de réglage n'est pas une erreur, c'est « non configuré ».
    """
    env = os.environ if environ is None else environ
    raw, source = (env.get(LOGS_ENV, "").strip(), "env")
    if not raw:
        raw, source = (_project_setting(project_root).strip(), "project")
    if not raw:
        return LogsLocation(None, "none")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        return LogsLocation(None, source, f"emplacement refusé, chemin absolu attendu : {raw!r}")
    resolved = candidate.resolve()
    if not resolved.is_dir():
        return LogsLocation(resolved, source, f"dossier introuvable : {resolved}")
    return LogsLocation(resolved, source)


def confined_path(path: Path, base: Path) -> Path | None:
    """*path* résolu, ``None`` s'il sort de *base* résolu (lien sortant, ``..``)."""
    resolved = path.resolve()
    return resolved if resolved.is_relative_to(base.resolve()) else None


def list_journals(base: Path) -> list[Path]:
    """Les journaux sous *base* : ``<session>/events.jsonl`` et ``*.jsonl``, confinés à *base*, bornés."""
    root = base.resolve()
    found: list[Path] = []
    for pattern in ("*/events.jsonl", "*.jsonl"):
        for candidate in sorted(root.glob(pattern)):
            confined = confined_path(candidate, root)
            if confined is not None and confined.is_file() and confined not in found:
                found.append(confined)
            if len(found) >= _MAX_JOURNALS:
                return found
    return found


def locate_session_journal(base: Path, session_id: str) -> Path | None:
    """Le journal de *session_id* (nom du dossier ou du fichier), ``None`` si absent ou si le nom n'est pas sûr."""
    if not session_id or any(ch not in "0123456789abcdefABCDEF-" for ch in session_id) or len(session_id) > 64:
        return None
    root = base.resolve()
    for candidate in (root / session_id / "events.jsonl", root / f"{session_id}.jsonl"):
        confined = confined_path(candidate, root)
        if confined is not None and confined.is_file():
            return confined
    return None


# ── Lecture ─────────────────────────────────────────────────────────────────


def _count(value: Any) -> int | None:
    """Un compteur valide : entier non négatif, jamais un booléen ; ``None`` = inconnu."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != value:
        return None
    return float(value)


def _text(value: Any) -> str:
    return value.encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else ""


def _instant(value: Any) -> str:
    """Un instant ISO 8601 UTC depuis des millisecondes d'epoch ou un texte ISO ; ``""`` si illisible."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            return ""
    return ""


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Usage d'un modèle dans un ``session.shutdown`` ; ``None`` = le format ne le donne pas."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    requests: int | None = None
    #: Rang du ``session.shutdown`` dans le journal (0 pour le premier).
    shutdown_index: int = 0


@dataclass(frozen=True, slots=True)
class SubagentUsage:
    """Un sous-agent vu dans le journal : son nom, son modèle (s'il est donné), la sortie de ses messages."""

    tool_call_id: str
    agent_name: str
    model: str
    output_tokens: int
    messages: int
    completed: bool


@dataclass(frozen=True, slots=True)
class CopilotSession:
    """Ce qu'un journal Copilot dit d'une session, et ce qu'il a fallu écarter."""

    session_id: str
    path: str
    #: ``False`` si aucune ligne n'a la forme ``{"type", "data"}`` reconnue : tout est alors inconnu.
    recognized: bool
    cwd: str = ""
    copilot_version: str = ""
    started_at: str = ""
    last_at: str = ""
    #: Un ``session.shutdown`` a été lu : la session est terminée et son usage par modèle est connu.
    ended: bool = False
    shutdowns: int = 0
    #: Modèle réel : ``currentModel`` du dernier arrêt, sinon le dernier modèle porté par un message.
    model: str = ""
    models: tuple[ModelUsage, ...] = ()
    messages: int = 0
    #: Somme de ``outputTokens`` des messages (un par ``messageId``) ; seulement ceux qui le portent.
    message_output_tokens: int = 0
    messages_without_output: int = 0
    premium_requests: float | None = None
    subagents: tuple[SubagentUsage, ...] = ()
    unreadable_lines: int = 0
    invalid_usage: int = 0
    #: Le journal dépasse :data:`MAX_JOURNAL_BYTES` : la fin n'a pas été lue.
    truncated: bool = False

    def usage(self) -> dict[str, Any]:
        """Compteurs de la session ; ``None`` pour ce qui est inconnu (jamais 0 par défaut)."""
        if self.models:
            known_cache_read = [m.cache_read_tokens for m in self.models]
            known_cache_write = [m.cache_write_tokens for m in self.models]
            return {
                "source": "shutdown",
                "input_tokens": sum(m.input_tokens for m in self.models),
                "output_tokens": sum(m.output_tokens for m in self.models),
                "cache_read_tokens": None if None in known_cache_read else sum(v or 0 for v in known_cache_read),
                "cache_write_tokens": None if None in known_cache_write else sum(v or 0 for v in known_cache_write),
                "cost_usd": None,
                "premium_requests": self.premium_requests,
                "verified": False,
            }
        counted = self.messages - self.messages_without_output
        return {
            "source": "messages" if counted > 0 else "none",
            "input_tokens": None,
            "output_tokens": self.message_output_tokens if counted > 0 else None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "cost_usd": None,
            "premium_requests": None,
            "verified": False,
        }


@dataclass(slots=True)
class _Accumulator:
    session_id: str = ""
    cwd: str = ""
    version: str = ""
    started_at: str = ""
    last_at: str = ""
    shutdowns: int = 0
    current_model: str = ""
    last_message_model: str = ""
    models: list[ModelUsage] = field(default_factory=list)
    premium: float | None = None
    seen_messages: set[str] = field(default_factory=set)
    messages: int = 0
    output: int = 0
    without_output: int = 0
    subagents: dict[str, dict[str, Any]] = field(default_factory=dict)
    invalid: int = 0
    recognized: bool = False


def _fold_shutdown(acc: _Accumulator, data: Mapping[str, Any]) -> None:
    index = acc.shutdowns
    acc.shutdowns += 1
    current = _text(data.get("currentModel"))
    if current:
        acc.current_model = current
    started = _instant(data.get("sessionStartTime"))
    if started and not acc.started_at:
        acc.started_at = started
    premium = _number(data.get("totalPremiumRequests"))
    if premium is not None:
        acc.premium = (acc.premium or 0.0) + premium
    metrics = data.get("modelMetrics")
    if not isinstance(metrics, Mapping):
        acc.invalid += 1
        return
    for name, entry in metrics.items():
        usage = entry.get("usage") if isinstance(entry, Mapping) else None
        if not isinstance(name, str) or not name or not isinstance(usage, Mapping):
            acc.invalid += 1
            continue
        tokens_in, tokens_out = _count(usage.get("inputTokens")), _count(usage.get("outputTokens"))
        if tokens_in is None or tokens_out is None:
            acc.invalid += 1
            continue
        requests = entry.get("requests")
        acc.models.append(
            ModelUsage(
                model=_text(name),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cache_read_tokens=_count(usage.get("cacheReadTokens")),
                cache_write_tokens=_count(usage.get("cacheWriteTokens")),
                reasoning_tokens=_count(usage.get("reasoningTokens")),
                requests=_count(requests.get("count")) if isinstance(requests, Mapping) else None,
                shutdown_index=index,
            )
        )


def _fold_message(acc: _Accumulator, data: Mapping[str, Any]) -> None:
    message_id = _text(data.get("messageId"))
    if message_id:
        if message_id in acc.seen_messages:
            return
        acc.seen_messages.add(message_id)
    model = _text(data.get("model"))
    if model:
        acc.last_message_model = model
    tokens = _count(data.get("outputTokens"))
    parent = _text(data.get("parentToolCallId"))
    sub = acc.subagents.get(parent) if parent else None
    if sub is not None:
        sub["messages"] += 1
        if model:
            sub["model"] = model
        if tokens is not None:
            sub["output"] += tokens
        return
    acc.messages += 1
    if tokens is None:
        acc.without_output += 1
    else:
        acc.output += tokens


def _fold_event(acc: _Accumulator, event: Mapping[str, Any]) -> None:
    kind, data = event.get("type"), event.get("data")
    if not isinstance(kind, str) or not isinstance(data, Mapping):
        return
    acc.recognized = True
    stamp = _instant(event.get("timestamp"))
    if stamp:
        acc.last_at = stamp
    if kind == "session.start":
        acc.session_id = acc.session_id or _text(data.get("sessionId"))
        version = _text(data.get("copilotVersion"))
        acc.version = version if version and version != "unknown" else acc.version
    elif kind == "hook.start":
        hook_input = data.get("input")
        cwd = _text(hook_input.get("cwd")) if isinstance(hook_input, Mapping) else ""
        acc.cwd = acc.cwd or cwd
    elif kind == "subagent.started":
        call_id = _text(data.get("toolCallId"))
        if call_id:
            acc.subagents.setdefault(
                call_id,
                {"agent": _text(data.get("agentName")), "model": "", "output": 0, "messages": 0, "done": False},
            )
    elif kind == "subagent.completed":
        sub = acc.subagents.get(_text(data.get("toolCallId")))
        if sub is not None:
            sub["done"] = True
    elif kind == "assistant.message":
        _fold_message(acc, data)
    elif kind == "session.shutdown":
        _fold_shutdown(acc, data)


def read_copilot_journal(path: Path, *, base: Path | None = None) -> CopilotSession:
    """Lit un journal de session Copilot (un objet JSON ``{"type", "data"}`` par ligne), sans rien inventer.

    *base* : quand il est donné, le journal doit se trouver sous lui une fois résolu (``ValueError`` sinon).
    Ouverture par :func:`grimoire.core.regular_file.open_regular` (fichier ordinaire, jamais un lien, une FIFO
    ni un périphérique), taille bornée par :data:`MAX_JOURNAL_BYTES` et :data:`MAX_LINE_BYTES`. Une ligne
    illisible (dont la dernière, tronquée) est comptée, jamais complétée ; un journal sans aucune ligne de la
    forme attendue rend ``recognized=False`` et aucun compteur. Lève ``OSError`` / ``ValueError`` si le fichier
    est introuvable, illisible ou hors de *base*.
    """
    resolved = path.resolve()
    if base is not None and not resolved.is_relative_to(base.resolve()):
        raise ValueError(f"{path.name} sort du dossier des journaux Copilot : refusé")
    acc = _Accumulator()
    unreadable = 0
    consumed = 0
    truncated = False
    with open_regular(resolved) as handle:
        while True:
            raw = handle.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            consumed += len(raw)
            if consumed > MAX_JOURNAL_BYTES:
                truncated = True
                break
            if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
                unreadable += 1
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(MAX_LINE_BYTES + 1)
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                unreadable += 1
                continue
            if isinstance(event, dict):
                _fold_event(acc, event)
    stem = resolved.parent.name if resolved.name == "events.jsonl" else resolved.stem
    models = tuple(acc.models)
    subagents = tuple(
        SubagentUsage(call_id, str(s["agent"]), str(s["model"]), int(s["output"]), int(s["messages"]), bool(s["done"]))
        for call_id, s in acc.subagents.items()
    )
    return CopilotSession(
        session_id=acc.session_id or stem,
        path=str(resolved),
        recognized=acc.recognized,
        cwd=acc.cwd,
        copilot_version=acc.version,
        started_at=acc.started_at,
        last_at=acc.last_at,
        ended=acc.shutdowns > 0,
        shutdowns=acc.shutdowns,
        model=acc.current_model or acc.last_message_model or (models[-1].model if models else ""),
        models=models,
        messages=acc.messages,
        message_output_tokens=acc.output,
        messages_without_output=acc.without_output,
        premium_requests=acc.premium,
        subagents=subagents,
        unreadable_lines=unreadable,
        invalid_usage=acc.invalid,
        truncated=truncated,
    )


def session_task_usage(session: CopilotSession) -> dict[str, Any]:
    """L'usage d'une tâche lu **directement** dans son journal Copilot, sans rien écrire (``task usage`` / ``task show``).

    Même forme que :func:`grimoire.traces.transcript_usage.task_usage`, avec ``None`` pour ce que le format ne donne
    pas : une session non arrêtée n'a ni entrée, ni cache, ni total ; le coût est toujours inconnu (``unknown``).
    """
    usage = session.usage()
    ended = usage["source"] == "shutdown"
    cache_read, cache_write = usage["cache_read_tokens"], usage["cache_write_tokens"]
    known = ended and cache_read is not None and cache_write is not None
    calls = len(session.models) if ended else session.messages
    return {
        "usage_source": SOURCE_COPILOT_SESSION,
        "calls": calls,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_write,
        "total_tokens": (usage["input_tokens"] + usage["output_tokens"] + cache_read + cache_write) if known else None,
        "cost_usd": None,
        "cost_status": "unknown",
        "cost_known": False,
        "unpriced_calls": calls,
        "reported": None,
        "discrepancy": None,
        "verified": False,
        "ended": ended,
        "note": UNVERIFIED_NOTE,
    }


# ── Repli dans le TraceLedger ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CopilotFoldResult:
    """Résultat d'un repli : nouveau, déjà connu, écarté, et pourquoi rien n'a été replié."""

    source: str
    session_id: str
    new_calls: int
    already_folded: int
    unreadable_lines: int
    invalid_usage: int
    shutdowns: int
    #: Vide quand le repli a eu lieu ; sinon la raison (``unrecognized``, ``not_ended``, ``truncated``).
    skipped: str = ""
    held_by_other_tasks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "usage_source": SOURCE_COPILOT_SESSION,
            "session_id": self.session_id,
            "new_calls": self.new_calls,
            "already_folded": self.already_folded,
            "unreadable_lines": self.unreadable_lines,
            "invalid_usage": self.invalid_usage,
            "shutdowns": self.shutdowns,
            "skipped": self.skipped,
            "held_by_other_tasks": dict(self.held_by_other_tasks),
            "verified": False,
            "note": UNVERIFIED_NOTE,
        }


def _trace_for(
    usage: ModelUsage, session: CopilotSession, *, task_id: str, mission_id: str, pricing_for: PricingLookup | None
) -> TraceRecord:
    cost: float | None = None
    pricing = pricing_for(usage.model) if pricing_for is not None else None
    if pricing is not None and usage.cache_read_tokens is not None and usage.cache_write_tokens is not None:
        cost = pricing.cost_usd(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_write_tokens,
            cache_write_5m_tokens=None,
            cache_write_1h_tokens=None,
        )
    total = usage.input_tokens + usage.output_tokens + (usage.cache_read_tokens or 0) + (usage.cache_write_tokens or 0)
    key = usage_key(f"copilot:{session.session_id or session.path}", f"{usage.shutdown_index}:{usage.model}")
    return TraceRecord(
        id=f"TRC-usage-{key}",
        run_id=f"usage-{task_id}",
        workflow_instance_id="",
        mission_id=mission_id,
        task_id=task_id,
        recipe_id=COPILOT_RECIPE_ID,
        outcome=TraceOutcome.SUCCESS,
        started_at=session.started_at,
        completed_at=session.last_at or session.started_at,
        host_id=HostId.GITHUB_COPILOT.value,
        model=usage.model,
        token_usage=TokenUsage(
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            total_tokens=total,
            estimated_cost_usd=cost,
            unpriced_calls=0 if cost is not None else 1,
        ),
        tags=(
            f"{SOURCE_TAG_PREFIX}{SOURCE_COPILOT_SESSION}",
            f"{CACHE_READ_TAG_PREFIX}{'' if usage.cache_read_tokens is None else usage.cache_read_tokens}",
            f"{CACHE_CREATION_TAG_PREFIX}{'' if usage.cache_write_tokens is None else usage.cache_write_tokens}",
            f"{REASONING_TAG_PREFIX}{'' if usage.reasoning_tokens is None else usage.reasoning_tokens}",
            f"{PREMIUM_TAG_PREFIX}{'' if session.premium_requests is None else session.premium_requests}",
            "usage.verified:false",
        ),
    )


def fold_copilot_journal(
    ledger: TraceLedger,
    path: Path,
    *,
    task_id: str,
    mission_id: str = "",
    base: Path | None = None,
    pricing_for: PricingLookup | None = None,
) -> CopilotFoldResult:
    """Replie l'usage par modèle d'une session Copilot *arrêtée* dans le *ledger*, rattaché à *task_id*.

    Idempotent (voir le docstring du module) : rejouer le repli, ou le rejouer depuis une copie du journal,
    n'écrit rien de plus ; ce que tient une autre tâche est nommé dans ``held_by_other_tasks``. Une session
    sans ``session.shutdown``, un journal tronqué ou un format non reconnu ne replient **rien** et le disent
    (``skipped``) : un chiffre partiel serait un chiffre inventé.
    """
    session = read_copilot_journal(path, base=base)

    def _result(skipped: str, new: int = 0, known: int = 0, held: dict[str, int] | None = None) -> CopilotFoldResult:
        return CopilotFoldResult(
            source=session.path,
            session_id=session.session_id,
            new_calls=new,
            already_folded=known,
            unreadable_lines=session.unreadable_lines,
            invalid_usage=session.invalid_usage,
            shutdowns=session.shutdowns,
            skipped=skipped,
            held_by_other_tasks=held or {},
        )

    if not session.recognized:
        return _result("unrecognized")
    if session.truncated:
        return _result("truncated")
    if not session.ended or not session.models:
        return _result("not_ended")
    traces = [
        _trace_for(m, session, task_id=task_id, mission_id=mission_id, pricing_for=pricing_for) for m in session.models
    ]
    written = ledger.append_new(traces)
    held: dict[str, int] = {}
    if len(written) < len(traces):
        pending = {t.id for t in traces} - {t.id for t in written}
        for existing in ledger.list_traces():
            if existing.id in pending and existing.task_id != task_id:
                held[existing.task_id] = held.get(existing.task_id, 0) + 1
    return _result("", len(written), len(traces) - len(written), held)
