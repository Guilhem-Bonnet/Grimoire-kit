"""Manifestes de la racine : dépendances déclarées, points d'entrée, commandes, couverture.

Chaque entrée cite sa ligne dans le manifeste. Une commande n'est dite
*observée* que si le projet la porte (script ``package.json``, cible
``Makefile``, configuration d'outil dans ``pyproject.toml``) ; une commande
qui ne vient que du défaut d'un marqueur (``pyproject.toml`` → ``pytest -q``,
:mod:`grimoire.core.execution_needs`) est dite *supposée*, et une commande
introuvable reste *inconnue* — jamais inventée.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.execution_needs import resolve_execution_needs
from grimoire.onboarding.facts import Fact

__all__ = ["COMMAND_NEEDS", "LOW_COVERAGE", "ManifestScan", "scan_manifests"]

#: Besoins d'exécution documentés (sous-ensemble de ``EXECUTION_NEED_IDS``).
COMMAND_NEEDS: tuple[str, ...] = ("test-runner", "lint", "typecheck", "build")
#: Sous ce taux de lignes couvertes (``coverage.xml``), un fichier est une zone à risque.
LOW_COVERAGE = 0.5
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_NPM_SCRIPT_NEED = {"test": "test-runner", "lint": "lint", "typecheck": "typecheck", "build": "build"}
_MAKE_NEED = {"test": "test-runner", "lint": "lint", "typecheck": "typecheck", "build": "build"}
_PY_TOOL_NEED = (("[tool.pytest.ini_options]", "test-runner", "pytest"), ("[tool.ruff", "lint", "ruff check ."),
                 ("[tool.mypy]", "typecheck", "mypy ."))


@dataclass
class ManifestScan:
    facts: list[Fact] = field(default_factory=list)
    #: besoin -> (statut, commande, source) ; statut : declared | observed | assumed | unknown
    commands: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    coverage_measured: bool = False
    has_entry_points: bool = False
    #: Scripts de ``package.json`` (``None`` : pas de ``package.json`` lisible).
    npm_scripts: set[str] | None = None


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _find(lines: list[str], test: Callable[[str], bool], start: int = 0) -> int:
    """Numéro (1-based) de la première ligne qui satisfait *test* à partir de *start*, sinon 0."""
    for index in range(start, len(lines)):
        if test(lines[index]):
            return index + 1
    return 0


def _find_any(lines: list[str], needles: tuple[str, ...], start: int = 0) -> int:
    """Première ligne (1-based) qui contient l'une des *needles*, sinon 0."""
    return _find(lines, lambda ln: any(n in ln for n in needles), start)


def _find_prefix(lines: list[str], prefixes: tuple[str, ...], start: int = 0) -> int:
    """Première ligne (1-based) qui commence (blancs ôtés) par l'un des *prefixes*, sinon 0."""
    return _find(lines, lambda ln: ln.strip().startswith(prefixes), start)


def _section_line(lines: list[str], header: str) -> int:
    return _find(lines, lambda ln: ln.strip() == header)


def _pyproject(root: Path, scan: ManifestScan, observed: dict[str, list[tuple[str, str]]]) -> None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return
    lines = _lines(path)
    try:
        data = tomllib.loads("\n".join(lines))
    except tomllib.TOMLDecodeError:
        scan.facts.append(Fact(id="manifest:pyproject-unreadable", section="dependencies",
                               text="`pyproject.toml` illisible (TOML invalide) : rien n'en est déduit",
                               sources=("pyproject.toml",)))
        return
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    deps_start = _find(lines, lambda ln: ln.strip().startswith("dependencies"))
    for dep in project.get("dependencies", []) or []:
        line = _find_any(lines, (f'"{dep}"', f"'{dep}'"), max(deps_start - 1, 0))
        scan.facts.append(Fact(id=f"declared:pyproject:{dep}", section="dependencies",
                               text=f"`{dep}` déclarée (pyproject.toml)", sources=(f"pyproject.toml:{line or 1}",)))
    for table in ("scripts", "gui-scripts"):
        header = _section_line(lines, f"[project.{table}]")
        for name, target in sorted((project.get(table) or {}).items()):
            line = _find_prefix(lines, (f"{name} ", f"{name}=", f'"{name}"'), header)
            scan.has_entry_points = True
            scan.facts.append(Fact(id=f"entry:pyproject:{name}", section="entry_points",
                                   text=f"commande `{name}` → `{target}`", sources=(f"pyproject.toml:{line or header or 1}",),
                                   data={"kind": "console_script", "target": str(target)}))
    for tool_header, need, command in _PY_TOOL_NEED:
        line = _find_prefix(lines, (tool_header,))
        if line:
            observed.setdefault(need, []).append((command, f"pyproject.toml:{line}"))


def _requirements(root: Path, scan: ManifestScan) -> None:
    for name in ("requirements.txt", "requirements-dev.txt"):
        path = root / name
        if not path.is_file():
            continue
        for number, raw in enumerate(_lines(path), start=1):
            text = raw.split("#", 1)[0].strip()
            match = _REQ_NAME.match(text)
            if text and not text.startswith("-") and match:
                scan.facts.append(Fact(id=f"declared:{name}:{match.group(1)}", section="dependencies",
                                       text=f"`{text}` déclarée ({name})", sources=(f"{name}:{number}",)))


def _key_line(lines: list[str], key: str, start: int = 0) -> int:
    return _find(lines, lambda ln: ln.strip().startswith(f'"{key}"'), start)


def _package_json(root: Path, scan: ManifestScan, observed: dict[str, list[tuple[str, str]]]) -> None:
    path = root / "package.json"
    if not path.is_file():
        return
    lines = _lines(path)
    try:
        data: Any = json.loads("\n".join(lines))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        scan.facts.append(Fact(id="manifest:package-json-unreadable", section="dependencies",
                               text="`package.json` illisible : rien n'en est déduit", sources=("package.json",)))
        return
    for table in ("dependencies", "devDependencies"):
        start = _key_line(lines, table)
        for name, version in sorted((data.get(table) or {}).items()):
            line = _key_line(lines, name, start) or start
            scan.facts.append(Fact(id=f"declared:package.json:{table}:{name}", section="dependencies",
                                   text=f"`{name}@{version}` déclarée (package.json, {table})",
                                   sources=(f"package.json:{line or 1}",)))
    scripts = data.get("scripts") or {}
    scan.npm_scripts = set(scripts)
    start = _key_line(lines, "scripts")
    for name, command in sorted(scripts.items()):
        line = _key_line(lines, name, start) or start
        need = _NPM_SCRIPT_NEED.get(name)
        if need:
            observed.setdefault(need, []).append(("npm test" if name == "test" else f"npm run {name}", f"package.json:{line}"))
        if name in {"start", "serve", "dev"}:
            scan.has_entry_points = True
            scan.facts.append(Fact(id=f"entry:npm:{name}", section="entry_points",
                                   text=f"`npm run {name}` → `{command}`", sources=(f"package.json:{line}",),
                                   data={"kind": "npm_script", "command": str(command)}))
    bins = data.get("bin")
    bin_map = {str(data.get("name", "bin")): bins} if isinstance(bins, str) else bins if isinstance(bins, dict) else {}
    for name, target in sorted(bin_map.items()):
        line = _key_line(lines, "bin")
        scan.has_entry_points = True
        scan.facts.append(Fact(id=f"entry:bin:{name}", section="entry_points",
                               text=f"exécutable `{name}` → `{target}`", sources=(f"package.json:{line or 1}",),
                               data={"kind": "bin", "target": str(target)}))
    if isinstance(data.get("main"), str):
        scan.facts.append(Fact(id="entry:main", section="entry_points",
                               text=f"module principal `{data['main']}`",
                               sources=(f"package.json:{_key_line(lines, 'main') or 1}",), data={"kind": "main"}))


def _makefile(root: Path, observed: dict[str, list[tuple[str, str]]]) -> None:
    path = root / "Makefile"
    if not path.is_file():
        return
    for number, raw in enumerate(_lines(path), start=1):
        match = _MAKE_TARGET.match(raw)
        if match and match.group(1) in _MAKE_NEED:
            observed.setdefault(_MAKE_NEED[match.group(1)], []).append((f"make {match.group(1)}", f"Makefile:{number}"))


def _coverage(root: Path, scan: ManifestScan) -> None:
    path = root / "coverage.xml"
    if not path.is_file():
        scan.facts.append(Fact(id="coverage:unmeasured", section="risks",
                               text="Couverture non mesurée : aucun `coverage.xml` à la racine (inconnue, pas nulle)",
                               data={"measured": False}))
        return
    lines = _lines(path)
    try:
        tree = ET.fromstring("\n".join(lines))  # noqa: S314 — fichier du projet lu localement, sans DTD externe
    except ET.ParseError:
        scan.facts.append(Fact(id="coverage:unreadable", section="risks",
                               text="`coverage.xml` illisible : couverture inconnue", sources=("coverage.xml",)))
        return
    scan.coverage_measured = True
    for cls in tree.iter("class"):
        filename, rate = cls.get("filename", ""), cls.get("line-rate", "")
        try:
            value = float(rate)
        except ValueError:
            continue
        if value < LOW_COVERAGE:
            line = _find_any(lines, (f'filename="{filename}"',))
            scan.facts.append(Fact(id=f"coverage:low:{filename}", section="risks",
                                   text=f"`{filename}` : {value:.0%} des lignes couvertes",
                                   sources=(f"coverage.xml:{line or 1}",), data={"line_rate": value}))


def scan_manifests(root: Path) -> ManifestScan:
    """Dépendances déclarées, points d'entrée des manifestes, commandes et couverture."""
    scan = ManifestScan()
    observed: dict[str, list[tuple[str, str]]] = {}
    _pyproject(root, scan, observed)
    _requirements(root, scan)
    _package_json(root, scan, observed)
    _makefile(root, observed)
    if (root / "tox.ini").is_file():
        observed.setdefault("test-runner", []).append(("tox", "tox.ini:1"))
    _coverage(root, scan)
    resolved = resolve_execution_needs(root)
    for need in COMMAND_NEEDS:
        res = resolved[need]
        if res.source == "declared" and res.command:
            scan.commands[need] = ("declared", res.command, "project-context.yaml")
        elif need in observed:
            scan.commands[need] = ("observed", *observed[need][0])
        elif res.source == "detected" and res.command and not _contradicted(res.command, scan.npm_scripts):
            marker = res.evidence if (root / res.evidence).is_file() else f"marker:{res.evidence}"
            scan.commands[need] = ("assumed", res.command, marker)
        else:
            scan.commands[need] = ("unknown", "", "")
    labels = {"declared": "déclarée", "observed": "observée", "assumed": "supposée (défaut du marqueur)"}
    for need, (status, command, source) in scan.commands.items():
        if status == "unknown":
            continue
        scan.facts.append(Fact(id=f"command:{need}", section="commands", text=f"{need} : `{command}` — {labels[status]}",
                               sources=(source,), data={"status": status, "command": command}))
        for other, other_source in observed.get(need, [])[1:]:
            scan.facts.append(Fact(id=f"command:{need}:{other}", section="commands",
                                   text=f"{need} : `{other}` — observée aussi", sources=(other_source,),
                                   data={"status": "observed", "command": other}))
    return scan


def _contradicted(command: str, npm_scripts: set[str] | None) -> bool:
    """Le défaut ``npm run X`` / ``npm test`` d'un ``package.json`` qui n'a pas ce script est faux, pas supposé."""
    if npm_scripts is None or not command.startswith("npm "):
        return False
    script = command.removeprefix("npm run ").removeprefix("npm ")
    return script not in npm_scripts
