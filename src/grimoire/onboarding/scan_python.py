"""Le code Python d'un projet existant, lu par le graphe de code et par l'AST.

Le graphe (:class:`grimoire.codegraph.CodeGraph`) donne les modules, leurs
symboles publics, leur docstring et la nature test / non test. Les imports
sont relus ici : le graphe les tronque au premier segment (``app`` pour
``from app.store import x``) et ne garde pas leur ligne, or la carte des
dépendances entre composants et la carte des tests ont besoin des deux.

Un *composant* est un paquet de premier niveau sous la racine des modules
(``app.api`` pour ``src/app/api/routes.py``), ou le module lui-même s'il est
à plat. Sa responsabilité n'est écrite que si le code la déclare (docstring
du paquet ou du module) ; sinon, c'est une question ouverte.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from grimoire.codegraph.graph import CodeGraph
from grimoire.codegraph.schemas import NodeKind
from grimoire.onboarding.facts import Fact

__all__ = ["LARGE_MODULE_LINES", "PyModule", "PyScan", "is_test_key", "scan_python"]

#: Au-delà, un module est signalé comme zone à risque (taille seule, pas un défaut).
LARGE_MODULE_LINES = 400
_SERVER_CALLS = frozenset({
    "FastAPI", "Flask", "Starlette", "Quart", "Sanic", "HTTPServer", "ThreadingHTTPServer", "make_server",
})
_SNAKE = re.compile(r"^_?[a-z][a-z0-9_]*$")
_STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


@dataclass
class PyModule:
    """Un fichier Python et ce qu'on en a lu."""

    key: str
    module: str
    lines: int
    is_test: bool
    docstring: str = ""
    doc_line: int = 0
    public_symbols: int = 0
    test_funcs: int = 0
    imports: list[tuple[str, int]] = field(default_factory=list)
    main_guard: int = 0
    servers: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class PyScan:
    """Résultat de la lecture Python : modules, composants, faits."""

    modules: list[PyModule] = field(default_factory=list)
    components: dict[str, list[PyModule]] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    undocumented: list[str] = field(default_factory=list)
    large: list[PyModule] = field(default_factory=list)


def is_test_key(key: str) -> bool:
    """Fichier de test : ``test_*.py``, ``*_test.py`` ou sous un dossier ``tests/``/``test/``.

    Plus strict que l'heuristique du graphe, qui prend ``latest_version.py``
    pour un test (``test_`` au milieu du nom).
    """
    parts = key.split("/")
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py") or any(p in {"tests", "test"} for p in parts[:-1])


def _module_root(root: Path, key: str) -> Path:
    return root / "src" if key.startswith("src/") else root


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    parts = module.split(".") if module else []
    pkg = parts if is_package else parts[:-1]
    base = pkg[: max(len(pkg) - (level - 1), 0)]
    return ".".join([*base, *(target.split(".") if target else [])])


def _read_ast(mod: PyModule, path: Path) -> None:
    """Imports (cible pointée, ligne), garde ``__main__``, serveurs, ligne de la docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        mod.doc_line = first.lineno
    is_package = path.name == "__init__.py"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mod.imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = _resolve_relative(mod.module, is_package, node.level, node.module)
            for alias in node.names:
                mod.imports.append((f"{base}.{alias.name}" if base else alias.name, node.lineno))
        elif isinstance(node, ast.If) and _is_main_guard(node.test):
            mod.main_guard = node.lineno
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in _SERVER_CALLS:
                mod.servers.append((name, node.lineno))


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name) and test.left.id == "__name__"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == "__main__"
    )


def _load_modules(root: Path, files: dict[str, Path]) -> list[PyModule]:
    modules: list[PyModule] = []
    for key, path in files.items():
        if not key.endswith(".py"):
            continue
        graph = CodeGraph()
        graph.index_file(path, root=str(_module_root(root, key)))
        nodes = graph.nodes_in_file(str(path))
        head = next((n for n in nodes if n.kind == NodeKind.MODULE), None)
        if head is None:  # fichier illisible par l'AST : compté, jamais interprété
            continue
        mod = PyModule(
            key=key, module=head.module, lines=head.line_end, is_test=is_test_key(key),
            docstring=head.docstring.strip().splitlines()[0] if head.docstring.strip() else "",
            public_symbols=sum(
                1 for n in nodes if n.kind in (NodeKind.CLASS, NodeKind.FUNCTION) and n.is_public
                and not n.name.startswith("test")
            ),
            test_funcs=sum(1 for n in nodes if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD) and n.name.startswith("test")),
        )
        _read_ast(mod, path)
        modules.append(mod)
    return modules


def _component_of(root: Path, mod: PyModule) -> str:
    parts = mod.module.split(".")
    pkg_dir = _module_root(root, mod.key) / parts[0]
    if len(parts) >= 2 and (pkg_dir / "__init__.py").is_file():
        return ".".join(parts[:2])
    return parts[0]


def _match_component(target: str, components: list[str]) -> str:
    best = ""
    for comp in components:
        if (target == comp or target.startswith(comp + ".")) and len(comp) > len(best):
            best = comp
    return best


def _head(comp: str, members: list[PyModule]) -> PyModule:
    """Le fichier qui porte la responsabilité déclarée : ``__init__.py`` du paquet ou le module."""
    for mod in members:
        if mod.module == comp:
            return mod
    return members[0]


def scan_python(root: Path, files: dict[str, Path]) -> PyScan:
    """Lit les ``.py`` de *files* et rend les faits Python de la base."""
    scan = PyScan(modules=_load_modules(root, files))
    groups: dict[str, list[PyModule]] = defaultdict(list)
    for mod in scan.modules:
        if not mod.is_test and mod.module:
            groups[_component_of(root, mod)].append(mod)
    scan.components = {c: sorted(m, key=lambda x: x.key) for c, m in sorted(groups.items())}
    names = list(scan.components)
    tests = [m for m in scan.modules if m.is_test]
    local_tops = {m.module.split(".")[0] for m in scan.modules if m.module}

    tested_by: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for test in tests:
        hits: dict[str, int] = {}
        for target, line in test.imports:
            comp = _match_component(target, names)
            if comp and comp not in hits:
                hits[comp] = line
        for comp, line in hits.items():
            tested_by[comp].append((test.key, line))
        scan.facts.append(Fact(
            id=f"testmap:{test.key}", section="tests",
            text=(f"`{test.key}` : {test.test_funcs} test(s)"
                  + (f", importe {', '.join(f'`{c}`' for c in sorted(hits))}" if hits else ", n'importe aucun composant")),
            sources=(test.key, *(f"{test.key}:{ln}" for _, ln in sorted(hits.items()))),
            data={"tests": test.test_funcs, "components": sorted(hits)},
        ))

    fan_in: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    third_party: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for comp, members in scan.components.items():
        deps: dict[str, tuple[str, int]] = {}
        for mod in members:
            for target, line in mod.imports:
                dep = _match_component(target, names)
                if dep and dep != comp and dep not in deps:
                    deps[dep] = (mod.key, line)
                top = target.split(".")[0]
                if not dep and top and top not in local_tops and top not in _STDLIB:
                    third_party[top].append((mod.key, line))
        for dep, (key, line) in deps.items():
            fan_in[dep].append((comp, key, line))
        head = _head(comp, members)
        doc_ref = f"{head.key}:{head.doc_line}" if head.docstring and head.doc_line else head.key
        responsibility = head.docstring
        if not responsibility:
            scan.undocumented.append(comp)
        files_n, lines_n = len(members), sum(m.lines for m in members)
        text = f"`{comp}` — {files_n} fichier(s), {lines_n} lignes, {sum(m.public_symbols for m in members)} symbole(s) public(s)"
        text += f". Responsabilité déclarée : « {responsibility} »" if responsibility else ". Responsabilité non déclarée"
        scan.facts.append(Fact(
            id=f"component:{comp}", section="components", text=text,
            sources=(doc_ref, *(m.key for m in members if m is not head)),
            data={"files": [m.key for m in members], "lines": lines_n, "responsibility": responsibility,
                  "depends_on": sorted(deps), "tested_by": sorted(k for k, _ in tested_by.get(comp, []))},
        ))
        if deps:
            scan.facts.append(Fact(
                id=f"internal:{comp}", section="dependencies",
                text=f"`{comp}` dépend de {', '.join(f'`{d}`' for d in sorted(deps))}",
                sources=tuple(f"{k}:{ln}" for _, (k, ln) in sorted(deps.items())),
                data={"depends_on": sorted(deps)},
            ))
        if comp not in tested_by and any(m.public_symbols for m in members):
            scan.facts.append(Fact(
                id=f"untested:{comp}", section="risks",
                text=f"`{comp}` : aucun fichier de test ne l'importe (un test par sous-processus n'est pas vu)",
                sources=(head.key, *(t.key for t in tests)),
                data={"component": comp},
            ))

    for name, refs in sorted(third_party.items()):
        files_n = len({k for k, _ in refs})
        scan.facts.append(Fact(
            id=f"imported:{name}", section="dependencies",
            text=f"`{name}` (tiers) importé par {files_n} fichier(s)",
            sources=tuple(f"{k}:{ln}" for k, ln in refs[:3]),
            data={"files": files_n},
        ))
    for comp, users in sorted(fan_in.items()):
        if len(users) >= 3:
            scan.facts.append(Fact(
                id=f"hotspot:{comp}", section="risks",
                text=f"`{comp}` est importé par {len(users)} composants : le modifier touche {', '.join(f'`{u}`' for u, _, _ in users)}",
                sources=tuple(f"{k}:{ln}" for _, k, ln in users),
                data={"fan_in": len(users)},
            ))
    _entry_points(scan)
    _conventions(root, scan, tests)
    scan.large = sorted((m for m in scan.modules if m.lines > LARGE_MODULE_LINES), key=lambda m: (-m.lines, m.key))
    return scan


def _entry_points(scan: PyScan) -> None:
    for mod in scan.modules:
        if mod.is_test:
            continue
        if mod.key.endswith("__main__.py"):
            package = mod.module
            scan.facts.append(Fact(
                id=f"entry:main-module:{mod.key}", section="entry_points",
                text=f"`python -m {package}` (module `__main__`)", sources=(f"{mod.key}:1",),
                data={"kind": "module", "command": f"python -m {package}"},
            ))
        elif mod.main_guard:
            scan.facts.append(Fact(
                id=f"entry:script:{mod.key}", section="entry_points",
                text=f"`python {mod.key}` (garde `__main__`)", sources=(f"{mod.key}:{mod.main_guard}",),
                data={"kind": "script", "command": f"python {mod.key}"},
            ))
        for name, line in mod.servers:
            scan.facts.append(Fact(
                id=f"entry:server:{mod.key}:{line}", section="entry_points",
                text=f"serveur `{name}` construit dans `{mod.key}`", sources=(f"{mod.key}:{line}",),
                data={"kind": "server", "framework": name},
            ))


def _conventions(root: Path, scan: PyScan, tests: list[PyModule]) -> None:
    code = [m for m in scan.modules if not m.is_test]
    if not code:
        return
    keys = tuple(m.key for m in code)
    under_src = [k for k in keys if k.startswith("src/")]
    layout = "sous `src/`" if under_src else "à la racine (sans `src/`)"
    scan.facts.append(Fact(
        id="convention:layout", section="conventions",
        text=f"Code Python {layout} : {len(under_src) if under_src else len(keys)} fichier(s) sur {len(keys)}",
        sources=keys, data={"src_layout": bool(under_src)},
    ))
    stems = [Path(k).stem for k in keys if Path(k).stem != "__init__"]
    snake = sum(1 for s in stems if _SNAKE.match(s))
    documented = sum(1 for m in code if m.docstring)
    scan.facts.append(Fact(
        id="convention:naming", section="conventions",
        text=f"Modules en snake_case : {snake}/{len(stems)} ; modules avec docstring : {documented}/{len(code)}",
        sources=keys, data={"snake_case": snake, "modules": len(stems), "documented": documented},
    ))
    if tests:
        prefixed = sum(1 for t in tests if Path(t.key).name.startswith("test_"))
        suffixed = sum(1 for t in tests if Path(t.key).stem.endswith("_test"))
        in_dir = sum(1 for t in tests if t.key.split("/")[0] in {"tests", "test"})
        scan.facts.append(Fact(
            id="convention:tests", section="conventions",
            text=(f"Tests Python : {len(tests)} fichier(s), {sum(t.test_funcs for t in tests)} test(s) ; "
                  f"`test_*.py` : {prefixed}, `*_test.py` : {suffixed}, sous `tests/` : {in_dir}"),
            sources=tuple(t.key for t in tests),
            data={"files": len(tests), "test_prefix": prefixed, "test_suffix": suffixed, "in_tests_dir": in_dir},
        ))
