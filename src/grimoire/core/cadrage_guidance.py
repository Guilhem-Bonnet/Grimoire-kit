"""Dire ce qu'on attend, rubrique par rubrique : l'invite, le statut, et ce qu'il reste à faire (lecture seule).

Une personne qui ouvre un chantier ne sait pas ce qu'elle doit remplir, ni si elle doit tout remplir. Ce module répond,
sans modèle et sans rien écrire :

- **l'invite** de chaque rubrique (ce qu'on y met, un exemple), une source unique réutilisable par le CLI ;
- son **statut**, déduit de la vérité du kit et non d'une liste inventée :

  - ``necessaire`` : ``cadrage check`` l'exige. Soit la phase porte le gate (``Phase.gate`` : exigences et cahier des
    charges, toutes leurs rubriques), soit un entretien est entamé et un angle obligatoire encore ouvert vise cette
    rubrique (``check_interview``) ;
  - ``utile`` : l'entretien du kit pose des questions sur ce point (c'est une rubrique cible d'un angle), mais aucun
    contrôle ne l'exige ;
  - ``facultatif`` : ni gate ni question d'entretien ; la rubrique peut rester vide ;

- le **reste à faire** : les rubriques nécessaires encore à compléter, les angles obligatoires encore ouverts, puis ce qui
  est facultatif.

Une rubrique peut être marquée « sans objet pour ce besoin » : une ligne qui commence par le gabarit
(:data:`~grimoire.core.cadrage.PLACEHOLDER`), donc ignorée par ``cadrage check`` exactement comme le gabarit lui-même. Ce
marquage ne change JAMAIS le verdict du gate : une rubrique nécessaire marquée sans objet reste à compléter, « à confirmer ».
Ni ``cadrage.check`` ni ``_section_filled`` ne sont modifiés : ce module ne fait que les lire.
"""

from __future__ import annotations

from typing import Any

from grimoire.core.cadrage import PHASES, PLACEHOLDER, Phase, _section_filled

__all__ = [
    "GUIDE",
    "LIBELLES",
    "SANS_OBJET",
    "reste_a_faire",
    "rubriques",
    "section_bodies",
]

#: Le début de toute ligne « sans objet » : celle de l'entretien (« … d'après l'entretien ») comme celle de la page.
SANS_OBJET = f"{PLACEHOLDER} sans objet"
_CONFIRM = "(à confirmer)"

LIBELLES: dict[str, str] = {
    "necessaire": "nécessaire pour valider",
    "utile": "utile",
    "facultatif": "peut rester vide",
}

#: (ce qu'on y met en une phrase, un exemple court) par (phase, rubrique). Français simple, sans jargon.
GUIDE: dict[tuple[str, str], tuple[str, str]] = {
    ("brief", "Le problème"): (
        "Ce qui ne va pas aujourd'hui, et pour qui, en une ou deux phrases.",
        "Quand je note une idée en réunion, je la perds : elle n'est écrite nulle part.",
    ),
    ("brief", "L'utilisateur"): (
        "Qui s'en servira, et dans quelle situation.",
        "Un développeur seul, le soir, qui reprend un projet laissé en plan.",
    ),
    ("brief", "Pourquoi maintenant"): (
        "Ce qui rend le sujet urgent, ou ce qui a changé récemment.",
        "Le prochain jalon est dans trois semaines et rien n'est encore posé.",
    ),
    ("brief", "Un succès ressemble à"): (
        "Comment on verra que c'est réussi, en termes simples, sans parler de technique.",
        "Je retrouve les idées de la semaine en moins d'une minute.",
    ),
    ("brainstorm", "Pistes"): (
        "Les façons possibles de répondre au besoin, même imparfaites. Une idée par ligne.",
        "Un carnet dans l'appli ; un champ de saisie rapide ; un import depuis les mails.",
    ),
    ("brainstorm", "Questions ouvertes"): (
        "Ce que tu ne sais pas encore et qu'il faudra trancher.",
        "Faut-il garder l'historique des idées supprimées ?",
    ),
    ("brainstorm", "Écarté (et pourquoi)"): (
        "Les idées abandonnées, avec la raison : elle évite d'y revenir.",
        "Synchronisation en ligne : trop lourde pour une première version.",
    ),
    ("comprehension", "Personas"): (
        "Les types de personnes concernées (des « personas »), avec ce que chacune cherche.",
        "Léa, cheffe de projet : veut un résumé lisible en deux minutes.",
    ),
    ("comprehension", "Faits"): (
        "Ce qui est vérifié (observé, mesuré, écrit quelque part), pas supposé.",
        "Aujourd'hui les notes sont réparties dans trois fichiers différents.",
    ),
    ("comprehension", "Hypothèses"): (
        "Ce que tu crois sans l'avoir vérifié : à confirmer plus tard.",
        "Je pense que la plupart des gens n'ont qu'un seul projet à la fois.",
    ),
    ("comprehension", "Contraintes"): (
        "Les limites qui s'imposent : délai, technique, compatibilité, règles à respecter.",
        "Doit marcher sans connexion ; aucune nouvelle dépendance.",
    ),
    ("exigences", "Fonctionnelles"): (
        "Ce que le produit doit faire, une ligne par exigence : le quoi, pas le comment. "
        "Numérote-les (REQ-001…) et marque l'importance entre crochets : Must (indispensable), Should (important), "
        "Could (souhaitable).",
        "- REQ-001 [Must] : enregistrer une idée en un seul geste",
    ),
    ("exigences", "Non-fonctionnelles"): (
        "Les qualités attendues : vitesse, sécurité, accessibilité, fiabilité.",
        "Réponse en moins d'une seconde ; utilisable au clavier seul.",
    ),
    ("exigences", "Priorisation (MoSCoW)"): (
        "Le tri des exigences par importance : indispensable (Must), important (Should), souhaitable (Could), "
        "pas cette fois (Won't).",
        "Must : REQ-001, REQ-002. Should : REQ-003. Won't : l'export en PDF.",
    ),
    ("exigences", "Critères d'acceptation"): (
        "Pour chaque exigence indispensable, ce qu'on vérifiera pour dire qu'elle est satisfaite.",
        "REQ-001 : une idée saisie apparaît dans la liste en moins de 2 secondes.",
    ),
    ("cahier-des-charges", "Synthèse"): (
        "Le besoin et la réponse retenue, en quelques lignes, pour quelqu'un qui n'a rien lu d'autre.",
        "Un carnet d'idées à saisie rapide, local, qui se retrouve par recherche.",
    ),
    ("cahier-des-charges", "Périmètre"): (
        "Ce qui sera livré.",
        "La saisie, la liste, la recherche par mot.",
    ),
    ("cahier-des-charges", "Hors périmètre"): (
        "Ce qui ne sera pas livré, pour borner l'effort et éviter les dérives.",
        "Le partage entre utilisateurs ; la synchronisation en ligne.",
    ),
    ("cahier-des-charges", "Exigences priorisées"): (
        "La liste finale des exigences, triée par importance.",
        "REQ-001 [Must] enregistrer une idée ; REQ-003 [Should] la retrouver par mot.",
    ),
    ("cahier-des-charges", "Critères d'acceptation"): (
        "Comment chaque exigence sera vérifiée avant la livraison.",
        "REQ-001 : test automatique de la saisie, puis essai à la main.",
    ),
    ("cahier-des-charges", "Risques"): (
        "Ce qui peut mal tourner, et comment le repérer tôt.",
        "Les notes se perdent si le fichier est abîmé : sauvegarde à chaque ajout.",
    ),
    ("cahier-des-charges", "Jalons"): (
        "Les grandes étapes, avec leurs dates ou au moins leur ordre.",
        "1. Saisie et liste ; 2. Recherche ; 3. Sauvegarde.",
    ),
}


def section_bodies(text: str) -> dict[str, list[str]]:
    """Le corps de chaque rubrique (``## …``) d'un fichier de phase, lu comme ``phase_report`` le lit."""
    current: str | None = None
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            bodies.setdefault(current, [])
        elif current is not None:
            bodies[current].append(line)
    return bodies


def _angle_targets() -> set[tuple[str, str]]:
    """Les rubriques que l'entretien vise : celle de chaque angle et celles de ses questions d'amorce."""
    from grimoire.core.cadrage_interview import ANGLES

    out: set[tuple[str, str]] = set()
    for angle in ANGLES:
        out.add((angle.phase, angle.section))
        out.update(angle.targets)
    return out


def _open_angles(interview: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Les angles obligatoires encore ouverts d'un entretien entamé (ce que ``check_interview`` réclame)."""
    if not interview or not interview.get("interview", True):
        return []
    from grimoire.core.cadrage_interview import ANGLES

    by_id = {a.id: a for a in ANGLES}
    wanted = set(interview.get("open_required", []))
    return [
        {"id": a["id"], "title": a["title"], "phase": by_id[a["id"]].phase, "section": by_id[a["id"]].section}
        for a in interview.get("angles", [])
        if a["id"] in wanted and a["id"] in by_id
    ]


def _statut(phase: Phase, section: str, opened: set[tuple[str, str]], targets: set[tuple[str, str]]) -> tuple[str, str]:
    if phase.gate:
        return "necessaire", "Le contrôle de validation (`grimoire cadrage check`) exige cette rubrique."
    if (phase.id, section) in opened:
        return "necessaire", "Un angle obligatoire de l'entretien vise cette rubrique et n'est pas encore couvert."
    if (phase.id, section) in targets:
        return "utile", "L'entretien pose des questions sur ce point, mais aucun contrôle ne l'exige."
    return "facultatif", "Aucun contrôle ni question d'entretien ne dépend de cette rubrique."


def _sans_objet(lines: list[str]) -> tuple[bool, str]:
    """La rubrique n'a que le gabarit, dont une ligne « sans objet » : (oui, raison donnée ou vide)."""
    for raw in lines:
        line = raw.strip()
        if line.startswith(SANS_OBJET):
            rest = line[len(SANS_OBJET):].lstrip()
            if rest.startswith("pour ce besoin"):
                rest = rest[len("pour ce besoin"):]
            elif rest.startswith("d'après l'entretien"):
                rest = rest[len("d'après l'entretien"):]
            rest = rest.removesuffix(_CONFIRM).strip().lstrip(":").strip()
            return True, rest
    return False, ""


def rubriques(
    phase: Phase, markdown: str, progress_phase: dict[str, Any] | None, interview: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Les rubriques d'une phase avec leur invite, leur statut et leur état. Lecture seule.

    ``progress_phase`` : la phase de ``cadrage_progress.progress`` (``sections``: ``rempli`` | ``vide`` |
    ``je-ne-sais-pas`` | ``sans-objet``) ; ``interview`` : la vue de l'entretien (``None`` s'il n'y en a pas).
    """
    bodies = section_bodies(markdown)
    targets = _angle_targets()
    opened = {(a["phase"], a["section"]) for a in _open_angles(interview)}
    states = (progress_phase or {}).get("sections", {})
    out: list[dict[str, Any]] = []
    for section in phase.sections:
        quoi, exemple = GUIDE[(phase.id, section)]
        statut, pourquoi = _statut(phase, section, opened, targets)
        lines = bodies.get(section, [])
        etat = states.get(section) or ("rempli" if _section_filled(lines) else "vide")
        raison = ""
        if etat != "rempli":
            marked, raison = _sans_objet(lines)
            if marked:
                etat = "sans-objet"
        out.append({
            "section": section, "statut": statut, "statut_libelle": LIBELLES[statut], "pourquoi": pourquoi,
            "quoi": quoi, "exemple": exemple, "etat": etat, "raison": raison,
            "gabarit": f"{PLACEHOLDER} {section.lower()}.", "marqueur": f"{SANS_OBJET} pour ce besoin",
        })
    return out


def reste_a_faire(phases: list[dict[str, Any]], interview: dict[str, Any] | None, erreur: str = "") -> dict[str, Any]:
    """Le résumé honnête de ce qu'il reste à faire, à partir des rubriques de :func:`rubriques` (clé ``rubriques`` de
    chaque phase du document) et de l'entretien. Le même critère que ``cadrage check`` : ``pret`` est vrai exactement
    quand ``check`` ne rend aucune erreur de rubrique ni d'entretien.
    """
    necessaires: list[dict[str, Any]] = []
    utiles: list[dict[str, Any]] = []
    facultatives: list[dict[str, Any]] = []
    sans_objet = 0
    titles = {p.id: p.title for p in PHASES}
    angles = _open_angles(interview)
    for phase in phases:
        for rub in phase["rubriques"]:
            if rub["etat"] == "sans-objet":
                sans_objet += 1
            item = {
                "phase": phase["id"], "phase_title": titles.get(phase["id"], phase["id"]), "section": rub["section"],
                "etat": rub["etat"], "kind": "rubrique", "raison": rub["raison"],
            }
            if rub["etat"] == "rempli":
                continue
            if rub["statut"] == "necessaire":
                necessaires.append(item)
            elif rub["etat"] == "sans-objet":
                continue  # sorti du facultatif : la personne a dit que ça ne la concerne pas
            elif rub["statut"] == "utile":
                utiles.append(item)
            else:
                facultatives.append(item)
    here = {(i["phase"], i["section"]) for i in necessaires}
    for angle in angles:
        if (angle["phase"], angle["section"]) not in here:  # la rubrique est remplie, mais l'angle reste à couvrir
            necessaires.append({
                "phase": angle["phase"], "phase_title": titles.get(angle["phase"], angle["phase"]),
                "section": angle["section"], "etat": "angle", "kind": "angle", "raison": "", "angle": angle["title"],
            })
    return {
        "pret": not necessaires and not erreur,
        "necessaires": necessaires,
        "utiles": utiles,
        "facultatives": facultatives,
        "sans_objet": sans_objet,
        "erreur": erreur,
    }
