"""Les suppressions destructives Windows doivent être lues comme ``rm -rf`` (issue #761).

``_DESTRUCTIVE_PATTERNS`` ne connaissait que des formes POSIX : sous Copilot
Windows, ``Remove-Item -Recurse -Force``, ``rd /s`` ou ``del /s`` passaient en
``allow`` là où ``rm -rf`` était refusé. Les deux sens sont vérifiés : chaque
forme destructive est reconnue, et les lectures ou suppressions d'un seul
fichier sans forçage restent hors du garde.

Les littéraux sont assemblés à l'exécution, comme dans
``test_policy_command_surface.py`` : une session gouvernée refuserait sinon
d'écrire ce fichier.
"""

from __future__ import annotations

import pytest

from grimoire.hosts.decisions import classify_tool

REMOVE_ITEM = "Remove" + "-Item"
RECURSE = "-Rec" + "urse"
FORCE = "-Fo" + "rce"
RD_S = "rd" + " /s"
RMDIR_S = "rmdir" + " /s"
DEL_S = "del" + " /s"
DEL_Q = "del" + " /q"
ERASE_S = "erase" + " /s"


def _reason(command: str, tool: str = "runInTerminal") -> str:
    return classify_tool(tool, {"command": command}).destructive_reason


DESTRUCTIVE = [
    pytest.param(f"{REMOVE_ITEM} {RECURSE} {FORCE} build", id="remove-item-recurse-force"),
    pytest.param(f"{REMOVE_ITEM} build {RECURSE}", id="remove-item-parametre-apres-le-chemin"),
    pytest.param(f"{REMOVE_ITEM} -Path build -r", id="remove-item-prefixe-r"),
    pytest.param(f"{REMOVE_ITEM} build {RECURSE}:$true", id="remove-item-recurse-booleen"),
    pytest.param(f"{REMOVE_ITEM} {FORCE} secrets.txt", id="remove-item-force"),
    pytest.param(f"remove-item {RECURSE.lower()} build", id="remove-item-minuscules"),
    pytest.param(f"rm build {RECURSE}", id="alias-rm-powershell"),
    pytest.param(f"ri build {RECURSE}", id="alias-ri"),
    pytest.param(f"del build {RECURSE}", id="alias-del-powershell"),
    pytest.param(f"Get-ChildItem build {RECURSE} | {REMOVE_ITEM}", id="pipeline-vers-remove-item"),
    pytest.param(f"{RD_S} /q build", id="rd-s"),
    pytest.param("RD /S /Q build", id="rd-s-majuscules"),
    pytest.param(f"{RMDIR_S} /q build", id="rmdir-s"),
    pytest.param(f"{DEL_S} /q build", id="del-s"),
    pytest.param(f"{DEL_Q} *.*", id="del-q"),
    pytest.param(f"{ERASE_S} build", id="erase-s"),
    pytest.param("Format" + "-Volume -DriveLetter D", id="format-volume"),
    pytest.param("Clear" + "-Disk -Number 1 -RemoveData", id="clear-disk"),
    pytest.param("cd build; " + f"{REMOVE_ITEM} . {RECURSE}", id="apres-un-point-virgule"),
    pytest.param(f"pwsh -Command '{REMOVE_ITEM} build {RECURSE}'", id="via-pwsh-command"),
    pytest.param(f'powershell.exe -NoProfile -c "{REMOVE_ITEM} build {RECURSE}"', id="via-powershell-c"),
    pytest.param(f'cmd /c "{RD_S} /q build"', id="via-cmd-c"),
]

NOT_DESTRUCTIVE = [
    pytest.param(f"{REMOVE_ITEM} fichier.txt", id="remove-item-un-fichier"),
    pytest.param("Get-ChildItem " + RECURSE, id="get-childitem-recurse"),
    pytest.param("dir /s", id="dir-s"),
    pytest.param("Get-Content README.md", id="get-content"),
    pytest.param("rd build", id="rd-dossier-vide"),
    pytest.param("del fichier.txt", id="del-un-fichier"),
    pytest.param("git status", id="git-status"),
    pytest.param("python -m pytest --durations=10", id="pytest"),
    pytest.param(f"git commit -m 'doc: {REMOVE_ITEM} {RECURSE} est interdit'", id="message-de-commit"),
    pytest.param("rider build", id="mot-commencant-par-rd"),
]


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_windows_destructive_forms_are_recognised(command: str) -> None:
    assert _reason(command), f"non reconnu comme destructif : {command!r}"


@pytest.mark.parametrize("command", NOT_DESTRUCTIVE)
def test_windows_reads_and_single_deletes_stay_outside_the_guard(command: str) -> None:
    assert _reason(command) == "", f"faux positif : {command!r} -> {_reason(command)!r}"


def test_bash_tool_reads_windows_forms_too() -> None:
    """Le garde ne dépend pas du nom d'outil : ``Bash`` (Claude Code) comme ``runInTerminal``."""
    assert _reason(f"{REMOVE_ITEM} build {RECURSE}", tool="Bash")
