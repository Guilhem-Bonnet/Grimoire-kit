"""P06 — la Console et les commandes que le cockpit cite existent vraiment.

Deux surfaces, une même panne silencieuse :

- ``workspace_exec.ALLOWED`` : la liste blanche de la Console du dock. Une
  entrée qui ne se résout pas en commande *feuille* de la CLI répond « No such
  command » (``blueprint list``) ou l'aide d'un groupe (``task board``) — la
  palette la proposait quand même.
- les commandes « équivalentes » affichées par l'interface (``ctx.dock.echo``,
  formules d'en-tête, commandes des états vides) : ``grimoire blueprint
  simulate`` ou ``grimoire doctor --check-paths`` n'ont jamais existé, et
  quelqu'un qui les recopie dans un terminal se heurte à une erreur.

Chaque chemin est parcouru dans le vrai arbre Typer (groupes paresseux
compris), comme ``test_hosts_command_refs.py`` le fait pour les surfaces
générées.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest
import typer.main

from grimoire.cli.app import app
from grimoire.tools import workspace_exec

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"

#: `grimoire <mots>` puis ses arguments, jusqu'à la fin de la chaîne JS ou du gabarit.
_CITED = re.compile(r"\bgrimoire((?: [a-z][a-z0-9-]*)+)((?: [^\s'\"`;|]+)*)")
_FLAG = re.compile(r"--?[a-z][a-z-]*")


def _is_group(command: object) -> bool:
    # Typer embarque sa propre copie de click : pas d'isinstance, la capacité.
    return callable(getattr(command, "get_command", None)) and callable(getattr(command, "list_commands", None))


def _resolve(words: list[str]) -> tuple[object | None, list[str], str]:
    """Descend *words* dans l'arbre : (commande atteinte, reste, mot inconnu)."""
    command: object = typer.main.get_command(app)
    ctx = click.Context(command)  # type: ignore[arg-type]
    for index, word in enumerate(words):
        if not _is_group(command):
            return command, words[index:], ""
        child = command.get_command(ctx, word)  # type: ignore[attr-defined]
        if child is None:
            return None, words[index:], word
        command = child
        ctx = click.Context(child, parent=ctx)
    return command, [], ""


def _options(command: object) -> set[str]:
    names: set[str] = set()
    for param in getattr(command, "params", []):
        names.update(getattr(param, "opts", []))
        names.update(getattr(param, "secondary_opts", []))
    return names


def _arguments(command: object) -> int:
    return sum(1 for param in getattr(command, "params", []) if getattr(param, "param_type_name", "") == "argument")


def _problem(words: list[str], flags: list[str]) -> str:
    command, _rest, unknown = _resolve(words)
    shown = "grimoire " + " ".join(words)
    if command is None:
        return f"{shown} — sous-commande inconnue : {unknown}"
    if _is_group(command):
        return f"{shown} — groupe sans sous-commande : n'exécute rien"
    missing = sorted(flag for flag in flags if flag not in _options(command))
    if missing:
        return f"{shown} — drapeau(x) inconnu(s) : {', '.join(missing)}"
    return ""


# ── La liste blanche de la Console ──────────────────────────────────────────


@pytest.mark.parametrize("key", sorted(workspace_exec.ALLOWED))
def test_chaque_entree_de_la_liste_blanche_est_une_commande_feuille(key: str) -> None:
    sub = workspace_exec.ALLOWED[key]
    problem = _problem(list(sub.argv), sorted(sub.flags))
    assert not problem, problem
    command, _rest, _unknown = _resolve(list(sub.argv))
    assert sub.positionals <= _arguments(command), (
        f"{key} : {sub.positionals} positionnel(s) déclarés, la CLI en accepte moins"
    )


@pytest.mark.parametrize("key", sorted(workspace_exec.ALLOWED))
def test_chaque_entree_s_execute_sans_no_such_command(key: str, tmp_path: Path) -> None:
    """Exécutée pour de vrai, par la route de la Console, dans un dossier vide :
    la commande peut échouer faute de projet, jamais faute d'exister."""
    sub = workspace_exec.ALLOWED[key]
    result = workspace_exec.run_command(tmp_path, list(sub.argv), timeout=60)
    assert not result["timed_out"], key
    assert "No such command" not in result["output"], result["output"][-400:]
    assert "No such option" not in result["output"], result["output"][-400:]


def test_les_entrees_mortes_sont_retirees() -> None:
    assert "blueprint list" not in workspace_exec.ALLOWED
    assert "task board" not in workspace_exec.ALLOWED
    with pytest.raises(workspace_exec.CommandRefusedError):
        workspace_exec.run_command(ROOT, ["doctor", "--check-paths"])


# ── Les commandes citées par l'interface ────────────────────────────────────


def _cited() -> list[tuple[str, list[str], list[str]]]:
    found: list[tuple[str, list[str], list[str]]] = []
    for path in sorted(WORKSPACE.rglob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            for match in _CITED.finditer(line):
                words = match.group(1).split()
                flags = [w.split("=")[0] for w in match.group(2).split() if _FLAG.fullmatch(w.split("=")[0])]
                found.append((f"{path.relative_to(ROOT)}:{number}", words, flags))
    return found


def test_l_interface_ne_cite_que_des_commandes_qui_existent() -> None:
    cited = _cited()
    assert len(cited) > 30, "le relevé ne trouve presque rien : l'expression ne lit plus les modules"
    failures = [f"{where} : {problem}" for where, words, flags in cited if (problem := _problem(words, flags))]
    assert not failures, "commandes citées par le cockpit qui n'existent pas :\n" + "\n".join(failures)


def test_la_garde_attrape_une_commande_morte() -> None:
    """Une garde qui ne peut pas échouer ne prouve rien."""
    assert "inconnue" in _problem(["blueprint", "list"], [])
    assert "inconnue" in _problem(["blueprint", "simulate"], [])
    assert "groupe" in _problem(["task", "board"], [])
    assert "drapeau" in _problem(["doctor"], ["--check-paths"])
    assert not _problem(["task", "list"], ["--all-projects"])
    assert not _problem(["blueprint", "validate"], [])
