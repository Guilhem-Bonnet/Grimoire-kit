"""Gate de preuve sur les transitions de tâches.

``_grimoire/standard/evidence-gates.yaml`` déclare, depuis le début, quelles
preuves chaque transition exige. Rien ne le consultait : le standard *vérifiait
que le fichier était bien formé*, et aucune transition ne lui demandait jamais
l'autorisation. Une tâche pouvait donc passer en revue sans preuve et se fermer
sans verdict, exactement ce que le fichier interdit.

Ce module rend la déclaration opposable. Il parle le vocabulaire du board (huit
états), qui est celui du fichier de gates ; la traduction depuis les états du
ledger appartient à l'appelant, ce qui garde ce module libre de toute copie de
la carte board ↔ ledger.

Deux règles portent tout le reste :

- **Une preuve introuvable est un refus, jamais un silence.** Un nom de preuve
  sans résolveur refuse en le disant. Le contraire — ignorer ce qu'on ne sait
  pas vérifier — rendrait le gate vert d'autant plus qu'il comprend moins.
- **Le refus nomme l'artefact.** « preuve manquante » n'aide personne ; le
  chemin attendu, si.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.core.standard_profile_manifest import read_profile
from grimoire.evidence import EvidenceService, VerdictResult

__all__ = [
    "GATES_FILE",
    "GateRefusal",
    "GateVerdict",
    "GatesFileError",
    "check_transition",
    "declared_transitions",
]

GATES_FILE = STANDARD_DIR / "evidence-gates.yaml"
_PROFILE_FILE = STANDARD_DIR / "standard-profile.yaml"

#: Là où le standard écrit ses artefacts de tâche.
_CONTEXT_DIR = Path("_grimoire-output/context")
_DECISION_DIR = Path("_grimoire-output/decisions")
_EVIDENCE_LEDGER = Path("_grimoire-runtime-output/evidence")

#: Strictness appliquée quand le profil actif est inconnu du fichier de gates.
#: Fermé par défaut : un profil non déclaré n'est pas un profil permissif.
_UNKNOWN_PROFILE_STRICTNESS = "hard_fail"


@dataclass(frozen=True, slots=True)
class GateRefusal:
    """Une preuve exigée qui n'est pas là, et ce qu'il faut faire."""

    evidence: str
    reason: str
    remedy: str

    def __str__(self) -> str:
        return f"{self.evidence} — {self.reason}"


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Ce que le gate répond pour une transition donnée."""

    transition_id: str
    strictness: str
    refusals: tuple[GateRefusal, ...]

    @property
    def blocked(self) -> bool:
        """Le passage est-il interdit ?

        Une transition non déclarée n'est pas gardée : elle passe. Les gates
        décrivent ce qui est exigé, pas ce qui est permis — inverser rendrait
        tout blocage muet impossible à distinguer d'un oubli de déclaration.
        """
        return bool(self.refusals) and self.strictness == "hard_fail"

    @property
    def declared(self) -> bool:
        return self.transition_id != ""


class GatesFileError(GrimoireMissionError):
    """Un fichier du standard existe mais ne se lit pas.

    Rendre ``{}`` à la place — ce que faisait le lecteur — transformait un
    fichier de gates abîmé en fichier sans transition : chaque passage devenait
    libre, et le gate était d'autant plus vert que son fichier était cassé.
    """

    def __init__(self, rel: Path, cause: str) -> None:
        self.rel = rel
        self.cause = cause
        super().__init__(f"{rel.as_posix()} illisible : {cause}")


def _yaml_mapping(root: Path, rel: Path) -> dict[str, Any]:
    """Le fichier *rel* comme table ; ``{}`` s'il n'existe pas, erreur s'il est cassé."""
    path = root / rel
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        raise GatesFileError(rel, f"{type(exc).__name__}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise GatesFileError(rel, f"attendu une table YAML, trouvé {type(data).__name__}")
    return data


def _unreadable_verdict(exc: GatesFileError) -> GateVerdict:
    """Un fichier de gates illisible ferme toutes les portes, en le disant."""
    return GateVerdict(
        transition_id=exc.rel.name,
        strictness=_UNKNOWN_PROFILE_STRICTNESS,
        refusals=(GateRefusal(
            exc.rel.as_posix(),
            f"fichier de gates illisible — {exc.cause}",
            "réparer le YAML ; tant qu'il est illisible, aucune transition ne passe",
        ),),
    )


def declared_transitions(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Transitions déclarées, indexées par (depuis, vers) en vocabulaire board.

    Lève :class:`GatesFileError` si le fichier existe et ne se lit pas : un
    appelant qui veut la liste doit savoir qu'il n'en a pas une.
    """
    gates = _yaml_mapping(root, GATES_FILE)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in gates.get("transitions", []) or []:
        if not isinstance(entry, dict):
            continue
        src, dst = entry.get("from"), entry.get("to")
        if isinstance(src, str) and isinstance(dst, str):
            out[(src, dst)] = entry
    return out


def _strictness(root: Path) -> str:
    gates = _yaml_mapping(root, GATES_FILE)
    table = gates.get("profile_strictness")
    if not isinstance(table, dict):
        return _UNKNOWN_PROFILE_STRICTNESS
    profile = read_profile(root / _PROFILE_FILE) or "starter"
    value = table.get(profile)
    return value if isinstance(value, str) else _UNKNOWN_PROFILE_STRICTNESS


# ── Résolveurs de preuve ────────────────────────────────────────────────────
# Chacun rend None quand la preuve est là, un GateRefusal sinon. Le nom de la
# clé est celui écrit dans `required_evidence`.

def _file_resolver(rel: Callable[[str], Path], label: str) -> Callable[..., GateRefusal | None]:
    def resolve(root: Path, task: Any, name: str) -> GateRefusal | None:
        tid = _task_id(task)
        if not _SAFE_TASK_ID.fullmatch(tid) or tid in {".", ".."}:
            return GateRefusal(name, f"identifiant inutilisable dans un chemin : {tid!r}",
                               "renommer la tâche : lettres, chiffres, . _ - uniquement")
        path = rel(tid)
        if (root / path).is_file():
            return None
        return GateRefusal(name, f"{label} absent", f"attendu : {path}")

    return resolve


#: Un identifiant employé comme nom de dossier ne peut porter qu'un segment.
#: Le ledger le garantit à la création ; un ledger plus ancien peut porter des
#: identifiants d'avant cette garantie, et on ne construit pas un chemin avec.
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _task_id(task: Any) -> str:
    return str(getattr(task, "id", "") or "")


def _resolve_acceptance(root: Path, task: Any, name: str) -> GateRefusal | None:
    if tuple(getattr(task, "acceptance", ()) or ()):
        return None
    return GateRefusal(name, "la tâche ne porte aucun critère d'acceptation",
                       "renseigner `acceptance` à la création de la tâche")


def _resolve_owner(root: Path, task: Any, name: str) -> GateRefusal | None:
    if str(getattr(task, "owner", "") or "").strip():
        return None
    claim = getattr(task, "claim", None)
    if claim is not None and str(getattr(claim, "actor_id", "") or "").strip():
        return None
    return GateRefusal(name, "ni propriétaire ni claim : personne n'en répond",
                       "`--owner` à la création, ou réclamer la tâche")


def _resolve_provider_policy(root: Path, task: Any, name: str) -> GateRefusal | None:
    try:
        registry = _yaml_mapping(root, STANDARD_DIR / "llm-provider-registry.yaml")
    except GatesFileError as exc:
        return GateRefusal(name, f"registre illisible — {exc.cause}", f"réparer {exc.rel.as_posix()}")
    providers = registry.get("providers")
    if isinstance(providers, list) and any(
        isinstance(p, dict) and p.get("enabled") is True for p in providers
    ):
        return None
    return GateRefusal(name, "aucun fournisseur activé dans le registre",
                       f"activer un fournisseur dans {STANDARD_DIR / 'llm-provider-registry.yaml'}")


def _resolve_evidence_pack(root: Path, task: Any, name: str) -> GateRefusal | None:
    service = EvidenceService(root / _EVIDENCE_LEDGER)
    if service.list_packs(_task_id(task)):
        return None
    return GateRefusal(name, "aucun pack de preuve pour cette tâche",
                       "produire un evidence pack avant de passer en revue")


def _resolve_review_gate(root: Path, task: Any, name: str) -> GateRefusal | None:
    """Le verdict de vérification, et son résultat.

    C'est ici que se joue « on ne ferme pas sans verdict accepté » : un verdict
    absent et un verdict échoué sont deux refus distincts, parce qu'ils
    appellent deux gestes différents.
    """
    verdict = EvidenceService(root / _EVIDENCE_LEDGER).get_latest_verdict(_task_id(task))
    if verdict is None:
        return GateRefusal(name, "aucun verdict de vérification",
                           "faire vérifier le pack de preuve avant de fermer")
    if verdict.verdict is not VerdictResult.PASSED:
        return GateRefusal(name, f"dernier verdict : {verdict.verdict.value}",
                           "corriger ce que le verdict signale, puis revérifier")
    return None


def _resolve_evidence_gate(root: Path, task: Any, name: str) -> GateRefusal | None:
    """La couverture du pack : chaque critère d'acceptation doit être couvert."""
    packs = EvidenceService(root / _EVIDENCE_LEDGER).list_packs(_task_id(task))
    if not packs:
        return GateRefusal(name, "aucun pack de preuve à opposer aux critères",
                           "produire un evidence pack couvrant l'acceptation")
    coverage = packs[-1].coverage
    if coverage is None:
        return GateRefusal(name, "le pack ne déclare aucune couverture",
                           "vérifier le pack pour calculer sa couverture")
    if coverage.acceptance_missing:
        manquants = ", ".join(coverage.acceptance_missing)
        return GateRefusal(name, f"critères non couverts : {manquants}",
                           "compléter le pack sur ces critères")
    return None


_RESOLVERS: dict[str, Callable[..., GateRefusal | None]] = {
    "acceptance_criteria": _resolve_acceptance,
    "owner_or_agent_role": _resolve_owner,
    "provider_policy": _resolve_provider_policy,
    "evidence_pack": _resolve_evidence_pack,
    "review_gate": _resolve_review_gate,
    "evidence_gate": _resolve_evidence_gate,
    "context_bundle": _file_resolver(
        lambda tid: _CONTEXT_DIR / tid / "context-bundle.yaml", "context bundle"
    ),
    "decision_trace": _file_resolver(
        lambda tid: _DECISION_DIR / tid / "decision-trace.yaml", "trace de décision"
    ),
}


def check_transition(root: Path, task: Any, from_board: str, to_board: str) -> GateVerdict:
    """Le gate autorise-t-il cette transition, en vocabulaire board ?

    `task` est un `MissionTask` ; le typage reste large pour que ce module ne
    dépende pas du ledger — il vérifie des artefacts, il ne transitionne rien.
    """
    try:
        entry = declared_transitions(root).get((from_board, to_board))
        strictness = _strictness(root)
    except GatesFileError as exc:
        return _unreadable_verdict(exc)
    if entry is None:
        return GateVerdict("", strictness, ())

    refusals: list[GateRefusal] = []
    for name in entry.get("required_evidence", []) or []:
        if not isinstance(name, str):
            continue
        resolver = _RESOLVERS.get(name)
        if resolver is None:
            # Fail-closed : ne pas savoir vérifier n'est pas une autorisation.
            refusals.append(GateRefusal(
                name,
                "preuve exigée mais aucun résolveur ne sait la vérifier",
                "ajouter un résolveur dans grimoire.missions.gates, "
                "ou retirer cette exigence du fichier de gates",
            ))
            continue
        refusal = resolver(root, task, name)
        if refusal is not None:
            refusals.append(refusal)

    return GateVerdict(str(entry.get("id", "")), strictness, tuple(refusals))
