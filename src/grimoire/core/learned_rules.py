"""Règles candidates tirées d'événements observés, et effet mesuré (W6-04).

Chez AI-DLC, l'apprentissage est un journal que le modèle rédige lui-même, puis
qu'un humain trie à chaque porte ; ce qui est retenu s'écrit sans jamais être
mesuré. Ici, rien n'est rédigé ni demandé :

- **Le candidat est un fait observé.** La source est le refus de gate que
  :meth:`grimoire.missions.service.TaskService._record_refusal` écrit au
  TraceLedger (``recipe_id`` ``grimoire.task-gate``) : une transition déclarée
  dans ``evidence-gates.yaml`` (``in_progress_to_review``…) et la preuve qui
  manquait (``evidence_pack``…). La signature est ce couple.
- **Le seuil compte des tâches distinctes**, deux au moins, jamais une : une
  tâche qui bute dix fois sur le même gate est une tâche coincée, pas une
  leçon. Le nombre brut de refus est rendu à côté, sans s'y substituer.
- **La proposition est une règle parsable** (grammaire de
  :mod:`grimoire.core.layered_rules`) : ``ALWAYS provide <preuve> before
  <transition>``. Une signature dont la règle ne se parse pas n'est pas
  proposée, et le rapport le dit.
- **L'acceptation passe par** :func:`grimoire.core.layered_rules.add_rule`,
  donc par la frontière de confiance, la grammaire, le conflit et le plafond.
  Un refus n'écrit rien, ni la règle ni le statut de la proposition.
- **L'effet se mesure sur la même signature.** Pour une règle acceptée, les
  tâches exposées à la transition (refusées ou passées) se répartissent avant
  et après l'acceptation, selon leur première exposition ; le taux est la part
  de ces tâches que la signature a refusées. Le statut est ``unproven`` tant
  qu'un côté compte moins de ``min_tasks`` tâches, puis se décide par un test
  exact de Fisher unilatéral (``effective`` si le taux baisse significativement,
  ``harmful`` s'il monte, ``unproven`` sinon). Aucun score de confiance n'est
  affiché : rien ne le calibre.

Le calcul ne lit aucune horloge : deux exécutions sur les mêmes journaux
rendent le même JSON, et ``propose`` rejoué ne réécrit aucun fichier.

Limites, écrites plutôt que tues :

- la seule source est le refus de gate de tâche ; un refus d'outil par la
  policy, un ``flow resume`` après rejet ou une correction humaine ne
  produisent pas encore de candidat ;
- une tâche est rangée dans la période de sa *première* exposition : refusée
  avant l'acceptation puis à nouveau après, elle compte dans « avant » ;
- avant/après n'est pas une expérience contrôlée : une baisse peut venir d'autre
  chose que de la règle (nouveau modèle, autre équipe). ``effective`` dit que la
  signature a reculé après l'acceptation, pas que la règle en est la cause ;
- une transition réussie qui ne passe pas par un état du ledger connu du board
  n'est pas comptée comme exposition, ce qui ne peut que retarder ``effective``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.core.standard_generation import PROPOSALS_DIR, TRACES_DIR

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_EFFECT_MIN_TASKS",
    "DEFAULT_MIN_TASKS",
    "DEFAULT_REVIEW_AFTER",
    "GATE_RECIPE",
    "RULE_PROPOSALS_DIR",
    "Exposure",
    "ProposeReport",
    "Refusal",
    "RuleCandidate",
    "RuleProposalError",
    "accept_rule_proposal",
    "candidate_text",
    "candidates_from_refusals",
    "exposures_from_events",
    "fisher_one_sided",
    "list_rule_proposals",
    "measure_effects",
    "proposal_id",
    "propose_rules",
    "refusals_from_traces",
    "reject_rule_proposal",
    "rule_effects",
]

#: Le ``recipe_id`` des refus de gate écrits par ``TaskService._record_refusal``.
#: Recopié de :data:`grimoire.missions.trace.TASK_GATE_RECIPE` (un test vérifie
#: l'égalité) : ce module ne charge pas la timeline pour une constante.
GATE_RECIPE = "grimoire.task-gate"

#: Sous-dossier des propositions de règle. ``proposals.py`` ne lit que
#: ``proposals/*.yaml`` (glob non récursif) : les deux cycles ne se mélangent pas.
RULE_PROPOSALS_DIR = PROPOSALS_DIR / "rules"

#: Tâches distinctes qu'il faut à une signature pour devenir une proposition.
#: Jamais moins de deux, comme le déclencheur d'agents (``proposals.py``).
DEFAULT_MIN_TASKS = 2

#: Tâches exposées qu'il faut de chaque côté de l'acceptation avant de juger.
DEFAULT_EFFECT_MIN_TASKS = 5

#: Tâches exposées après l'acceptation au-delà desquelles une règle toujours
#: ``unproven`` est proposée au retrait.
DEFAULT_REVIEW_AFTER = 20

#: Seuil du test exact unilatéral.
DEFAULT_ALPHA = 0.05

#: Citations de preuve gardées par proposition (le total est rendu à part).
_MAX_CITATIONS = 20

#: Ce qu'un mot de la grammaire admet ; une signature hors de ce jeu ne devient
#: pas une règle (elle est signalée ``skipped``).
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")

_STATUSES = ("pending", "accepted", "rejected")


class RuleProposalError(GrimoireMemoryError):
    """Une proposition introuvable, illisible, ou dans un statut qui interdit le geste."""


# ── Faits observés ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Refusal:
    """Un refus de gate : la tâche, la transition déclarée, la preuve qui manquait."""

    task_id: str
    transition: str
    evidence: str
    at: str
    trace_id: str

    @property
    def signature(self) -> tuple[str, str]:
        return (self.transition, self.evidence)


@dataclass(frozen=True, slots=True)
class Exposure:
    """Une tâche qui s'est présentée à une transition (refusée ou passée), et quand."""

    task_id: str
    transition: str
    at: str


@dataclass(frozen=True, slots=True)
class RuleCandidate:
    """Une signature vue sur assez de tâches distinctes, et la règle qui la vise."""

    transition: str
    evidence: str
    rule: str
    occurrences: int
    tasks: tuple[str, ...]
    first_seen: str
    last_seen: str
    citations: tuple[dict[str, str], ...]

    @property
    def id(self) -> str:
        return proposal_id(self.transition, self.evidence)


@dataclass(frozen=True, slots=True)
class ProposeReport:
    """Ce que ``propose`` a fait, fichier par fichier."""

    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    reopened: tuple[str, ...] = ()
    skipped: tuple[dict[str, str], ...] = ()
    below_threshold: int = 0
    min_tasks: int = DEFAULT_MIN_TASKS

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_tasks": self.min_tasks,
            "created": list(self.created),
            "updated": list(self.updated),
            "reopened": list(self.reopened),
            "unchanged": list(self.unchanged),
            "skipped": [dict(s) for s in self.skipped],
            "below_threshold": self.below_threshold,
        }


def _parse_at(raw: str) -> datetime | None:
    """ISO 8601 vers un instant UTC ; ``None`` si illisible (jamais deviné)."""
    try:
        moment = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _order_key(at: str, tie: str) -> tuple[int, datetime, str]:
    """Tri chronologique stable ; un horodatage illisible passe en dernier."""
    moment = _parse_at(at)
    return (0, moment, tie) if moment is not None else (1, datetime.max.replace(tzinfo=UTC), tie)


def refusals_from_traces(traces: Iterable[Any], declared: Iterable[str]) -> list[Refusal]:
    """Les refus de gate des *traces*, sur une transition déclarée. Pure.

    *declared* : les identifiants de transition de ``evidence-gates.yaml``. Un
    refus hors déclaration (fichier de gates illisible, garde ``exclusive_files``)
    n'est pas une leçon sur une preuve : il n'est pas retenu.
    """
    known = set(declared)
    out: list[Refusal] = []
    for trace in traces:
        if getattr(trace, "recipe_id", "") != GATE_RECIPE or not getattr(trace, "task_id", ""):
            continue
        transition = next((t for t in trace.tags if t != "task.gate"), "")
        if transition not in known:
            continue
        for verdict in trace.policy_verdicts:
            if verdict.verdict == "block" and verdict.verdict_id:
                out.append(Refusal(trace.task_id, transition, verdict.verdict_id, trace.started_at, trace.id))
    return out


def exposures_from_events(
    refusals: Iterable[Refusal],
    events: Iterable[Any],
    transition_of: Callable[[str, str], str],
) -> list[Exposure]:
    """Chaque tâche présentée à une transition, au moment de sa première présentation. Pure.

    Deux sources : un refus de gate (la tâche s'est présentée et a été refusée),
    un ``task.transitioned`` du Mission Ledger que *transition_of* (états ledger
    → identifiant de transition déclarée, ``""`` sinon) rattache à une transition.
    """
    first: dict[tuple[str, str], tuple[tuple[int, datetime, str], str]] = {}

    def see(task_id: str, transition: str, at: str, tie: str) -> None:
        key = (task_id, transition)
        order = _order_key(at, tie)
        if key not in first or order < first[key][0]:
            first[key] = (order, at)

    for refusal in refusals:
        see(refusal.task_id, refusal.transition, refusal.at, refusal.trace_id)
    for event in events:
        if getattr(event, "event_type", "") != "task.transitioned":
            continue
        payload = getattr(event, "payload", {}) or {}
        transition = transition_of(str(payload.get("from_state", "")), str(payload.get("to_state", "")))
        task_id = str(payload.get("task_id", "") or getattr(event, "entity_id", ""))
        if transition and task_id:
            see(task_id, transition, str(getattr(event, "created_at", "")), str(getattr(event, "id", "")))
    return [Exposure(task, transition, at) for (task, transition), (_, at) in sorted(first.items())]


# ── Candidats ─────────────────────────────────────────────────────────────────


def candidate_text(transition: str, evidence: str) -> str:
    """La règle qui vise une signature : fournir la preuve avant de demander la transition."""
    return f"ALWAYS provide {evidence} before {transition}"


def proposal_id(transition: str, evidence: str) -> str:
    """Identifiant stable d'une signature : lisible, et sans collision grâce à l'empreinte."""
    digest = hashlib.sha256(f"{transition}\0{evidence}".encode()).hexdigest()[:8]
    readable = _SLUG_UNSAFE.sub("-", f"{transition}-{evidence}".lower()).strip("-")[:60]
    return f"gate-{readable}-{digest}" if readable else f"gate-{digest}"


def _render(transition: str, evidence: str) -> tuple[str, str]:
    """``(règle canonique, "")`` si la signature fait une règle, ``("", motif)`` sinon.

    La preuve et la transition doivent chacune tenir en un mot de la grammaire :
    un nom de preuve à espace (``a before b``) changerait le sens de la règle
    au lieu d'être refusé par elle.
    """
    from grimoire.core.layered_rules import RuleRefusedError, parse_rule

    for name, what in ((evidence, "preuve"), (transition, "transition")):
        if not name or any(c.isspace() for c in name):
            return "", f"nom de {what} {name[:40]!r} : un mot, sans espace"
    try:
        rule = parse_rule(candidate_text(transition, evidence))
    except RuleRefusedError as exc:
        return "", str(exc)
    return rule.render(), ""


def candidates_from_refusals(
    refusals: Iterable[Refusal], *, min_tasks: int = DEFAULT_MIN_TASKS
) -> tuple[list[RuleCandidate], list[dict[str, str]], int]:
    """Regrouper par signature ; ne garder que celles vues sur *min_tasks* tâches distinctes. Pure.

    Rend ``(candidats, écartés, sous_le_seuil)`` : les signatures dont la règle ne
    se parse pas sont écartées avec leur motif, celles sous le seuil sont comptées.
    """
    if min_tasks < 2:
        raise ValueError(f"min_tasks vaut au moins 2 (reçu {min_tasks}) : une occurrence n'est pas une leçon.")
    groups: dict[tuple[str, str], list[Refusal]] = {}
    for refusal in refusals:
        groups.setdefault(refusal.signature, []).append(refusal)
    candidates: list[RuleCandidate] = []
    skipped: list[dict[str, str]] = []
    below = 0
    for (transition, evidence), items in sorted(groups.items()):
        tasks = tuple(sorted({r.task_id for r in items}))
        if len(tasks) < min_tasks:
            below += 1
            continue
        text, motif = _render(transition, evidence)
        if motif:
            skipped.append({"transition": transition, "evidence": evidence, "reason": motif})
            continue
        ordered = sorted(items, key=lambda r: _order_key(r.at, r.trace_id))
        dated = [r.at for r in ordered if _parse_at(r.at) is not None]
        candidates.append(RuleCandidate(
            transition=transition,
            evidence=evidence,
            rule=text,
            occurrences=len(items),
            tasks=tasks,
            first_seen=dated[0] if dated else "",
            last_seen=dated[-1] if dated else "",
            citations=tuple(
                {"task_id": r.task_id, "trace_id": r.trace_id, "at": r.at} for r in ordered[:_MAX_CITATIONS]
            ),
        ))
    return candidates, skipped, below


# ── Stockage des propositions ─────────────────────────────────────────────────


def _proposals_dir(root: Path) -> Path:
    return Path(root) / RULE_PROPOSALS_DIR


def _proposal_path(root: Path, pid: str) -> Path:
    if not re.fullmatch(r"gate-[a-z0-9-]{1,80}", pid):
        raise RuleProposalError(f"Identifiant de proposition invalide : {pid!r}.")
    return _proposals_dir(root) / f"{pid}.yaml"


def _dump(data: Mapping[str, Any]) -> str:
    import io

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(dict(data), buf)
    return buf.getvalue()


def _load(path: Path) -> dict[str, Any]:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise RuleProposalError(f"{path.name} illisible : {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict) or data.get("status") not in _STATUSES:
        raise RuleProposalError(f"{path.name} : proposition mal formée (statut attendu : {', '.join(_STATUSES)}).")
    return data


def _candidate_record(candidate: RuleCandidate, *, status: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": candidate.id,
        "kind": "rule",
        "status": status,
        "rule": candidate.rule,
        "signature": {"source": GATE_RECIPE, "transition": candidate.transition, "evidence": candidate.evidence},
        "occurrences": candidate.occurrences,
        "distinct_tasks": len(candidate.tasks),
        "tasks": list(candidate.tasks),
        "first_seen": candidate.first_seen,
        "last_seen": candidate.last_seen,
        "evidence": [dict(c) for c in candidate.citations],
    }
    record.update(extra or {})
    return record


def _write_if_changed(path: Path, text: str) -> bool:
    from grimoire.core.fsio import atomic_write

    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    atomic_write(path, text)
    return True


def propose_rules(
    root: Path, *, ledger_root: Path | None = None, min_tasks: int = DEFAULT_MIN_TASKS
) -> ProposeReport:
    """Lire les refus observés et écrire une proposition par signature au-dessus du seuil.

    Idempotent : une proposition ``pending`` est réécrite seulement si ses faits
    ont changé ; ``accepted`` n'est jamais touchée (son instantané date
    l'acceptation) ; ``rejected`` ne revient en ``pending`` qu'une fois le
    nombre de tâches distinctes au moins doublé depuis le refus.
    """
    from grimoire.core.fsio import locked

    root = Path(root)
    refusals, _ = _observe(root, ledger_root)
    candidates, skipped, below = candidates_from_refusals(refusals, min_tasks=min_tasks)
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    reopened: list[str] = []
    for candidate in candidates:
        path = _proposal_path(root, candidate.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path):
            if not path.is_file():
                _write_if_changed(path, _dump(_candidate_record(candidate, status="pending")))
                created.append(candidate.id)
                continue
            current = _load(path)
            status = current["status"]
            if status == "accepted":
                unchanged.append(candidate.id)
                continue
            if status == "rejected":
                at_refusal = int(current.get("rejected_at_tasks") or 0)
                if len(candidate.tasks) < 2 * max(at_refusal, 1):
                    unchanged.append(candidate.id)
                    continue
                _write_if_changed(path, _dump(_candidate_record(candidate, status="pending")))
                reopened.append(candidate.id)
                continue
            changed = _write_if_changed(path, _dump(_candidate_record(candidate, status="pending")))
            (updated if changed else unchanged).append(candidate.id)
    return ProposeReport(
        created=tuple(created), updated=tuple(updated), unchanged=tuple(unchanged), reopened=tuple(reopened),
        skipped=tuple(skipped), below_threshold=below, min_tasks=min_tasks,
    )


def list_rule_proposals(root: Path, *, status: str | None = None) -> list[dict[str, Any]]:
    """Les propositions de règle, triées par identifiant ; *status* filtre."""
    if status is not None and status not in _STATUSES:
        raise RuleProposalError(f"Statut inconnu : {status!r} (attendus : {', '.join(_STATUSES)}).")
    folder = _proposals_dir(Path(root))
    if not folder.is_dir():
        return []
    found = [_load(path) for path in sorted(folder.glob("gate-*.yaml"))]
    return [p for p in found if status is None or p["status"] == status]


def _transition_status(root: Path, pid: str, update: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    from grimoire.core.fsio import atomic_write, locked

    path = _proposal_path(Path(root), pid)
    if not path.is_file():
        raise RuleProposalError(f"Proposition introuvable : {pid}. `grimoire memory rules proposals` les liste.")
    with locked(path):
        current = _load(path)
        if current["status"] != "pending":
            raise RuleProposalError(f"{pid} est déjà {current['status']} : seule une proposition pending se décide.")
        record = update(current)
        atomic_write(path, _dump(record))
    return record


def accept_rule_proposal(
    root: Path, pid: str, *, layer: str = "project", project_name: str = "project", now: datetime | None = None
) -> dict[str, Any]:
    """Écrire la règle par :func:`~grimoire.core.layered_rules.add_rule`, puis marquer ``accepted``.

    Un refus de la règle (frontière de confiance, conflit, plafond) lève
    :class:`~grimoire.core.layered_rules.RuleRefusedError` : rien n'est écrit,
    la proposition reste ``pending``.
    """
    from grimoire.core.layered_rules import add_rule

    root = Path(root)
    path = _proposal_path(root, pid)
    if not path.is_file():
        raise RuleProposalError(f"Proposition introuvable : {pid}. `grimoire memory rules proposals` les liste.")
    current = _load(path)
    if current["status"] != "pending":
        raise RuleProposalError(f"{pid} est déjà {current['status']} : seule une proposition pending se décide.")
    # Émetteur « cli » : `accept` est une commande de la CLI, et « cli » figure
    # dans toute liste d'autorisation, y compris celles déjà copiées dans
    # `memory-policy.yaml` d'un projet existant (un nouvel émetteur ajouté aux
    # défauts n'y serait jamais, et `emitter_enforcement: refuse` l'écarterait).
    add_rule(root, layer, str(current.get("rule", "")), project_name=project_name, emitter="cli")
    moment = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    return _transition_status(root, pid, lambda rec: {**rec, "status": "accepted", "layer": layer, "accepted_at": moment})


def reject_rule_proposal(root: Path, pid: str) -> dict[str, Any]:
    """Marquer ``rejected`` en retenant le nombre de tâches : il faudra le doubler pour revenir."""
    return _transition_status(
        Path(root), pid,
        lambda rec: {**rec, "status": "rejected", "rejected_at_tasks": int(rec.get("distinct_tasks") or 0)},
    )


# ── Effet mesuré ──────────────────────────────────────────────────────────────


def _hypergeom(k: int, total: int, hits: int, draws: int) -> Fraction:
    return Fraction(math.comb(hits, k) * math.comb(total - hits, draws - k), math.comb(total, draws))


def fisher_one_sided(hits_before: int, n_before: int, hits_after: int, n_after: int) -> tuple[float, float]:
    """Test exact de Fisher, unilatéral dans les deux sens : ``(p_baisse, p_hausse)``. Pure.

    ``p_baisse`` : probabilité, sous l'hypothèse « la règle ne change rien »,
    d'observer *hits_after* refus ou moins après l'acceptation. Calcul exact en
    fractions puis arrondi à six décimales : le JSON est le même partout.
    """
    total, hits, draws = n_before + n_after, hits_before + hits_after, n_after
    low, high = max(0, draws - (total - hits)), min(hits, draws)
    p_less = sum((_hypergeom(k, total, hits, draws) for k in range(low, hits_after + 1)), Fraction(0))
    p_more = sum((_hypergeom(k, total, hits, draws) for k in range(hits_after, high + 1)), Fraction(0))
    return round(float(p_less), 6), round(float(p_more), 6)


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 6) if n else None


@dataclass(frozen=True, slots=True)
class _Split:
    n_before: int = 0
    n_after: int = 0
    hits_before: int = 0
    hits_after: int = 0
    unplaced: int = 0


def _split(accepted: datetime, signature: tuple[str, str], refusals: list[Refusal], exposures: list[Exposure]) -> _Split:
    transition = signature[0]
    hit = {r.task_id for r in refusals if r.signature == signature}
    n_before = n_after = hits_before = hits_after = unplaced = 0
    for exposure in exposures:
        if exposure.transition != transition:
            continue
        moment = _parse_at(exposure.at)
        if moment is None:
            unplaced += 1
            continue
        refused = exposure.task_id in hit
        if moment < accepted:
            n_before += 1
            hits_before += refused
        else:
            n_after += 1
            hits_after += refused
    return _Split(n_before, n_after, hits_before, hits_after, unplaced)


def measure_effects(
    proposals: Iterable[Mapping[str, Any]],
    refusals: Iterable[Refusal],
    exposures: Iterable[Exposure],
    *,
    min_tasks: int = DEFAULT_EFFECT_MIN_TASKS,
    alpha: float = DEFAULT_ALPHA,
    review_after: int = DEFAULT_REVIEW_AFTER,
) -> dict[str, Any]:
    """L'effet de chaque règle acceptée sur sa signature. Pure, sans horloge.

    ``unproven`` tant qu'un côté compte moins de *min_tasks* tâches exposées,
    quel que soit l'écart des taux : un petit échantillon ne prouve rien.
    """
    if min_tasks < 2:
        raise ValueError(f"min_tasks vaut au moins 2 (reçu {min_tasks}).")
    refusal_list, exposure_list = list(refusals), list(exposures)
    rows: list[dict[str, Any]] = []
    for proposal in sorted((p for p in proposals if p.get("status") == "accepted"), key=lambda p: str(p.get("id", ""))):
        signature_raw = proposal.get("signature") or {}
        signature = (str(signature_raw.get("transition", "")), str(signature_raw.get("evidence", "")))
        accepted = _parse_at(str(proposal.get("accepted_at", "")))
        row: dict[str, Any] = {
            "id": str(proposal.get("id", "")),
            "rule": str(proposal.get("rule", "")),
            "layer": str(proposal.get("layer", "")),
            "signature": {"transition": signature[0], "evidence": signature[1]},
            "accepted_at": str(proposal.get("accepted_at", "")),
        }
        if accepted is None:
            row.update({
                "n_before": None, "n_after": None, "hits_before": None, "hits_after": None,
                "before_rate": None, "after_rate": None, "p_reduced": None, "p_increased": None,
                "unplaced": None, "status": "unproven", "reason": "accepted_at illisible : aucune frontière avant/après",
                "retire_suggested": False,
            })
            rows.append(row)
            continue
        split = _split(accepted, signature, refusal_list, exposure_list)
        row.update({
            "n_before": split.n_before, "n_after": split.n_after,
            "hits_before": split.hits_before, "hits_after": split.hits_after,
            "before_rate": _rate(split.hits_before, split.n_before),
            "after_rate": _rate(split.hits_after, split.n_after),
            "unplaced": split.unplaced,
        })
        # Une référence sous le seuil ne se rattrape pas : `_split` ne compte
        # `n_before` que sur les expositions antérieures à l'acceptation, que
        # plus rien n'allonge. Une règle sans référence n'est pas une règle qui
        # échoue : on ne peut pas la juger, donc on ne propose pas de la retirer.
        baseline_closed = split.n_before < min_tasks
        if split.n_before < min_tasks or split.n_after < min_tasks:
            reason = f"échantillon insuffisant : {split.n_before} avant, {split.n_after} après, {min_tasks} requis de chaque côté"
            if baseline_closed:
                reason += " ; la référence d'avant l'acceptation est close, l'effet est non mesurable (retrait non suggéré)"
            row.update({"p_reduced": None, "p_increased": None, "status": "unproven", "reason": reason})
        else:
            p_reduced, p_increased = fisher_one_sided(split.hits_before, split.n_before, split.hits_after, split.n_after)
            row.update({"p_reduced": p_reduced, "p_increased": p_increased})
            if p_reduced < alpha:
                row.update({"status": "effective", "reason": f"baisse significative (p={p_reduced} < {alpha})"})
            elif p_increased < alpha:
                row.update({"status": "harmful", "reason": f"hausse significative (p={p_increased} < {alpha})"})
            else:
                row.update({"status": "unproven", "reason": f"écart non significatif (p={min(p_reduced, p_increased)} ≥ {alpha})"})
        row["retire_suggested"] = row["status"] == "harmful" or (
            row["status"] == "unproven" and not baseline_closed and split.n_after >= review_after
        )
        rows.append(row)
    summary = {status: sum(1 for r in rows if r["status"] == status) for status in ("effective", "unproven", "harmful")}
    return {
        "schema": "grimoire.rule_effect.v1",
        "min_tasks": min_tasks,
        "alpha": alpha,
        "review_after": review_after,
        "summary": summary,
        "rules": rows,
    }


# ── Lecture des journaux du projet ────────────────────────────────────────────


def _declared(root: Path) -> dict[tuple[str, str], str]:
    """``(depuis, vers)`` en vocabulaire board → identifiant de transition déclarée.

    Lève :class:`~grimoire.missions.gates.GatesFileError` si le fichier de gates
    est illisible : sans lui, on ne sait pas quels refus sont des leçons.
    """
    from grimoire.missions.gates import declared_transitions

    return {key: str(entry.get("id", "")) for key, entry in declared_transitions(root).items() if entry.get("id")}


def _ledger_transition(declared: Mapping[tuple[str, str], str]) -> Callable[[str, str], str]:
    from grimoire.missions.board import board_status_of
    from grimoire.missions.schemas import TaskState

    def transition_of(src: str, dst: str) -> str:
        try:
            pair = (board_status_of(TaskState(src)), board_status_of(TaskState(dst)))
        except (KeyError, ValueError):  # silent-ok — état inconnu de cette version : pas une exposition qu'on sait nommer
            return ""
        return declared.get(pair, "")

    return transition_of


def _observe(root: Path, ledger_root: Path | None) -> tuple[list[Refusal], list[Exposure]]:
    """Refus et expositions lus dans le TraceLedger et le Mission Ledger, sans rien créer."""
    from grimoire.missions.trace import DEFAULT_LEDGER_RELPATH

    declared = _declared(root)
    traces: list[Any] = []
    if (root / TRACES_DIR / "traces.jsonl").is_file():
        from grimoire.traces.ledger import TraceLedger

        traces = TraceLedger(root / TRACES_DIR).list_traces()
    refusals = refusals_from_traces(traces, declared.values())
    ledger_dir = ledger_root or DEFAULT_LEDGER_RELPATH
    ledger_dir = ledger_dir if ledger_dir.is_absolute() else root / ledger_dir
    events: list[Any] = []
    if (ledger_dir / "events.jsonl").is_file():
        from grimoire.missions.ledger import MissionLedger

        events = MissionLedger(ledger_dir).list_events()
    return refusals, exposures_from_events(refusals, events, _ledger_transition(declared))


def rule_effects(
    root: Path,
    *,
    ledger_root: Path | None = None,
    min_tasks: int = DEFAULT_EFFECT_MIN_TASKS,
    alpha: float = DEFAULT_ALPHA,
    review_after: int = DEFAULT_REVIEW_AFTER,
) -> dict[str, Any]:
    """:func:`measure_effects` sur les journaux et les propositions acceptées du projet."""
    root = Path(root)
    refusals, exposures = _observe(root, ledger_root)
    return measure_effects(
        list_rule_proposals(root, status="accepted"), refusals, exposures,
        min_tasks=min_tasks, alpha=alpha, review_after=review_after,
    )
