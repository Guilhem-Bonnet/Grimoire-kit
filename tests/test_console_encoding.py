"""La sortie des outils de `framework/` doit s'imprimer sur une console Windows.

`Framework Tools Tests (pytest, windows-latest)` échouait sur `main` :
`agent-caller.py` imprimait un filet `│` (U+2502), absent de cp1252, ce qui
levait UnicodeEncodeError avant la première ligne utile. Le job tourne avec
`-x` et est déclaré `continue-on-error` pour Windows — il s'arrêtait au premier
fichier fautif et son rouge ne bloquait rien. Corriger un outil révélait donc
le suivant, un run de CI à la fois.

Ce test ferme la file. Il est **comportemental**, et c'est délibéré : aucune
règle statique ne décide correctement de ce qui atteint la console.

- « les caractères sur une ligne `print(` » rate le texte d'aide d'argparse et
  les docstrings passés en `description=__doc__` — c'est ce qui laissait
  `memory-sync.py` et `rag-indexer.py` casser après une première passe ;
- « tous les littéraux de chaîne » sur-collecte de 2977 occurrences, dont les
  2013 d'`observatory.py` qui sont les filets d'une page web embarquée : elles
  partent vers un navigateur, jamais vers un terminal.

Exécuter l'outil tranche sans se tromper.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = KIT_DIR / "framework" / "tools"

# L'encodage par défaut d'une console Windows occidentale : c'est lui qui décide
# si un caractère s'imprime ou fait tomber le programme.
WINDOWS_CONSOLE_ENCODING = "cp1252"
MARKER = "UnicodeEncodeError"


def _run_under_windows_console(argv: list[str]) -> str:
    """Lancer une commande avec un stdout encodé comme sur Windows."""
    env = dict(os.environ, PYTHONIOENCODING=WINDOWS_CONSOLE_ENCODING)
    result = subprocess.run(
        [sys.executable, *argv],
        capture_output=True,
        text=True,
        # On relit avec l'encodage qu'on a imposé à l'enfant : il écrit du
        # cp1252, le décoder en UTF-8 ferait échouer le test sur un tiret cadratin
        # — un caractère que cp1252 sait pourtant très bien représenter.
        encoding=WINDOWS_CONSOLE_ENCODING,
        errors="replace",
        timeout=60,
        env=env,
        cwd=KIT_DIR,
    )
    return result.stdout + result.stderr


class TestConsoleEncoding(unittest.TestCase):
    def test_framework_tools_help_survives_a_windows_console(self) -> None:
        """`--help` touche le docstring, la description argparse et l'aide.

        C'est la surface qu'un utilisateur voit en premier, et celle qui
        traverse le plus de chaînes d'un coup.
        """
        tools = sorted(TOOLS_DIR.glob("*.py"))
        self.assertTrue(tools, f"aucun outil trouvé sous {TOOLS_DIR}")
        broken = [
            tool.name
            for tool in tools
            if MARKER in _run_under_windows_console([str(tool), "--help"])
        ]
        self.assertEqual(
            broken,
            [],
            "outils dont la sortie ne s'encode pas sur une console Windows "
            f"({WINDOWS_CONSOLE_ENCODING}) : {broken}",
        )

    def test_the_guard_sees_a_relapse(self) -> None:
        """Un garde qui ne sait pas échouer ne garde rien."""
        script = KIT_DIR / "tests" / "_console_encoding_probe.py"
        script.write_text('print("a │ b")\n', encoding="utf-8")
        try:
            output = _run_under_windows_console([str(script)])
        finally:
            script.unlink(missing_ok=True)
        self.assertIn(MARKER, output, "le garde ne voit pas un filet réintroduit")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
