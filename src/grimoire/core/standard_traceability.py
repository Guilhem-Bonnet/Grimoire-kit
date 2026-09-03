"""The bridge's traceability to the normative corpus, as data.

``traceability.yaml`` states, for each artifact type and each verifier
family, which requirements (``AG-*``) and controls (``CTRL-*``) of the
upstream standard it satisfies — with the citation that justifies the link —
and which requirements each conformance level leaves uncovered. It is the
matrix AG-AUD-001 asks for: declared conformance tied to requirement, control
and evidence, rather than asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from grimoire.core.agentic_standard import get_profile, load_profile_map
from grimoire.data import framework_path

TRACEABILITY_PATH = Path("agentic-standard/traceability.yaml")
REQUIREMENT_ID = re.compile(r"^AG-[A-Z]{3}-\d{3}$")
CONTROL_ID = re.compile(r"^CTRL-[A-Z]{2,3}-\d{3}$")
LEVELS = ("N1", "N2", "N3", "N4", "N5")


@lru_cache(maxsize=1)
def load_traceability() -> dict[str, Any]:
    from ruamel.yaml import YAML

    path = framework_path() / TRACEABILITY_PATH
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "artifacts" not in data:
        msg = "traceability.yaml must define an artifacts mapping."
        raise ValueError(msg)
    return data


@dataclass(frozen=True, slots=True)
class ArtifactTrace:
    artifact_type: str
    required: bool
    requirements: tuple[str, ...]
    controls: tuple[str, ...]
    evidence: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "required": self.required,
            "requirements": list(self.requirements),
            "controls": list(self.controls),
            "evidence": self.evidence,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class TraceabilityMatrix:
    profile_id: str
    level: str
    upstream_commit: str
    artifacts: tuple[ArtifactTrace, ...]
    gaps: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def covered_requirements(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for trace in self.artifacts:
            if trace.required:
                for req in trace.requirements:
                    seen.setdefault(req, None)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile_id,
            "level": self.level,
            "upstream_commit": self.upstream_commit,
            "covered_requirements": list(self.covered_requirements),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "gaps": list(self.gaps),
        }


def level_for(profile_id: str) -> str:
    levels = load_traceability().get("levels", {})
    return str(levels.get(profile_id, ""))


def matrix_for(profile_id: str) -> TraceabilityMatrix:
    """Requirements and controls the artifacts of *profile_id* satisfy, and what its level still leaves open."""
    data = load_traceability()
    profile = get_profile(profile_id)
    required = set(profile.required_artifacts)
    traces = tuple(
        ArtifactTrace(
            artifact_type=name,
            required=name in required,
            requirements=tuple(entry.get("requirements") or ()),
            controls=tuple(entry.get("controls") or ()),
            evidence=str(entry.get("evidence") or entry.get("reason") or ""),
            note=str(entry.get("note") or ""),
        )
        for name, entry in data["artifacts"].items()
    )
    level = level_for(profile_id)
    cumulative: list[dict[str, str]] = []
    for lvl in LEVELS:
        cumulative.extend(dict(g) for g in data.get("gaps", {}).get(lvl, []) or [])
        if lvl == level:
            break
    return TraceabilityMatrix(
        profile_id=profile_id,
        level=level,
        upstream_commit=str(data.get("metadata", {}).get("upstream_commit", "")),
        artifacts=traces,
        gaps=tuple(cumulative),
    )


def declared_artifact_types() -> set[str]:
    return set(load_profile_map().get("artifact_types", {}))
