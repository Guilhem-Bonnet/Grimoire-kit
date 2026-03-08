"""Automatic tech stack detection by file markers.

Scans project files and directories to detect which languages,
frameworks, and infrastructure tools are in use.

Usage::

    scanner = StackScanner(Path("."))
    result = scanner.scan()
    for det in result.stacks:
        print(f"{det.name}: {det.confidence:.0%} ({', '.join(det.evidence)})")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StackDetection:
    """A detected technology with confidence score and evidence."""

    name: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Complete scan output."""

    stacks: tuple[StackDetection, ...]
    project_type: str
    root: Path


# ── Marker definitions ────────────────────────────────────────────────────────

# Each entry: (stack_name, [(marker_glob, weight), ...])
# Weights: sum ≥ 1.0 = high confidence, 0.5-0.9 = medium.
_FILE_MARKERS: list[tuple[str, list[tuple[str, float]]]] = [
    ("python", [
        ("pyproject.toml", 0.6),
        ("setup.py", 0.5),
        ("requirements.txt", 0.5),
        ("Pipfile", 0.4),
        ("poetry.lock", 0.4),
        (".python-version", 0.3),
    ]),
    ("javascript", [
        ("package.json", 0.6),
        (".npmrc", 0.3),
        ("yarn.lock", 0.3),
        ("pnpm-lock.yaml", 0.3),
    ]),
    ("typescript", [
        ("tsconfig.json", 0.8),
        ("tsconfig.*.json", 0.3),
    ]),
    ("go", [
        ("go.mod", 0.9),
        ("go.sum", 0.3),
    ]),
    ("rust", [
        ("Cargo.toml", 0.9),
        ("Cargo.lock", 0.3),
    ]),
    ("java", [
        ("pom.xml", 0.8),
        ("build.gradle", 0.8),
        ("build.gradle.kts", 0.8),
        ("gradlew", 0.3),
    ]),
    ("ruby", [
        ("Gemfile", 0.8),
        ("Gemfile.lock", 0.3),
        (".ruby-version", 0.3),
    ]),
    ("csharp", [
        ("*.csproj", 0.8),
        ("*.sln", 0.5),
        ("global.json", 0.3),
    ]),
    ("docker", [
        ("Dockerfile", 0.7),
        ("docker-compose.yml", 0.5),
        ("docker-compose.yaml", 0.5),
        (".dockerignore", 0.3),
    ]),
    ("terraform", [
        ("*.tf", 0.8),
        ("*.tfvars", 0.4),
        (".terraform.lock.hcl", 0.4),
    ]),
    ("kubernetes", [
        ("k8s/", 0.7),
        ("kubernetes/", 0.7),
        ("kustomization.yaml", 0.7),
        ("helmfile.yaml", 0.5),
    ]),
    ("ansible", [
        ("ansible.cfg", 0.8),
        ("playbook.yml", 0.6),
        ("playbook.yaml", 0.6),
        ("inventory/", 0.4),
    ]),
    ("react", [
        ("src/App.tsx", 0.8),
        ("src/App.jsx", 0.8),
        ("src/index.tsx", 0.5),
        ("src/index.jsx", 0.5),
    ]),
    ("vue", [
        ("vue.config.js", 0.8),
        ("nuxt.config.ts", 0.8),
        ("src/App.vue", 0.7),
    ]),
    ("django", [
        ("manage.py", 0.6),
        ("*/wsgi.py", 0.5),
        ("*/asgi.py", 0.5),
    ]),
    ("fastapi", [
        ("app/main.py", 0.5),
        ("src/main.py", 0.4),
    ]),
]

# Project type inference rules: stack combination → type
_TYPE_RULES: list[tuple[frozenset[str], str]] = [
    (frozenset({"terraform"}), "infrastructure"),
    (frozenset({"kubernetes"}), "infrastructure"),
    (frozenset({"ansible"}), "infrastructure"),
    (frozenset({"react"}), "webapp"),
    (frozenset({"vue"}), "webapp"),
    (frozenset({"django"}), "webapp"),
    (frozenset({"fastapi"}), "api"),
    (frozenset({"docker", "python"}), "service"),
    (frozenset({"docker", "go"}), "service"),
]


# ── Scanner ───────────────────────────────────────────────────────────────────


class StackScanner:
    """Scan a project directory to detect its tech stack."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def scan(self) -> ScanResult:
        """Analyse the project directory and return detected stacks."""
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}

        for stack_name, markers in _FILE_MARKERS:
            for pattern, weight in markers:
                if self._matches(pattern):
                    scores[stack_name] = scores.get(stack_name, 0.0) + weight
                    evidence.setdefault(stack_name, []).append(pattern)

        # Build detections, capping confidence at 1.0
        detections: list[StackDetection] = []
        for name, raw_score in sorted(scores.items(), key=lambda x: -x[1]):
            conf = min(raw_score, 1.0)
            if conf >= 0.3:  # threshold
                detections.append(StackDetection(
                    name=name,
                    confidence=conf,
                    evidence=tuple(evidence.get(name, [])),
                ))

        project_type = self._infer_type({d.name for d in detections})

        return ScanResult(
            stacks=tuple(detections),
            project_type=project_type,
            root=self._root,
        )

    # ── Private helpers ───────────────────────────────────────────────

    def _matches(self, pattern: str) -> bool:
        """Check if a marker pattern is present in the project root."""
        # Directory marker (ends with /)
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            return (self._root / dir_name).is_dir()

        # Glob with wildcard → use Path.glob (non-recursive by default)
        if "*" in pattern or "?" in pattern:
            # Pattern may contain path separators
            return any(True for _ in self._root.glob(pattern))

        # Exact file
        return (self._root / pattern).exists()

    @staticmethod
    def _infer_type(detected: set[str]) -> str:
        """Infer the project type from detected stacks."""
        for required, ptype in _TYPE_RULES:
            if required.issubset(detected):
                return ptype
        return "generic"
