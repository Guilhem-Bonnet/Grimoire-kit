"""Budget de cérémonie : ce que les routes ont le droit de coûter, vérifié en CI (W6-01).

``evals/ceremony-budget.yaml`` fige trois choses, et :func:`check_budget` rend
chaque écart visible au lieu de le laisser dériver :

1. **le coût de chaque route** (étapes, tours humains, profondeur) : ajouter
   une étape à une route sans relever son budget dans un diff relisible échoue ;
2. **les seuils** (:data:`grimoire.missions.ceremony.INDICES`) : chacun porte
   ``calibrated`` et sa date, et le fichier doit les recopier à l'identique —
   changer un seuil dans le code sans le dater dans le budget échoue ;
3. **un corpus de tâches types** dont la route attendue est figée, et des
   plafonds sur le corpus entier (tours humains, part de routes lourdes).

La calibration est datée d'une version du kit : au-delà de deux versions
mineures d'écart, elle est déclarée périmée et le contrôle échoue. Rien ici ne
prétend qu'un seuil est juste ; le contrôle garantit seulement qu'il est
**daté, recopié et rejoué**.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from grimoire.missions.ceremony import INDICES, MAX_ROUTES, ROUTES, inputs_for, suggest
from grimoire.missions.schemas import RiskProfile

__all__ = ["BUDGET_SCHEMA", "MAX_CALIBRATION_MINOR_LAG", "MIN_CORPUS", "check_budget"]

BUDGET_SCHEMA = "grimoire-ceremony-budget/v1"

#: Écart maximal, en versions mineures, entre la calibration et le kit.
MAX_CALIBRATION_MINOR_LAG = 2

#: Taille minimale du corpus figé.
MIN_CORPUS = 12

_VERSION = re.compile(r"^(\d+)\.(\d+)")


def _major_minor(text: str) -> tuple[int, int] | None:
    match = _VERSION.match(str(text).strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _is_date(value: Any) -> bool:
    if isinstance(value, date):
        return True
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return False
    return True


def _check_calibration(doc: Mapping[str, Any], kit_version: str, out: list[str]) -> None:
    cal = doc.get("calibration")
    if not isinstance(cal, Mapping):
        out.append("calibration : bloc absent")
        return
    if not isinstance(cal.get("calibrated"), bool):
        out.append("calibration.calibrated : booléen attendu")
    if not _is_date(cal.get("as_of")):
        out.append(f"calibration.as_of : date ISO attendue, reçu {cal.get('as_of')!r}")
    calibrated_on = _major_minor(str(cal.get("kit_version", "")))
    current = _major_minor(kit_version)
    if calibrated_on is None:
        out.append(f"calibration.kit_version : version X.Y attendue, reçu {cal.get('kit_version')!r}")
    elif current is None:
        out.append(f"version du kit illisible : {kit_version!r}")
    elif calibrated_on[0] != current[0] or current[1] - calibrated_on[1] > MAX_CALIBRATION_MINOR_LAG:
        out.append(
            f"calibration périmée : faite sur {calibrated_on[0]}.{calibrated_on[1]}, kit en "
            f"{current[0]}.{current[1]} (au plus {MAX_CALIBRATION_MINOR_LAG} versions mineures d'écart) — "
            "rejouer le corpus, revoir les seuils, puis redater calibration"
        )
    elif current[1] < calibrated_on[1]:
        out.append(f"calibration.kit_version {cal.get('kit_version')} postérieure au kit {kit_version}")


def _check_indices(doc: Mapping[str, Any], out: list[str]) -> None:
    declared = doc.get("indices")
    if not isinstance(declared, Mapping):
        out.append("indices : table absente")
        return
    code = {i.name: i for i in INDICES}
    for name in sorted(set(declared) - set(code)):
        out.append(f"indices.{name} : inconnu du code")
    for name, index in code.items():
        entry = declared.get(name)
        if not isinstance(entry, Mapping):
            out.append(f"indices.{name} : absent du budget (le code l'emploie)")
            continue
        for key in ("value", "calibrated", "as_of"):
            if key not in entry:
                out.append(f"indices.{name}.{key} : absent")
        if "value" in entry and entry.get("value") != index.value:
            out.append(f"indices.{name}.value : budget {entry.get('value')!r}, code {index.value!r}")
        if "calibrated" in entry and entry.get("calibrated") is not index.calibrated:
            out.append(f"indices.{name}.calibrated : budget {entry.get('calibrated')!r}, code {index.calibrated!r}")
        if "as_of" in entry and str(entry.get("as_of")) != index.as_of:
            out.append(f"indices.{name}.as_of : budget {entry.get('as_of')!r}, code {index.as_of!r}")


def _check_routes(doc: Mapping[str, Any], out: list[str]) -> None:
    if len(ROUTES) > MAX_ROUTES:
        out.append(f"{len(ROUTES)} routes > {MAX_ROUTES}")
    budget = doc.get("routes")
    if not isinstance(budget, Mapping):
        out.append("routes : table absente")
        return
    for name in sorted(set(budget) - {r.name for r in ROUTES}):
        out.append(f"routes.{name} : route inconnue du code")
    for route in ROUTES:
        entry = budget.get(route.name)
        if not isinstance(entry, Mapping):
            out.append(f"routes.{route.name} : sans budget")
            continue
        max_steps, max_human = entry.get("max_steps"), entry.get("max_human_turns")
        if not isinstance(max_steps, int) or len(route.steps) > max_steps:
            out.append(f"routes.{route.name} : {len(route.steps)} étapes, budget {max_steps!r}")
        if not isinstance(max_human, int) or route.human_turns > max_human:
            out.append(f"routes.{route.name} : {route.human_turns} tours humains, budget {max_human!r}")
        if entry.get("test_depth") != route.test_depth.value:
            out.append(
                f"routes.{route.name}.test_depth : budget {entry.get('test_depth')!r}, code {route.test_depth.value!r}"
            )


def _route_of(task: Mapping[str, Any]) -> tuple[str, str]:
    risk = task.get("risk_profile")
    files = task.get("files")
    suggestion = suggest(inputs_for(
        title=str(task.get("title", "")),
        acceptance=tuple(str(a) for a in task.get("acceptance", []) or []),
        expected_evidence=tuple(str(a) for a in task.get("expected_evidence", []) or []),
        has_checks=bool(task.get("checks")),
        declared_risk=RiskProfile(risk) if risk else None,
        description=str(task.get("description", "")),
        files=None if files is None else [str(f) for f in files],
        test_runner=task.get("test_runner"),
        impact_files=task.get("impact_files"),
    ))
    return suggestion.route.name, suggestion.route.test_depth.value


def _check_corpus(doc: Mapping[str, Any], out: list[str]) -> None:
    corpus = doc.get("corpus")
    if not isinstance(corpus, list):
        out.append("corpus : liste absente")
        return
    if len(corpus) < MIN_CORPUS:
        out.append(f"corpus : {len(corpus)} tâches types, au moins {MIN_CORPUS} exigées")
    seen: set[str] = set()
    counts: dict[str, int] = {r.name: 0 for r in ROUTES}
    human = 0
    for position, task in enumerate(corpus, start=1):
        if not isinstance(task, Mapping) or not task.get("id"):
            out.append(f"corpus[{position}] : entrée sans id")
            continue
        tid = str(task["id"])
        if tid in seen:
            out.append(f"corpus.{tid} : id en double")
        seen.add(tid)
        expect = task.get("expect")
        if not isinstance(expect, Mapping):
            out.append(f"corpus.{tid} : expect absent")
            continue
        try:
            route, depth = _route_of(task)
        except (ValueError, TypeError) as exc:
            out.append(f"corpus.{tid} : entrée invalide — {exc}")
            continue
        if route != expect.get("route") or depth != expect.get("test_depth"):
            out.append(
                f"corpus.{tid} : route {route}/{depth}, figée {expect.get('route')}/{expect.get('test_depth')}"
            )
        counts[route] += 1
        human += next(r.human_turns for r in ROUTES if r.name == route)
    limits = doc.get("corpus_budget")
    if not isinstance(limits, Mapping):
        out.append("corpus_budget : table absente")
        return
    max_human = limits.get("max_human_turns")
    if not isinstance(max_human, int) or human > max_human:
        out.append(f"corpus_budget.max_human_turns : {human} tours humains sur le corpus, budget {max_human!r}")
    per_route = limits.get("max_per_route") or {}
    if not isinstance(per_route, Mapping):
        out.append("corpus_budget.max_per_route : table attendue")
        return
    for name, cap in sorted(per_route.items()):
        if name not in counts:
            out.append(f"corpus_budget.max_per_route.{name} : route inconnue")
        elif not isinstance(cap, int) or counts[name] > cap:
            out.append(f"corpus_budget.max_per_route.{name} : {counts[name]} tâches, budget {cap!r}")


def check_budget(doc: Any, *, kit_version: str) -> list[str]:
    """Les écarts entre le budget *doc* et le code ; ``[]`` si tout tient."""
    if not isinstance(doc, Mapping):
        return ["budget : table YAML attendue"]
    out: list[str] = []
    if doc.get("$schema") != BUDGET_SCHEMA:
        out.append(f"$schema : {BUDGET_SCHEMA!r} attendu, reçu {doc.get('$schema')!r}")
    _check_calibration(doc, kit_version, out)
    _check_indices(doc, out)
    _check_routes(doc, out)
    _check_corpus(doc, out)
    return out
