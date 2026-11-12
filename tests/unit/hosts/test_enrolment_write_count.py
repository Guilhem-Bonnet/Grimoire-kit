"""Le hook de clôture ne compte que les écritures réelles dans le projet.

Constat du 2026-11-12, profil ``governed`` : une session d'analyse en lecture
seule (``ls``, ``grep``, ``git log``, ``gh issue list``, ``python`` qui lit un
fichier, sous-agents en lecture) a été comptée à « 116 action(s) d'écriture »
et le ``Stop`` a bloqué neuf clôtures de suite avec « Clôture hors tâche ».

Le compteur est celui de ``PostToolUse`` (:mod:`grimoire.hosts.decisions.
evidence_trace` → :func:`grimoire.policies.session_state.note_session_mutation`),
lu par ``Stop`` (:mod:`grimoire.hosts.decisions.evidence_gate`). Le constat est
déjà corrigé sur ``main`` par #875 (lectures Bash et écritures hors projet) ;
ce fichier verrouille le contrat, par les vrais payloads d'hôte et le vrai
chemin ``run_hook``, pour qu'une régression ne repasse pas :

1. comptent : les outils d'édition de fichier (``Edit``, ``Write``,
   ``NotebookEdit``) visant un chemin sous la racine du projet, et une
   commande ``Bash`` que le classement existant juge écrivante ;
2. ne comptent pas : une commande ``Bash`` en lecture seule, une écriture hors
   de la racine du projet, les commandes en lecture d'un sous-agent ;
3. une session sans écriture réelle se clôt sans blocage ni message ;
4. une session avec une vraie écriture hors tâche garde le blocage actuel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.policies.session_state import session_mutations

SESSION = "a7c4e1f0-0000-4000-8000-0000000000c1"
SUBAGENT = "a7c4e1f0-0000-4000-8000-0000000000c2"


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un projet réellement enrôlé en profil ``governed`` (le compteur y est actif)."""
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _payload(root: Path, event: str, **extra: Any) -> dict[str, Any]:
    """La forme exacte que Claude Code envoie sur stdin."""
    return {"session_id": SESSION, "hook_event_name": event, "cwd": str(root), **extra}


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _tool(
    root: Path,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    session_id: str = SESSION,
    agent_id: str = "",
) -> tuple[dict[str, Any], Any]:
    extra: dict[str, Any] = {"tool_name": tool_name, "tool_input": tool_input, "tool_response": {"exit_code": 0}}
    if agent_id:
        extra["agent_id"] = agent_id
    return _hook(root, HookEvent.POST_TOOL_USE, {**_payload(root, "PostToolUse", **extra), "session_id": session_id})


def _bash(root: Path, command: str, *, session_id: str = SESSION, agent_id: str = "") -> None:
    _tool(root, "Bash", {"command": command}, session_id=session_id, agent_id=agent_id)


def _stop(root: Path) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.STOP, _payload(root, "Stop", stop_hook_active=False))


# ── 1. ce qui compte comme écriture réelle ───────────────────────────────────


def test_les_outils_d_edition_sous_la_racine_comptent(governed: Path) -> None:
    """``Edit``, ``Write`` et ``NotebookEdit`` visant le projet : trois écritures."""
    _tool(governed, "Edit", {"file_path": str(governed / "src/app.py"), "old_string": "a", "new_string": "b"})
    _tool(governed, "Write", {"file_path": "docs/notes.md", "content": "x"})
    _tool(governed, "NotebookEdit", {"notebook_path": "notebooks/analyse.ipynb", "new_source": "print(1)"})
    assert session_mutations(governed, SESSION) == 3


def test_une_commande_bash_jugee_ecrivante_compte(governed: Path) -> None:
    """Le classement existant distingue lecture et écriture : ``sed -i`` et un commit écrivent."""
    _bash(governed, "sed -i 's/a/b/' src/app.py")
    _bash(governed, "git commit -m x")
    assert session_mutations(governed, SESSION) == 2


# ── 2. ce qui ne compte pas ──────────────────────────────────────────────────


def test_les_commandes_bash_de_lecture_ne_comptent_pas(governed: Path) -> None:
    """Les commandes du constat, plus les formes que le classement sait reconnaître."""
    for command in (
        "ls -la",
        "grep -rn foo .",
        "git log --oneline -5",
        "git status",
        "gh issue list --limit 5",
        "sed -n 1,20p README.md",
        "awk '{print $1}' f | sort | uniq -c",
        "find . -name '*.py'",
    ):
        _bash(governed, command)
    for tool_name, tool_input in (
        ("Read", {"file_path": str(governed / "README.md")}),
        ("Grep", {"pattern": "foo"}),
        ("Glob", {"pattern": "**/*.py"}),
    ):
        _tool(governed, tool_name, tool_input)
    assert session_mutations(governed, SESSION) == 0


def test_une_ecriture_hors_du_projet_ne_compte_pas(governed: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Brouillon sous le temporaire, mémoire de l'hôte : ce n'est pas du travail du projet."""
    elsewhere = tmp_path_factory.mktemp("hors-projet")
    _tool(governed, "Write", {"file_path": str(elsewhere / "brouillon.md"), "content": "x"})
    _tool(governed, "Edit", {"file_path": str(elsewhere / "memoire.md"), "old_string": "a", "new_string": "b"})
    assert session_mutations(governed, SESSION) == 0


def test_les_lectures_d_un_sous_agent_ne_comptent_pas(governed: Path) -> None:
    """Un sous-agent hérite du ``session_id`` : ses lectures ne pèsent pas sur la clôture du parent."""
    _tool(governed, "Read", {"file_path": str(governed / "README.md")}, agent_id=SUBAGENT)
    _bash(governed, "rg 'def main' src/", agent_id=SUBAGENT)
    _bash(governed, "git log --oneline -5", agent_id=SUBAGENT)
    assert session_mutations(governed, SESSION) == 0


# ── 3 et 4. le verdict de ``Stop`` ───────────────────────────────────────────


def test_une_session_sans_ecriture_se_cloture_sans_message_ni_blocage(governed: Path) -> None:
    """Point 3 : aucune écriture réelle ⇒ le hook de clôture n'a rien à dire ni à bloquer.

    Seul peut subsister l'avis du gate de preuve sur ``bootstrap`` encore
    ``proposed`` — il tombe pour n'importe quelle session vide, écriture ou
    pas, et ne vient pas du compteur ; l'assertion le laisse passer mais
    exige qu'aucun « Clôture hors tâche » ne soit rendu.
    """
    _bash(governed, "ls -la")
    _bash(governed, "grep -rn foo .")
    _bash(governed, "git log --oneline -5")
    _bash(governed, "gh issue list --limit 5")
    _tool(governed, "Read", {"file_path": str(governed / "README.md")}, agent_id=SUBAGENT)
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.ALLOW
    assert "decision" not in rendered
    assert "Clôture hors tâche" not in decision.reason
    assert "Clôture hors tâche" not in decision.context
    assert "blocked_on" not in decision.detail


def test_une_ecriture_reelle_hors_tache_garde_le_blocage(governed: Path) -> None:
    """Point 4 : une vraie écriture sous la racine, elle, bloque comme avant."""
    _tool(governed, "Edit", {"file_path": str(governed / "src/app.py"), "old_string": "a", "new_string": "b"})
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.BLOCK
    assert rendered["decision"] == "block"
    assert "Clôture hors tâche" in decision.reason
    assert decision.detail["blocked_on"] == "no_active_task"
    assert decision.detail["mutations"] >= 1
