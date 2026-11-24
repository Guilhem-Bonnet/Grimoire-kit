"""Entretien de cadrage — sept angles, une profondeur, un état (épique #1342, lot M2).

Le kit n'appelle aucun modèle : il tient la **structure** de l'entretien (les angles, leur
intention, deux questions d'amorce génériques), l'**état** de chaque angle, les **réponses** et le
contrôle de **complétude**. C'est l'agent orchestrateur qui formule les questions adaptées à la
demande, puis consigne les réponses par ``grimoire cadrage interview answer``.

La profondeur (express, standard, approfondi) vient de l'intake déterministe
(:func:`grimoire.missions.intake.intake_proposal`) : type de mission, profil de risque, périmètre.
Elle borne le nombre de questions par angle et fixe les angles obligatoires. L'état est écrit dans
``entretien.json`` du chantier ; les réponses sont reportées dans les fichiers de phase.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES, PLACEHOLDER, cadrage_dir

__all__ = [
    "ANGLES",
    "DEPTHS",
    "Angle",
    "answer",
    "check_interview",
    "classify_depth",
    "interview_plan",
]

STATE_FILE = "entretien.json"
DEPTHS: tuple[str, ...] = ("express", "standard", "approfondi")
STATES: tuple[str, ...] = ("ouvert", "couvert", "ecarte")
UNKNOWN_PREFIX = "À valider (je ne sais pas) : "


@dataclass(frozen=True, slots=True)
class Angle:
    """Un angle d'entretien : intention, amorces génériques, section du cadrage qui reçoit la réponse."""

    id: str
    title: str
    intent: str
    seeds: tuple[str, str]
    phase: str
    section: str


ANGLES: tuple[Angle, ...] = (
    Angle("utilisateurs", "Utilisateurs et usage réel",
          "Savoir qui s'en sert, dans quelle situation, et ce qu'il fait aujourd'hui sans.",
          ("Qui utilisera cela, et dans quelle situation concrète ?",
           "Que fait cette personne aujourd'hui pour s'en passer, et qu'est-ce qui l'agace ?"),
          "brief", "L'utilisateur"),
    Angle("existant", "Existant à réutiliser",
          "Éviter de reconstruire ce qui existe déjà dans le dépôt, le kit ou l'écosystème.",
          ("Qu'existe-t-il déjà qui résout tout ou partie du besoin ?",
           "Quelles briques du dépôt ou de l'écosystème peut-on étendre plutôt que remplacer ?"),
          "comprehension", "Faits"),
    Angle("criteres", "Critères de réussite",
          "Rendre le succès observable : un verdict qu'un test ou une personne peut rendre.",
          ("À quoi voit-on, concrètement, que c'est réussi ?",
           "Quel verdict mesurable (test, commande, revue) le prouverait ?"),
          "brief", "Un succès ressemble à"),
    Angle("hors-perimetre", "Hors périmètre",
          "Poser ce qu'on ne fera pas, pour borner l'effort et éviter la dérive.",
          ("Qu'est-ce qui est explicitement exclu de cette livraison ?",
           "Quelles demandes voisines refuse-t-on de traiter maintenant ?"),
          "cahier-des-charges", "Hors périmètre"),
    Angle("contraintes", "Contraintes",
          "Lister les limites subies : techniques, de délai, de compatibilité, de conformité.",
          ("Quelles contraintes techniques, de délai ou de compatibilité s'imposent ?",
           "Qu'est-ce qui ne doit surtout pas casser ?"),
          "comprehension", "Contraintes"),
    Angle("risques", "Risques et pré-mortem",
          "Imaginer l'échec pour en tirer les risques avant de construire.",
          ("Si cela échoue dans six mois, quelle en sera la cause la plus probable ?",
           "Quel risque est le plus coûteux s'il se réalise, et comment le détecte-t-on tôt ?"),
          "cahier-des-charges", "Risques"),
    Angle("alternatives", "Alternatives plus simples",
          "Vérifier qu'une solution plus petite ne suffit pas.",
          ("Quelle version plus simple, voire sans code, répondrait à 80 % du besoin ?",
           "Qu'a-t-on envisagé puis écarté, et pourquoi ?"),
          "brainstorm", "Écarté (et pourquoi)"),
)
_BY_ID = {a.id: a for a in ANGLES}

#: Par profondeur : (angles obligatoires, questions d'amorce par angle).
_PROFILE: dict[str, tuple[tuple[str, ...], int]] = {
    "express": (("utilisateurs", "criteres", "hors-perimetre"), 1),
    "standard": (("utilisateurs", "criteres", "hors-perimetre", "contraintes", "risques"), 2),
    "approfondi": (tuple(a.id for a in ANGLES), 2),
}
_DEEP_TYPES = {"migration", "security", "architecture"}
_DEEP_RISKS = {"strict", "security_critical"}


def classify_depth(proposal: dict[str, Any]) -> str:
    """Profondeur déduite d'une proposition d'intake : risque, type de mission, périmètre."""
    risk = str(proposal.get("risk_profile", ""))
    mission_type = str(proposal.get("mission_type", ""))
    scopes = [s for s in proposal.get("scope_hints", []) if s != "repo"]
    if risk in _DEEP_RISKS or mission_type in _DEEP_TYPES or len(scopes) >= 3:
        return "approfondi"
    if risk == "light" and len(scopes) <= 1:
        return "express"
    return "standard"


def _state_path(root: Path, chantier: str | None) -> Path:
    return cadrage_dir(root, chantier) / STATE_FILE


def _load(root: Path, chantier: str | None) -> dict[str, Any] | None:
    path = _state_path(root, chantier)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("depth") in DEPTHS else None


def _save(root: Path, chantier: str | None, data: dict[str, Any]) -> None:
    path = _state_path(root, chantier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _request_text(root: Path, chantier: str | None) -> str:
    """La demande telle que le cadrage la porte : titre du chantier puis contenu rempli du brief."""
    from grimoire.core.chantiers import read_chantier

    parts: list[str] = []
    with contextlib.suppress(Exception):  # un chantier illisible ne bloque pas l'entretien
        parts.append(read_chantier(root, chantier).titre)
    brief = cadrage_dir(root, chantier) / PHASES[0].filename
    if brief.is_file():
        for line in brief.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "---", "_", PLACEHOLDER, "phase:", "projet:", "status:")):
                parts.append(s)
    return "\n".join(p for p in parts if p).strip() or (chantier or "cadrage")


def interview_plan(
    root: Path, chantier: str | None = None, *, depth: str | None = None, text: str | None = None
) -> dict[str, Any]:
    """Le plan d'entretien : fige la profondeur au premier appel, puis rend l'état courant des angles.

    ``depth`` force (et enregistre) la profondeur ; sans elle, l'état enregistré fait foi, sinon
    l'intake déterministe la calcule.
    """
    if depth is not None and depth not in DEPTHS:
        raise ValueError(f"profondeur inconnue : {depth!r} — {', '.join(DEPTHS)}")
    from grimoire.missions.intake import intake_proposal

    state = _load(root, chantier)
    source = "enregistrée"
    if depth is not None:
        source = "forcée"
    elif state is not None:
        depth = str(state["depth"])
    else:
        proposal = intake_proposal(text or _request_text(root, chantier))
        depth = classify_depth(proposal)
        source = f"intake ({proposal['risk_profile']}, {proposal['mission_type']})"
    if state is None or state["depth"] != depth:
        state = {"depth": depth, "angles": (state or {}).get("angles", {})}
        _save(root, chantier, state)
    return _render(state, source)


def _render(state: dict[str, Any], source: str) -> dict[str, Any]:
    depth = str(state["depth"])
    required, per_angle = _PROFILE[depth]
    recorded = state.get("angles", {})
    angles = []
    for a in ANGLES:
        rec = recorded.get(a.id, {})
        angles.append({
            "id": a.id, "title": a.title, "intent": a.intent,
            "required": a.id in required, "state": rec.get("state", "ouvert"),
            "questions": list(a.seeds[:per_angle]),
            "answers": list(rec.get("answers", [])), "reason": rec.get("reason", ""),
        })
    open_required = [x["id"] for x in angles if x["required"] and x["state"] == "ouvert"]
    return {
        "depth": depth, "depth_source": source, "questions_per_angle": per_angle,
        "angles": angles, "open_required": open_required, "complete": not open_required,
    }


def _append_to_section(path: Path, section: str, line: str) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    head = f"## {section}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == head), None)
    if start is None:
        lines += ["", head, "", line]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        body = [ln for ln in lines[start + 1:end] if not ln.strip().startswith(PLACEHOLDER)]
        while body and not body[-1].strip():
            body.pop()
        lines[start + 1:end] = [*body, line, ""] if body else ["", line, ""]
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def answer(
    root: Path, chantier: str | None, angle_id: str, text: str,
    *, unknown: bool = False, skip: bool = False,
) -> dict[str, Any]:
    """Consigne une réponse, un « je ne sais pas » (hypothèse) ou l'écart motivé d'un angle."""
    angle = _BY_ID.get(angle_id)
    if angle is None:
        raise ValueError(f"angle inconnu : {angle_id!r} — {', '.join(_BY_ID)}")
    text = text.strip()
    if unknown and skip:
        raise ValueError("--unknown et --skip s'excluent")
    if skip and not text:
        raise ValueError("écarter un angle exige une raison")
    if not unknown and not skip and not text:
        raise ValueError("la réponse est vide : donnez-la, ou --unknown")
    state = _load(root, chantier)
    if state is None:
        interview_plan(root, chantier)
        state = _load(root, chantier) or {"depth": "standard", "angles": {}}
    from grimoire.core.cadrage import scaffold

    if not cadrage_dir(root, chantier).joinpath(PHASES[0].filename).is_file():
        scaffold(root, project_name=root.name, chantier=chantier)
    rec = state["angles"].setdefault(angle.id, {"state": "ouvert", "answers": []})
    target = cadrage_dir(root, chantier)
    if skip:
        rec.update(state="ecarte", reason=text)
        _append_to_section(target / next(p.filename for p in PHASES if p.id == "brainstorm"),
                           "Écarté (et pourquoi)", f"- Angle « {angle.title} » écarté : {text}")
    elif unknown:
        note = f"{UNKNOWN_PREFIX}{angle.title}" + (f" — {text}" if text else "")
        rec["answers"].append(note)
        rec["state"] = "couvert"
        _append_to_section(target / next(p.filename for p in PHASES if p.id == "comprehension"),
                           "Hypothèses", f"- {note}")
    else:
        rec["answers"].append(text)
        rec["state"] = "couvert"
        filename = next(p.filename for p in PHASES if p.id == angle.phase)
        _append_to_section(target / filename, angle.section, f"- {text}")
    _save(root, chantier, state)
    return _render(state, "enregistrée")


def check_interview(root: Path, chantier: str | None = None) -> list[str]:
    """Erreurs du gate : un angle obligatoire resté ouvert. Sans entretien entamé, rien à contrôler."""
    state = _load(root, chantier)
    if state is None:
        return []
    view = _render(state, "enregistrée")
    if not view["open_required"]:
        return []
    names = ", ".join(a["id"] for a in view["angles"] if a["id"] in view["open_required"])
    return [f"Entretien de cadrage ({STATE_FILE}, profondeur {view['depth']}) : angle(s) obligatoire(s) "
            f"encore ouvert(s) — {names}"]
