"""L'avancement honnête d'un chantier de cadrage : UN seul calcul, lu par l'assistant, la page et la liste.

Un « bloc » est une rubrique d'un fichier de phase (``## …``). Quatre états, jamais davantage :

- ``rempli`` : du contenu hors gabarit ;
- ``vide`` : le gabarit n'a pas été touché (« À compléter — … ») ;
- ``je-ne-sais-pas`` : le gabarit est intact, mais l'entretien a consigné « je ne sais pas » sur l'angle de la rubrique
  (l'hypothèse est dans « Hypothèses ») ;
- ``sans-objet`` : le gabarit est intact, mais l'angle de la rubrique a été écarté avec une raison.

« Je ne sais pas » et « sans objet » comptent comme couverts, pas comme remplis. Le chantier n'est **complet** que
si aucune rubrique n'est restée au gabarit ; sinon on dit combien de points restent à préciser. Lecture seule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES, phase_report

__all__ = ["ETATS", "progress"]

ETATS: tuple[str, ...] = ("a-cadrer", "en-cours", "cadre", "complet")


def _angle_marks(view: dict[str, Any] | None) -> dict[tuple[str, str], str]:
    """``(phase, rubrique) -> je-ne-sais-pas | sans-objet`` pour les angles dont la rubrique propre est restée au gabarit."""
    if not view or not view.get("interview", True):
        return {}
    from grimoire.core.cadrage_interview import ANGLES, UNKNOWN_PREFIX

    marks: dict[tuple[str, str], str] = {}
    by_id = {a["id"]: a for a in view.get("angles", [])}
    for angle in ANGLES:
        rec = by_id.get(angle.id)
        if rec is None:
            continue
        answers = [str(a) for a in rec.get("answers", [])]
        if rec["state"] == "ecarte" and str(rec.get("reason", "")).strip():
            marks[(angle.phase, angle.section)] = "sans-objet"
        elif rec["state"] == "couvert" and answers and all(a.startswith(UNKNOWN_PREFIX) for a in answers):
            marks[(angle.phase, angle.section)] = "je-ne-sais-pas"
    return marks


def _phase(root: Path, phase: Any, chantier: str | None, marks: dict[tuple[str, str], str]) -> dict[str, Any]:
    report = phase_report(root, phase, chantier)
    sections: dict[str, str] = {}
    for name in phase.sections:
        if report["sections"].get(name) == "rempli":
            sections[name] = "rempli"
        else:  # le « sans objet » de l'entretien reste au gabarit pour le gate : la marque de l'angle le dit
            sections[name] = marks.get((phase.id, name), "vide")
    count = {s: sum(1 for v in sections.values() if v == s) for s in ("rempli", "vide", "je-ne-sais-pas", "sans-objet")}
    return {
        "id": phase.id, "title": phase.title, "file": report["file"], "present": report["state"] != "missing",
        "sections": sections, "total": len(sections), "remplis": count["rempli"], "vides": count["vide"],
        "je_ne_sais_pas": count["je-ne-sais-pas"], "sans_objet": count["sans-objet"],
        "complete": count["vide"] == 0 and report["state"] != "missing",
    }


def _summary(etat: str, vides: int, unknown: int, covered: int, total: int) -> str:
    if etat == "a-cadrer":
        return "À cadrer : rien n’est encore rempli."
    if etat == "en-cours":
        head = f"Cadrage en cours : {covered} angle{'s' if covered > 1 else ''} sur {total} couvert{'s' if covered > 1 else ''}" if total else "Cadrage en cours"
        return f"{head} ; {_points(vides)} au gabarit."
    if etat == "cadre":
        return f"Cadré, avec {_points(vides)} à préciser."
    tail = f", dont {unknown} marqué{'s' if unknown > 1 else ''} « je ne sais pas »" if unknown else ""
    return f"Complet : tous les points sont renseignés{tail}."


def _points(n: int) -> str:
    return f"{n} point{'s' if n > 1 else ''}"


def progress(root: Path, chantier: str | None = None) -> dict[str, Any]:
    """L'avancement du chantier (``None`` ou ``principal`` : le chantier par défaut).

    ``etat`` : ``a-cadrer`` (rien de rempli), ``en-cours`` (des points remplis, des angles obligatoires encore ouverts),
    ``cadre`` (angles obligatoires couverts, des points restent au gabarit : ``a_preciser`` en donne le nombre) ou
    ``complet`` (aucune rubrique au gabarit). Une rubrique ``je-ne-sais-pas`` ou ``sans-objet`` est couverte, pas remplie.
    """
    from grimoire.core.cadrage_interview import view as interview_view

    error = ""
    try:
        view = interview_view(root, chantier)
    except ValueError as exc:  # entretien.json abîmé : dit, sans casser la page
        view, error = None, str(exc)
    marks = _angle_marks(view)
    phases = [_phase(root, p, chantier, marks) for p in PHASES]
    vides = sum(p["vides"] for p in phases)
    unknown = sum(p["je_ne_sais_pas"] for p in phases)
    touched = any(p["remplis"] or p["je_ne_sais_pas"] or p["sans_objet"] for p in phases)
    required = [a for a in (view or {}).get("angles", []) if a["required"]] if view and view.get("interview", True) else []
    covered = sum(1 for a in required if a["state"] == "couvert" or (a["state"] == "ecarte" and str(a.get("reason", "")).strip()))
    open_required = bool(view and view.get("interview", True) and view.get("open_required"))
    if vides == 0 and all(p["present"] for p in phases):
        etat = "complet"
    elif not touched:
        etat = "a-cadrer"
    elif view is not None and view.get("interview", True) and not open_required and required:
        etat = "cadre"
    else:
        etat = "en-cours"
    return {
        "etat": etat,
        "resume": _summary(etat, vides, unknown, covered, len(required)),
        "a_preciser": vides,
        "a_confirmer": unknown,
        "angles": {"couverts": covered, "total": len(required), "complet": bool(required) and not open_required},
        "phases": phases,
        "complet_phases": sum(1 for p in phases if p["complete"]),
        "erreur": error,
    }
