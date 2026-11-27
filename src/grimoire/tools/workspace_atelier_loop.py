"""Fermer la boucle de l'Atelier de spec : ce qui suit l'écriture, et ce qui attend la personne (épique #1197).

Trois choses, toutes dérivées de faits que le kit écrit déjà, jamais d'une saisie :

- **l'état du chantier avance d'après des faits** (:func:`fact_state`, :func:`after_write`) : cadrage en cours, exigences
  écrites, découpé en N tâches. Il ne fait qu'avancer (voir :func:`grimoire.core.chantiers.record_write`) et la date de
  modification est remise à jour à chaque écriture de l'Atelier dans le chantier, jamais à une lecture ;
- **les tâches créées par le découpage** (:func:`tasks`), listées sur la page du chantier avec leur état et de quoi
  ouvrir l'espace Travail ;
- **ce qui attend la personne** (:func:`waiting`) : la question en cours, les suggestions à trancher, les exigences à
  valider, la question posée au découpage. Lecture seule.

Rien ici ne valide une exigence ni ne lance un travail.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

__all__ = ["after_write", "author", "duplicate_requirements", "fact_state", "tasks", "waiting"]


def _name(slug: str) -> str | None:
    return None if slug == "principal" else slug


def _own(key: str, slug: str) -> str | None:
    """L'identifiant ``REQ-nnn`` d'une clé de couverture si elle est de CE chantier, sinon ``None``."""
    head, sep, tail = key.rpartition("/")
    if slug == "principal":
        return tail if not sep and tail else None
    return tail if sep and head == slug and tail else None


def _service(root: Path) -> Any:
    from grimoire.tools.workspace_atelier_trace import _service as service

    return service(root)


def tasks(root: Path, slug: str) -> list[dict[str, Any]]:
    """Les tâches vivantes qui servent une exigence de ce chantier : identifiant, titre, état, exigence. Lecture seule."""
    from grimoire.missions.requirement_gate import requirement_of
    from grimoire.missions.schemas import TaskState

    service = _service(root)
    if not service.has_ledger:
        return []
    out: list[dict[str, Any]] = []
    for task in service.list_tasks():
        req = _own(requirement_of(task), slug)
        if req and task.status is not TaskState.CANCELLED:
            out.append({"id": task.id, "title": task.title, "status": task.status.value, "requirement": req})
    return sorted(out, key=lambda t: (t["requirement"], t["id"]))


def _questions_at_split(root: Path, slug: str) -> int:
    """Les questions encore ouvertes que le découpage a posées pour ce chantier (tâche d'ancrage ``cadrage: questions``)."""
    from grimoire.missions.cadrage_plan import ANCHOR
    from grimoire.missions.questions import OPEN, records_of, status_of
    from grimoire.missions.schemas import TaskState

    service = _service(root)
    if not service.has_ledger:
        return 0
    count = 0
    for task in service.list_tasks():
        extra = task.extra or {}
        if extra.get("cadrage") != ANCHOR or task.status in (TaskState.CANCELLED, TaskState.CLOSED):
            continue
        if (extra.get("chantier") or "principal") == slug:
            count += sum(1 for r in records_of(task) if status_of(task, r) == OPEN)
    return count


def _musts_to_validate(root: Path, slug: str) -> int:
    """Les exigences Must écrites que la MOA n'a pas (ou plus) validées. Zéro sans exigence : rien n'est construit."""
    from grimoire.core.cadrage_requirements import read_requirements

    try:
        found = read_requirements(root, _name(slug))
    except (ValueError, OSError):
        return 0
    if not any(r.priority == "must" for r in found.requirements):
        return 0
    from grimoire.tools import workspace_atelier_trace as live

    try:
        built = live._built(root, slug)
    except (ValueError, OSError, FileNotFoundError):
        return 0
    return sum(1 for item in built.requirements if item.priority == "must" and not item.validated)


def waiting(root: Path, slug: str, *, suggestions: int, asking: bool) -> dict[str, int]:
    """Ce qui attend la personne sur ce chantier, en nombres : ``question`` (0 ou 1), ``suggestions``, ``a_valider``,
    ``decoupage`` (questions posées au découpage). ``asking`` : une question d'entretien est posée (agent ou guidée)."""
    out = {"question": int(asking), "suggestions": suggestions, "a_valider": 0, "decoupage": 0}
    with contextlib.suppress(Exception):  # un ledger ou un journal abîmé ne casse pas la liste : le compteur reste à 0
        out["a_valider"] = _musts_to_validate(root, slug)
        out["decoupage"] = _questions_at_split(root, slug)
    return out


def duplicate_requirements(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les identifiants d'exigence définis plus d'une fois dans UN MÊME fichier de phase (``phases`` : vues du document).

    Le même ``REQ-nnn`` repris d'un fichier à l'autre (exigences, puis cahier des charges) est normal ; deux fois dans
    le même fichier, le kit ne sait plus laquelle compte (``cadrage plan`` pose une question bloquante).
    """
    out: list[dict[str, Any]] = []
    for phase in phases:
        seen: dict[str, int] = {}
        for block in phase.get("blocks", []):
            req = block.get("req") if block.get("kind") == "exigence" else None
            if isinstance(req, str) and req:
                seen[req] = seen.get(req, 0) + 1
        for req, count in seen.items():
            if count > 1:
                out.append({
                    "req": req, "phase": phase["id"], "count": count,
                    "message": f"{req} est défini {count} fois dans cette phase : le kit ne saura pas laquelle compte.",
                })
    return out


def author(root: Path) -> dict[str, str]:
    """Le nom que porte un commentaire de l'humain : la MOA déclarée, sinon l'utilisateur du projet (``user.name``), sinon
    rien (la page écrit « Toi »). ``source`` : ``moa``, ``utilisateur`` ou ``""``."""
    from ruamel.yaml import YAML

    from grimoire.missions.requirement_gate import gate_mode, origin_root

    project = origin_root(root)
    mode = gate_mode(project)
    if mode.declared and mode.moa.strip():
        return {"nom": mode.moa.strip(), "source": "moa"}
    with contextlib.suppress(Exception):
        path = project / "project-context.yaml"
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) if path.is_file() else None
        user = data.get("user") if isinstance(data, dict) else None
        name = user.get("name") if isinstance(user, dict) else None
        # « Developer » est ce que `grimoire init` écrit faute de connaître la personne : ce n'est pas un nom déclaré.
        if isinstance(name, str) and name.strip() and "\n" not in name and name.strip().casefold() != "developer":
            return {"nom": name.strip()[:60], "source": "utilisateur"}
    return {"nom": "", "source": ""}


def fact_state(root: Path, slug: str) -> str:
    """L'état que les faits justifient : ``en-cours`` (découpé), ``pret-a-valider`` (exigences écrites ET cadrage sans
    point à préciser : l'écran dit « Complet »), ``en-discussion`` (cadrage entamé), sinon ``idee``."""
    from grimoire.core.cadrage_progress import progress
    from grimoire.core.cadrage_requirements import read_requirements

    with contextlib.suppress(Exception):
        if tasks(root, slug):
            return "en-cours"
    advance = progress(root, _name(slug))["etat"]
    written = False
    with contextlib.suppress(ValueError, OSError):
        written = bool(read_requirements(root, _name(slug)).identified)
    # Des exigences écrites ne suffisent pas : « Prêt à valider » ne se dit que si rien ne reste à préciser.
    if written and advance == "complet":
        return "pret-a-valider"
    return "idee" if advance == "a-cadrer" and not written else "en-discussion"


def after_write(root: Path, slug: str) -> None:
    """Après une écriture RÉUSSIE de l'Atelier dans le chantier : date de modification et état d'après les faits."""
    from grimoire.core.chantiers import record_write
    from grimoire.core.exceptions import GrimoireError

    with contextlib.suppress(GrimoireError, OSError):
        record_write(root, slug, fact_state(root, slug))
