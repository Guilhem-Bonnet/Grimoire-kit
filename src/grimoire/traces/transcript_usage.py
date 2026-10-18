"""Usage de tokens replié depuis le journal de session de l'hôte (W4-01, OBS-2).

Avant ce module, le coût d'une tâche ne venait que du JSON d'un ouvrier
(``missions/dispatch.py``) ou d'un ``task record-model-call`` lancé à la main :
une session interactive, elle, ne laissait aucun chiffre. Claude Code écrit pourtant
chaque appel dans ``<session>.jsonl`` (``assistant.message.usage``, fixture K0
``tests/fixtures/hosts/claude-code/08-token-usage.json``). On le replie ici dans le
TraceLedger, rattaché à la tâche.

Règles, chacune prise sur le journal réel :

* **Un appel API = un ``message.id``.** Claude Code écrit une ligne par bloc de
  contenu (réflexion, texte, outil) et répète le même ``usage`` sur chacune : sommer
  les lignes multiplierait le coût par le nombre de blocs. On compte un appel par
  ``message.id`` (``uuid`` à défaut), avec le maximum de chaque compteur du groupe.
* **Idempotent par construction.** L'identifiant de la trace est dérivé de
  ``(fichier source, message.id)`` : rejouer le repli, ou le rejouer sur un fichier
  qui a grandi au milieu d'un groupe, n'écrit rien de plus. Le fichier source fait
  partie de la clé : le même ``message.id`` dans deux fichiers reste deux appels.
* **Jamais de chiffre inventé.** Une ligne dont les compteurs sont absents ou
  invalides est comptée dans ``invalid_usage``, pas ajoutée. Le coût d'un appel est
  inconnu (``None``) quand le modèle n'est pas tarifé ou que des tokens de cache
  existent : la table de tarifs (:mod:`grimoire.tools.cost_model`) ne modélise pas
  les tarifs de cache, les chiffrer au tarif d'entrée serait faux.

Limite : seul le format de journal de Claude Code est lu. Un hôte sans source
vérifiée (``local_token_usage`` de :mod:`grimoire.hosts.capabilities`) est déclaré
``unavailable`` par :func:`task_usage`, pas rempli d'estimations.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.costs import Cost
from grimoire.tools.cost_model import rate_for
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TokenUsage, TraceOutcome, TraceRecord

#: Valeur de ``usage_source`` pour un appel replié du journal de session Claude Code.
SOURCE_CLAUDE_TRANSCRIPT = "claude-code-transcript"
SOURCE_TAG_PREFIX = "usage.source:"
CACHE_READ_TAG_PREFIX = "usage.cache_read:"
CACHE_CREATION_TAG_PREFIX = "usage.cache_creation:"
TRANSCRIPT_RECIPE_ID = "grimoire.model-call.transcript"
#: Recettes des traces dont le coût vient de l'ouvrier ou d'un appel déclaré à la main.
REPORTED_RECIPE_IDS: frozenset[str] = frozenset({"grimoire.dispatch", "grimoire.model-call"})
#: Écart relatif au-delà duquel le coût du journal et le coût rapporté se contredisent.
DISCREPANCY_RATIO = 0.10
_SYNTHETIC_MODEL = "<synthetic>"


@dataclass(frozen=True, slots=True)
class UsageCall:
    """Un appel API tel que le journal l'a écrit (un ``message.id``)."""

    message_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    timestamp: str
    agent: str
    session_id: str


@dataclass(frozen=True, slots=True)
class TranscriptRead:
    """Ce qu'une lecture du journal a trouvé, et ce qu'elle a dû écarter."""

    calls: tuple[UsageCall, ...]
    #: Lignes qui ne sont pas du JSON (dont la dernière, tronquée, d'un journal en cours d'écriture).
    unreadable_lines: int = 0
    #: Lignes d'assistant sans compteurs entiers valides : écartées, jamais complétées.
    invalid_usage: int = 0
    #: Lignes ``<synthetic>`` : messages d'erreur de l'hôte, pas des appels.
    synthetic: int = 0


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Résultat d'un repli : ce qui est nouveau, ce qui l'était déjà, ce qui a été écarté."""

    source: str
    new_calls: int
    already_folded: int
    unreadable_lines: int
    invalid_usage: int
    synthetic: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "usage_source": SOURCE_CLAUDE_TRANSCRIPT,
            "new_calls": self.new_calls,
            "already_folded": self.already_folded,
            "unreadable_lines": self.unreadable_lines,
            "invalid_usage": self.invalid_usage,
            "synthetic": self.synthetic,
        }


def _text(value: Any) -> str:
    """Une chaîne du journal, encodable en UTF-8 strict (un substitut isolé devient ``?``).

    ``json.loads`` accepte l'échappement d'un substitut UTF-16 isolé et rend un
    ``str`` que ni le journal de traces ni la frontière PyO3 ne savent encoder : on le neutralise à la lecture.
    """
    return value.encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else ""


def _count(value: Any) -> int | None:
    """Un compteur de tokens valide : entier non négatif, jamais un booléen."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _call_from_line(line: dict[str, Any]) -> UsageCall | None:
    """L'appel porté par une ligne ``assistant``, ``None`` si les compteurs ne sont pas exploitables."""
    message = line.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(message, dict) or not isinstance(usage, dict):
        return None
    tokens_in = _count(usage.get("input_tokens"))
    tokens_out = _count(usage.get("output_tokens"))
    cache_read = _count(usage.get("cache_read_input_tokens", 0))
    cache_creation = _count(usage.get("cache_creation_input_tokens", 0))
    if tokens_in is None or tokens_out is None or cache_read is None or cache_creation is None:
        return None
    raw_id = _text(message.get("id") or line.get("uuid"))
    if not raw_id:
        return None
    return UsageCall(
        message_id=raw_id,
        model=_text(message.get("model")),
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        timestamp=_text(line.get("timestamp")),
        agent=_text(line.get("attributionAgent")),
        session_id=_text(line.get("sessionId")),
    )


def read_claude_transcript(path: Path) -> TranscriptRead:
    """Lit un journal de session Claude Code : un :class:`UsageCall` par ``message.id``.

    Les lignes d'un même groupe se fondent en une seule, au maximum de chaque
    compteur (elles portent le même ``usage``). L'ordre est celui de la première
    ligne de chaque groupe. Lève ``OSError`` si le fichier est illisible : le
    repli ne l'avale pas.
    """
    merged: dict[str, UsageCall] = {}
    unreadable = invalid = synthetic = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            text = raw.strip()
            if not text:
                continue
            try:
                line = json.loads(text)
            except json.JSONDecodeError:
                unreadable += 1
                continue
            if not isinstance(line, dict) or line.get("type") != "assistant":
                continue
            message = line.get("message")
            if isinstance(message, dict) and message.get("model") == _SYNTHETIC_MODEL:
                synthetic += 1
                continue
            call = _call_from_line(line)
            if call is None:
                invalid += 1
                continue
            first = merged.get(call.message_id)
            if first is None:
                merged[call.message_id] = call
                continue
            merged[call.message_id] = UsageCall(
                message_id=first.message_id,
                model=first.model or call.model,
                input_tokens=max(first.input_tokens, call.input_tokens),
                output_tokens=max(first.output_tokens, call.output_tokens),
                cache_read_tokens=max(first.cache_read_tokens, call.cache_read_tokens),
                cache_creation_tokens=max(first.cache_creation_tokens, call.cache_creation_tokens),
                timestamp=first.timestamp or call.timestamp,
                agent=first.agent or call.agent,
                session_id=first.session_id or call.session_id,
            )
    return TranscriptRead(tuple(merged.values()), unreadable, invalid, synthetic)


def usage_key(source: str, message_id: str) -> str:
    """Clé stable d'un appel : le fichier source et le ``message.id``, jamais l'un sans l'autre."""
    digest = hashlib.sha256(f"{source}\0{message_id}".encode()).hexdigest()
    return digest[:24]


def call_cost_usd(call: UsageCall) -> float | None:
    """Coût d'un appel au tarif de sa version exacte ; ``None`` si on ne peut pas l'affirmer.

    Des tokens de cache ou un modèle absent de la table donnent ``None`` : la
    table ne porte ni tarif de cache ni famille de repli.
    """
    if call.cache_read_tokens or call.cache_creation_tokens:
        return None
    rates = rate_for(call.model)
    if rates is None:
        return None
    return round((call.input_tokens * rates["in"] + call.output_tokens * rates["out"]) / 1_000_000, 6)


def _trace_for(call: UsageCall, *, source: str, task_id: str, mission_id: str) -> TraceRecord:
    cost = call_cost_usd(call)
    total = call.input_tokens + call.output_tokens + call.cache_read_tokens + call.cache_creation_tokens
    return TraceRecord(
        id=f"TRC-usage-{usage_key(source, call.message_id)}",
        run_id=f"usage-{task_id}",
        workflow_instance_id="",
        mission_id=mission_id,
        task_id=task_id,
        recipe_id=TRANSCRIPT_RECIPE_ID,
        outcome=TraceOutcome.SUCCESS,
        started_at=call.timestamp,
        completed_at=call.timestamp,
        agent_id=call.agent,
        host_id="host-claude-code-cli",
        model=call.model,
        token_usage=TokenUsage(
            prompt_tokens=call.input_tokens,
            completion_tokens=call.output_tokens,
            total_tokens=total,
            estimated_cost_usd=cost,
            unpriced_calls=0 if cost is not None else 1,
        ),
        tags=(
            f"{SOURCE_TAG_PREFIX}{SOURCE_CLAUDE_TRANSCRIPT}",
            f"{CACHE_READ_TAG_PREFIX}{call.cache_read_tokens}",
            f"{CACHE_CREATION_TAG_PREFIX}{call.cache_creation_tokens}",
        ),
    )


def fold_claude_transcript(
    ledger: TraceLedger,
    path: Path,
    *,
    task_id: str,
    mission_id: str = "",
) -> FoldResult:
    """Replie les appels d'un journal Claude Code dans le *ledger*, rattachés à *task_id*.

    Idempotent : un appel déjà replié (même fichier, même ``message.id``) n'est
    pas réécrit, même sous une autre tâche. Le fichier source est identifié par
    son chemin résolu.
    """
    source = str(path.resolve()).encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    read = read_claude_transcript(path)
    traces = [_trace_for(call, source=source, task_id=task_id, mission_id=mission_id) for call in read.calls]
    written = ledger.append_new(traces)
    return FoldResult(
        source=source,
        new_calls=len(written),
        already_folded=len(traces) - len(written),
        unreadable_lines=read.unreadable_lines,
        invalid_usage=read.invalid_usage,
        synthetic=read.synthetic,
    )


def _tag_value(tags: Iterable[str], prefix: str) -> str:
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return ""


def _tag_count(tags: Iterable[str], prefix: str) -> int:
    value = _tag_value(tags, prefix)
    return int(value) if value.isdigit() else 0


def task_usage(traces: Sequence[TraceRecord], *, host_usage_available: bool | None = None) -> dict[str, Any]:
    """Les tokens et le coût d'une tâche (*traces* : celles de la tâche), avec la provenance de chaque chiffre.

    ``usage_source`` dit d'où vient le chiffre : ``claude-code-transcript``
    (journal de l'hôte), ``reported`` (seulement le JSON d'un ouvrier ou un appel
    déclaré à la main), ``unavailable`` (l'hôte de la tâche n'a pas de source
    vérifiée, *host_usage_available* est ``False``) ou ``none`` (rien replié).
    ``cost_known`` n'est vrai que si tous les appels comptés sont tarifés.

    Le coût du journal et le coût rapporté ne s'additionnent pas (ce sont deux
    vues des mêmes dépenses) : ils sont exposés côte à côte, et un écart
    supérieur à :data:`DISCREPANCY_RATIO` est signalé dans ``discrepancy``.
    """
    folded = [t for t in traces if _tag_value(t.tags, SOURCE_TAG_PREFIX) == SOURCE_CLAUDE_TRANSCRIPT]
    reported = [t for t in traces if t.recipe_id in REPORTED_RECIPE_IDS]
    cost = Cost.total(t.token_usage.cost for t in folded)
    reported_cost = Cost.total(t.token_usage.cost for t in reported)

    if folded:
        usage_source = SOURCE_CLAUDE_TRANSCRIPT
    elif reported:
        usage_source = "reported"
    elif host_usage_available is False:
        usage_source = "unavailable"
    else:
        usage_source = "none"

    cost_known = bool(folded) and cost.status == "exact"
    reported_known = bool(reported) and reported_cost.status == "exact"
    discrepancy: dict[str, Any] | None = None
    if cost_known and reported_known and cost.usd is not None and reported_cost.usd is not None:
        top = max(cost.usd, reported_cost.usd)
        gap = abs(cost.usd - reported_cost.usd) / top if top else 0.0
        if gap > DISCREPANCY_RATIO:
            discrepancy = {
                "transcript_cost_usd": cost.usd,
                "reported_cost_usd": reported_cost.usd,
                "relative_gap": round(gap, 4),
            }

    return {
        "usage_source": usage_source,
        "calls": len(folded),
        "input_tokens": sum(t.token_usage.prompt_tokens for t in folded),
        "output_tokens": sum(t.token_usage.completion_tokens for t in folded),
        "cache_read_tokens": sum(_tag_count(t.tags, CACHE_READ_TAG_PREFIX) for t in folded),
        "cache_creation_tokens": sum(_tag_count(t.tags, CACHE_CREATION_TAG_PREFIX) for t in folded),
        "total_tokens": sum(t.token_usage.total_tokens for t in folded),
        "cost_usd": cost.usd if folded else None,
        "cost_status": cost.status if folded else "unknown",
        "cost_known": cost_known,
        "unpriced_calls": cost.unpriced_calls if folded else 0,
        "reported": (
            {
                "calls": len(reported),
                "cost_usd": reported_cost.usd,
                "cost_status": reported_cost.status,
                "unpriced_calls": reported_cost.unpriced_calls,
            }
            if reported
            else None
        ),
        "discrepancy": discrepancy,
    }
