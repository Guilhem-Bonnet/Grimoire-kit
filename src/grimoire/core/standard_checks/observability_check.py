"""Vérificateur du pattern ``governed-observability-cockpit`` (issue #636).

``observability-policy.yaml`` était généré (profils ``governed`` et
``production``) mais jamais lu : aucun ``_verify_*`` ne l'ouvrait, et le
gabarit annonçait trois ``checks`` qu'aucun code n'émettait. Trois contrôles,
sur ce que l'artefact déclare réellement :

- ``observability.policy_missing`` : l'artefact manque alors que le profil
  exige le pattern (``governed`` et au-delà) ;
- ``observability.inputs_undeclared`` : ``declared_inputs`` vide, une entrée
  sans ``id``/``path``/``owner``/``trust_level``/``freshness``, ou une vue qui
  cite une entrée absente — la règle ``observability.inputs-are-declared`` ;
- ``observability.secret_export_allowed`` : ``redaction`` n'interdit pas
  ``secrets`` et ``credentials``, ou n'exige pas le contrôle de caviardage à
  l'export — la règle ``observability.no-secret-export``.

``observability.cockpit-read-only`` reste une règle de code (le cockpit ne
possède aucune route d'écriture sur les artefacts du standard) : rien dans
l'artefact ne permet de la vérifier, elle n'a donc pas de check ici.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import (
    StandardProfile,
    StandardVerificationResult,
    _add_check,
    _load_yaml_file,
)
from grimoire.core.standard_generation import STANDARD_DIR

__all__ = ["_verify_observability_policy"]

POLICY_PATH = STANDARD_DIR / "observability-policy.yaml"
_INPUT_KEYS = ("id", "path", "owner", "trust_level", "freshness")
_REQUIRED_FORBIDDEN_CLASSES = frozenset({"secrets", "credentials"})


def _ids(items: Any) -> set[str]:
    return {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")} if isinstance(items, list) else set()


def _verify_inputs(data: dict[str, Any], result: StandardVerificationResult) -> None:
    inputs = data.get("declared_inputs")
    if not isinstance(inputs, list) or not inputs:
        _add_check(
            result,
            "observability.inputs_undeclared",
            "error",
            "Observability policy declares no cockpit input: nothing says what the cockpit reads.",
            path=POLICY_PATH,
        )
        return
    incomplete = [
        str(entry.get("id") or f"#{index}") if isinstance(entry, dict) else f"#{index}"
        for index, entry in enumerate(inputs, start=1)
        if not isinstance(entry, dict) or any(not entry.get(key) for key in _INPUT_KEYS)
    ]
    if incomplete:
        _add_check(
            result,
            "observability.inputs_undeclared",
            "error",
            f"Cockpit input(s) without id, path, owner, trust_level and freshness: {', '.join(incomplete)}.",
            path=POLICY_PATH,
        )
    declared = _ids(inputs)
    views = data.get("views")
    unknown: list[str] = []
    for view in views if isinstance(views, list) else []:
        if not isinstance(view, dict):
            continue
        refs = view.get("inputs")
        for ref in refs if isinstance(refs, list) else []:
            if str(ref) not in declared:
                unknown.append(f"{view.get('id')}:{ref}")
    if unknown:
        _add_check(
            result,
            "observability.inputs_undeclared",
            "error",
            f"Cockpit view(s) read an input that is not declared: {', '.join(unknown)}.",
            path=POLICY_PATH,
        )


def _verify_redaction(data: dict[str, Any], result: StandardVerificationResult) -> None:
    redaction = data.get("redaction")
    if not isinstance(redaction, dict):
        _add_check(
            result,
            "observability.secret_export_allowed",
            "error",
            "Observability policy has no redaction block: cockpit exports may carry secrets.",
            path=POLICY_PATH,
        )
        return
    classes = redaction.get("forbidden_data_classes")
    forbidden = {str(item) for item in classes} if isinstance(classes, list) else set()
    missing = sorted(_REQUIRED_FORBIDDEN_CLASSES - forbidden)
    if missing:
        _add_check(
            result,
            "observability.secret_export_allowed",
            "error",
            f"Cockpit exports do not forbid data class(es): {', '.join(missing)}.",
            path=POLICY_PATH,
        )
    if redaction.get("export_requires_redaction_check") is not True:
        _add_check(
            result,
            "observability.secret_export_allowed",
            "error",
            "Cockpit exports are not gated by a redaction check (export_requires_redaction_check must be true).",
            path=POLICY_PATH,
        )


def _verify_observability_policy(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    if not (root / POLICY_PATH).is_file():
        if profile.id in {"governed", "production"}:
            _add_check(
                result,
                "observability.policy_missing",
                "error",
                f"Profile {profile.id!r} requires governed-observability-cockpit but {POLICY_PATH} is absent.",
                path=POLICY_PATH,
            )
        return
    data = _load_yaml_file(root, POLICY_PATH, result)
    if not isinstance(data, dict):
        return
    _verify_inputs(data, result)
    _verify_redaction(data, result)
