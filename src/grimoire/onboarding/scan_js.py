"""Le code JavaScript / TypeScript d'un projet existant, lu par motifs.

Le graphe de code ne lit que Python. Pour JS/TS, la base ne déduit que ce
qu'un motif établit sans ambiguïté : les fichiers et leurs lignes, les
imports relatifs (``import … from './x'``, ``require('./x')``) pour la carte
des tests, les imports nus pour les dépendances utilisées, et les serveurs
(``createServer(``, ``.listen(``). Pas de symboles, pas de responsabilité :
ce qui demande un analyseur n'est pas affirmé.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from grimoire.onboarding.facts import JS_SUFFIXES, Fact

__all__ = ["JsFile", "JsScan", "scan_js"]

_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|\brequire\(\s*)['"]([^'"]+)['"]""")
_SERVER = re.compile(r"\bcreateServer\(|\.listen\(")


@dataclass
class JsFile:
    key: str
    lines: int
    is_test: bool
    imports: list[tuple[str, int]] = field(default_factory=list)
    server_line: int = 0


@dataclass
class JsScan:
    files: list[JsFile] = field(default_factory=list)
    components: dict[str, list[JsFile]] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)


def _is_test(key: str) -> bool:
    name = key.rsplit("/", 1)[-1]
    return ".test." in name or ".spec." in name or "__tests__" in key.split("/") or key.split("/")[0] in {"test", "tests"}


def _component(key: str) -> str:
    parts = key.split("/")
    if parts[0] == "src" and len(parts) > 2:
        return f"src/{parts[1]}"
    return parts[0] if len(parts) > 1 else key


def _resolve(importer: str, spec: str, known: set[str]) -> str:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    for candidate in (base, *(base + s for s in JS_SUFFIXES), *(f"{base}/index{s}" for s in JS_SUFFIXES)):
        if candidate in known:
            return candidate
    return ""


def scan_js(files: dict[str, Path]) -> JsScan:
    """Lit les fichiers JS/TS de *files* et rend leurs faits."""
    scan = JsScan()
    for key, path in files.items():
        if not key.endswith(JS_SUFFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        item = JsFile(key=key, lines=len(text), is_test=_is_test(key))
        for number, line in enumerate(text, start=1):
            item.imports.extend((m.group(1), number) for m in _IMPORT.finditer(line))
            if not item.server_line and _SERVER.search(line):
                item.server_line = number
        scan.files.append(item)
    known = {f.key for f in scan.files}
    groups: dict[str, list[JsFile]] = defaultdict(list)
    for item in scan.files:
        if not item.is_test:
            groups[_component(item.key)].append(item)
    scan.components = dict(sorted(groups.items()))
    tested: dict[str, list[str]] = defaultdict(list)
    used: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for item in scan.files:
        hits: dict[str, int] = {}
        for spec, lineno in item.imports:
            if spec.startswith("."):
                target = _resolve(item.key, spec, known)
                if target and item.is_test:
                    hits.setdefault(_component(target), lineno)
            elif not spec.startswith(("node:", "/")):
                parts = spec.split("/")
                used["/".join(parts[:2]) if spec.startswith("@") else parts[0]].append((item.key, lineno))
        if item.is_test:
            for comp in hits:
                tested[comp].append(item.key)
            scan.facts.append(Fact(
                id=f"testmap:{item.key}", section="tests",
                text=f"`{item.key}`" + (f" importe {', '.join(f'`{c}`' for c in sorted(hits))}" if hits
                                        else " n'importe aucun fichier du projet par chemin relatif"),
                sources=(item.key, *(f"{item.key}:{ln}" for _, ln in sorted(hits.items()))),
                data={"components": sorted(hits)},
            ))
        elif item.server_line:
            scan.facts.append(Fact(
                id=f"entry:server:{item.key}:{item.server_line}", section="entry_points",
                text=f"serveur démarré dans `{item.key}`", sources=(f"{item.key}:{item.server_line}",),
                data={"kind": "server"},
            ))
    tests = [f.key for f in scan.files if f.is_test]
    for comp, members in scan.components.items():
        scan.facts.append(Fact(
            id=f"component:{comp}", section="components",
            text=f"`{comp}` (JS/TS) — {len(members)} fichier(s), {sum(m.lines for m in members)} lignes. Responsabilité non déclarée",
            sources=tuple(m.key for m in members),
            data={"files": [m.key for m in members], "lines": sum(m.lines for m in members), "responsibility": "",
                  "tested_by": sorted(set(tested.get(comp, [])))},
        ))
        if comp not in tested:
            scan.facts.append(Fact(
                id=f"untested:{comp}", section="risks",
                text=f"`{comp}` (JS/TS) : aucun fichier de test ne l'importe par chemin relatif",
                sources=(members[0].key, *tests), data={"component": comp},
            ))
    for name, refs in sorted(used.items()):
        scan.facts.append(Fact(
            id=f"imported:js:{name}", section="dependencies",
            text=f"`{name}` (JS, tiers) importé par {len({k for k, _ in refs})} fichier(s)",
            sources=tuple(f"{k}:{ln}" for k, ln in refs[:3]), data={"files": len({k for k, _ in refs})},
        ))
    return scan
