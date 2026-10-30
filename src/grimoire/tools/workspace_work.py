"""Qui travaille sur chaque carte, avec quoi, à quel coût (P75, GAO-p75-qui-trav-001).

Trois sources, chacune nommée — jamais un chiffre inventé :

- **tokens et coût** : :func:`grimoire.traces.transcript_usage.task_usage` sur
  les traces déjà repliées de la tâche (``grimoire task fold-usage``), jamais un
  second agrégateur. Les statuts ``none`` / ``unavailable`` / ``reported`` /
  ``claude-code-transcript`` passent tels quels jusqu'à la vue ;
- **modèle réel, effort, skills** : le journal de session Claude Code
  (``$CLAUDE_CONFIG_DIR|~/.claude/projects/<slug>/<session>.jsonl``), lu
  incrémentalement (cache par fichier : seuls les octets nouveaux sont relus
  entre deux pouls). Un hôte sans journal connu, ou un journal absent, rend
  ``source`` ``no_transcript`` : la vue écrit « non rapporté », rien d'autre ;
- **taux USD→CAD** : ``project-context.yaml``, section ``cockpit.usd_cad``
  (:data:`DEFAULT_USD_CAD` écrit comme défaut tant que le projet n'a rien
  déclaré). L'écriture passe par ``ruamel`` round-trip : les commentaires du
  fichier de l'utilisateur survivent.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_USD_CAD",
    "annotate_work",
    "cad_of",
    "model_tier",
    "project_options",
    "read_transcript_work",
    "session_work",
    "transcript_path",
    "usage_of",
    "write_usd_cad",
]

#: Défaut raisonnable, écrit comme défaut (``usd_cad_source: default``) tant que
#: le projet n'a rien déclaré — jamais présenté comme un taux du jour.
DEFAULT_USD_CAD = 1.40

#: Section de ``project-context.yaml`` qui porte les options du cockpit.
#: ``GrimoireConfig`` garde les sections inconnues dans ``extra`` : en ajouter
#: une ne casse aucune validation.
OPTIONS_SECTION = "cockpit"

#: Paliers de modèle reconnus dans un identifiant de version (``claude-opus-5-5``).
MODEL_TIERS = ("haiku", "sonnet", "opus")

#: Un identifiant de session sûr comme composant de chemin (UUID d'hôte).
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

#: Ligne d'erreur de l'hôte, pas un appel (même garde que ``transcript_usage``).
_SYNTHETIC_MODEL = "<synthetic>"

#: Skills gardés en mémoire par session — assez pour l'inspecteur, borné.
_MAX_SKILLS = 30


def model_tier(model: str) -> str | None:
    """Le palier (``haiku``/``sonnet``/``opus``) lu dans l'identifiant de *model*, ou ``None``.

    ``None`` n'est pas une erreur : un modèle hors de ces trois familles
    (``claude-fable-5``…) garde son nom, la vue n'invente pas de palier.
    """
    lowered = model.lower()
    for tier in MODEL_TIERS:
        if tier in lowered:
            return tier
    return None


def claude_projects_dir(home: Path | None = None) -> Path:
    """Le dossier des journaux de session Claude Code (``CLAUDE_CONFIG_DIR`` sinon ``~/.claude``)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir) if config_dir else (home or Path.home()) / ".claude"
    return base / "projects"


def transcript_path(project_root: Path, session_id: str, *, home: Path | None = None) -> Path | None:
    """Le journal ``<slug>/<session>.jsonl`` de la session, ou ``None`` s'il n'existe pas.

    Le slug est la convention de Claude Code : le chemin résolu du projet, tout
    caractère non alphanumérique remplacé par ``-``. Un ``session_id`` qui n'est
    pas un composant de chemin sûr est refusé plutôt que nettoyé.
    """
    if not _SAFE_SESSION_RE.match(session_id or ""):
        return None
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project_root.resolve()))
    path = claude_projects_dir(home) / slug / f"{session_id}.jsonl"
    return path if path.is_file() else None


@dataclass
class _TranscriptState:
    """Ce qu'une lecture incrémentale du journal a retenu, et jusqu'où elle a lu."""

    offset: int = 0
    model: str = ""
    effort: str = ""
    skills: list[str] = field(default_factory=list)

    def as_work(self) -> dict[str, Any]:
        return {
            "source": "transcript",
            "model": self.model or None,
            "tier": model_tier(self.model) if self.model else None,
            "effort": self.effort or None,
            "skills": list(self.skills),
        }


#: Cache par chemin résolu : le pouls du cockpit relit le board toutes les
#: quelques secondes, un journal de session peut peser des centaines de Mo —
#: seuls les octets écrits depuis la dernière lecture sont relus.
_TRANSCRIPT_CACHE: dict[str, _TranscriptState] = {}


def _note_skill(state: _TranscriptState, name: str) -> None:
    """Ajoute *name* à la liste ordonnée des skills (dernière invocation en queue, sans doublon)."""
    if name in state.skills:
        state.skills.remove(name)
    state.skills.append(name)
    del state.skills[:-_MAX_SKILLS]


def _fold_line(state: _TranscriptState, raw: bytes) -> None:
    """Replie une ligne du journal dans *state* — une ligne illisible est ignorée, jamais complétée."""
    try:
        line = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return
    if not isinstance(line, dict) or line.get("type") != "assistant":
        return
    effort = line.get("effort")
    if isinstance(effort, str) and effort:
        state.effort = effort
    message = line.get("message")
    if not isinstance(message, dict):
        return
    model = message.get("model")
    if isinstance(model, str) and model and model != _SYNTHETIC_MODEL:
        state.model = model
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
            continue
        payload = block.get("input")
        skill = payload.get("skill") if isinstance(payload, dict) else None
        if isinstance(skill, str) and skill:
            _note_skill(state, skill)


def read_transcript_work(path: Path) -> dict[str, Any]:
    """Modèle réel, effort et skills du journal *path* — lecture incrémentale.

    Seules les lignes complètes (terminées par ``\\n``) sont repliées : la
    dernière ligne d'un journal en cours d'écriture attend le prochain passage.
    Un fichier raccourci (session réécrite) repart de zéro. Lève ``OSError``
    si le fichier est illisible : l'appelant le dit, il ne l'avale pas.
    """
    key = str(path.resolve())
    state = _TRANSCRIPT_CACHE.get(key)
    size = path.stat().st_size
    if state is None or size < state.offset:
        state = _TranscriptState()
        _TRANSCRIPT_CACHE[key] = state
    if size > state.offset:
        with path.open("rb") as handle:
            handle.seek(state.offset)
            chunk = handle.read(size - state.offset)
        complete, newline, _partial = chunk.rpartition(b"\n")
        if newline:
            for raw in complete.split(b"\n"):
                if raw.strip():
                    _fold_line(state, raw)
            state.offset += len(complete) + len(newline)
    return state.as_work()


def session_work(project_root: Path, session_id: str, host: str, *, home: Path | None = None) -> dict[str, Any]:
    """Ce que le journal de session dit du travail en cours — honnête quand il ne dit rien.

    ``source`` : ``transcript`` (journal lu), ``no_transcript`` (hôte sans
    journal connu, journal absent, ou journal illisible — chaque champ est
    alors ``None`` / vide, la vue écrit « non rapporté »).
    """
    empty: dict[str, Any] = {"source": "no_transcript", "model": None, "tier": None, "effort": None, "skills": []}
    from grimoire.bridges.schemas import HostId
    from grimoire.missions.session_link import canonical_host

    if canonical_host(host) != HostId.CLAUDE_CODE_CLI.value:
        return empty
    path = transcript_path(project_root, session_id, home=home)
    if path is None:
        return empty
    try:
        return read_transcript_work(path)
    except OSError:
        return empty


def usage_of(project_root: Path, task: Any, *, traces: list[Any] | None = None) -> dict[str, Any]:
    """Tokens et coût de la tâche — :func:`task_usage` sur ses traces repliées, rien d'autre.

    *traces* évite une relecture du TraceLedger quand l'appelant a déjà les
    traces de la tâche (board entier) ; sans lui, elles sont lues ici. La
    disponibilité d'une source vérifiée vient du contrat de l'hôte du claim
    (``local_token_usage``), comme ``grimoire task show``.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.capabilities import recorded_contract, resolve_host
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import task_usage

    if traces is None:
        traces_path = project_root / TRACES_DIR
        traces = (
            TraceLedger(traces_path).list_traces(task_id=task.id) if (traces_path / "traces.jsonl").is_file() else []
        )
    host = resolve_host(task.claim.host_id) if task.claim else None
    available = None if host is None else recorded_contract(host).verified("local_token_usage")
    return task_usage(traces, host_usage_available=available)


def _traces_by_task(project_root: Path) -> dict[str, list[Any]]:
    """Les traces du projet groupées par tâche — un seul passage sur ``traces.jsonl``."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger

    traces_path = project_root / TRACES_DIR
    grouped: dict[str, list[Any]] = {}
    if not (traces_path / "traces.jsonl").is_file():
        return grouped
    for trace in TraceLedger(traces_path).list_traces():
        if trace.task_id:
            grouped.setdefault(trace.task_id, []).append(trace)
    return grouped


def cad_of(usd: float | None, rate: float) -> float | None:
    """*usd* converti au taux *rate*, arrondi au cent — ``None`` reste ``None``, jamais ``0``."""
    if usd is None:
        return None
    return round(usd * rate, 2)


def _context_path(project_root: Path) -> Path:
    return project_root.resolve() / "project-context.yaml"


def _declared_rate(data: Any) -> float | None:
    """Le taux déclaré dans la section ``cockpit``, ou ``None`` — jamais un défaut silencieux."""
    section = data.get(OPTIONS_SECTION) if isinstance(data, dict) else None
    raw = section.get("usd_cad") if isinstance(section, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    rate = float(raw)
    return rate if 0 < rate < 1000 else None


def project_options(project_root: Path) -> dict[str, Any]:
    """Les options du projet que le cockpit lit : le taux USD→CAD et sa provenance.

    ``usd_cad_source`` dit si le taux vient du projet (``project``) ou du
    défaut du kit (``default``) — la vue écrit « défaut » plutôt que de faire
    passer 1,40 pour un choix de l'utilisateur.
    """
    from grimoire.tools._common import load_yaml

    try:
        data = load_yaml(_context_path(project_root))
    except (OSError, ValueError):
        data = None
    declared = _declared_rate(data)
    return {
        "usd_cad": declared if declared is not None else DEFAULT_USD_CAD,
        "usd_cad_source": "project" if declared is not None else "default",
        "usd_cad_default": DEFAULT_USD_CAD,
    }


def write_usd_cad(project_root: Path, rate: float) -> dict[str, Any]:
    """Écrit ``cockpit.usd_cad`` dans ``project-context.yaml`` (round-trip : commentaires gardés).

    Lève ``ValueError`` pour un taux hors de ``]0, 1000[`` et
    ``FileNotFoundError`` sans ``project-context.yaml`` : un taux ne se pose
    pas sur un projet qui n'a pas de config.
    """
    from grimoire.tools._common import load_yaml_roundtrip, save_yaml

    if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 < float(rate) < 1000:
        raise ValueError(f"taux usd_cad invalide : {rate!r} (attendu un réel entre 0 et 1000, exclus)")
    path = _context_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(f"project-context.yaml absent : {path}")
    data = load_yaml_roundtrip(path)
    if not isinstance(data, dict):
        raise ValueError("project-context.yaml n'est pas une table YAML : taux non écrit")
    section = data.get(OPTIONS_SECTION)
    if not isinstance(section, dict):
        section = {}
        data[OPTIONS_SECTION] = section
    section["usd_cad"] = round(float(rate), 4)
    save_yaml(data, path)
    return project_options(project_root)


def annotate_work(project_root: Path, tasks: list[Any], cards: list[dict[str, Any]]) -> None:
    """Pose ``work`` sur chaque carte : usage (:func:`task_usage`), coût en CAD, et —
    pour une tâche tenue — ce que le journal de session dit (modèle réel, effort, skills).

    Appelé par ``workspace_portfolio.annotate_cards`` : le board projet et le
    portefeuille servent la même chose. Les traces sont lues en un passage ;
    le journal de session n'est ouvert que pour une tâche tenue (claimed/
    running) dont la carte porte un ``session_id``.
    """
    from grimoire.missions.schemas import TaskState

    grouped = _traces_by_task(project_root)
    options = project_options(project_root)
    rate = float(options["usd_cad"])
    for task, card in zip(tasks, cards, strict=True):
        usage = usage_of(project_root, task, traces=grouped.get(task.id, []))
        work: dict[str, Any] = {
            "usage": usage,
            # ``cost_usd`` est ``None`` tant que rien n'est replié : la conversion
            # suit ``cost_status`` (une borne basse convertie reste une borne basse).
            "cost_cad": cad_of(usage["cost_usd"], rate),
            "usd_cad": rate,
            "usd_cad_source": options["usd_cad_source"],
        }
        session_id = str(card.get("session_id") or "")
        if usage["usage_source"] == "none" and session_id:
            from grimoire.missions.usage_fold import MISSING_LABEL, transcript_is_missing

            # Un journal absent ou purgé n'est pas « zéro » : la carte le dit (GAO-i12).
            work["usage_note"] = MISSING_LABEL if transcript_is_missing(session_id) else None
        if session_id and task.status in (TaskState.CLAIMED, TaskState.RUNNING):
            work["session"] = session_work(project_root, session_id, str(card.get("host") or ""))
        else:
            work["session"] = None
        card["work"] = work
