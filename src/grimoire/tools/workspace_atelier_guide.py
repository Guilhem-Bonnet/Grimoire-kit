"""Le cadrage guidé SANS agent, dans la page de l'Atelier de spec (épique #1197).

Le kit sait déjà établir la liste des questions d'entretien (:func:`grimoire.core.cadrage_interview.interview_plan`) et
consigner une réponse (:func:`~grimoire.core.cadrage_interview.answer`, ``grimoire cadrage interview answer``). Ce module
fait jouer cet entretien dans la page, **sans aucun modèle** : une question à la fois, la réponse consignée dans le
bon bloc du document exactement comme la commande, « je ne sais pas » (hypothèse), « sans objet » (angle écarté), retour
à la question précédente pour corriger, reprise après rechargement (le curseur se déduit de ``entretien.json``).

Aucune route nouvelle : ce sont des gestes de ``POST atelier/chantiers/<slug>/assistant`` (mêmes gardes : jeton de session,
cible de projet, refus d'une dictée pour tout geste sauf la réponse en texte libre). Le geste n'envoie jamais de consigne :
un identifiant de la liste fermée :data:`GESTES`, la réponse de l'humain, et la question que l'écran montrait (refus 409
si elle a changé entre-temps). Aucun chemin n'est construit depuis le client : le chantier est retrouvé dans la liste
des chantiers du projet, chaque fichier de phase passe par la garde de confinement de :mod:`workspace_atelier`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES, cadrage_dir

__all__ = ["GESTES", "REPONDRE", "run", "view"]

DEMARRER = "guide-demarrer"
REPONDRE = "guide-repondre"
NE_SAIS_PAS = "guide-ne-sais-pas"
SANS_OBJET = "guide-sans-objet"
RETOUR = "guide-retour"
#: La liste fermée des gestes du cadrage guidé ; la page n'en envoie pas d'autre.
GESTES: tuple[str, ...] = (DEMARRER, REPONDRE, NE_SAIS_PAS, SANS_OBJET, RETOUR)
MAX_ANSWER_CHARS = 4000
SKIP_REASON = "sans objet"


def _name(slug: str) -> str | None:
    return None if slug == "principal" else slug


def _done(rec: dict[str, Any], index: int) -> bool:
    """La question *index* d'un angle a-t-elle sa réponse ? Un écart motivé ou un « je ne sais pas » ferment tout l'angle."""
    from grimoire.core.cadrage_interview import UNKNOWN_PREFIX

    if rec["state"] == "ecarte" and str(rec.get("reason", "")).strip():
        return True
    answers = [str(a) for a in rec.get("answers", [])]
    if any(a.startswith(UNKNOWN_PREFIX) for a in answers):
        return True
    return len(answers) > index


def _steps(interview: dict[str, Any]) -> list[dict[str, Any]]:
    """Les questions de l'entretien dans l'ordre : pour chaque angle obligatoire, ses questions d'amorce."""
    from grimoire.core.cadrage_interview import ANGLES

    phase_of = {a.id: a for a in ANGLES}
    titles = {p.id: p.title for p in PHASES}
    out: list[dict[str, Any]] = []
    for rec in interview["angles"]:
        if not rec["required"]:
            continue
        meta = phase_of[rec["id"]]
        for index, question in enumerate(rec["questions"]):
            out.append({
                "angle": rec["id"], "title": rec["title"], "intent": rec["intent"], "index": index,
                "of": len(rec["questions"]), "question": question, "phase": meta.phase,
                "phase_title": titles[meta.phase], "section": meta.section, "done": _done(rec, index),
                "answers": list(rec.get("answers", [])), "reason": rec.get("reason", ""), "state": rec["state"],
            })
    return out


def view(root: Path, slug: str) -> dict[str, Any]:
    """L'état du cadrage guidé : la question courante, la précédente (pour la corriger), où l'on en est. Lecture seule."""
    from grimoire.core.cadrage_interview import view as interview_view

    try:
        interview = interview_view(root, _name(slug))
    except ValueError as exc:
        return {"available": False, "started": False, "error": str(exc)}
    if interview is None:
        return {"available": True, "started": False, "finished": False, "total": 0, "position": 0, "current": None,
                "previous": None}
    steps = _steps(interview)
    if not steps:  # nature sans entretien bloquant : rien à jouer ici
        return {"available": False, "started": True, "error": "", "finished": True, "total": 0, "position": 0,
                "current": None, "previous": None, "note": "Cette demande ne déclenche pas d'entretien."}
    cursor = next((i for i, s in enumerate(steps) if not s["done"]), None)
    last_done = max((i for i, s in enumerate(steps) if s["done"] and (cursor is None or i < cursor)), default=None)

    def public(step: dict[str, Any] | None) -> dict[str, Any] | None:
        if step is None:
            return None
        return {k: step[k] for k in ("angle", "title", "intent", "index", "of", "question", "phase", "phase_title", "section")}

    previous = None if last_done is None else {**public(steps[last_done]), "answer": _shown(steps[last_done])}  # type: ignore[dict-item]
    return {
        "available": True, "started": True, "error": "", "finished": cursor is None, "total": len(steps),
        "position": len(steps) if cursor is None else cursor, "current": None if cursor is None else public(steps[cursor]),
        "previous": previous,
    }


def _shown(step: dict[str, Any]) -> str:
    """La réponse de la question *step*, telle qu'on la montre pour la corriger."""
    from grimoire.core.cadrage_interview import UNKNOWN_PREFIX

    if step["state"] == "ecarte" and str(step["reason"]).strip():
        return "Sans objet"
    answers = step["answers"]
    if any(str(a).startswith(UNKNOWN_PREFIX) for a in answers):
        return "Je ne sais pas"
    return str(answers[step["index"]]) if len(answers) > step["index"] else ""


def _confine(root: Path, slug: str) -> None:
    """Chaque fichier de phase du chantier passe par la garde de confinement de l'Atelier (lien symbolique : 404)."""
    from grimoire.tools import workspace_atelier as wa

    for phase in PHASES:
        wa._phase_path(root, slug, phase.filename)


def _current(root: Path, slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """La question courante, si c'est bien celle que la page montrait (``angle`` et ``index`` du corps), sinon 409."""
    from grimoire.tools import workspace_atelier as wa

    shown = view(root, slug)
    current = shown.get("current")
    if not shown.get("started") or current is None:
        raise ValueError("aucune question en cours : démarre le cadrage guidé, ou il est terminé")
    if body.get("angle") != current["angle"] or body.get("index") != current["index"]:
        error = wa.ConflictError("la question a changé depuis l'affichage : recharge le cadrage")
        error.reason = "question-changee"
        raise error
    return dict(current)


def run(root: Path, slug: str, geste: str, body: dict[str, Any]) -> dict[str, Any]:
    """Un geste du cadrage guidé. ``slug`` est déjà validé et retrouvé par l'appelant (:func:`assistant.act`)."""
    from grimoire.core.cadrage_interview import answer, interview_plan, retract
    from grimoire.core.exceptions import GrimoireError
    from grimoire.core.fsio import locked
    from grimoire.tools import workspace_atelier_loop as loop

    _confine(root, slug)
    chantier = _name(slug)
    out: dict[str, Any] = {"ok": True}
    with locked(cadrage_dir(root, chantier) / "entretien.json"):
        try:
            if geste == DEMARRER:
                interview_plan(root, chantier)
            elif geste == RETOUR:
                previous = view(root, slug).get("previous")
                if previous is None:
                    raise ValueError("aucune réponse à corriger : c'est la première question")
                out["retracted"] = retract(root, chantier, previous["angle"])
                out["retracted"]["angle"] = previous["angle"]
            else:
                current = _current(root, slug, body)
                if geste == REPONDRE:
                    text = body.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("text requis : la réponse ne peut pas être vide")
                    if len(text) > MAX_ANSWER_CHARS:
                        raise ValueError(f"réponse trop longue ({MAX_ANSWER_CHARS} caractères au plus)")
                    answer(root, chantier, current["angle"], text)
                elif geste == NE_SAIS_PAS:
                    answer(root, chantier, current["angle"], "", unknown=True)
                else:
                    answer(root, chantier, current["angle"], SKIP_REASON, skip=True)
        except GrimoireError as exc:
            raise ValueError(str(exc)) from exc
    loop.after_write(root, slug)
    out["guide"] = view(root, slug)
    return out
