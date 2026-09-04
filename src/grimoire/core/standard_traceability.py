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
from fnmatch import fnmatch
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


VERDICTS = ("ok", "warning", "error", "absent")
"""Verdict of ``standard verify`` on one artifact, worst check first."""


@dataclass(frozen=True, slots=True)
class TraceabilityMatrix:
    profile_id: str
    level: str
    upstream_commit: str
    artifacts: tuple[ArtifactTrace, ...]
    gaps: tuple[dict[str, str], ...] = field(default_factory=tuple)
    verdicts: dict[str, str] = field(default_factory=dict)
    """Per required artifact, once joined with a project: see :func:`with_verdicts`."""

    @property
    def covered_requirements(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for trace in self.artifacts:
            if trace.required:
                for req in trace.requirements:
                    seen.setdefault(req, None)
        return tuple(seen)

    @property
    def verified_requirements(self) -> tuple[str, ...]:
        """Requirements whose artifact exists and verifies without error in the joined project."""
        seen: dict[str, None] = {}
        for trace in self.artifacts:
            if trace.required and self.verdicts.get(trace.artifact_type) in {"ok", "warning"}:
                for req in trace.requirements:
                    seen.setdefault(req, None)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "profile": self.profile_id,
            "level": self.level,
            "upstream_commit": self.upstream_commit,
            "covered_requirements": list(self.covered_requirements),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "gaps": list(self.gaps),
        }
        if self.verdicts:
            data["verdicts"] = dict(self.verdicts)
            data["verified_requirements"] = list(self.verified_requirements)
        return data


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


def _artifact_of(path: Path, targets: dict[str, str]) -> str | None:
    """The artifact type whose generation target *path* instantiates, if any."""
    text = path.as_posix()
    for artifact_type, target in targets.items():
        if fnmatch(text, target.replace("{task-id}", "*")):
            return artifact_type
    return None


def with_verdicts(matrix: TraceabilityMatrix, project_root: Path, *, task_id: str = "bootstrap") -> TraceabilityMatrix:
    """Join the matrix with what ``standard verify`` says of *project_root*.

    AG-AUD-001 asks that declared conformance tie requirement, control,
    evidence **and verdict**. The first three are data; the verdict is what
    the verifiers say of each required artifact, worst check first: ``absent``
    when the file is missing, else ``error``, ``warning`` or ``ok``.
    """
    from grimoire.core.agentic_standard import _generation_targets, verify_standard_profile

    result = verify_standard_profile(project_root, profile_id=matrix.profile_id, task_id=task_id)
    targets = _generation_targets()
    rank = {v: i for i, v in enumerate(VERDICTS)}
    verdicts = {trace.artifact_type: "ok" for trace in matrix.artifacts if trace.required}

    def worsen(artifact_type: str | None, verdict: str) -> None:
        if artifact_type in verdicts and rank[verdict] > rank[verdicts[artifact_type]]:
            verdicts[artifact_type] = verdict

    for missing in result.missing:
        worsen(_artifact_of(Path(missing), targets), "absent")
    for check in result.checks:
        if check.path is not None and check.severity in {"error", "warning"}:
            worsen(_artifact_of(Path(check.path), targets), check.severity)
    return TraceabilityMatrix(
        profile_id=matrix.profile_id,
        level=matrix.level,
        upstream_commit=matrix.upstream_commit,
        artifacts=matrix.artifacts,
        gaps=matrix.gaps,
        verdicts=verdicts,
    )
