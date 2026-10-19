#!/usr/bin/env python3
"""Garde des tenets — chaque principe de contribution est tenu par un test qui existe.

``CONTRIBUTING.md`` porte une section ``## Tenets``. Un tenet que seule la
bonne volonté d'un relecteur fait respecter est une intention : chacun cite ici
le test qui le fait respecter, et ce garde échoue si ce test a disparu.

Deux contrôles :

tenets
    la section existe, compte au moins trois tenets (``### Tenet N — titre``),
    et chacun cite au moins un chemin ``tests/...py`` qui existe ; une
    référence ``::nom`` doit aussi nommer une fonction ou une classe du fichier.

gabarit de PR
    ``.github/PULL_REQUEST_TEMPLATE.md`` ne cite que des commandes de garde
    qui existent (``python scripts/x.py``, ``make cible``, ``grimoire gates repo
    --only id``), et nomme chaque garde de ``grimoire gates repo`` : une liste
    de contrôle qui renvoie à une commande disparue apprend à l'ignorer.

Usage::

    python scripts/check-contributing.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_TENETS = 3

_TENETS_SECTION = re.compile(r"(?ms)^## Tenets[ \t]*\r?\n(?P<body>.*?)(?=^## |\Z)")
_TENET_HEADING = re.compile(r"(?m)^### Tenet \d+ — .+$")
_TEST_REF = re.compile(r"`(?P<path>tests/[\w./-]+\.py)(?:::(?P<name>\w+))?`")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_MAKE_TARGET = re.compile(r"(?m)^(?P<target>[A-Za-z0-9_.-]+):(?!=)")


def tenet_problems(root: Path = ROOT) -> list[str]:
    path = root / "CONTRIBUTING.md"
    if not path.is_file():
        return ["CONTRIBUTING.md absent"]
    section = _TENETS_SECTION.search(path.read_text(encoding="utf-8"))
    if not section:
        return ["CONTRIBUTING.md n'a pas de section `## Tenets`"]
    body = section.group("body")
    headings = list(_TENET_HEADING.finditer(body))
    if len(headings) < MIN_TENETS:
        return [f"`## Tenets` compte {len(headings)} tenet(s) `### Tenet N — titre`, il en faut au moins {MIN_TENETS}"]
    found: list[str] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        chunk = body[heading.end():end]
        title = heading.group(0).removeprefix("### ").strip()
        refs = list(_TEST_REF.finditer(chunk))
        if not refs:
            found.append(f"{title} : ne cite aucun test (`tests/...py`)")
            continue
        for ref in refs:
            test_file = root / ref.group("path")
            if not test_file.is_file():
                found.append(f"{title} : `{ref.group('path')}` n'existe pas")
            elif ref.group("name") and not re.search(
                rf"(?m)^\s*(?:async\s+)?(?:def|class)\s+{re.escape(ref.group('name'))}\b",
                test_file.read_text(encoding="utf-8"),
            ):
                found.append(f"{title} : `{ref.group('name')}` n'est ni une fonction ni une classe de {ref.group('path')}")
    return found


def template_problems(root: Path = ROOT) -> list[str]:
    path = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    if not path.is_file():
        return [".github/PULL_REQUEST_TEMPLATE.md absent"]
    text = path.read_text(encoding="utf-8")
    makefile = root / "Makefile"
    targets = (
        {m.group("target") for m in _MAKE_TARGET.finditer(makefile.read_text(encoding="utf-8"))}
        if makefile.is_file()
        else set()
    )
    from grimoire.repo_gates import GATE_IDS

    found: list[str] = []
    for span in (m.group(1).strip() for m in _CODE_SPAN.finditer(text)):
        script = re.match(r"python3? (scripts/[\w./-]+\.py)\b", span)
        if script and not (root / script.group(1)).is_file():
            found.append(f"cite `{span}` : {script.group(1)} n'existe pas")
        make = re.match(r"make ([\w.-]+)", span)
        if make and make.group(1) not in targets:
            found.append(f"cite `{span}` : la cible `{make.group(1)}` n'est pas dans le Makefile")
        gate = re.match(r"grimoire gates repo\b(.*)", span)
        if gate:
            for gate_id in re.findall(r"--only[ =]([\w-]+)", gate.group(1)):
                if gate_id not in GATE_IDS:
                    found.append(f"cite `{span}` : la garde `{gate_id}` n'existe pas (connues : {', '.join(GATE_IDS)})")
    if "grimoire gates repo" not in text:
        found.append("ne renvoie pas à `grimoire gates repo`")
    for gate_id in GATE_IDS:
        if f"`{gate_id}`" not in text:
            found.append(f"ne nomme pas la garde `{gate_id}`")
    return found


def main() -> int:
    found = [*tenet_problems(), *template_problems()]
    if found:
        print(f"{len(found)} défaut(s) dans les tenets ou le gabarit de PR :", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("ok — chaque tenet cite un test existant, le gabarit de PR ne cite que des gardes existantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
