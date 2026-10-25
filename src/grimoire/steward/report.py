"""Le rapport de l'intendant (``grimoire steward report``), schéma ``grimoire-steward-report/v1``.

Carte + verdicts validés (et rejetés, avec leur raison), regroupés par **action proposée** :

- ``fermer_avec_preuve`` — ``livree``, ``obsolete``, ``doublon`` établis ;
- ``reprendre_pr_abandonnee`` — ``abandonnee`` établi ;
- ``realigner`` — ``conflit_simple`` (CHANGELOG seul) et ``conflit_semantique`` établis ;
- ``a_faire_par_theme`` — ``a_faire`` établis, rangés par ``theme`` ;
- ``a_l_humain`` — ``humain`` établis ;
- ``rejetes`` — verdicts dont la preuve ne s'est pas vérifiée, avec la raison ;
- ``contradictions`` — un signal (conflit, branche orpheline, worktree sale, état antérieur) posé sur un
  élément qui a pourtant un verdict établi : jamais masqué, montré à côté du verdict ;
- ``signaux_sans_verdict`` — ce que la carte signale et qu'aucun verdict établi ne couvre.

Étape 1 : une **proposition**. Le rapport ne ferme rien, ne fusionne rien, n'écrit ni au ledger ni sur GitHub.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grimoire.steward import REPORT_SCHEMA
from grimoire.steward.project_map import find_element
from grimoire.steward.sources import ensure_output_dir

__all__ = ["ACTIONS", "CONTRADICTING", "build_report", "render_text", "write_output"]

#: Signaux qui contredisent un verdict établi sur le même élément (sauf un conflit jugé conflit).
CONTRADICTING = frozenset(
    {"conflit_changelog_seul", "conflit_autre", "branche_orpheline", "worktree_sale", "branche_etat_anterieur"}
)

ACTIONS = {
    "fermer_avec_preuve": ("livree", "obsolete", "doublon"),
    "reprendre_pr_abandonnee": ("abandonnee",),
    "realigner": ("conflit_simple", "conflit_semantique"),
    "a_l_humain": ("humain",),
}
_TITLES = {
    "fermer_avec_preuve": "Fermer avec preuve",
    "reprendre_pr_abandonnee": "Reprendre une PR ou une branche abandonnée",
    "realigner": "Réaligner sur la cible",
    "a_faire_par_theme": "À faire, par thème",
    "a_l_humain": "À l'humain",
    "rejetes": "Verdicts rejetés (preuve non vérifiée)",
    "contradictions": "Contradictions : un signal contredit un verdict établi",
    "signaux_sans_verdict": "Signaux sans verdict établi",
}


def _item(entry: Mapping[str, Any], carte: Mapping[str, Any] | None) -> dict[str, Any]:
    element = find_element(carte, str(entry.get("element"))) if carte else None
    item = {k: entry[k] for k in ("element", "verdict", "theme", "taille", "note", "raisons", "conflit") if k in entry}
    item["titre"] = str((element or {}).get("titre") or (element or {}).get("nom") or "")
    item["preuves"] = [
        {k: p[k] for k in ("type", "verifiee", "constat") if k in p} for p in entry.get("preuves") or [] if isinstance(p, dict)
    ]
    return item


def build_report(carte: Mapping[str, Any], validation: Mapping[str, Any] | None) -> dict[str, Any]:
    established = list((validation or {}).get("etablis") or [])
    groups: dict[str, Any] = {name: [] for name in ACTIONS}
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for entry in established:
        verdict = entry.get("verdict")
        if verdict == "a_faire":
            by_theme.setdefault(str(entry.get("theme") or "sans thème"), []).append(_item(entry, carte))
            continue
        for name, verdicts in ACTIONS.items():
            if verdict in verdicts:
                groups[name].append(_item(entry, carte))
    judged = {str(e.get("element")): str(e.get("verdict")) for e in established}
    signals: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    for signal in carte.get("signaux") or []:
        hits = [r for r in (signal.get("ref"), signal.get("branche")) if r and r in judged]
        if not hits:
            signals.append(dict(signal))
            continue
        verdict = judged[hits[0]]
        both_conflicts = str(signal.get("type", "")).startswith("conflit_") and verdict.startswith("conflit_")
        if signal.get("type") in CONTRADICTING and not both_conflicts:
            contradictions.append({**signal, "element": hits[0], "verdict": verdict})
    return {
        "$schema": REPORT_SCHEMA,
        "genere_le": carte.get("genere_le"),
        "cible": carte.get("cible"),
        "carte": carte.get("resume"),
        "verdicts": (validation or {}).get("resume") or None,
        "actions": {
            "fermer_avec_preuve": groups["fermer_avec_preuve"],
            "reprendre_pr_abandonnee": groups["reprendre_pr_abandonnee"],
            "realigner": groups["realigner"],
            "a_faire_par_theme": dict(sorted(by_theme.items())),
            "a_l_humain": groups["a_l_humain"],
        },
        "rejetes": [_item(e, carte) for e in (validation or {}).get("rejetes") or []],
        "contradictions": contradictions,
        "signaux_sans_verdict": signals,
        "limites": [
            "Le jugement reste celui d'un LLM : seule la preuve citée est vérifiée par du code.",
            "Étape 1 : une proposition. Aucune tâche fermée, aucune PR touchée, rien d'écrit au ledger ni sur GitHub.",
        ],
    }


def _line(item: Mapping[str, Any]) -> str:
    title = f" — {item['titre']}" if item.get("titre") else ""
    proofs = ", ".join(str(p.get("constat") or p.get("type")) for p in item.get("preuves") or [] if p.get("type") != "note")
    return f"  - {item.get('element')} [{item.get('verdict')}]{title}" + (f"\n      preuve : {proofs}" if proofs else "")


def render_text(report: Mapping[str, Any]) -> str:
    lines = [f"Intendant — état des lieux (cible {(report.get('cible') or {}).get('ref')}, {report.get('genere_le')})"]
    resume = report.get("carte") or {}
    lines.append(
        "Carte : "
        + ", ".join(f"{k} {resume.get(k)}" for k in ("taches_ouvertes", "branches", "worktrees", "prs", "issues"))
    )
    verdicts = report.get("verdicts")
    lines.append(
        f"Verdicts : {verdicts['etablis']} établi(s), {verdicts['rejetes']} rejeté(s)"
        if verdicts
        else "Verdicts : aucun fichier fourni (--verdicts), seule la carte est rapportée"
    )
    actions = report.get("actions") or {}
    for name in ("fermer_avec_preuve", "reprendre_pr_abandonnee", "realigner"):
        if actions.get(name):
            lines += ["", f"{_TITLES[name]} ({len(actions[name])})", *(_line(i) for i in actions[name])]
    if actions.get("a_faire_par_theme"):
        lines += ["", _TITLES["a_faire_par_theme"]]
        for theme, items in actions["a_faire_par_theme"].items():
            lines.append(f"  {theme} ({len(items)})")
            lines += [f"  {_line(i)}" for i in items]
    if actions.get("a_l_humain"):
        lines += ["", f"{_TITLES['a_l_humain']} ({len(actions['a_l_humain'])})", *(_line(i) for i in actions["a_l_humain"])]
    if report.get("rejetes"):
        lines += ["", f"{_TITLES['rejetes']} ({len(report['rejetes'])})"]
        for item in report["rejetes"]:
            lines.append(f"  - {item.get('element')} [{item.get('verdict')}]")
            lines += [f"      raison : {reason}" for reason in item.get("raisons") or []]
    if report.get("contradictions"):
        lines += ["", f"{_TITLES['contradictions']} ({len(report['contradictions'])})"]
        lines += [
            f"  - {c['element']} [{c['verdict']}] contredit par {c['type']} : {c['detail']}" for c in report["contradictions"]
        ]
    if report.get("signaux_sans_verdict"):
        lines += ["", f"{_TITLES['signaux_sans_verdict']} ({len(report['signaux_sans_verdict'])})"]
        lines += [f"  - {s['type']} {s['ref']} : {s['detail']}" for s in report["signaux_sans_verdict"]]
    lines += ["", "Limites :", *(f"  - {limit}" for limit in report.get("limites") or [])]
    return "\n".join(lines) + "\n"


def write_output(root: Path, name: str, payload: Mapping[str, Any] | str) -> Path:
    """Écrit *name* sous ``_grimoire-output/steward/`` — le seul endroit où l'intendant écrit."""
    target = ensure_output_dir(root) / name
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    target.write_text(text, encoding="utf-8")
    return target
