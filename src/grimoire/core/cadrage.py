"""Cadrage produit — comprendre avant de construire (brique B4).

LE pattern d'entreprise absent du kit : ne pas foncer à l'aveugle. Un flux
guidé en cinq phases — brief, brainstorm, compréhension, exigences, cahier des
charges — matérialisé en artefacts gouvernés sous ``_grimoire/cadrage/``.

Discipline embarquée dans les gabarits :
- le **brainstorm** diverge sans censure et note ce qui est écarté (et
  pourquoi) ;
- la **compréhension** sépare strictement les *faits* des *hypothèses* (même
  discipline que le contrat ``handoff-packet``) ;
- les **exigences** sont priorisées (MoSCoW) et chacune porte ses critères
  d'acceptation ;
- le **cahier des charges** consolide périmètre / hors-périmètre / risques /
  jalons et ne passe le gate que complet.

Le kit ne « pense » pas (il n'est jamais un moteur d'exécution) : il structure,
mesure la progression et **gate** la complétude. La réflexion se fait dans vos
sessions d'agents, guidées par ces gabarits.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

CADRAGE_DIR = Path("_grimoire") / "cadrage"
PLACEHOLDER = "> À compléter —"


@dataclass(frozen=True, slots=True)
class Phase:
    """Une phase du cadrage : fichier + sections exigées."""

    id: str
    filename: str
    title: str
    sections: tuple[str, ...]
    gate: bool  # True : la complétude est exigée par `cadrage check`


PHASES: tuple[Phase, ...] = (
    Phase(
        "brief",
        "01-brief.md",
        "Brief — l'intention",
        ("Le problème", "L'utilisateur", "Pourquoi maintenant", "Un succès ressemble à"),
        gate=False,
    ),
    Phase(
        "brainstorm",
        "02-brainstorm.md",
        "Brainstorm — diverger",
        ("Pistes", "Questions ouvertes", "Écarté (et pourquoi)"),
        gate=False,
    ),
    Phase(
        "comprehension",
        "03-comprehension.md",
        "Compréhension — l'utilisateur réel",
        ("Personas", "Faits", "Hypothèses", "Contraintes"),
        gate=False,
    ),
    Phase(
        "exigences",
        "04-exigences.md",
        "Exigences — quoi, pas comment",
        (
            "Fonctionnelles",
            "Non-fonctionnelles",
            "Priorisation (MoSCoW)",
            "Critères d'acceptation",
        ),
        gate=True,
    ),
    Phase(
        "cahier-des-charges",
        "05-cahier-des-charges.md",
        "Cahier des charges",
        (
            "Synthèse",
            "Périmètre",
            "Hors périmètre",
            "Exigences priorisées",
            "Critères d'acceptation",
            "Risques",
            "Jalons",
        ),
        gate=True,
    ),
)

_INTROS: dict[str, str] = {
    "brief": (
        "Une page, pas plus. Si le problème ou l'utilisateur restent flous "
        "ici, tout le reste le sera aussi."
    ),
    "brainstorm": (
        "Divergence sans censure : les mauvaises idées d'aujourd'hui cadrent "
        "les bonnes de demain. Noter AUSSI ce qu'on écarte, et pourquoi — "
        "c'est la moitié de la valeur."
    ),
    "comprehension": (
        "Séparer strictement les FAITS (observés, sourcés) des HYPOTHÈSES "
        "(à valider). Une hypothèse déguisée en fait est la première cause "
        "de produit à côté de la plaque."
    ),
    "exigences": (
        "Le QUOI, jamais le COMMENT. Chaque exigence Must porte au moins un "
        "critère d'acceptation vérifiable."
    ),
    "cahier-des-charges": (
        "La consolidation opposable : ce document est ce qu'on s'engage à "
        "livrer — et surtout ce qu'on s'engage à NE PAS livrer (hors "
        "périmètre). Le gate `grimoire cadrage check` exige sa complétude."
    ),
}


def _template(phase: Phase, project_name: str) -> str:
    lines = [
        "---",
        f"phase: {phase.id}",
        f"projet: {project_name}",
        "status: draft",
        "---",
        "",
        f"# {phase.title}",
        "",
        f"_{_INTROS[phase.id]}_",
        "",
    ]
    for section in phase.sections:
        lines += [f"## {section}", "", f"{PLACEHOLDER} {section.lower()}.", ""]
    return "\n".join(lines)


def scaffold(root: Path, *, project_name: str, force: bool = False) -> list[Path]:
    """Écrit les gabarits des cinq phases. Ne réécrit jamais sans ``force``."""
    target = root / CADRAGE_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for phase in PHASES:
        path = target / phase.filename
        if path.exists() and not force:
            continue
        path.write_text(_template(phase, project_name), encoding="utf-8")
        written.append(path)
    return written


def _section_filled(body_lines: list[str]) -> bool:
    """Une section est remplie si elle contient du contenu hors gabarit."""
    return any(
        line.strip() and not line.strip().startswith(PLACEHOLDER)
        for line in body_lines
    )


def phase_report(root: Path, phase: Phase) -> dict[str, Any]:
    """État d'une phase : absent / vide / partiel / rempli, par section."""
    path = root / CADRAGE_DIR / phase.filename
    report: dict[str, Any] = {
        "phase": phase.id,
        "file": str(CADRAGE_DIR / phase.filename),
        "state": "missing",
        "sections": {},
        "gate": phase.gate,
    }
    if not path.is_file():
        return report
    # Un fichier corrompu ou sauvé dans un autre encodage ne doit pas casser
    # `cadrage status`/`check` : on remplace les octets invalides.
    text = path.read_text(encoding="utf-8", errors="replace")
    current: str | None = None
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            bodies.setdefault(current, [])
        elif current is not None:
            bodies[current].append(line)
    filled = 0
    for section in phase.sections:
        ok = section in bodies and _section_filled(bodies[section])
        report["sections"][section] = "rempli" if ok else "à compléter"
        filled += 1 if ok else 0
    report["state"] = (
        "rempli"
        if filled == len(phase.sections)
        else ("partiel" if filled else "vide")
    )
    return report


def status(root: Path) -> dict[str, Any]:
    """Progression du cadrage, phase par phase."""
    reports = [phase_report(root, p) for p in PHASES]
    done = sum(1 for r in reports if r["state"] == "rempli")
    return {
        "phases": reports,
        "progress": f"{done}/{len(PHASES)}",
        "initialized": any(r["state"] != "missing" for r in reports),
    }


def check(root: Path) -> tuple[list[str], list[str]]:
    """Gate du cadrage : erreurs (phases `gate`) et avertissements (les autres).

    Un cahier des charges incomplet est une **erreur** — on ne construit pas
    sur un engagement flou. Les phases amont incomplètes sont des
    avertissements : le chemin est recommandé, pas imposé.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for phase in PHASES:
        report = phase_report(root, phase)
        if report["state"] == "rempli":
            continue
        missing = [
            s for s, state in report["sections"].items() if state != "rempli"
        ] or list(phase.sections)
        msg = (
            f"{phase.title} ({report['file']}) : "
            f"{report['state']} — sections à compléter : {', '.join(missing)}"
        )
        if phase.gate:
            errors.append(msg)
        else:
            warnings.append(msg)
    return errors, warnings
