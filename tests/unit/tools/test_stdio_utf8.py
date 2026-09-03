"""Quarante-six outils meurent en UnicodeEncodeError sur une console cp1252.

Le correctif est de classe et tient en deux lignes en tête de chaque ``main()`` :
la sortie est reconfigurée en UTF-8 avec remplacement. Pas de module partagé —
ces outils sont chargés de trois façons (script, ``import_module``, chargeur par
chemin) et seul un code sans import survit aux trois. Ces tests ne dépendent
pas de la plateforme : la console cp1252 est un flux capturé, forcé à cet
encodage.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "framework" / "tools"
NON_CP1252 = re.compile("[←-➿─-╿]")
FIX = 'reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")'


def _cp1252_console() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True)


def test_a_cp1252_console_really_dies_on_a_table_rule() -> None:
    """Sans quoi le correctif ne prouverait rien."""
    with pytest.raises(UnicodeEncodeError):
        print("│ → ✓", file=_cp1252_console())


def test_the_two_line_fix_lets_the_same_output_through() -> None:
    console = _cp1252_console()
    for _s in (console,):
        getattr(_s, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")
    print("│ → ✓", file=console)
    console.flush()
    assert "│ → ✓".encode() in console.buffer.getvalue()


def test_a_stream_without_reconfigure_is_left_alone() -> None:
    captured = io.StringIO()
    getattr(captured, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")
    print("│", file=captured)
    assert captured.getvalue() == "│\n"


def _tools_printing_non_cp1252() -> list[Path]:
    return sorted(p for p in TOOLS.glob("*.py") if NON_CP1252.search(p.read_text(encoding="utf-8")))


def test_every_tool_that_prints_such_characters_fixes_its_console_in_main() -> None:
    """Le correctif de classe : la classe entière, pas un outil."""
    missing = []
    for path in _tools_printing_non_cp1252():
        text = path.read_text(encoding="utf-8")
        main = re.search(r"^def main\([^)]*\)[^\n]*:\n(.*?)(?=^def |\Z)", text, flags=re.MULTILINE | re.DOTALL)
        body = main.group(1) if main else ""
        if FIX not in body[:700]:
            missing.append(path.name)
    assert not missing, f"outils qui impriment hors cp1252 sans corriger leur console en tête de main() : {missing}"


def test_the_class_is_not_empty() -> None:
    assert len(_tools_printing_non_cp1252()) >= 40
