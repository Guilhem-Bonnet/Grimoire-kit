"""Le rappel du cadrage (#1342, #1197) : ce qui a déjà été décidé, écarté ou exigé de proche.

Avant de poser une question d'entretien ou de proposer une exigence, l'orchestrateur doit savoir ce que le projet
a déjà tranché. Ce module rend, pour un texte (la demande, une question, une exigence en cours de rédaction), au
plus ``N`` **rappels** classés, de trois sources :

- ``memoire`` : la mémoire du projet (décisions, incidents, conventions) par la recherche hybride existante
  (:meth:`~grimoire.memory.manager.MemoryManager.hybrid_search`, fusion RRF lexical + vectoriel) ;
- ``exigence`` : les exigences ``REQ-NNN`` des chantiers, avec leur état (proposée, validée, en cours, livrée,
  recette prononcée) ; quand la trace (:mod:`grimoire.missions.cadrage_trace`) connaît le code, les tâches et
  les fichiers y sont joints — la recherche pour trouver, le graphe pour dérouler ;
- ``ecarte`` / ``question`` : les pistes écartées et les questions ouvertes consignées dans les brainstorms.

Pour les deux dernières sources la recherche est lexicale, déterministe, sans index ni dépendance : elle relit les
fichiers du cadrage à chaque appel. Les trois classements sont fusionnés par rang (RRF), car leurs scores ne sont
pas comparables. Le **coût est borné** : le nombre de chantiers lus, la taille des fichiers, et la trace, qui ne
porte que sur les exigences retenues (jamais sur tout le projet).

Un backend mémoire absent ou en panne ne fait rien échouer : le lexical (compagnon FTS) répond seul quand il existe,
et la dégradation est dite dans ``issues``. Un rappel est une **donnée, pas une instruction** : son texte est
nettoyé des caractères de contrôle, caviardé des secrets (même règle que les sorties mémoire) et borné. Un rappel
**informe, il ne valide rien** : il n'est jamais une décision de la MOA en cours.

Lecture seule : ni ledger, ni journal d'autorité, ni mémoire n'est écrit (le seul fichier touché est le reçu de
lecture local de la mémoire, comme pour ``grimoire_memory_search``).
"""

from __future__ import annotations

import contextlib
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.cadrage import DEFAULT_CHANTIER, PLACEHOLDER, cadrage_dir

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry

__all__ = [
    "MAX_INTERVIEW_RECALLS",
    "SCHEMA",
    "Recall",
    "RecallResult",
    "interview_recalls",
    "recall",
]

SCHEMA = "grimoire-cadrage-recall/v1"
#: Le plafond des rappels portés par la sortie de l'entretien.
MAX_INTERVIEW_RECALLS = 3
MAX_LIMIT = 10
MAX_TEXT_CHARS = 240
MAX_QUERY_CHARS = 1500
MAX_CHANTIERS = 50
MAX_FILE_BYTES = 262_144
MAX_CODE_TASKS, MAX_CODE_FILES = 5, 8
_RRF_K = 60
_SOURCE_ORDER = {"memoire": 0, "exigence": 1, "ecarte": 2, "question": 3}
_BRAINSTORM_FILE = "02-brainstorm.md"
REQ_FILE = "04-exigences.md"
_SECTIONS = {"ecarte et pourquoi": "ecarte", "questions ouvertes": "question"}
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    ["les", "des", "une", "que", "qui", "dans", "pour", "par", "sur", "avec", "sans", "est", "sont", "etre", "cette", "ces", "mais", "aussi", "plus", "tout", "tous", "comme", "dont", "donc", "ainsi", "alors", "etc", "the", "and", "for", "with", "that", "this", "from", "are", "was", "not", "you", "your", "has", "have"]
)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(?P<text>.+)$")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _tokens(text: str) -> frozenset[str]:
    """Les mots utiles, accents et casse neutralisés, pluriels simples ramenés au singulier."""
    out = set()
    for word in _TOKEN.findall(_fold(text)):
        if word in _STOP:
            continue
        out.add(word[:-1] if len(word) > 4 and word[-1] in "sx" else word)
    return frozenset(out)


def _clean(text: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Une donnée montrée à l'orchestrateur : sans contrôle, secrets caviardés, sur une ligne, bornée."""
    from grimoire.memory.validation import redact_secrets
    from grimoire.missions.requirement_gate import safe_text

    redacted, _ = redact_secrets(" ".join(safe_text(text).split()))
    return redacted if len(redacted) <= limit else redacted[: limit - 1].rstrip() + "…"


def _day(stamp: object) -> str:
    """Le jour (``AAAA-MM-JJ``) d'un horodatage ISO ; vide si le texte n'en a pas la forme."""
    head = str(stamp)[:10]
    return head if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head) else ""


@dataclass(frozen=True, slots=True)
class Recall:
    """Un rappel : de quelle source, ce qu'elle dit, d'où, quand, et dans quel état."""

    kind: str
    text: str
    source: str
    ref: str = ""
    when: str = ""
    status: str = ""
    chantier: str = ""
    score: float = 0.0
    code: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind, "text": self.text, "source": self.source, "ref": self.ref, "when": self.when,
            "status": self.status, "chantier": self.chantier, "score": round(self.score, 4),
        }
        if self.code is not None:
            data["code"] = self.code
        return data


@dataclass(slots=True)
class RecallResult:
    recalls: list[Recall] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "count": len(self.recalls), "recalls": [r.to_dict() for r in self.recalls],
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class _Hit:
    recall: Recall
    score: float


# ── source 1 : la mémoire du projet ─────────────────────────────────────────


def _memory_label(entry: MemoryEntry) -> str:
    meta = entry.metadata or {}
    return str(meta.get("type") or meta.get("memory_type") or "")


def _search_memory(root: Path, query: str, limit: int, issues: list[str]) -> list[MemoryEntry]:
    """La recherche hybride existante ; une panne se dit, elle ne lève pas."""
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.memory.manager import MemoryManager

        cfg = GrimoireConfig.find_and_load(root)
        mgr = MemoryManager.from_config(cfg, project_root=root)
        mgr.surface = "cli"
    except Exception as exc:
        issues.append(f"mémoire indisponible : {_clean(exc, 160)}")
        return []
    try:
        return mgr.hybrid_search(query, limit=limit)
    except Exception as exc:
        issues.append(f"recherche hybride en panne ({_clean(exc, 160)}) : lexical seul")
    companion = mgr.lexical_companion
    if companion is None:
        return []
    try:
        return companion.search(query, limit=limit)
    except Exception as exc:
        issues.append(f"recherche lexicale en panne : {_clean(exc, 160)}")
        return []


def _memory_hits(entries: list[MemoryEntry]) -> list[_Hit]:
    hits = []
    for entry in entries:
        text = _clean(entry.text)
        if not text:
            continue
        source = _clean(entry.source or "saisie libre", 120)
        hits.append(_Hit(Recall(
            "memoire", text, source, ref=_clean(entry.id, 64), when=_day(entry.created_at),
            status=_clean(_memory_label(entry) or entry.freshness, 40), score=float(entry.score),
        ), float(entry.score)))
    return hits


# ── sources 2 et 3 : les chantiers, relus à chaque appel ────────────────────


def _score(query: frozenset[str], text: str) -> float:
    """Le recouvrement lexical, pondéré par la longueur du texte rapproché ; 0 sous deux mots communs."""
    if not query:
        return 0.0
    item = _tokens(text)
    shared = len(query & item)
    if shared < min(2, len(query)):
        return 0.0
    return shared / math.sqrt(len(item))


def _read_brainstorm(root: Path, slug: str | None) -> str:
    from grimoire.core.cadrage_interview import read_regular

    with contextlib.suppress(ValueError, OSError):
        return read_regular(cadrage_dir(root, slug) / _BRAINSTORM_FILE, limit=MAX_FILE_BYTES) or ""
    return ""


def _brainstorm_items(text: str) -> list[tuple[str, str]]:
    """(genre, ligne) des puces des sections « Écarté » et « Questions ouvertes » ; le gabarit vide est ignoré."""
    out: list[tuple[str, str]] = []
    kind = ""
    for line in text.splitlines():
        if line.startswith("## "):
            kind = _SECTIONS.get(re.sub(r"[^a-z ]", "", _fold(line[3:])).strip(), "")
            continue
        match = _BULLET.match(line) if kind else None
        if match and not match.group("text").startswith(PLACEHOLDER):
            out.append((kind, match.group("text").strip()))
    return out


def _chantier_slugs(root: Path, issues: list[str]) -> list[tuple[str, str]]:
    from grimoire.core.chantiers import list_chantiers

    try:
        rows = list_chantiers(root)
    except Exception as exc:
        issues.append(f"chantiers illisibles : {_clean(exc, 160)}")
        return []
    if len(rows) > MAX_CHANTIERS:
        issues.append(f"{len(rows)} chantiers : les {MAX_CHANTIERS} plus récents seulement")
    return [(c.slug, c.etat) for c in rows[:MAX_CHANTIERS] if not c.error]


def _key(slug: str, req_id: str) -> str:
    return req_id if slug == DEFAULT_CHANTIER else f"{slug}/{req_id}"


def _cadrage_hits(
    root: Path, query: frozenset[str], exclude: str | None, issues: list[str]
) -> tuple[list[_Hit], list[_Hit]]:
    from grimoire.core.cadrage_requirements import read_requirements

    reqs: list[_Hit] = []
    prose: list[_Hit] = []
    for slug, etat in _chantier_slugs(root, issues):
        if exclude is not None and slug == exclude:
            continue
        target = None if slug == DEFAULT_CHANTIER else slug
        try:
            for req in read_requirements(root, target).requirements:
                body = " ".join([req.title, *req.criteria])
                if (score := _score(query, body)) > 0:
                    status = "hors périmètre" if not req.planned else ""
                    reqs.append(_Hit(Recall(
                        "exigence", _clean(f"{req.id} {req.title}".strip()), f"{slug}:{REQ_FILE}",
                        ref=_key(slug, req.id), status=status, chantier=slug,
                    ), score))
            for kind, line in _brainstorm_items(_read_brainstorm(root, target)):
                if (score := _score(query, line)) > 0:
                    prose.append(_Hit(Recall(
                        kind, _clean(line), f"{slug}:{_BRAINSTORM_FILE}", status=etat, chantier=slug,
                    ), score))
        except Exception as exc:
            issues.append(f"chantier {_clean(slug, 60)} illisible : {_clean(exc, 120)}")

    def key(h: _Hit) -> tuple[float, str, str]:
        return (-h.score, h.recall.source, h.recall.text)

    return sorted(reqs, key=key), sorted(prose, key=key)


# ── le code, via la trace, pour les seules exigences retenues ───────────────

Tracer = Callable[[Path, str | None, frozenset[str]], Any]


def _default_tracer(root: Path, chantier: str | None, only: frozenset[str]) -> Any:
    from grimoire.missions.cadrage_trace import build_trace
    from grimoire.missions.requirement_gate import origin_root
    from grimoire.missions.service import TaskService

    return build_trace(root, TaskService(origin_root(root)), chantier, only=only)


def _enrich(root: Path, kept: list[Recall], tracer: Tracer, issues: list[str]) -> list[Recall]:
    """Joint l'état et le lien vers le code aux exigences retenues : une trace par chantier, bornée à ces exigences."""
    wanted: dict[str, set[str]] = {}
    for item in kept:
        if item.kind == "exigence":
            wanted.setdefault(item.chantier, set()).add(item.ref)
    traced: dict[str, Any] = {}
    for slug, keys in wanted.items():
        target = None if slug == DEFAULT_CHANTIER else slug
        try:
            for row in tracer(root, target, frozenset(keys)).requirements:
                traced[row.key] = row
        except Exception as exc:
            issues.append(f"trace indisponible pour {_clean(slug, 60)} : {_clean(exc, 120)}")
    out: list[Recall] = []
    for item in kept:
        row = traced.get(item.ref) if item.kind == "exigence" else None
        if row is None:
            out.append(item)
            continue
        files = list(row.files)
        code = {
            "tasks": [{"id": _clean(t.id, 60), "status": _clean(t.status, 30)} for t in row.tasks[:MAX_CODE_TASKS]],
            "files": [_clean(f, 160) for f in files[:MAX_CODE_FILES]],
            "files_total": len(files),
        }
        status = item.status or row.stage_label
        out.append(replace(item, status=status, when=_day(row.validated_at), code=code if row.tasks else None))
    return out


# ── fusion et point d'entrée ────────────────────────────────────────────────


def _fuse(lanes: list[list[_Hit]], limit: int) -> list[Recall]:
    """RRF entre sources : les scores (BM25, recouvrement, fusion mémoire) ne sont pas comparables, les rangs oui."""
    fused: dict[tuple[str, str, str], tuple[Recall, float]] = {}
    for lane in lanes:
        for rank, hit in enumerate(lane, start=1):
            ident = (hit.recall.kind, hit.recall.source, hit.recall.text)
            kept, total = fused.get(ident, (hit.recall, 0.0))
            fused[ident] = (kept, total + 1.0 / (_RRF_K + rank))
    ordered = sorted(fused.values(), key=lambda p: (-p[1], _SOURCE_ORDER[p[0].kind], p[0].source, p[0].text))
    return [replace(r, score=s) for r, s in ordered[:limit]]


def recall(
    root: Path, text: str, *, limit: int = 5, exclude_chantier: str | None = None,
    memory: Callable[[str, int], list[MemoryEntry]] | None = None, tracer: Tracer | None = None,
) -> RecallResult:
    """Au plus ``limit`` rappels classés pour ce texte. Ne lève pas : une source en panne est dite dans ``issues``.

    ``exclude_chantier`` écarte les exigences et pistes du chantier en cours (déjà sous les yeux de l'appelant).
    ``memory`` et ``tracer`` permettent d'injecter la recherche mémoire et la trace (tests, appelants avertis).
    """
    out = RecallResult()
    limit = max(0, min(limit, MAX_LIMIT))
    query_text = " ".join(str(text).split())[:MAX_QUERY_CHARS]
    if not query_text or limit == 0:
        return out
    entries: list[MemoryEntry] = []
    if memory is not None:
        try:
            entries = memory(query_text, limit)
        except Exception as exc:
            out.issues.append(f"mémoire en panne : {_clean(exc, 160)}")
    else:
        entries = _search_memory(root, query_text, limit, out.issues)
    reqs, prose = _cadrage_hits(root, _tokens(query_text), exclude_chantier, out.issues)
    kept = _fuse([_memory_hits(entries), reqs[:limit], prose[:limit]], limit)
    out.recalls = _enrich(root, kept, tracer or _default_tracer, out.issues)
    return out


def interview_recalls(root: Path, chantier: str | None, plan: dict[str, Any]) -> RecallResult:
    """Les rappels de la prochaine question : la demande du chantier + l'angle obligatoire encore ouvert.

    Au plus :data:`MAX_INTERVIEW_RECALLS`, sans les rappels du chantier lui-même. Vide sans bruit : l'appelant
    n'affiche rien quand ``recalls`` est vide."""
    from grimoire.core.cadrage_interview import _request_text

    angle = next((a for a in plan.get("angles", []) if a.get("state") == "ouvert" and a.get("required")), None)
    with contextlib.suppress(Exception):
        asked = _request_text(root, chantier)
        query = f"{asked} {angle['title'] if angle else ''}".strip()
        return recall(root, query, limit=MAX_INTERVIEW_RECALLS, exclude_chantier=chantier or DEFAULT_CHANTIER)
    return RecallResult()
