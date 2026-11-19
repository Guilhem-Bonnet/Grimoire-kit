"""Faux blocages au ``Stop`` et rappels répétés : la garde exacte, dite une fois.

Constats mesurés sur 14 jours d'un poste réel (profil ``governed``) :

1. 185 refus « Clôture hors tâche » ; dans une session qui n'avait fait que lire et
   lancer des sous-agents de lecture, le compteur d'écritures montait de 4 à 65. Il
   comptait les commandes des sous-agents (``find | xargs grep``, ``pytest``…), les
   écritures du brouillon hors projet par redirection, les commandes de vérification
   et les outils de tenue de session (``TodoWrite``).
2. Le rappel « Aucune tâche du Mission Ledger n'est en cours… » réinjecté à chaque
   ``UserPromptSubmit`` (627 fois) ; l'avertissement « gate fini, shadow » redit à
   chaque ``Stop`` sans nouvelle mutation.
3. « Écriture enregistrée… » à chaque écriture, « code python interprété… » à chaque
   commande interprétée, doublé en ``systemMessage``.

Chaque test ci-dessous a d'abord été écrit rouge.
"""

from __future__ import annotations

import io
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    build_file_write_event,
)
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.service import TaskService
from grimoire.policies.session_state import say_once, session_mutations, session_state_path

SESSION = "faux0000-0000-4000-8000-000000000001"
SUB = "agent-lecture-1"
NO_TASK = "Aucune tâche du Mission Ledger"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_DONE_GATE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return root


@pytest.fixture
def scratch() -> Iterator[Path]:
    """Un brouillon hors projet dans un dossier neutre : aucun mot de vérification dans son chemin."""
    with tempfile.TemporaryDirectory(prefix="brouillon-") as raw:
        path = Path(raw)
        assert "pytest" not in str(path)
        yield path


def _hook(root: Path, event: HookEvent, **extra: Any) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": SESSION, "hook_event_name": event.value, "cwd": str(root), **extra}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _post(root: Path, tool: str, tool_input: dict[str, Any], *, agent: str = "") -> tuple[dict[str, Any], Any]:
    extra: dict[str, Any] = {"tool_name": tool, "tool_input": tool_input, "tool_response": {"exit_code": 0}}
    if agent:
        extra |= {"agent_id": agent, "agent_type": "Explore"}
    return _hook(root, HookEvent.POST_TOOL_USE, **extra)


def _bash(root: Path, command: str, *, agent: str = "") -> tuple[dict[str, Any], Any]:
    return _post(root, "Bash", {"command": command}, agent=agent)


def _stop(root: Path) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.STOP, stop_hook_active=False)


def _prompt(root: Path) -> str:
    rendered, _ = _hook(root, HookEvent.USER_PROMPT_SUBMIT, prompt="regarde ça")
    return str(rendered.get("hookSpecificOutput", {}).get("additionalContext", ""))


# ── 1. le compteur ne compte que les écritures réelles dans le projet ─────────


def test_une_session_de_lectures_et_de_sous_agents_de_lecture_compte_zero_ecriture(governed: Path, scratch: Path) -> None:
    # La session principale lit, délègue, tient sa liste de tâches, écrit son brouillon hors projet.
    _post(governed, "Read", {"file_path": str(governed / "README.md")})
    _post(governed, "Agent", {"prompt": "cherche X", "subagent_type": "Explore"})
    _post(governed, "TodoWrite", {"todos": [{"content": "lire", "status": "pending"}]})
    _bash(governed, f"cat > {scratch}/notes.md <<'EOF'\nvoir src/app.py\nEOF")
    _bash(governed, "python -m pytest tests -q")
    _bash(governed, "ruff check src")
    # Le sous-agent de lecture : des commandes que le classement lecture seule ne reconnaît pas toutes.
    for command in (
        "python -m pytest tests/unit -q",
        "ruff check src",
        f"grep -rn foo src > {scratch}/hits.txt",
        "cd src && grep -rn foo .",
    ):
        _bash(governed, command, agent=SUB)
    _post(governed, "Write", {"file_path": str(scratch / "sub.md"), "content": "x"}, agent=SUB)

    assert session_mutations(governed, SESSION) == 0
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.ALLOW
    assert "decision" not in rendered


def test_une_ecriture_reelle_d_un_sous_agent_dans_le_projet_compte(governed: Path) -> None:
    _post(governed, "Edit", {"file_path": str(governed / "src/app.py"), "old_string": "a", "new_string": "b"}, agent=SUB)
    _bash(governed, "echo x > src/genere.py", agent=SUB)
    assert session_mutations(governed, SESSION) == 2


def test_la_session_principale_reste_comptee_sur_une_commande_qui_peut_ecrire(governed: Path, scratch: Path) -> None:
    """Fail-closed pour la session elle-même : ce qu'on ne sait pas lire compte."""
    _bash(governed, "git commit -m x")
    _bash(governed, "make build")
    _bash(governed, f"cp {scratch}/a.py src/a.py")
    assert session_mutations(governed, SESSION) == 3


# ── 1bis. après un refus, la même clôture devient un avertissement ───────────


def _edit(root: Path, name: str) -> None:
    _post(root, "Edit", {"file_path": str(root / f"src/{name}.py"), "old_string": "a", "new_string": "b"})


def test_au_seuil_le_refus_complet_revient(governed: Path) -> None:
    """Le gate n'est pas désarmé : dix écritures après le refus, il refuse de nouveau."""
    _edit(governed, "a")
    assert _stop(governed)[1].outcome is Outcome.BLOCK
    for i in range(9):
        _edit(governed, f"f{i}")
    assert _stop(governed)[1].outcome is Outcome.ALLOW
    _edit(governed, "f9")
    _, again = _stop(governed)
    assert again.outcome is Outcome.BLOCK and again.detail["mutations"] == 11
    assert "grimoire task add" in again.reason
    _edit(governed, "g")
    assert _stop(governed)[1].outcome is Outcome.ALLOW, "le palier repart du dernier refus"


def test_le_seuil_se_regle_dans_le_profil(governed: Path) -> None:
    profile = governed / "_grimoire/standard/standard-profile.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").rstrip("\n") + "\noptions:\n  unattached_reblock_after: 2\n",
        encoding="utf-8",
    )
    _edit(governed, "a")
    assert _stop(governed)[1].outcome is Outcome.BLOCK
    _edit(governed, "b")
    assert _stop(governed)[1].outcome is Outcome.ALLOW
    _edit(governed, "c")
    assert _stop(governed)[1].outcome is Outcome.BLOCK


def test_un_journal_reecrit_pour_acquitter_ne_desarme_pas_le_refus(governed: Path) -> None:
    _edit(governed, "a")
    path = session_state_path(governed, SESSION)
    data = json.loads(path.read_text(encoding="utf-8"))
    data |= {"acknowledged_mutations": 10_000, "no_task_blocked_at": 10_000}
    path.write_text(json.dumps(data), encoding="utf-8")
    _, decision = _stop(governed)
    assert decision.outcome is Outcome.BLOCK


def test_sous_le_seuil_le_stop_suivant_avertit_sans_bloquer(governed: Path) -> None:
    _post(governed, "Edit", {"file_path": str(governed / "src/app.py"), "old_string": "a", "new_string": "b"})
    _, first = _stop(governed)
    assert first.outcome is Outcome.BLOCK and first.detail["blocked_on"] == "no_active_task"

    _, quiet = _stop(governed)
    assert quiet.outcome is Outcome.ALLOW and "hors tâche" not in quiet.context

    _post(governed, "Edit", {"file_path": str(governed / "src/b.py"), "old_string": "a", "new_string": "b"})
    rendered, second = _stop(governed)
    assert second.outcome is Outcome.ALLOW
    assert "decision" not in rendered
    assert second.detail["blocked_on"] == "no_active_task" and second.detail["mutations"] == 2
    assert "grimoire task add" not in second.context, "le remède a déjà été donné : pas recopié"
    assert "hors tâche" in rendered["systemMessage"]


def test_le_rappel_tache_proposed_du_stop_n_est_dit_qu_une_fois(governed: Path) -> None:
    rendered, _ = _stop(governed)
    assert "encore en état" in rendered["systemMessage"]
    rendered, again = _stop(governed)
    assert again.outcome is Outcome.ALLOW and "systemMessage" not in rendered


# ── 2. UserPromptSubmit : le rappel « aucune tâche » dit une fois ─────────────


def test_le_rappel_aucune_tache_n_est_injecte_qu_au_premier_prompt(governed: Path) -> None:
    assert NO_TASK in _prompt(governed)
    assert NO_TASK not in _prompt(governed)
    assert NO_TASK not in _prompt(governed)


def test_le_rappel_revient_quand_l_etat_change(governed: Path) -> None:
    assert NO_TASK in _prompt(governed)
    service = TaskService(governed)
    added = service.add("Un sujet", ("c'est fait",), owner="claude", actor="claude", ready=True)
    service.context(added.task.id)
    service.claim(added.task.id, "claude", host="local")
    after = _prompt(governed)
    assert f"Tâche courante : {added.task.id}" in after
    assert "Tâche courante" not in _prompt(governed)


def test_le_session_start_compte_comme_la_premiere_fois(governed: Path) -> None:
    rendered, _ = _hook(governed, HookEvent.SESSION_START, source="startup")
    assert NO_TASK in rendered["hookSpecificOutput"]["additionalContext"]
    assert NO_TASK not in _prompt(governed)


# ── 3. PostToolUse / PreToolUse : rappels identiques dits une fois ───────────


def test_le_rappel_de_preuve_n_est_rendu_qu_a_la_premiere_ecriture_de_la_tache(governed: Path) -> None:
    first, _ = _post(governed, "Edit", {"file_path": str(governed / "a.py"), "old_string": "a", "new_string": "b"})
    second, _ = _post(governed, "Edit", {"file_path": str(governed / "b.py"), "old_string": "a", "new_string": "b"})
    assert "Écriture enregistrée" in first["hookSpecificOutput"]["additionalContext"]
    assert "Écriture enregistrée" not in str(second)


def test_un_outil_de_tenue_de_session_n_a_pas_de_rappel_de_preuve(governed: Path) -> None:
    rendered, _ = _post(governed, "TodoWrite", {"todos": []})
    assert "Écriture enregistrée" not in str(rendered)


def test_l_avertissement_de_code_interprete_est_dit_une_fois_et_sans_doublon(governed: Path) -> None:
    command = "python3 - <<'EOF'\nprint('hello')\nEOF\n"
    first, _ = _hook(governed, HookEvent.PRE_TOOL_USE, tool_name="Bash", tool_input={"command": command})
    second, _ = _hook(governed, HookEvent.PRE_TOOL_USE, tool_name="Bash", tool_input={"command": command + "# 2\n"})
    assert "interprété" in first["hookSpecificOutput"]["additionalContext"]
    assert "systemMessage" not in first, "l'agent le reçoit déjà : pas de doublon pour l'humain"
    assert "interprété" not in str(second)


# ── 2bis. Stop : l'avertissement « gate fini, shadow » dit une fois par mutation ─


def _done_gate_project(root: Path) -> Path:
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    board = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == "bootstrap":
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board.write_text(stream.getvalue(), encoding="utf-8")
    scaffold_task_artifacts(root, task_id="bootstrap")
    return root


def _gate_stop(root: Path) -> Any:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id=SESSION))


def test_l_avertissement_shadow_n_est_redit_qu_apres_une_nouvelle_mutation(tmp_path: Path) -> None:
    root = _done_gate_project(tmp_path)
    append_evidence_event(root, "bootstrap", build_bash_event("pytest -q", exit_code=0))
    append_evidence_event(root, "bootstrap", build_file_write_event(str(root / "src" / "foo.py")))
    first = _gate_stop(root)
    assert "postérieure au dernier check vert" in first.context
    again = _gate_stop(root)
    assert "postérieure" not in again.context
    assert again.detail["done_gate"]["stale"] is True, "le verdict reste tracé, seul le rappel se tait"

    append_evidence_event(root, "bootstrap", build_file_write_event(str(root / "src" / "bar.py")))
    assert "postérieure au dernier check vert" in _gate_stop(root).context


# ── say_once : bornes ────────────────────────────────────────────────────────


def test_say_once_sans_session_dit_toujours(tmp_path: Path) -> None:
    assert say_once(tmp_path, "", "k", "x", now_iso="t") is True
    assert say_once(tmp_path, "", "k", "x", now_iso="t") is True
    assert not (tmp_path / "_grimoire-output").exists()


def test_say_once_redit_quand_l_etat_change(tmp_path: Path) -> None:
    assert say_once(tmp_path, "s", "k", "a", now_iso="t") is True
    assert say_once(tmp_path, "s", "k", "a", now_iso="t") is False
    assert say_once(tmp_path, "s", "k", "b", now_iso="t") is True
    assert say_once(tmp_path, "s", "autre", "b", now_iso="t") is True


# ── 4. `cd` n'est jamais refusé pour lui-même ────────────────────────────────


@pytest.mark.parametrize(
    "suffix",
    ["git status", "git log --oneline -3", "python -m pytest -q", "echo x > a.txt", "git commit -m x"],
)
def test_cd_vers_le_projet_ou_son_worktree_n_est_pas_refuse(governed: Path, suffix: str) -> None:
    worktree = governed / ".claude/worktrees/x"
    worktree.mkdir(parents=True)
    for where in (governed, worktree):
        _, decision = _hook(
            governed, HookEvent.PRE_TOOL_USE, tool_name="Bash", tool_input={"command": f"cd {where} && {suffix}"}
        )
        assert decision.outcome is Outcome.ALLOW, decision.reason


def test_un_refus_derriere_un_cd_vient_de_la_commande_qui_suit(governed: Path) -> None:
    """Les refus journalisés « bash:cd » portent la clé du premier mot ; le motif est la suite."""
    _, decision = _hook(
        governed, HookEvent.PRE_TOOL_USE, tool_name="Bash", tool_input={"command": f"cd {governed} && rm -rf build"}
    )
    assert decision.outcome is Outcome.DENY
    assert "recursive/forced delete" in decision.reason
