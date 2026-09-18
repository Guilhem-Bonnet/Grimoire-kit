"""Map detected stacks to archetype and agent selections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.scanner import ScanResult


@dataclass(frozen=True, slots=True)
class ResolvedArchetype:
    """Result of archetype resolution from a scan."""

    archetype: str
    stack_agents: tuple[str, ...]
    feature_agents: tuple[str, ...]
    reason: str
    # Multi-archetype support: ordered tuple of selected archetypes
    archetypes: tuple[str, ...] = ()
    # True when `archetype` is an automatic best-guess (no rule matched, no
    # explicit override) — decision 2026-09-18 (Guilhem): `resolve()` never
    # returns `minimal` on its own; this flags the pick as one the caller
    # should name, explain, and offer to change (`grimoire up -a <other>`),
    # as opposed to a confident rule match or an explicit user choice.
    is_best_guess: bool = False

    @property
    def is_composite(self) -> bool:
        """True if more than one archetype was selected."""
        return len(self.archetypes) > 1


# Stack name → expert agent filename (without .md)
STACK_AGENT_MAP: dict[str, str] = {
    # Depuis la refonte de l'archétype `stack` (#375), les experts par langage
    # sont des skills attachés à un seul généraliste. La détection d'une pile
    # livre donc ce généraliste — et le scaffold copie ses skills avec lui,
    # sinon son frontmatter `skills:` désignerait des fichiers absents et la
    # collecte refuserait la surface (fail-closed, voulu).
    "go": "stack-engineer",
    "python": "stack-engineer",
    "javascript": "stack-engineer",
    "typescript": "stack-engineer",
    "docker": "stack-engineer",
    "terraform": "stack-engineer",
    "kubernetes": "stack-engineer",
    "ansible": "stack-engineer",
}

# Known archetype IDs — keep in sync with cli/app.py _KNOWN_ARCHETYPES
_VALID_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
    "agentic-standard",
})

# Archetype selection rules — evaluated top to bottom, first match wins.
# Each rule: (required_stacks, archetype_name, human_reason)
_ARCHETYPE_RULES: list[tuple[frozenset[str], str, str]] = [
    (frozenset({"terraform"}), "infra-ops", "Terraform detected"),
    (frozenset({"kubernetes"}), "infra-ops", "Kubernetes detected"),
    (frozenset({"ansible"}), "infra-ops", "Ansible detected"),
    (frozenset({"react", "python"}), "web-app", "React + Python backend"),
    (frozenset({"react", "go"}), "web-app", "React + Go backend"),
    (frozenset({"vue", "python"}), "web-app", "Vue + Python backend"),
    (frozenset({"vue", "go"}), "web-app", "Vue + Go backend"),
    (frozenset({"react"}), "web-app", "React frontend"),
    (frozenset({"vue"}), "web-app", "Vue frontend"),
    (frozenset({"django"}), "web-app", "Django web framework"),
    (frozenset({"fastapi"}), "web-app", "FastAPI service"),
]

# ── Naked-stack recommendation ──────────────────────────────────────────────
#
# When no rule above matches, `resolve()` used to fall back to `minimal` with
# the opaque reason "No specific stack pattern matched" — indistinguishable
# from an empty repo, and identical for a naked Python backend, a naked Node
# script or a Rust binary. Onboarding audit 2026-09-18 named this the root
# cause of "-y"/"up" always landing on the poorest archetype without saying
# why. Decision 2026-09-18 (Guilhem) went further: `resolve()` never installs
# `minimal` automatically at all — `recommend_naked_stack()` now names the
# *actual* archetype applied (never just a displayed suggestion), and why.

_TEST_DIR_MARKERS: tuple[str, ...] = ("tests", "test", "spec", "__tests__")
_JS_TEST_RUNNERS: tuple[str, ...] = ("jest", "vitest", "mocha")


@dataclass(frozen=True, slots=True)
class StackRecommendation:
    """A named, reasoned recommendation for a stack `resolve()` could not specialize."""

    archetype: str
    reason: str
    propose_discovery: bool
    #: "python" | "node" | "rust" | "go" | "" (mixed or unrecognized stacks)
    language: str = ""


def _detect_test_evidence(root: Path) -> str:
    """Best-effort mention of a test directory — never fatal on a fake/missing root."""
    try:
        for name in _TEST_DIR_MARKERS:
            candidate = root / name
            if candidate.is_dir() and any(candidate.iterdir()):
                return f"with a {name}/ directory"
    except OSError:
        pass
    return ""


def _package_json_test_runner(root: Path) -> str | None:
    """Name the JS test runner declared in package.json, if any — never fatal."""
    try:
        raw = (root / "package.json").read_text(encoding="utf-8")
        data: Any = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(section)
    for runner in _JS_TEST_RUNNERS:
        if runner in deps:
            return runner
    return None


def recommend_naked_stack(scan: ScanResult) -> StackRecommendation:
    """Best-guess *specialized* archetype for a stack no rule matched confidently.

    Decision 2026-09-18 (Guilhem, product owner): ``resolve()`` never installs
    ``minimal`` automatically — a naked/ambiguous stack still gets a named,
    explained, specialized archetype; ``minimal`` is reachable only via an
    explicit ``-a minimal`` override (demo/test escape hatch). Two archetypes
    cover the fallback space: ``platform-engineering`` (backend/architecture,
    the only one of the 7 with no language or frontend assumption in its DNA)
    for anything without a confirmed frontend, and ``web-app`` for a Node/JS
    project (the kit's only JS/TS-oriented archetype). ``propose_discovery``
    stays ``True`` throughout: this is the automatic pick for the express
    path, not a replacement for asking in a real TTY.
    """
    detected = {d.name for d in scan.stacks}

    if not detected:
        return StackRecommendation(
            archetype="platform-engineering",
            reason=(
                "No stack marker detected (empty repo, or README only) — "
                "platform-engineering is the kit's most general specialized "
                "archetype; pick a better fit via guided discovery."
            ),
            propose_discovery=True,
        )

    if "python" in detected:
        evidence = _detect_test_evidence(scan.root)
        note = f", {evidence}" if evidence else ""
        return StackRecommendation(
            archetype="platform-engineering",
            reason=(
                f"Python project detected (pyproject.toml/setup.py/requirements.txt{note}) "
                "with no web framework (Django/FastAPI) or infra signal — "
                "platform-engineering is the closest general-purpose backend archetype."
            ),
            propose_discovery=True,
            language="python",
        )

    if "javascript" in detected or "typescript" in detected:
        runner = _package_json_test_runner(scan.root)
        note = f" ({runner})" if runner else ""
        return StackRecommendation(
            archetype="web-app",
            reason=(
                f"Node project detected (package.json{note}) with no confirmed frontend "
                "framework (React/Vue) — web-app is the kit's only JS/TS-oriented archetype."
            ),
            propose_discovery=True,
            language="node",
        )

    if "rust" in detected:
        return StackRecommendation(
            archetype="platform-engineering",
            reason=(
                "Rust project detected (Cargo.toml) — no archetype targets Rust "
                "specifically; platform-engineering is the closest general-purpose one."
            ),
            propose_discovery=True,
            language="rust",
        )

    if "go" in detected:
        return StackRecommendation(
            archetype="platform-engineering",
            reason=(
                "Go project detected (go.mod) — no archetype targets Go specifically; "
                "platform-engineering is the closest general-purpose one."
            ),
            propose_discovery=True,
            language="go",
        )

    return StackRecommendation(
        archetype="platform-engineering",
        reason=(
            f"Detected stacks ({', '.join(sorted(detected))}) match no specialized "
            "archetype directly; platform-engineering is the closest general-purpose one."
        ),
        propose_discovery=True,
    )


# ── Weak-signal suggestion ───────────────────────────────────────────────────
#
# Narrower than `recommend_naked_stack`: an "almost there" signal — a bundler
# already in package.json, a bare Dockerfile, CI wired to real tests — that
# points at one *specific* archetype, more confident than the generic
# language-based fallback above. `resolve()` checks this first, before
# `recommend_naked_stack`, when no `_ARCHETYPE_RULES` entry matched.

_CI_MARKERS: tuple[str, ...] = (
    ".github/workflows", ".gitlab-ci.yml", ".circleci/config.yml",
    "azure-pipelines.yml", ".travis.yml", "Jenkinsfile",
)
_JS_BUNDLERS: tuple[str, ...] = ("vite", "webpack")


def _package_json_bundler(root: Path) -> str | None:
    """Name the frontend bundler declared in package.json, if any — never fatal."""
    try:
        raw = (root / "package.json").read_text(encoding="utf-8")
        data: Any = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(section)
    for bundler in _JS_BUNDLERS:
        if bundler in deps:
            return bundler
    return None


def _has_ci_and_tests(root: Path) -> bool:
    try:
        has_ci = any((root / marker).exists() for marker in _CI_MARKERS)
        if not has_ci:
            return False
        return any(
            (root / name).is_dir() and any((root / name).iterdir())
            for name in _TEST_DIR_MARKERS
        )
    except OSError:
        return False


def weak_signal_suggestion(scan: ScanResult) -> tuple[str, str] | None:
    """A specific, named archetype hint from an almost-there signal, or ``None``.

    Checked in a fixed, most-specific-first order: a bundler already chosen
    beats a bare Dockerfile, which beats the more generic CI+tests signal.
    """
    detected = {d.name for d in scan.stacks}

    if "javascript" in detected or "typescript" in detected:
        bundler = _package_json_bundler(scan.root)
        if bundler:
            return ("web-app", f"frontend tooling detected ({bundler}) with no confirmed framework")

    if "docker" in detected:
        return ("infra-ops", "Dockerfile detected — ops/infra likely even without Terraform/K8s/Ansible")

    if _has_ci_and_tests(scan.root):
        return ("fix-loop", "CI + tests detected — a certified fix loop (TDD proofs) may fit")

    return None


class ArchetypeResolver:
    """Resolve a ScanResult into archetype + agent selections."""

    # Archetypes exposed for wizard display (exclude internal dirs)
    _USER_ARCHETYPES = ("minimal", "web-app", "infra-ops", "platform-engineering", "agentic-standard", "creative-studio", "fix-loop")

    def resolve(
        self,
        scan: ScanResult,
        *,
        backend: str = "local",
        archetype_override: str | None = None,
        archetypes_override: list[str] | None = None,
    ) -> ResolvedArchetype:
        detected = {d.name for d in scan.stacks}
        is_best_guess = False

        # Multi-archetype support
        if archetypes_override:
            invalid = [a for a in archetypes_override if a not in _VALID_ARCHETYPES]
            if invalid:
                msg = f"Unknown archetype(s): {', '.join(repr(a) for a in invalid)}"
                raise ValueError(msg)
            archetypes = tuple(archetypes_override)
            archetype = archetypes[0]  # primary for backward compat
            reason = f"User selected: {', '.join(archetypes)}"
        elif archetype_override:
            if archetype_override not in _VALID_ARCHETYPES:
                msg = f"Unknown archetype: {archetype_override!r}"
                raise ValueError(msg)
            archetype = archetype_override
            archetypes = (archetype_override,)
            reason = f"User selected: {archetype_override}"
        else:
            matched: tuple[str, str] | None = None
            for required, arch, desc in _ARCHETYPE_RULES:
                if required <= detected:
                    matched = (arch, desc)
                    break
            if matched:
                archetype, reason = matched
                archetypes = (archetype,)
            else:
                # No rule matched — decision 2026-09-18 (Guilhem): never a
                # silent (or automatic) `minimal`. Try the more confident
                # weak-signal match first (bundler/Dockerfile/CI+tests),
                # then the generic language-based fallback — both always
                # named and explained, never `minimal`.
                is_best_guess = True
                weak = weak_signal_suggestion(scan)
                if weak:
                    archetype, reason = weak
                else:
                    hint = recommend_naked_stack(scan)
                    archetype = hint.archetype
                    reason = hint.reason
                archetypes = (archetype,)

        # Stack agents — deduplicated
        stack_agents: list[str] = []
        seen: set[str] = set()
        for stack_name in sorted(detected):
            agent = STACK_AGENT_MAP.get(stack_name)
            if agent and agent not in seen:
                stack_agents.append(agent)
                seen.add(agent)

        # Feature agents
        feature_agents: list[str] = []
        if backend in ("qdrant-local", "qdrant-server", "ollama"):
            feature_agents.append("vectus")

        return ResolvedArchetype(
            archetype=archetype,
            stack_agents=tuple(stack_agents),
            feature_agents=tuple(feature_agents),
            reason=reason,
            archetypes=archetypes,
            is_best_guess=is_best_guess,
        )

    def suggest_archetypes(self, scan: ScanResult) -> list[str]:
        """Suggest archetypes based on scan results (for guided discovery)."""
        detected = {d.name for d in scan.stacks}
        suggestions: list[str] = []
        seen: set[str] = set()
        for required, arch, _desc in _ARCHETYPE_RULES:
            if required <= detected and arch not in seen:
                suggestions.append(arch)
                seen.add(arch)
        return suggestions or ["minimal"]
