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


def _printed_via_variable(path: Path) -> list[str]:
    """Caractères hors cp1252 dans une chaîne qui finit dans un `print()`.

    Complément indispensable au garde comportemental : `--help` ne traverse que
    le docstring et l'aide d'argparse. `token-budget check` construisait sa
    barre de progression sur une ligne (`bar = "█" * n`) et l'imprimait sur la
    suivante — invisible pour `--help`, invisible pour une règle qui ne regarde
    que les lignes `print(`, et fatale sous Windows.

    On ne relève que ce qui atteint réellement la sortie : une variable
    imprimée dans le même périmètre. Un caractère décoratif dans un gabarit ou
    une expression régulière n'est pas concerné — `agent-forge.py` utilise `→`
    comme jeton du format `shared-context.md`, et le remplacer casserait la
    lecture des fichiers existants.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        printed = {
            sub.id
            for node in ast.walk(scope)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name)
        }
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(x, ast.Name) and x.id in printed for x in node.targets):
                continue
            chars = _unencodable(_literal_text(node.value))
            if chars:
                offenders.append(
                    f"{path.name}:{node.lineno} imprime {''.join(sorted(set(chars)))!r}"
                )
    return sorted(set(offenders))


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

    def test_no_tool_prints_an_unencodable_variable(self) -> None:
        """Ce que `--help` ne traverse pas, une règle statique doit le voir."""
        offenders = []
        for tool in sorted(TOOLS_DIR.glob("*.py")):
            offenders.extend(_printed_via_variable(tool))
        self.assertEqual(
            offenders, [], "\n  ".join(["chaînes imprimées non encodables :", *offenders])
        )

    def test_the_variable_guard_sees_a_relapse(self) -> None:
        probe = TOOLS_DIR.parent / "zz-probe-print.py"
        probe.write_text(
            "def main():\n"
            '    bar = "█" * 10\n'
            "    print(bar)\n",
            encoding="utf-8",
        )
        try:
            offenders = _printed_via_variable(probe)
        finally:
            probe.unlink(missing_ok=True)
        self.assertTrue(offenders, "la barre construite en variable n'est pas vue")

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


def _locale_written_non_ascii(path: Path) -> list[str]:
    """Écritures de texte non-ASCII sans `encoding=`, dans un fichier de test.

    Les chaînes passent souvent par une variable locale — c'est ce qui avait
    laissé passer `test_parse_markdown_sections`, dont le contenu est affecté
    avant d'être écrit. On résout donc les affectations simples du même
    périmètre en plus des littéraux passés directement.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        known: dict[str, str] = {}
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                text = _literal_text(node.value)
                if text:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            known[target.id] = text
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in {"write_text", "open"}:
                continue
            if any(keyword.arg == "encoding" for keyword in node.keywords):
                continue
            payload = "".join(
                known.get(arg.id, "") if isinstance(arg, ast.Name) else _literal_text(arg)
                for arg in node.args
            )
            chars = sorted({c for c in payload if ord(c) > 127})
            if chars:
                offenders.append(
                    f"{path.name}:{node.lineno} écrit {''.join(chars)!r} "
                    "sans encoding= explicite"
                )
    return sorted(set(offenders))


def _locale_read_in_non_ascii_test(path: Path) -> list[str]:
    """Lectures sans `encoding=` dans un test qui manipule du non-ASCII.

    C'est le versant discret du même défaut. Lire des octets UTF-8 comme du
    cp1252 ne lève presque jamais d'erreur : cp1252 traduit la quasi-totalité
    des octets. On obtient du mojibake, donc un `assertIn("migré", …)` qui
    échoue sans rien dire d'utile sur la cause.

    La règle est volontairement large — toute lecture nue dans une fonction de
    test contenant du non-ASCII. Un faux positif coûte un argument nommé ;
    l'omission inverse coûte un run de CI et une demi-heure de diagnostic.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        has_non_ascii = any(
            ord(c) > 127
            for node in ast.walk(scope)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            for c in node.value
        )
        if not has_non_ascii:
            continue
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "read_text":
                continue
            if any(keyword.arg == "encoding" for keyword in node.keywords):
                continue
            offenders.append(
                f"{path.name}:{node.lineno} lit sans encoding= dans {scope.name}()"
            )
    return sorted(set(offenders))


def _locale_written_non_ascii_in(root: Path) -> list[str]:
    """La même règle, appliquée au code produit.

    Un test mal encodé fait perdre une heure ; un outil mal encodé écrit un
    fichier que le reste de l'écosystème ne saura pas relire. C'est le même
    défaut, avec un coût différent.
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            offenders.extend(_locale_written_non_ascii(path))
        except SyntaxError:  # pragma: no cover - fichier non parsable
            continue
    return offenders


class TestProductFileEncoding(unittest.TestCase):
    """Ce que les outils écrivent doit se relire partout.

    `session-save.py` écrivait `session-state.md` — « # État de Session »,
    « ## Dernière Session » — sans encodage explicite. Sur Windows le fichier
    partait en cp1252, alors que tout ce qui le relit impose UTF-8. Le défaut
    ne s'est vu que le jour où un test a cessé de relire dans l'encodage de la
    locale, c'est-à-dire quand il a cessé de se tromper de la même façon.
    """

    def test_tools_write_non_ascii_with_an_explicit_encoding(self) -> None:
        kit = Path(__file__).resolve().parents[1]
        offenders = _locale_written_non_ascii_in(kit / "src")
        offenders += _locale_written_non_ascii_in(kit / "framework")
        self.assertEqual(
            offenders,
            [],
            "\n  ".join(["fichiers écrits dans l'encodage de la locale :", *offenders]),
        )


class TestFixtureFileEncoding(unittest.TestCase):
    """Un fixture doit s'écrire sur Windows comme il s'écrit ailleurs.

    `Path.write_text` et `open` sans `encoding=` prennent l'encodage de la
    locale. `test_agent_forge.py` écrivait un `shared-context.md` contenant
    `→` — un jeton du format que l'outil parse, pas une décoration : le
    fichier ne s'écrivait même pas sur Windows, et le test tombait avant
    d'avoir rien vérifié.

    Le garde a d'abord relevé les seuls caractères absents de cp1252. C'était
    trop étroit, et un cinquième test l'a montré : `test_context_summarizer`
    écrivait un tiret cadratin, que cp1252 sait très bien représenter.
    L'écriture passait donc sur Windows — et c'est la relecture qui échouait,
    le code produit lisant en UTF-8 explicite. Pire, `parse_file` avale
    `UnicodeDecodeError` et rend une liste vide : le test ne voyait pas une
    erreur, il voyait zéro section.

    La règle juste est donc l'aller-retour : **tout** contenu non-ASCII écrit
    avec l'encodage de la locale casse, puisque le lecteur, lui, impose UTF-8.

    Le garde ne condamne pas l'absence d'`encoding=` en général : elle est
    inoffensive sur du texte ASCII, et le dépôt en compte près de deux cents
    appels. Il ne relève que la combinaison qui casse — pas d'encodage
    explicite **et** du non-ASCII dans ce qu'on écrit.
    """

    def test_no_test_writes_unencodable_text_with_the_locale_encoding(self) -> None:
        offenders = []
        for path in sorted(Path(__file__).parent.glob("test_*.py")):
            offenders.extend(_locale_written_non_ascii(path))
        self.assertEqual(
            offenders, [], "\n  ".join(["fixtures illisibles sur Windows :", *offenders])
        )

    def test_the_guard_sees_a_payload_passed_by_variable(self) -> None:
        """Le cas qui a coûté un run de CI de plus.

        `test_parse_markdown_sections` affectait son contenu à une variable
        avant de l'écrire. Une règle qui n'inspecte que les littéraux passés
        directement ne voit rien — et laisse passer exactement le défaut
        qu'elle cherche.
        """
        probe = Path(__file__).parent / "test_zz_probe_encoding.py"
        probe.write_text(
            'from pathlib import Path\n'
            'def test_x():\n'
            '    contenu = "une décision — importante"\n'
            '    Path("f.md").write_text(contenu)\n',
            encoding="utf-8",
        )
        try:
            offenders = _locale_written_non_ascii(probe)
        finally:
            probe.unlink(missing_ok=True)
        self.assertTrue(offenders, "la charge passée par variable n'est pas vue")
        self.assertIn("—", offenders[0])

    def test_no_test_reads_non_ascii_with_the_locale_encoding(self) -> None:
        """Les outils écrivent en UTF-8 explicite ; les tests doivent relire pareil."""
        offenders = []
        for path in sorted(Path(__file__).parent.glob("test_*.py")):
            offenders.extend(_locale_read_in_non_ascii_test(path))
        self.assertEqual(
            offenders, [], "\n  ".join(["relectures faussées sur Windows :", *offenders])
        )

    def test_the_read_guard_sees_a_relapse(self) -> None:
        probe = Path(__file__).parent / "test_zz_probe_read.py"
        probe.write_text(
            'from pathlib import Path\n'
            'def test_x():\n'
            '    contenu = Path("f.md").read_text()\n'
            '    assert "migré" in contenu\n',
            encoding="utf-8",
        )
        try:
            offenders = _locale_read_in_non_ascii_test(probe)
        finally:
            probe.unlink(missing_ok=True)
        self.assertTrue(offenders, "la lecture nue n'est pas vue")

    def test_the_guard_sees_a_relapse(self) -> None:
        """Y compris sur un caractère que cp1252 sait représenter.

        C'est le cas qui avait échappé à la première version du garde : le
        tiret cadratin s'écrit sans erreur, et se relit de travers.
        """
        for text in ("un → deux", "un — deux"):
            source = f'p.write_text("{text}")\n'
            call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
            self.assertFalse(any(k.arg == "encoding" for k in call.keywords))
            payload = _literal_text(call.args[0])
            self.assertTrue(
                [c for c in payload if ord(c) > 127], f"non-ASCII non détecté dans {text!r}"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
