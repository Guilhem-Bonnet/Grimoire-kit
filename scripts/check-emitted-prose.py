#!/usr/bin/env python3
"""Garde statique — la prose émise ne demande jamais un chiffre sans mesure (#613).

Premier étage de la pyramide contre les affirmations sans source (les deux
autres : ``tests/unit/test_hosts.py`` pour l'émission des wrappers,
``evals/grounding-probe.py`` pour le comportement du modèle réel).

Corpus : tout ce que le kit copie ou émet dans un projet utilisateur —
``framework/copilot``, ``framework/hosts``, ``framework/prompt-templates``,
``framework/workflows``, ``archetypes/``, les deux socles ``agent-base*.md``
et les documents de protocole listés dans ``Scaffolder._PROTOCOL_DOCS``
(lus dans ``src/grimoire/core/scaffold.py``, jamais recopiés ici).

Règles, une ligne à la fois :

- ``placeholder-score`` — ``X/5``, ``[X]/100``, ``Score global : X``,
  ``score /10``, ``Noter … 1-5``, ``0-100`` : un score à remplir de tête,
  sans commande ni formule qui le calcule.
- ``invented-confidence`` — ``confiance : 30%``, ``confidence: 0.92``,
  ``confiance ≥ 80%`` : une confiance chiffrée que rien ne mesure, qu'elle
  soit à produire ou comparée à un seuil.
- ``numeric-trust-score`` — ``trust_score: 91``, ``synergy_score: 0.92``,
  ``Trust: {composite}/100``, ``91/100`` : un score de confiance
  inter-agents qu'aucune commande du kit ne calcule.
- ``estimate-placeholder`` — ``[estimé]`` ou ``estimation : 12`` : un nombre
  présenté comme estimation, sans mesure derrière. Un jugement qualitatif
  étiqueté comme tel (``S/M/L``, ``haute|moyenne|faible``) n'est pas visé.

Exceptions : ``scripts/emitted-prose-allowlist.txt``, une ligne
``<chemin>:<règle>  # justification`` par exception. Une exception sans
justification est refusée.

Usage :
    python scripts/check-emitted-prose.py            # code 1 si une ligne matche
    python scripts/check-emitted-prose.py --list     # liste le corpus
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "scripts" / "emitted-prose-allowlist.txt"
SCAFFOLD = ROOT / "src" / "grimoire" / "core" / "scaffold.py"

RULES: dict[str, re.Pattern[str]] = {
    # `X/10`, `[X]/100`, `Score global : X`, `score /10`, `Noter … 1-5`, `0-100`
    "placeholder-score": re.compile(
        r"\[?X\]?\s*/\s*(?:5|10|100)\b|Score (?:global|moyen)\s*:\s*\[?X\b|(?i:\bscore\s*/\s?(?:5|10|100)\b)|\b0-100\b|(?i:\bnoter\b.*\b1-5\b)"
    ),
    # `confiance : 30%`, `confidence: 0.92`, `confiance ≥ 80%`, `confidence < 0.3`, `(confiance: {%})`
    "invented-confidence": re.compile(
        r"(?i)\bconfi(?:ance|dence)(?:_level)?\s*[:=<>≥≤]+\s*[\"']?(?:\d+\s?%|0?\.\d|\{%\})"
    ),
    # `trust_score: 91`, `synergy_score: 0.92`, `Trust: {composite_score}/100`, `91/100`
    "numeric-trust-score": re.compile(
        r"(?i)\b(?:trust|synergy|match|composite|avg_trust)_?score\s*[:=]\s*[\"']?\d|\{[a-z_]+\}\s*/\s*100|\b\d{1,3}\s?/\s?100\b"
    ),
    "estimate-placeholder": re.compile(r"(?i)\[estim[ée]e?\]|\bestim(?:é|ée|ation|ate|ated)\w*\s*[:=]\s*[\"']?\d"),
}

_SUFFIXES = {".md"}


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}: [{self.rule}] {self.text.strip()}"


def protocol_docs() -> list[Path]:
    """Les documents de protocole que le scaffolder copie, lus à la source."""
    text = SCAFFOLD.read_text(encoding="utf-8")
    match = re.search(r"_PROTOCOL_DOCS: ClassVar\[tuple\[str, \.\.\.\]\] = \((.*?)\)", text, re.DOTALL)
    if not match:
        return []
    names = re.findall(r"\"([^\"]+\.md)\"", match.group(1))
    return [ROOT / "framework" / name for name in names if (ROOT / "framework" / name).is_file()]


def corpus() -> list[Path]:
    dirs = (
        ROOT / "framework" / "copilot",
        ROOT / "framework" / "hosts",
        ROOT / "framework" / "prompt-templates",
        ROOT / "framework" / "workflows",
        ROOT / "archetypes",
    )
    files: set[Path] = set()
    for d in dirs:
        if d.is_dir():
            files.update(p for p in d.rglob("*") if p.is_file() and p.suffix in _SUFFIXES)
    files.update(
        p for p in (ROOT / "framework" / "agent-base.md", ROOT / "framework" / "agent-base-compact.md") if p.is_file()
    )
    files.update(protocol_docs())
    return sorted(files)


def allowlist() -> dict[tuple[str, str], str]:
    entries: dict[tuple[str, str], str] = {}
    if not ALLOWLIST.is_file():
        return entries
    for n, raw in enumerate(ALLOWLIST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec, sep, why = line.partition("#")
        if not sep or not why.strip():
            raise SystemExit(f"{ALLOWLIST}:{n}: exception sans justification")
        path, _, rule = spec.strip().rpartition(":")
        if rule not in RULES:
            raise SystemExit(f"{ALLOWLIST}:{n}: règle inconnue {rule!r}")
        entries[(path, rule)] = why.strip()
    return entries


def scan(files: list[Path] | None = None) -> list[Hit]:
    allowed = allowlist()
    hits: list[Hit] = []
    for path in files if files is not None else corpus():
        rel = str(path.relative_to(ROOT))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for rule, pattern in RULES.items():
                if pattern.search(line) and (rel, rule) not in allowed:
                    hits.append(Hit(path, n, rule, line))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="lister le corpus scanné et sortir")
    args = parser.parse_args(argv)
    if args.list:
        for p in corpus():
            print(p.relative_to(ROOT))
        return 0
    hits = scan()
    for hit in hits:
        print(hit)
    if hits:
        print(
            f"\n{len(hits)} ligne(s) demandent un chiffre sans mesure — voir scripts/check-emitted-prose.py",
            file=sys.stderr,
        )
        return 1
    print(f"ok — {len(corpus())} fichiers émis, aucun chiffre à remplir de tête")
    return 0


if __name__ == "__main__":
    sys.exit(main())
