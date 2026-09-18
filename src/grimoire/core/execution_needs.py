"""Résolution des besoins d'exécution d'un flow (issue #205, lot 2).

Un flow ne déclare pas une commande concrète (``pytest -q``) mais un besoin
(``test-runner``) : à qui appartient la commande réelle dépend du projet qui
exécute le flow, pas du flow lui-même. Ce module résout un besoin en trois
temps, jamais un quatrième :

1. **Déclaration explicite** — ``needs.commands`` dans ``project-context.yaml``
   (:class:`grimoire.core.config.NeedsConfig`). La seule source qui l'emporte
   toujours sur la détection : un projet qui déclare ``test-runner: "tox -e py312"``
   n'a pas à démentir sa propre détection par marqueur.
2. **Détection par les marqueurs du projet** — un fichier de manifeste connu
   (``pyproject.toml``, ``package.json``, ``Cargo.toml``, ``go.mod``) implique
   une commande par défaut, pour certains besoins seulement (aucune commande
   universelle n'existe pour ``migration-tool`` ou ``format`` : ces deux
   besoins ne sont jamais détectés, seulement déclarés). Les marqueurs sont
   vérifiés dans l'ordre fixe de :data:`_MARKER_DEFAULTS` ; le premier trouvé
   qui couvre un besoin donné l'emporte — un projet Python+Node ne doit pas
   voir sa commande de test dépendre de l'ordre d'énumération du système de
   fichiers.
3. **Repli, ``test-runner`` seul** — issue #582 lot G3. Le banc à trois bras
   (``_scratch/bench-f/analyse-tours-kit-gov.md``, 21 runs) a mesuré que
   15/21 tâches (tous les exercices Python) ne portent aucun des quatre
   marqueurs ci-dessus (un exercice Exercism livre un seul fichier ``.py`` de
   stub, ni ``pyproject.toml`` ni ``setup.py``) : ``need.resolved`` restait
   structurellement faux, et ``grimoire standard gate run-tests`` ne pouvait
   qu'échouer tôt (``acceptance.no_test_command_detected``), quoi que fasse
   l'agent. :data:`_FALLBACK_TEST_RUNNER_MARKERS` couvre des écosystèmes que
   les quatre marqueurs historiques ne voient pas ; il n'est consulté que
   pour ``test-runner`` et seulement si ni la déclaration ni
   :data:`_MARKER_DEFAULTS` n'ont déjà résolu ce besoin — même règle du
   premier marqueur trouvé qui l'emporte, dans l'ordre fixe documenté sur la
   constante : un projet à deux langages n'invente rien, il retombe sur le
   premier de la liste.

Un besoin ni déclaré ni détecté est ``unresolved`` — jamais une commande
inventée à partir du seul id du besoin. Sans lien avec le ``needs`` de
gouvernance de ``framework/agentic-standard/needs-catalog.yaml`` (profils et
patterns à installer, résolu par
:mod:`grimoire.core.needs_suggest`/:mod:`grimoire.core.archetype_resolver`) :
même mot, deux concepts distincts — celui-ci résout une commande d'exécution,
l'autre une gouvernance à installer.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError

__all__ = [
    "EXECUTION_NEED_IDS",
    "KNOWN_MARKERS",
    "ResolvedNeed",
    "resolve_execution_needs",
    "resolve_need",
]

#: Le catalogue de besoins d'exécution (issue #205). Fixe et petit à dessein :
#: chaque id doit avoir un sens indépendant du langage du projet.
EXECUTION_NEED_IDS: tuple[str, ...] = (
    "test-runner",
    "lint",
    "typecheck",
    "build",
    "migration-tool",
    "format",
)

#: marqueur -> {besoin: commande}, vérifiés dans cet ordre fixe. Un besoin
#: absent d'une entrée n'a pas de défaut détecté pour ce marqueur (ex. :
#: ``go.mod`` ne détecte que ``test-runner`` — aucune commande de lint/build
#: Go n'est assez consensuelle pour être un défaut silencieux).
_MARKER_DEFAULTS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "pyproject.toml",
        {"test-runner": "pytest -q", "lint": "ruff check .", "typecheck": "mypy ."},
    ),
    (
        "package.json",
        {"test-runner": "npm test", "lint": "npm run lint", "build": "npm run build"},
    ),
    (
        "Cargo.toml",
        {"test-runner": "cargo test", "lint": "cargo clippy", "build": "cargo build"},
    ),
    ("go.mod", {"test-runner": "go test ./..."}),
)

#: Les marqueurs reconnus, dans l'ordre — cité tel quel dans les messages de
#: refus nommé (``blueprint_loader``) pour dire à l'auteur du projet quoi
#: ajouter s'il veut la détection plutôt qu'une déclaration explicite.
KNOWN_MARKERS: tuple[str, ...] = tuple(marker for marker, _ in _MARKER_DEFAULTS)


def _has_file(name: str) -> Callable[[Path], bool]:
    return lambda root: (root / name).is_file()


def _has_any_glob(*patterns: str) -> Callable[[Path], bool]:
    return lambda root: any(any(root.glob(pattern)) for pattern in patterns)


def _text_contains(name: str, needle: str) -> Callable[[Path], bool]:
    def _predicate(root: Path) -> bool:
        path = root / name
        if not path.is_file():
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return needle in text

    return _predicate


def _makefile_has_test_target(root: Path) -> bool:
    """Une cible ``test:`` réelle, pas seulement le mot « test » cité en commentaire ou en dépendance."""
    path = root / "Makefile"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return re.search(r"^test\s*:", text, re.MULTILINE) is not None


def _has_rspec_project(root: Path) -> bool:
    return (root / "Gemfile").is_file() and (root / "spec").is_dir()


def _gradle_command(root: Path) -> str:
    return "./gradlew test" if (root / "gradlew").is_file() else "gradle test"


#: Repli, ``test-runner`` seul (issue #582 lot G3) : (id pour l'évidence,
#: prédicat sur la racine du projet, commande). Vérifiés dans cet ordre fixe
#: après :data:`_MARKER_DEFAULTS`, le premier qui matche l'emporte — un projet
#: à deux langages (ex. ``pom.xml`` et ``Gemfile``+``spec/``) ne doit pas voir
#: sa commande dépendre de l'énumération du système de fichiers. Chaque
#: prédicat est une fonction pure sur la racine, jamais un accès réseau ni une
#: exécution : la résolution reste un simple ``stat``/``read_text`` par
#: candidat.
_FALLBACK_TEST_RUNNER_MARKERS: tuple[tuple[str, Callable[[Path], bool], Callable[[Path], str]], ...] = (
    ("pytest.ini", _has_file("pytest.ini"), lambda root: "python -m pytest -q"),
    ("setup.cfg[tool:pytest]", _text_contains("setup.cfg", "[tool:pytest]"), lambda root: "python -m pytest -q"),
    ("tox.ini", _has_file("tox.ini"), lambda root: "python -m pytest -q"),
    ("tests/", lambda root: (root / "tests").is_dir(), lambda root: "python -m pytest -q"),
    ("test_*.py|*_test.py", _has_any_glob("test_*.py", "*_test.py"), lambda root: "python -m pytest -q"),
    ("pom.xml", _has_file("pom.xml"), lambda root: "mvn -q test"),
    (
        "build.gradle|build.gradle.kts",
        lambda root: (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file(),
        _gradle_command,
    ),
    # ``ctest`` suppose un répertoire de build déjà configuré (``cmake -B build``) ;
    # la détection ne le vérifie pas, elle ne fait que reconnaître le projet CMake.
    ("CMakeLists.txt", _has_file("CMakeLists.txt"), lambda root: "ctest --test-dir build"),
    ("Makefile[test]", _makefile_has_test_target, lambda root: "make test"),
    ("mix.exs", _has_file("mix.exs"), lambda root: "mix test"),
    ("Gemfile+spec/", _has_rspec_project, lambda root: "bundle exec rspec"),
    ("*.csproj|*.sln", _has_any_glob("*.csproj", "*.sln"), lambda root: "dotnet test"),
    ("Package.swift", _has_file("Package.swift"), lambda root: "swift test"),
)


def _detect_test_runner_fallback(project_root: Path) -> tuple[str, str] | None:
    """(commande, id de marqueur) du premier repli qui matche, ou ``None``."""
    for marker_id, predicate, command in _FALLBACK_TEST_RUNNER_MARKERS:
        if predicate(project_root):
            return command(project_root), marker_id
    return None


@dataclass(frozen=True, slots=True)
class ResolvedNeed:
    """Le verdict de résolution d'un besoin, avec sa source — jamais silencieux."""

    need_id: str
    command: str | None
    #: ``"declared"`` (project-context.yaml), ``"detected"`` (marqueur de projet),
    #: ou ``"unresolved"`` (ni l'un ni l'autre — ``command`` vaut alors ``None``).
    source: str
    #: Le marqueur qui a détecté la commande, ou ``"project-context.yaml"`` si
    #: déclaré, ou ``""`` si non résolu.
    evidence: str

    @property
    def resolved(self) -> bool:
        return self.command is not None


def _load_config_best_effort(project_root: Path) -> GrimoireConfig | None:
    """``project-context.yaml`` du projet, ou ``None`` — jamais une exception ici.

    La résolution de besoins doit fonctionner même sans config valide (un
    projet tout juste scaffoldé, ou dont le fichier a une erreur sans rapport
    avec ``needs``) : dans ce cas, seule la détection par marqueurs s'applique.
    """
    candidate = project_root / "project-context.yaml"
    if not candidate.is_file():
        return None
    try:
        return GrimoireConfig.from_yaml(candidate)
    except GrimoireConfigError:
        return None


def _detect_defaults(project_root: Path) -> dict[str, tuple[str, str]]:
    """besoin -> (commande, marqueur) pour tout marqueur présent au projet."""
    found: dict[str, tuple[str, str]] = {}
    for marker, defaults in _MARKER_DEFAULTS:
        if not (project_root / marker).is_file():
            continue
        for need_id, command in defaults.items():
            found.setdefault(need_id, (command, marker))
    return found


def resolve_execution_needs(project_root: Path) -> dict[str, ResolvedNeed]:
    """Résout le catalogue entier de besoins pour *project_root*.

    Utilisé par ``grimoire needs resolve`` (vue humaine sur toute la
    résolution) ; :func:`resolve_need` est le point d'entrée pour un seul
    besoin (celui que le chargement d'un blueprint utilise).
    """
    cfg = _load_config_best_effort(project_root)
    declared = dict(cfg.needs.commands) if cfg is not None else {}
    detected = _detect_defaults(project_root)
    resolved: dict[str, ResolvedNeed] = {}
    for need_id in EXECUTION_NEED_IDS:
        if need_id in declared:
            resolved[need_id] = ResolvedNeed(need_id, declared[need_id], "declared", "project-context.yaml")
        elif need_id in detected:
            command, marker = detected[need_id]
            resolved[need_id] = ResolvedNeed(need_id, command, "detected", marker)
        elif need_id == "test-runner":
            # Lot G3 : repli sur des marqueurs moins consensuels, uniquement
            # pour ce besoin et uniquement quand rien de plus fort n'a déjà
            # tranché — voir :data:`_FALLBACK_TEST_RUNNER_MARKERS`.
            fallback = _detect_test_runner_fallback(project_root)
            if fallback is not None:
                command, marker = fallback
                resolved[need_id] = ResolvedNeed(need_id, command, "detected", marker)
            else:
                resolved[need_id] = ResolvedNeed(need_id, None, "unresolved", "")
        else:
            resolved[need_id] = ResolvedNeed(need_id, None, "unresolved", "")
    return resolved


def resolve_need(need_id: str, project_root: Path) -> ResolvedNeed:
    """Un seul besoin. ``need_id`` hors catalogue : rendu ``unresolved`` tel quel

    — c'est à l'appelant (``blueprint_loader``) de distinguer « id inconnu du
    catalogue » de « connu mais non résolvable pour ce projet » dans son
    message de refus, ce module ne connaît pas le contexte du blueprint.
    """
    return resolve_execution_needs(project_root).get(need_id, ResolvedNeed(need_id, None, "unresolved", ""))
