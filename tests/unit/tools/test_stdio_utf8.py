"""Quarante-cinq outils meurent en UnicodeEncodeError sur une console cp1252.

Le correctif est de classe : ``_stdio.force_utf8()`` en tête de ``main()``.
Ces tests ne dépendent pas de la plateforme du runner : la console cp1252 est
un flux capturé, forcé à cet encodage.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "framework" / "tools"
NON_CP1252 = re.compile("[←-➿─-╿]")


def _cp1252_console() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True)


def _load_stdio():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import _stdio

    return _stdio


def test_a_cp1252_console_really_dies_on_a_table_rule() -> None:
    """Sans quoi le correctif ne prouverait rien."""
    console = _cp1252_console()
    with pytest.raises(UnicodeEncodeError):
        print("│ → ✓", file=console)


def test_force_utf8_lets_the_same_output_through() -> None:
    _stdio = _load_stdio()
    console = _cp1252_console()
    _stdio.force_utf8(console)
    print("│ → ✓", file=console)
    console.flush()
    assert "│ → ✓".encode() in console.buffer.getvalue()


def test_a_stream_without_reconfigure_is_left_alone() -> None:
    _stdio = _load_stdio()
    captured = io.StringIO()
    _stdio.force_utf8(captured)  # ne lève pas
    print("│", file=captured)
    assert captured.getvalue() == "│\n"


def _tools_printing_non_cp1252() -> list[Path]:
    return sorted(
        p for p in TOOLS.glob("*.py")
        if p.name != "_stdio.py" and NON_CP1252.search(p.read_text(encoding="utf-8"))
    )


def test_every_tool_that_prints_such_characters_forces_utf8_in_main() -> None:
    """Le correctif de classe : pas un outil corrigé, la classe entière."""
    missing = []
    for path in _tools_printing_non_cp1252():
        text = path.read_text(encoding="utf-8")
        main = re.search(r"^def main\([^)]*\)[^\n]*:\n(.*?)(?=^def |\Z)", text, flags=re.MULTILINE | re.DOTALL)
        body = main.group(1) if main else ""
        if "_stdio.force_utf8(" not in body.split("\n\n")[0] and "_stdio.force_utf8(" not in body[:600]:
            missing.append(path.name)
    assert not missing, f"outils qui impriment hors cp1252 sans forcer l'UTF-8 en tête de main() : {missing}"


def test_the_class_is_not_empty() -> None:
    assert len(_tools_printing_non_cp1252()) >= 40
