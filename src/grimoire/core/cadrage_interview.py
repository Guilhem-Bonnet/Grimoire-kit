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
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import DEFAULT_CHANTIER, PHASES, PLACEHOLDER, cadrage_dir
from grimoire.core.regular_file import open_regular
from grimoire.missions.request_kind import KIND_PROFILES, RequestKind, parse_kind

__all__ = [
    "ANGLES",
    "DEPTHS",
    "Angle",
    "answer",
    "check_interview",
    "classify_depth",
    "interview_plan",
    "interview_states",
    "retract",
    "view",
]

STATE_FILE = "entretien.json"
DEPTHS: tuple[str, ...] = ("express", "standard", "approfondi")
STATES: tuple[str, ...] = ("ouvert", "couvert", "ecarte")
#: Un état d'entretien tient en quelques Kio ; au-delà, il est refusé (il est relu à chaque lecture du cockpit).
MAX_STATE_BYTES = 1_048_576
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
UNKNOWN_PREFIX = "À valider (je ne sais pas) : "
#: Ce qu'un « sans objet » de l'entretien écrit à la place du gabarit. Il commence par le gabarit (``PLACEHOLDER``) :
#: le gate de ``cadrage check`` l'ignore donc déjà (la rubrique reste « à compléter » jusqu'à confirmation humaine).
NOT_APPLICABLE = f"{PLACEHOLDER} sans objet d'après l'entretien"


@dataclass(frozen=True, slots=True)
class Angle:
    """Un angle d'entretien : intention, amorces génériques, section du cadrage qui reçoit la réponse."""

    id: str
    title: str
    intent: str
    seeds: tuple[str, str]
    phase: str
    section: str
    #: La rubrique (phase, section) que vise CHAQUE question d'amorce, dans l'ordre ; au-delà, ``phase``/``section``.
    targets: tuple[tuple[str, str], ...] = ()


ANGLES: tuple[Angle, ...] = (
    Angle("utilisateurs", "Utilisateurs et usage réel",
          "Savoir qui s'en sert, dans quelle situation, et ce qu'il fait aujourd'hui sans.",
          ("Qui utilisera cela, et dans quelle situation concrète ?",
           "Que fait cette personne aujourd'hui pour s'en passer, et qu'est-ce qui l'agace ?"),
          "brief", "L'utilisateur", (("brief", "L'utilisateur"), ("brief", "Le problème"))),
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
          "brainstorm", "Écarté (et pourquoi)", (("brainstorm", "Pistes"), ("brainstorm", "Écarté (et pourquoi)"))),
)
_BY_ID = {a.id: a for a in ANGLES}


def question_target(angle: Angle, index: int) -> tuple[str, str]:
    """La rubrique (phase, section) que vise la question d'amorce ``index`` de l'angle."""
    return angle.targets[index] if index < len(angle.targets) else (angle.phase, angle.section)


def _label(question: str) -> str:
    """Une question en une ligne, sans balise d'emphase (elle se pose en italique devant la réponse), bornée."""
    flat = _fold(question).replace("*", "")
    return flat if len(flat) <= 300 else flat[:299] + "…"

#: Par profondeur : (angles obligatoires, questions d'amorce par angle).
_PROFILE: dict[str, tuple[tuple[str, ...], int]] = {
    "express": (("utilisateurs", "criteres", "hors-perimetre"), 1),
    "standard": (("utilisateurs", "criteres", "hors-perimetre", "contraintes", "risques"), 2),
    "approfondi": (tuple(a.id for a in ANGLES), 2),
}
_DEEP_TYPES = {"migration", "security", "architecture"}
_DEEP_RISKS = {"strict", "security_critical"}


_DEEP_KINDS = {RequestKind.NEW_PROJECT, RequestKind.MIGRATION, RequestKind.SECURITY}


def classify_depth(proposal: dict[str, Any], kind: RequestKind | None = None) -> str:
    """Profondeur déduite d'une proposition d'intake : risque, type de mission, périmètre, nature."""
    if kind in _DEEP_KINDS:
        return "approfondi"
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


def read_regular(path: Path, *, limit: int | None = None) -> str | None:
    """Le texte d'un fichier ORDINAIRE du cadrage ; ``None`` s'il n'existe pas.

    Un lien, une FIFO, un périphérique ou un fichier trop gros lève ``ValueError`` AVANT toute lecture (une FIFO
    bloquerait le tour). Ouvert sans suivre de lien et sans bloquer, puis revérifié sur le descripteur ouvert.
    """
    try:
        handle = open_regular(path)
    except FileNotFoundError:
        return None
    with handle:
        if limit is not None and os.fstat(handle.fileno()).st_size > limit:
            raise ValueError(f"{path.name} dépasse {limit // 1024} Kio : refusé")
        return handle.read().decode("utf-8", errors="replace")


def _written_ok(entry: Any, angle: Angle, *, skip: bool = False) -> bool:
    """Une trace d'écriture ``{"phase", "section", "line"[, "text"]}`` : ce que ``answer`` a posé, pour que ``retract`` le retire.

    N'est acceptée que la trace qu'``answer`` peut produire pour CET angle : une rubrique que l'angle vise (ou les
    questions ouvertes pour « je ne sais pas »), et une ligne de la forme que l'entretien écrit (une puce d'une seule
    ligne, ``À préciser`` ou la mention « Sans objet »). Une trace éditée à la main ne peut pas viser une exigence."""
    if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("phase", "section", "line")):
        return False
    if not isinstance(entry.get("text", ""), str) or set(entry) - {"phase", "section", "line", "text"}:
        return False
    line = entry["line"]
    if "\n" in line or "\r" in line or len(line) > 5000:
        return False
    here = (entry["phase"], entry["section"])
    if skip:
        return here == (angle.phase, angle.section) and (line == "" or line.startswith(NOT_APPLICABLE))
    if line.startswith("- À préciser : "):
        return here == ("brainstorm", "Questions ouvertes")
    return line.startswith("- ") and (here == (angle.phase, angle.section) or here in angle.targets)


def _load(root: Path, chantier: str | None) -> dict[str, Any] | None:
    """L'état enregistré ; ``None`` si aucun entretien. Un fichier présent mais invalide lève ValueError."""
    path = _state_path(root, chantier)
    try:
        raw = read_regular(path, limit=MAX_STATE_BYTES)
        if raw is None:
            return None
        data = json.loads(raw)
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError(f"{STATE_FILE} illisible ({exc.__class__.__name__}) : corrigez-le ou supprimez-le") from exc
    if not isinstance(data, dict) or data.get("depth") not in DEPTHS:
        raise ValueError(f"{STATE_FILE} invalide : profondeur absente ou inconnue ({', '.join(DEPTHS)})")
    if "kind" in data:
        try:
            parse_kind(str(data["kind"]))
        except ValueError as exc:
            raise ValueError(f"{STATE_FILE} invalide : {exc}") from exc
    angles = data.get("angles", {})
    if not isinstance(angles, dict):
        raise ValueError(f"{STATE_FILE} invalide : « angles » doit être un objet")
    for aid, rec in angles.items():
        if aid not in _BY_ID or not isinstance(rec, dict):
            raise ValueError(f"{STATE_FILE} invalide : angle {aid!r} inconnu ou mal formé")
        if rec.get("state", "ouvert") not in STATES:
            raise ValueError(f"{STATE_FILE} invalide : état {rec.get('state')!r} inconnu pour {aid}")
        if not isinstance(rec.get("answers", []), list):
            raise ValueError(f"{STATE_FILE} invalide : « answers » de {aid} doit être une liste")
        written = rec.get("lines", [])
        if not isinstance(written, list) or not all(w is None or _written_ok(w, _BY_ID[aid]) for w in written):
            raise ValueError(f"{STATE_FILE} invalide : « lines » de {aid} mal formé")
        if "skip" in rec and not _written_ok(rec["skip"], _BY_ID[aid], skip=True):
            raise ValueError(f"{STATE_FILE} invalide : « skip » de {aid} mal formé")
    data["angles"] = angles
    return data


def _fold(text: str) -> str:
    """Replie la saisie sur une seule ligne : une réponse ne peut ni écrire de titre ni de frontmatter."""
    return " ".join(text.split())


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
    text = ""
    with contextlib.suppress(ValueError, OSError):  # un brief qui n'est pas un fichier ordinaire n'est pas lu
        text = read_regular(brief) or ""
    if text:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "---", "_", PLACEHOLDER, "phase:", "projet:", "status:")):
                parts.append(s)
    return "\n".join(p for p in parts if p).strip() or (chantier or "cadrage")


def interview_plan(
    root: Path, chantier: str | None = None, *, depth: str | None = None, text: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Le plan d'entretien : fige la profondeur au premier appel, puis rend l'état courant des angles.

    ``depth`` force (et enregistre) la profondeur ; sans elle, l'état enregistré fait foi, sinon
    l'intake déterministe la calcule. ``kind`` force la nature de la demande ; sans elle, la nature
    enregistrée, sinon celle que l'intake propose. Changer de nature conserve les réponses.
    Une question ne déclenche aucun entretien : rien n'est écrit.
    """
    if depth is not None and depth not in DEPTHS:
        raise ValueError(f"profondeur inconnue : {depth!r} — {', '.join(DEPTHS)}")
    forced_kind = parse_kind(kind) if kind else None
    from grimoire.missions.intake import intake_proposal

    state = _load(root, chantier)
    source = "enregistrée"
    proposal: dict[str, Any] | None = None
    if depth is None and state is not None:
        depth = str(state["depth"])
    else:
        proposal = intake_proposal(text or _request_text(root, chantier))
        source = "forcée" if depth is not None else f"intake ({proposal['risk_profile']}, {proposal['mission_type']})"
    if forced_kind is not None:
        current, kind_source = forced_kind, "forcée"
    elif state is not None and state.get("kind"):
        current, kind_source = parse_kind(str(state["kind"])), "enregistrée"
    else:
        proposal = proposal or intake_proposal(text or _request_text(root, chantier))
        current = parse_kind(str(proposal["request_kind"]["kind"]))
        kind_source = f"intake ({proposal['request_kind']['reason']})"
    if depth is None:
        depth = classify_depth(proposal, current) if proposal else "standard"
    if not KIND_PROFILES[current].interview:
        # Une question n'écrit rien et n'efface jamais un entretien déjà entamé : il reste contrôlé.
        view = _render({"depth": depth, "angles": {}, "kind": current.value}, source, kind_source)
        if state is not None:
            kept = _open_required(state)
            view.update(open_required=kept, complete=not kept, kept_kind=str(state.get("kind") or ""))
            view["note"] += " L'entretien déjà entamé est conservé et reste contrôlé par cadrage check."
        return view
    old_kind = (state or {}).get("kind")
    if state is None or state["depth"] != depth or old_kind != current.value:
        state = {"depth": depth, "kind": current.value, "angles": (state or {}).get("angles", {})}
        _save(root, chantier, state)
    return _render(state, source, kind_source)


def _angle_done(rec: dict[str, Any]) -> bool:
    state = rec.get("state", "ouvert")
    return state == "couvert" or (state == "ecarte" and bool(str(rec.get("reason", "")).strip()))


def _open_required(state: dict[str, Any]) -> list[str]:
    """Angles obligatoires de la profondeur encore ouverts — quelle que soit la nature (gate)."""
    required, _ = _PROFILE[str(state["depth"])]
    recorded = state.get("angles", {})
    return [aid for aid in required if not _angle_done(recorded.get(aid, {}))]


def _render(state: dict[str, Any], source: str, kind_source: str = "enregistrée") -> dict[str, Any]:
    depth = str(state["depth"])
    required, per_angle = _PROFILE[depth]
    kind = parse_kind(str(state["kind"])) if state.get("kind") else None
    profile = KIND_PROFILES[kind] if kind else None
    if kind is not None and profile is not None and not profile.interview:
        return {
            "kind": kind.value, "kind_label": profile.label, "kind_source": kind_source,
            "interview": False, "blocking": False, "postmortem_due": False, "depth": depth,
            "depth_source": source, "questions_per_angle": 0,
            "kind_questions": [], "deliverable": profile.deliverable, "acceptance": profile.acceptance,
            "deferred": "", "angles": [], "open_required": [], "complete": True,
            "note": "Une question ne déclenche aucun entretien : répondre directement.",
        }
    non_blocking = profile is not None and not profile.blocking
    recorded = state.get("angles", {})
    angles: list[dict[str, Any]] = []
    for a in ANGLES:
        rec = recorded.get(a.id, {})
        angles.append({
            "id": a.id, "title": a.title, "intent": a.intent,
            "required": a.id in required, "state": rec.get("state", "ouvert"),
            "questions": [] if non_blocking else list(a.seeds[:per_angle]),
            "answers": list(rec.get("answers", [])), "reason": rec.get("reason", ""),
        })
    open_required = _open_required(state)
    view: dict[str, Any] = {
        "depth": depth, "depth_source": source, "questions_per_angle": 0 if non_blocking else per_angle,
        "angles": angles, "open_required": open_required, "complete": not open_required,
        "interview": True, "blocking": not non_blocking, "postmortem_due": non_blocking and bool(open_required),
        "deferred": "",
    }
    if profile is not None and kind is not None:
        questions = list(profile.questions[: profile.max_questions] if profile.max_questions else profile.questions)
        view.update(
            kind=kind.value, kind_label=profile.label, kind_source=kind_source, kind_questions=questions,
            deliverable=profile.deliverable, acceptance=profile.acceptance,
        )
        if non_blocking:
            view["deferred"] = (
                "Rétablir d'abord : l'action immédiate n'est jamais bloquée ; l'entretien complet est reporté "
                "au post-mortem, dû tant que les angles obligatoires restent ouverts (cadrage check le signale)."
            )
    return view


def _refuse_accepted(path: Path) -> None:
    """Un document au front matter ``status`` accepté/validé n'est jamais réécrit par l'entretien."""
    from grimoire.core.cadrage_requirements import _ACCEPTED_STATUSES
    from grimoire.core.cadrage_requirements import _fold as fold_status

    lines = (read_regular(path) or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return
    for line in lines[1:]:
        if line.strip() == "---":
            return
        key, _, value = line.partition(":")
        if key.strip() == "status" and fold_status(value).strip().strip("'\"") in _ACCEPTED_STATUSES:
            raise ValueError(f"{path.name} est au statut accepté : l'entretien n'y écrit ni n'y retire rien")


def _append_to_section(path: Path, section: str, line: str) -> None:
    _refuse_accepted(path)
    lines = (read_regular(path) or "").splitlines()
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
    *, unknown: bool = False, skip: bool = False, question: str | None = None,
    rubrique: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Consigne une réponse, un « je ne sais pas » (question ouverte) ou l'écart motivé d'un angle (rubrique « Sans objet »).

    ``question`` : la question posée, si elle diffère de l'amorce de l'angle (sinon l'amorce de rang égal au nombre de
    réponses déjà données). Elle reste lisible devant la réponse.
    ``rubrique`` : la rubrique (phase, section) qui reçoit la réponse, si ce n'est pas celle que vise la question d'amorce ;
    elle doit être l'une de celles que l'angle porte (``ValueError`` sinon). Sans elle, la rubrique visée par la question.
    """
    angle = _BY_ID.get(angle_id)
    if angle is None:
        raise ValueError(f"angle inconnu : {angle_id!r} — {', '.join(_BY_ID)}")
    text = _fold(text)
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
    rec.setdefault("answers", [])
    target = cadrage_dir(root, chantier)
    index = len(rec["answers"])  # la question d'amorce à laquelle on répond : autant de réponses déjà données
    asked = _label(question) if question else (_label(angle.seeds[index]) if index < len(angle.seeds) else "")
    phase_id, section = question_target(angle, index)
    if rubrique is not None:
        if rubrique not in {(angle.phase, angle.section), *angle.targets}:
            raise ValueError(f"rubrique {rubrique[0]}/{rubrique[1]} : l'angle {angle.id!r} ne la porte pas")
        phase_id, section = rubrique
    if skip:
        # La rubrique de l'angle est marquée « Sans objet » à sa place (le gabarit s'efface), le brainstorm reste net.
        rec.update(state="ecarte", reason=text)
        reason = "" if text.rstrip(". ").lower() == "sans objet" else f" : {text}"
        marker = f"{NOT_APPLICABLE}{reason} (à confirmer)"
        phase_id, section = angle.phase, angle.section
        filename = next(p.filename for p in PHASES if p.id == phase_id)
        free = not _section_has_content(target / filename, section)
        if free:
            _append_to_section(target / filename, section, marker)
        rec["skip"] = {"phase": phase_id, "section": section, "line": marker if free else ""}
    elif unknown:
        # Une question ouverte exploitable : la question posée, pas le titre de l'angle.
        phase_id, section = "brainstorm", "Questions ouvertes"
        note = f"{UNKNOWN_PREFIX}{asked or angle.title}"
        if note not in rec["answers"]:
            _pad(rec)
            rec["answers"].append(note)
            line = f"- À préciser : {asked or angle.title}" + (f" — {text}" if text else "")
            rec["lines"].append({"phase": phase_id, "section": section, "line": line, "text": text})
            filename = next(p.filename for p in PHASES if p.id == phase_id)
            _append_to_section(target / filename, section, line)
        rec["state"] = "couvert"
    else:
        if text in rec["answers"]:  # même réponse redonnée : pas de doublon dans le cadrage
            return _render(state, "enregistrée")
        _pad(rec)
        rec["answers"].append(text)
        rec["state"] = "couvert"
        # La réponse va dans la rubrique que vise sa question, qui reste lisible en italique devant elle.
        line = f"- *{asked}* {text}" if asked else f"- {text}"
        rec["lines"].append({"phase": phase_id, "section": section, "line": line})
        filename = next(p.filename for p in PHASES if p.id == phase_id)
        _append_to_section(target / filename, section, line)
    _save(root, chantier, state)
    return _render(state, "enregistrée")


def _pad(rec: dict[str, Any]) -> None:
    """Aligne ``lines`` sur ``answers`` : les réponses d'avant ce suivi n'ont pas de trace (``None``)."""
    lines: list[dict[str, str] | None] = rec.setdefault("lines", [])
    missing = len(rec["answers"]) - len(lines)
    if missing > 0:
        lines[:0] = [None] * missing
    elif missing < 0:
        del lines[len(rec["answers"]):]


def _section_has_content(path: Path, section: str) -> bool:
    """La rubrique porte-t-elle autre chose que son gabarit ?"""
    lines = (read_regular(path) or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {section}"), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return any(ln.strip() and not ln.strip().startswith(PLACEHOLDER) for ln in lines[start + 1:end])


def _phase_file(root: Path, chantier: str | None, phase_id: str) -> Path:
    """Le fichier de phase d'un chantier, bâti depuis des listes réelles : jamais depuis le nom reçu.

    Le dossier du chantier est celui que la liste des sous-dossiers de ``_grimoire/cadrage/`` rend (le nom reçu ne sert
    qu'à le retrouver), le fichier vient de la table fixe des phases ; le chemin est résolu puis doit rester sous le
    dossier de cadrage du projet. Lien symbolique (dossier, chantier ou fichier) : refusé. ``ValueError`` sinon.
    """
    phase = next((p for p in PHASES if p.id == phase_id), None)
    base = cadrage_dir(root, None)
    if phase is None or base.is_symlink() or not base.is_dir():
        raise ValueError("dossier de cadrage introuvable ou en lien symbolique : refusé")
    directory = base
    if chantier not in (None, DEFAULT_CHANTIER):
        directory = next((c for c in base.iterdir() if c.name == chantier and c.is_dir() and not c.is_symlink()), base)
        if directory is base:
            raise ValueError("chantier inconnu")
    path = directory / phase.filename
    if path.is_symlink():
        raise ValueError(f"{phase.filename} est un lien symbolique : refusé")
    resolved = path.resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError(f"{phase.filename} sort du dossier de cadrage : refusé")
    return resolved


def _remove_from_section(path: Path, section: str, line: str) -> bool:
    """Retire de la rubrique la ligne exacte *line* (la DERNIÈRE si elle y est plusieurs fois : celle que l'entretien a
    ajoutée en dernier, pas une copie plus haut) ; si la rubrique n'a plus de contenu, le gabarit revient.

    Refuse d'écrire dans un document au statut accepté ou validé (``ValueError``)."""
    _refuse_accepted(path)
    lines = (read_regular(path) or "").splitlines()
    head = f"## {section}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == head), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    at = next((i for i in range(end - 1, start, -1) if lines[i].strip() == line.strip()), None)
    if at is None:
        return False  # la ligne a été retouchée dans le document : on n'y touche pas
    del lines[at]
    end -= 1
    if not any(ln.strip() for ln in lines[start + 1:end]):
        lines[start + 1:end] = ["", f"{PLACEHOLDER} {section.lower()}.", ""]
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return True


def retract(root: Path, chantier: str | None, angle_id: str) -> dict[str, Any]:
    """Annule la DERNIÈRE réponse consignée d'un angle (réponse, « je ne sais pas » ou écart) : l'inverse de :func:`answer`.

    La ligne que ``answer`` a ajoutée au document est retirée si elle y est encore telle quelle (une ligne retouchée à la
    main est laissée, ``removed`` le dit) ; l'angle revient à l'état d'avant. Rend ``{"kind", "text", "removed"}`` : le
    texte retiré, pour le proposer à la correction.
    """
    angle = _BY_ID.get(angle_id)
    if angle is None:
        raise ValueError(f"angle inconnu : {angle_id!r} — {', '.join(_BY_ID)}")
    _phase_file(root, chantier, "brief")  # le chantier doit être l'un des vrais dossiers, avant toute lecture
    state = _load(root, chantier)
    rec = (state or {}).get("angles", {}).get(angle.id)
    if state is None or not rec:
        raise ValueError(f"rien à retirer sur l'angle {angle.id!r}")
    answers: list[str] = rec.setdefault("answers", [])
    if rec.get("state") == "ecarte" and str(rec.get("reason", "")).strip():
        kind, text = "skip", str(rec.pop("reason"))
        mark = rec.pop("skip", None)
        if mark is None:  # écart d'avant la marque « Sans objet » : la ligne était dans le brainstorm
            removed = _remove_from_section(
                _phase_file(root, chantier, "brainstorm"),
                "Écarté (et pourquoi)", f"- Angle « {angle.title} » écarté : {text}")
        else:  # rien n'a été écrit si la rubrique avait déjà du contenu
            removed = not mark["line"] or _remove_from_section(
                _phase_file(root, chantier, mark["phase"]), mark["section"], mark["line"])
    elif answers:
        _pad(rec)
        text = str(answers.pop())
        entry = rec["lines"].pop()
        if entry is not None:  # la trace dit où la ligne a été posée et ce qu'elle disait
            kind = "unknown" if text.startswith(UNKNOWN_PREFIX) else "answer"
            removed = _remove_from_section(_phase_file(root, chantier, entry["phase"]), entry["section"], entry["line"])
            text = str(entry.get("text", "")) if kind == "unknown" else text
        elif text.startswith(UNKNOWN_PREFIX):  # d'avant la trace : l'hypothèse était dans « Hypothèses »
            kind, removed = "unknown", _remove_from_section(
                _phase_file(root, chantier, "comprehension"), "Hypothèses", f"- {text}")
            text = text[len(UNKNOWN_PREFIX) + len(angle.title):].lstrip(" —")
        else:
            kind, removed = "answer", _remove_from_section(
                _phase_file(root, chantier, angle.phase), angle.section, f"- {text}")
    else:
        raise ValueError(f"rien à retirer sur l'angle {angle.id!r}")
    rec["state"] = "couvert" if answers else "ouvert"
    _save(root, chantier, state)
    return {"kind": kind, "text": text, "removed": removed}


MAX_STATE_DIRS = 50


def interview_states(root: Path) -> list[dict[str, Any]]:
    """L'état des entretiens déjà entamés, en lecture seule : un par chantier qui a un ``entretien.json``.

    Rien n'est écrit, aucune intake n'est lancée : sans entretien, c'est une liste vide et trois ``stat`` au plus.
    Un état illisible donne une ligne ``error`` (la liste ne casse pas). Chaque ligne porte ``chantier``
    (``principal`` pour le dossier historique) et la vue de ``interview_plan`` (angles, ouverts, complet).
    """
    base = cadrage_dir(root)
    if base.is_symlink() or not base.is_dir():
        return [{"chantier": "principal", "error": "dossier de cadrage en lien symbolique : refusé"}] if base.is_symlink() else []
    try:
        if not base.resolve().is_relative_to(root.resolve()):
            return [{"chantier": "principal", "error": "dossier de cadrage hors du projet : refusé"}]
    except OSError:
        return []
    names: list[str | None] = [None]
    with contextlib.suppress(OSError):
        # Ne comptent que les dossiers à nom de chantier sûr qui portent un état d'entretien.
        found = sorted(
            d.name for d in base.iterdir()
            if _SLUG_RE.fullmatch(d.name) and d.is_dir() and not d.is_symlink() and os.path.lexists(d / STATE_FILE)
        )
        names += found[:MAX_STATE_DIRS]
    out: list[dict[str, Any]] = []
    for name in names:
        label = name or "principal"
        try:
            state = _load(root, name)
            if state is not None:
                out.append({"chantier": label, **_render(state, "enregistrée")})
        except ValueError as exc:
            out.append({"chantier": label, "error": str(exc)})
    return out


def view(root: Path, chantier: str | None = None) -> dict[str, Any] | None:
    """La vue de l'entretien d'UN chantier, en lecture seule ; ``None`` si aucun entretien n'a été entamé.

    Même vue que ``interview_plan`` (angles, réponses, complet), sans rien écrire ni lancer d'intake. Un état illisible
    lève ``ValueError``.
    """
    state = _load(root, chantier)
    return None if state is None else _render(state, "enregistrée")


def check_interview(root: Path, chantier: str | None = None) -> list[str]:
    """Erreurs du gate : un angle obligatoire resté ouvert. Sans entretien entamé, rien à contrôler."""
    try:
        state = _load(root, chantier)
    except ValueError as exc:
        return [f"Entretien de cadrage : {exc}"]
    if state is None:
        return []
    open_required = _open_required(state)
    if not open_required:
        return []
    names = ", ".join(open_required)
    kind = parse_kind(str(state["kind"])) if state.get("kind") else None
    if kind is not None and not KIND_PROFILES[kind].blocking:
        return [f"Post-mortem dû ({STATE_FILE}, nature {kind.value}, profondeur {state['depth']}) : "
                f"l'action immédiate n'a pas été bloquée ; angle(s) obligatoire(s) à couvrir — {names}"]
    return [f"Entretien de cadrage ({STATE_FILE}, profondeur {state['depth']}) : angle(s) obligatoire(s) "
            f"encore ouvert(s) ou écarté(s) sans raison — {names}"]
