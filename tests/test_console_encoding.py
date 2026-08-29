"""Ce que le dépôt écrit doit survivre à l'encodage par défaut de Windows.

Deux défauts distincts, une seule cause : l'encodage de la locale. Sur Linux
c'est UTF-8 et rien ne casse ; sur Windows c'est cp1252, et tout ce qui n'y
entre pas fait tomber le programme.

## La sortie des outils de `framework/`

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

import ast
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


def _unencodable(text: str) -> list[str]:
    """Les caractères de *text* qu'une console Windows ne sait pas écrire."""
    found = []
    for char in text:
        if ord(char) <= 127:
            continue
        try:
            char.encode(WINDOWS_CONSOLE_ENCODING)
        except UnicodeEncodeError:
            found.append(char)
    return found


def _literal_text(node: ast.AST) -> str:
    """Concaténer les chaînes littérales d'une expression, implicites comprises."""
    return "".join(
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )


class TestFixtureFileEncoding(unittest.TestCase):
    """Un fixture doit s'écrire sur Windows comme il s'écrit ailleurs.

    `Path.write_text` et `open` sans `encoding=` prennent l'encodage de la
    locale. `test_agent_forge.py` écrivait un `shared-context.md` contenant
    `→` — un jeton du format que l'outil parse, pas une décoration : le
    fichier ne s'écrivait même pas sur Windows, et le test tombait avant
    d'avoir rien vérifié.

    Le garde ne condamne pas l'absence d'`encoding=` en général : elle est
    inoffensive sur du texte ASCII, et le dépôt en compte près de deux cents
    appels. Il ne relève que la combinaison qui casse — pas d'encodage
    explicite **et** un caractère hors cp1252 dans ce qu'on écrit.
    """

    def test_no_test_writes_unencodable_text_with_the_locale_encoding(self) -> None:
        offenders = []
        for path in sorted(Path(__file__).parent.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name not in {"write_text", "open"}:
                    continue
                if any(keyword.arg == "encoding" for keyword in node.keywords):
                    continue
                chars = _unencodable("".join(_literal_text(a) for a in node.args))
                if chars:
                    offenders.append(
                        f"{path.name}:{node.lineno} écrit {''.join(sorted(set(chars)))!r} "
                        "sans encoding= explicite"
                    )
        self.assertEqual(offenders, [], "\n  ".join(["fixtures illisibles sur Windows :", *offenders]))

    def test_the_guard_sees_a_relapse(self) -> None:
        source = 'p.write_text("un → deux")\n'
        tree = ast.parse(source)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        self.assertFalse(any(k.arg == "encoding" for k in call.keywords))
        self.assertEqual(_unencodable(_literal_text(call.args[0])), ["→"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
