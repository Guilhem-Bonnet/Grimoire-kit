"""Une redirection qui n'écrit aucun fichier, ou un antislash dans un motif, ne fait pas une écriture.

Deux causes laissaient une lecture comptée comme une écriture après le retrait des
enveloppes (mesuré sur des journaux de preuve réels) :

- toute redirection classait la commande en mutation, y compris celles qui n'écrivent
  nulle part : duplication de descripteur (``2>&1``) et périphérique nul
  (``2>/dev/null``) ;
- un motif entre guillemets doubles qui porte un antislash (``grep -n "a\\|b" f``)
  n'était pas effacé de la surface : son ``|`` coupait la commande en deux.

La liste blanche des redirections est faite de formes exactes ; toute autre cible reste
une écriture. La surface que partage la détection destructive n'est pas modifiée.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome, classify_tool, command_surface
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.policies.schemas import MutationClass
from grimoire.policies.session_state import session_mutations

SESSION = "abcdef02-0000-4000-8000-00000000000b"


def _classe(command: str) -> MutationClass:
    return classify_tool("Bash", {"command": command}).mutation


# ── Redirections qui n'écrivent aucun fichier ────────────────────────────────

LECTURES = [
    "git status",
    "ls -la dossier",
    "cat notes.md",
    "grep -rn motif src",
    "find . -name '*.py'",
    "rtk git log --oneline -3",
    "timeout 30 journalctl -u grimoire-tick -n 5",
    "curl -s http://localhost:8765/health",
]

#: Formes exactes de la liste blanche ; ``{}`` reçoit la lecture.
SANS_FICHIER = [
    "{} 2>&1",
    "{} 1>&2",
    "{} >&2",
    "{} 2>&-",
    "{} 3>&1",
    "{} >/dev/null",
    "{} > /dev/null",
    "{} 1>/dev/null",
    "{} 2>/dev/null",
    "{} 2> /dev/null",
    "{} &>/dev/null",
    "{} &> /dev/null",
    "{} >>/dev/null",
    "{} 2>>/dev/null",
    "{} &>>/dev/null",
    "{} >/dev/null 2>&1",
    "{} 2>&1 >/dev/null",
    "{} 2>&1 | tail -3",
    "{} 2>&1|tail -3",
    "{} 2>/dev/null | head -5",
    "{} 2>/dev/null || true",
    "{} 2>/dev/null; git status",
    "{} 2>&1 && ls 2>/dev/null",
    "cd /tmp && {} 2>&1 | grep -c motif",
]


@pytest.mark.parametrize("command", [forme.format(lecture) for lecture in LECTURES for forme in SANS_FICHIER])
def test_une_lecture_redirigee_sans_fichier_est_read_only(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # Dans un script, et autour d'un script.
        "bash -c 'git status 2>&1 | tail -3'",
        "sh -c 'ls dossier 2>/dev/null'",
        "bash -c 'git status' 2>&1 | tail -2",
        "timeout 30 bash -c 'git status 2>/dev/null ; git log -1 2>&1'",
        "rtk proxy sh -c 'ls >/dev/null 2>&1' 2>/dev/null",
        # Sans espace avant l'opérateur ; un chiffre collé à un mot n'est pas un descripteur.
        "ls dossier>/dev/null",
        "cat fichier2>/dev/null",
        "for f in a b; do cat $f 2>/dev/null; done",
        "if git diff --quiet 2>/dev/null; then echo propre; fi",
    ],
)
def test_la_liste_blanche_vaut_dans_un_script_et_autour(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        # Un autre périphérique, ou le périphérique nul écrit autrement.
        "ls > /dev/stdout",
        "ls >/dev/stderr",
        "ls > /dev/fd/1",
        "ls > /proc/self/fd/1",
        "ls > /dev/tty",
        "ls > /dev//null",
        "ls > /dev/./null",
        "ls >/dev/NULL",
        'ls > "/dev/null"',
        "ls > '/dev/null'",
        "ls > /dev/nul\\l",
        # Un chemin qui ressemble au périphérique nul sans l'être.
        "ls > null",
        "ls > ./null",
        "ls > /tmp/null",
        "ls >/dev/nullx",
        "ls > /dev/null2",
        "ls > /dev/null.txt",
        "ls > /dev/null/../x",
        "ls >/dev/null/x",
        # Un autre opérateur.
        "ls >| /dev/null",
        "ls >| fichier",
        "ls <> fichier",
        "ls <> /dev/null",
        "ls 2<>/dev/null",
        "ls &> fichier",
        "ls &>> fichier",
        "ls >& fichier",
        "ls >&fichier",
        "ls >&2fichier",
        "ls >&10",
        # Un descripteur autre que 1 et 2 a pu être ouvert sur un fichier par une commande antérieure.
        "ls >&3",
        "ls 2>&3",
        "ls 1>&9",
        "ls >&0",
        "ls 3>fichier",
        "ls 2>fichier",
        # Une cible que le classement ne lit pas.
        "ls > $CIBLE",
        "ls > ${CIBLE}",
        "ls > $(mktemp)",
        "ls > `mktemp`",
        "ls 2>$JOURNAL",
        "ls >&$FD",
        # Une redirection sans fichier à côté d'une qui écrit.
        "ls 2>&1 > fichier",
        "ls > fichier 2>&1",
        "ls 2>&1>fichier",
        "ls >/dev/null >fichier",
        "ls >/dev/null 2>fichier",
        "ls 2>&1 | tee copie",
        "cat <<EOF > fichier 2>&1\nx\nEOF",
    ],
)
def test_toute_autre_redirection_reste_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        "rm fichier 2>/dev/null",
        "rm -f fichier >/dev/null 2>&1",
        "git commit -m x 2>&1",
        "git push origin main 2>&1 | tail -3",
        "touch drapeau >/dev/null 2>&1",
        "mkdir dossier 2>/dev/null || true",
        "sed -i s/a/b/ f 2>/dev/null",
        "python script.py 2>&1 | tail -5",
        "programme-inconnu 2>/dev/null",
        "rtk git add -A 2>&1",
        "timeout 30 mv a b 2>/dev/null",
        "bash -c 'rm fichier 2>/dev/null'",
        "bash -c 'git status 2>&1 ; cp a b 2>/dev/null'",
        "ls 2>/dev/null; rm fichier",
        "ls >/dev/null && touch drapeau",
        "gh pr merge 12 --squash 2>&1",
        "curl -s -X POST http://localhost:8765/x 2>/dev/null",
    ],
)
def test_une_ecriture_redirigee_sans_fichier_reste_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "2>&1" script.sh',
        "grep -n '>/dev/null' script.sh",
        'echo "a > b"',
        'grep -c "x >/dev/null y" notes.md',
    ],
)
def test_une_redirection_citee_entre_guillemets_est_une_donnee(command: str) -> None:
    """Non-régression : ces formes étaient déjà des lectures."""
    assert _classe(command) is MutationClass.READ_ONLY


# ── Antislash dans un motif entre guillemets doubles ─────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "a\\|b" f',
        'grep -rn "def \\|class " src',
        'grep -n "a\\|b" f | head -5',
        'grep -c "\\"cle\\"" donnees.json',
        'grep -En "a\\.b|c" f',
        'git log --grep="fix\\|feat" --oneline',
        'git grep -n "a\\|b" -- src',
        'rtk grep -n "rename\\|\\.bak" src/x.py',
        'rtk proxy grep -rn "_estime\\|Usage(" src/',
        'timeout 5 grep -n "a\\|b" f',
        'cd src && grep -rn "a\\|b" . | wc -l',
        'grep -rn "a\\|b" src 2>/dev/null | head -20',
        "bash -c 'grep -n \"a\\|b\" f'",
        'find . -name "a\\*b"',
        'echo "a\\\\b; c"',
    ],
)
def test_un_motif_avec_antislash_entre_guillemets_doubles_est_une_donnee(command: str) -> None:
    assert _classe(command) is MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        'grep -n "a\\|b" f > sortie',
        'grep -n "a\\|b" f; rm fichier',
        'grep -n "a\\|b" f | tee copie',
        'grep -n "a\\|b" f && touch drapeau',
        'sed -i "s/a\\/b/c/" f',
        'sed -n "s/a\\/b/c/w sortie" f',
        'awk "{print \\$1 > \\"sortie\\"}" f',
        'git commit -m "a\\|b"',
        'rm "a\\|b"',
        'find . -name "a\\|b" -delete',
        'echo "$(rm fichier)\\|b"',
        'echo "`rm fichier`\\|b"',
        'bash -c "grep \\"a\\" f"',  # script entre guillemets doubles avec antislash : non relu
        'python3 -c "print(\\"a\\")"',
        'programme-inconnu "a\\|b"',
    ],
)
def test_une_ecriture_avec_un_motif_a_antislash_reste_une_mutation(command: str) -> None:
    assert _classe(command) is not MutationClass.READ_ONLY


@pytest.mark.parametrize(
    "command",
    [
        'echo \\" ; rm fichier ; echo "a"',
        'echo \\" ; rm a\\ b ; echo "c"',
        "echo \\' ; touch drapeau ; echo 'a'",
        'grep -n \\" f ; mv a b ; grep -n "x\\|y" f',
    ],
)
def test_un_guillemet_echappe_hors_guillemets_n_ouvre_pas_un_texte_cite(command: str) -> None:
    """Lire les guillemets comme le shell vaut dans les deux sens : ce qui suit reste inspecté."""
    assert _classe(command) is not MutationClass.READ_ONLY


def test_un_guillemet_echappe_seul_reste_une_lecture() -> None:
    assert _classe('grep -n \\"motif\\" notes.md') is MutationClass.READ_ONLY


def test_la_surface_partagee_avec_la_detection_destructive_ne_change_pas() -> None:
    """La correction porte sur le chemin lecture seul : faux positifs destructifs compris."""
    facts = classify_tool("Bash", {"command": 'echo "x\\n rm -rf y"'})
    assert facts.mutation is MutationClass.DESTRUCTIVE
    assert facts.destructive_reason == "recursive/forced delete"
    # La surface publique garde le motif à antislash tel quel, comme sur main.
    assert command_surface('grep -n "a\\|b" f') == 'grep -n "a\\|b" f'
    assert command_surface("ls 2>&1 | tail") == "ls 2>&1 | tail"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build 2>/dev/null",
        "rm -rf build >/dev/null 2>&1",
        "bash -c 'rm -rf build 2>/dev/null'",
        "git reset --hard 2>&1",
        'rm -rf "a\\|b"',
    ],
)
def test_la_classe_destructive_ne_change_pas(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is MutationClass.DESTRUCTIVE
    assert facts.destructive_reason


# ── De bout en bout : le compteur de la session et Stop ──────────────────────


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


_NOM_HOTE = {HookEvent.POST_TOOL_USE: "PostToolUse", HookEvent.STOP: "Stop"}


def _hook(root: Path, event: HookEvent, **extra: Any) -> Any:
    payload = {"session_id": SESSION, "hook_event_name": _NOM_HOTE[event], "cwd": str(root), **extra}
    _, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return decision


def _post_bash(root: Path, command: str) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        tool_name="Bash",
        tool_input={"command": command},
        tool_response={"stdout": "", "exit_code": 0},
    )


SESSION_DE_LECTURE = [
    "rtk git status 2>&1 | tail -3",
    "rtk ls dossier 2>/dev/null",
    'rtk grep -rn "def \\|class " src 2>/dev/null | head -20',
    'grep -n "a\\|b" notes.md',
    "cd src && rtk git log --oneline -5 2>&1 | head -3",
    "timeout 30 journalctl --user -u grimoire-tick -n 20 2>&1 | tail -5",
    "bash -c 'git status 2>&1 ; ls 2>/dev/null'",
    "rtk gh pr checks 1229 2>&1 | tail -20",
    "cat absent.md 2>/dev/null || true",
    "curl -s http://localhost:8765/health >/dev/null 2>&1",
]


def test_des_lectures_redirigees_laissent_le_compteur_a_zero_et_stop_ne_bloque_pas(governed: Path) -> None:
    for command in SESSION_DE_LECTURE:
        _post_bash(governed, command)
    assert session_mutations(governed, SESSION) == 0

    decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.ALLOW, decision.reason
    assert "blocked_on" not in decision.detail


@pytest.mark.parametrize(
    "ecriture",
    [
        "rtk git commit -m x 2>&1 | tail -3",
        "touch drapeau 2>/dev/null",
        "rtk ls > liste.txt 2>&1",
        'grep -n "a\\|b" notes.md > extrait.txt',
    ],
)
def test_une_seule_ecriture_redirigee_fait_bloquer_stop_comme_avant(governed: Path, ecriture: str) -> None:
    for command in SESSION_DE_LECTURE:
        _post_bash(governed, command)
    _post_bash(governed, ecriture)
    assert session_mutations(governed, SESSION) == 1

    decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK
    assert decision.detail["blocked_on"] == "no_active_task"
    assert decision.detail["mutations"] == 1
