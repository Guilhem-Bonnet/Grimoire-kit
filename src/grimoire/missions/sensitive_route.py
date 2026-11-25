"""Routage fort des tâches qui visent des chemins sensibles (``orchestrator.roles.worker.sensitive_route``).

Constat d'origine : sous ``model_by_class`` et un ``provider`` bon marché, les PR de workers qui touchaient le code des
gardes, les hooks, l'autorité, la politique, la relecture ou la CI étaient rejetées puis réécrites par un modèle fort.
Le coût était payé deux fois. Avec ``sensitive_route: strong``, le lanceur envoie d'emblée ces tâches au modèle de
l'hôte au palier fort (``strong`` : ``opus`` sous Claude Code, table palier x hôte x fournisseur), sans route de
fournisseur.

**Quels chemins sont sensibles.** Aucune seconde liste : ce sont ceux que le merger réserve déjà
(:data:`~grimoire.missions.merger.DEFAULT_PROTECTED_PATHS`, plus ``merger.protected_paths``), ceux qui restent à
l'humain en double relecture (:data:`~grimoire.missions.dual_review.HUMAN_ONLY_PATHS`, plus
``merger.human_only_paths``), et les motifs que ``worker.sensitive_paths`` ajoute. Une PR qui les touche escaladera de
toute façon : autant qu'elle soit écrite par le modèle qui la fera passer.

**Quels chemins vise une tâche, avant tout code.** Seulement ce que la tâche dit d'elle-même, lu sans réseau ni
modèle : les chemins cités dans son titre, sa description, sa surface, ses critères d'acceptation, ses garde-fous, ses
preuves attendues et les consignes de l'orchestrateur. Le *context bundle* ne porte rien de plus (il reprend titre,
description et critères) et l'issue liée n'est pas lue : un appel ``gh`` au lancement rendrait la décision dépendante
du réseau. Un jeton compte comme chemin s'il contient un ``/``, commence par un ``.`` ou finit par une extension de
fichier connue ; une URL n'en est pas un. Un nom de fichier nu (``launcher.py``) vaut le chemin réservé qui finit par
ce nom.

**En cas de doute, rien ne change.** Aucun chemin cité, ou aucun chemin cité sensible : la tâche part comme avant
(``model_by_class``, ``provider``). Le routage ne fait que monter, jamais descendre.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from grimoire.missions.orchestrator_policy import OrchestratorPolicy
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "SENSITIVE_ROUTES",
    "SENSITIVE_ROUTE_OFF",
    "SENSITIVE_ROUTE_STRONG",
    "SENSITIVE_TOPIC_STEMS",
    "SensitiveRoute",
    "cited_paths",
    "mentions_sensitive_topic",
    "sensitive_patterns",
    "sensitive_route_for",
]

#: Défaut : la tâche part comme avant, quels que soient les chemins qu'elle cite.
SENSITIVE_ROUTE_OFF = "off"
#: Une tâche qui cite un chemin sensible part sur l'hôte au palier fort, sans route de fournisseur.
SENSITIVE_ROUTE_STRONG = "strong"
SENSITIVE_ROUTES: tuple[str, ...] = (SENSITIVE_ROUTE_OFF, SENSITIVE_ROUTE_STRONG)

#: Les extensions qui font d'un jeton sans ``/`` un nom de fichier.
_EXTENSIONS = frozenset(
    {
        "py",
        "pyi",
        "pyc",
        "pyo",
        "pyd",
        "so",
        "dll",
        "dylib",
        "exe",
        "whl",
        "egg",
        "pth",
        "toml",
        "yaml",
        "yml",
        "json",
        "jsonl",
        "cfg",
        "ini",
        "sh",
        "bash",
        "zsh",
        "ps1",
        "md",
        "rst",
        "txt",
        "js",
        "mjs",
        "cjs",
        "ts",
        "tsx",
        "css",
        "html",
        "rs",
        "go",
        "lock",
        "sql",
    }
)
#: Un jeton candidat : lettres, chiffres et ``_ . - / * ~``, entre des séparateurs.
_TOKEN = re.compile(r"[A-Za-z0-9_.*~/-]+")
_URL_BEFORE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S*$")
_MAX_PATHS = 64

#: Le même périmètre dit en mots, pour un texte libre qui ne cite aucun chemin (la description d'un appel ``Agent``,
#: voir ``hosts.decisions.dispatch_model``) : gardes, hooks, politique, autorité, relecture adversariale, CI, sécurité,
#: failles. Racines en minuscules et sans accents ; une racine de 4 lettres ou plus matche en préfixe, plus courte elle
#: doit être le mot entier (``ci``). Une seule liste : la décision de dispatch ne garde pas la sienne.
SENSITIVE_TOPIC_STEMS: tuple[str, ...] = (
    "garde",
    "guard",
    "hook",
    "polic",
    "politi",
    "autorit",
    "authori",
    "adversari",
    "ci",
    "secu",
    "securit",
    "faille",
    "vulnerab",
)
_ACCENTS = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")


def mentions_sensitive_topic(text: str) -> bool:
    """Le texte parle-t-il d'une garde, d'un hook, de la politique, de la CI, de la sécurité ou d'une faille ?"""
    for word in re.findall(r"[a-z]+", (text or "").lower().translate(_ACCENTS)):
        for stem in SENSITIVE_TOPIC_STEMS:
            if word == stem or (len(stem) >= 4 and word.startswith(stem)):
                return True
    return False


@dataclass(frozen=True, slots=True)
class SensitiveRoute:
    """La décision : ``strong`` avec les chemins qui l'ont décidée, ou ``""`` (routage habituel) et pourquoi."""

    route: str
    paths: tuple[str, ...]
    reason: str

    @property
    def strong(self) -> bool:
        return self.route == SENSITIVE_ROUTE_STRONG

    def to_dict(self) -> dict[str, Any]:
        return {"route": self.route, "paths": list(self.paths), "reason": self.reason}


def _texts(task: MissionTask) -> Iterable[str]:
    yield task.title
    yield task.description
    yield task.surface
    yield from task.acceptance
    yield from task.guardrails
    yield from task.expected_evidence
    for directive in task.directives:
        yield directive.text


def _is_path(token: str) -> bool:
    if not token or token in {".", "..", "/", "*"} or set(token) <= {".", "-", "_", "*", "/", "~"}:
        return False
    if "/" in token or token.startswith("."):
        return True
    _, dot, ext = token.rpartition(".")
    return bool(dot) and ext.lower() in _EXTENSIONS


def cited_paths(task: MissionTask) -> tuple[str, ...]:
    """Les chemins que la tâche cite, dans l'ordre d'apparition, sans doublon (au plus 64)."""
    seen: dict[str, None] = {}
    for text in _texts(task):
        if not text:
            continue
        for match in _TOKEN.finditer(text):
            if _URL_BEFORE.search(text[: match.end()]):
                continue  # un morceau d'URL, jamais un chemin du dépôt
            token = match.group(0).rstrip(".-")
            token = token.removeprefix("./")
            if token.startswith(("/", "~")):
                continue  # un chemin absolu ou sous HOME n'est pas un chemin du dépôt
            if _is_path(token):
                seen.setdefault(token, None)
            if len(seen) >= _MAX_PATHS:
                return tuple(seen)
    return tuple(seen)


def sensitive_patterns(policy: OrchestratorPolicy) -> tuple[str, ...]:
    """Les motifs sensibles : les chemins réservés du merger et de la double relecture, plus ceux de la politique."""
    from grimoire.missions.dual_review import HUMAN_ONLY_PATHS
    from grimoire.missions.merger import DEFAULT_PROTECTED_PATHS  # import tardif : le merger importe le lanceur

    merger, worker = policy.merger, policy.worker
    ordered = (
        *DEFAULT_PROTECTED_PATHS,
        *HUMAN_ONLY_PATHS,
        *merger.protected_paths,
        *merger.human_only_paths,
        *worker.sensitive_paths,
    )
    return tuple(dict.fromkeys(ordered))


def _matches(path: str, patterns: Sequence[str]) -> bool:
    folded = path.casefold()
    if any(fnmatch.fnmatchcase(folded, pat.casefold()) for pat in patterns):
        return True
    if "/" in folded:
        return False
    # Un nom de fichier nu vaut le chemin réservé, sans joker, qui finit par ce nom.
    return any(not any(c in pat for c in "*?[") and pat.casefold().rsplit("/", 1)[-1] == folded for pat in patterns)


def sensitive_route_for(task: MissionTask, policy: OrchestratorPolicy) -> SensitiveRoute:
    """La décision de routage de *task* sous *policy* ; ``route == ""`` laisse le lanceur faire comme avant."""
    if policy.worker.sensitive_route != SENSITIVE_ROUTE_STRONG:
        return SensitiveRoute("", (), "sensitive_route désactivé")
    cited = cited_paths(task)
    if not cited:
        return SensitiveRoute("", (), "aucun chemin cité par la tâche : routage habituel")
    patterns = sensitive_patterns(policy)
    hits = tuple(p for p in cited if _matches(p, patterns))
    if not hits:
        return SensitiveRoute("", (), f"{len(cited)} chemin(s) cité(s), aucun sensible : routage habituel")
    shown = ", ".join(hits[:6]) + ("…" if len(hits) > 6 else "")
    return SensitiveRoute(SENSITIVE_ROUTE_STRONG, hits, f"chemins sensibles cités : {shown}")
