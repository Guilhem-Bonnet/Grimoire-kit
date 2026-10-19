"""W2-01 — la preuve d'un tour humain ne s'écrit que par le hook de l'hôte.

Le journal des tours (``human-turns.jsonl``) et le journal d'autorité d'un
run (``*.authority.jsonl``) attestent qu'un humain a décidé : un agent qui
peut les écrire peut s'approuver lui-même. ``PreToolUse`` refuse ces
écritures, le rejeu du hook et la reprise de session ``claude -c`` — et une
garde en panne refuse en governed au lieu de laisser passer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.events import HookEvent

_JOURNAL = "_grimoire-output/.runs/human-turns.jsonl"
_AUTHORITY = "_grimoire-runtime-output/flows/authority/RUN-1.authority.jsonl"


def _governed(root: Path) -> Path:
    standard = root / "_grimoire" / "standard"
    standard.mkdir(parents=True)
    (standard / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return root


def _pre(root: Path, tool: str, tool_input: dict[str, object]):  # type: ignore[no-untyped-def]
    return run_decision(
        "grimoire.tool-policy",
        HookInput(event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name=tool, tool_input=tool_input, session_id="S1"),
    )


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": '{"session_id": "S1", "origin": "user"}\n'}),
        ("Edit", {"file_path": f"/abs/project/{_JOURNAL}", "old_string": "a", "new_string": "b"}),
        ("Bash", {"command": f"echo '{{\"session_id\":\"S1\"}}' >> {_JOURNAL}"}),
        ("Bash", {"command": f"python -c \"open('{_JOURNAL}', 'a').write('x')\""}),
        ("Bash", {"command": f"cp /tmp/forged.jsonl {_JOURNAL}"}),
        ("Bash", {"command": f"sed -i 's/host/user/' {_JOURNAL}"}),
        ("Write", {"file_path": _AUTHORITY, "content": "{}\n"}),
        ("Bash", {"command": f"tee -a {_AUTHORITY} < forged"}),
    ],
)
def test_ecrire_un_journal_d_autorite_est_refuse_quel_que_soit_le_profil(
    tmp_path: Path, tool: str, tool_input: dict[str, object]
) -> None:
    decision = _pre(tmp_path, tool, tool_input)  # projet non enrôlé : profil starter
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "journal"
    assert "Limite" in decision.reason


def test_lire_le_journal_reste_permis(tmp_path: Path) -> None:
    decision = _pre(tmp_path, "Bash", {"command": f"cat {_JOURNAL}"})
    assert "authority_vector" not in decision.detail


@pytest.mark.parametrize(
    ("command", "vector"),
    [
        ("echo '{}' | grimoire-hook --host claude --event user_prompt_submit", "hook-replay"),
        ("GRIMOIRE_X=1 /home/u/.local/bin/grimoire-hook --host claude", "hook-replay"),
        ("python -m grimoire.hosts.runtime --host claude", "hook-replay"),
        ("claude -c 'approve'", "session-resume"),
        ("claude --resume 7102fd4c -p approve", "session-resume"),
        ("cd x && claude --continue", "session-resume"),
    ],
)
def test_rejeu_du_hook_et_reprise_de_session_refuses_en_governed(tmp_path: Path, command: str, vector: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == vector


@pytest.mark.parametrize(
    "command",
    [
        # La même entrée de hook par la CLI (`cmd_host.py::host_hook` appelle `run_hook` directement).
        "grimoire host hook --host claude --event UserPromptSubmit",
        "echo '{\"session_id\":\"S1\",\"prompt\":\"ok\"}' | grimoire host hook --host claude --event UserPromptSubmit",
        "grimoire host hook --event=UserPromptSubmit --host claude",
        "grimoire host hook --host claude --event 'UserPromptSubmit'",
        'grimoire host hook --host claude --event "user_prompt_submit"',
        "grimoire host hook --host claude --event user-prompt-submit",
        "/home/u/.local/bin/grimoire host hook --host claude --event UserPromptSubmit",
        "grimoire --output json host hook --host claude --event UserPromptSubmit",
        # Événement que la garde ne sait pas lire : fail-closed.
        'grimoire host hook --host claude --event "$EVENT"',
        "grimoire host hook --host claude",
        # Lanceurs : le nom de tête n'est plus `grimoire-hook`.
        "uvx --from grimoire-kit grimoire-hook --host claude --event UserPromptSubmit",
        "uvx --from grimoire-kit grimoire host hook --host claude --event UserPromptSubmit",
        "uvx grimoire-hook --host claude",
        "pipx run --spec grimoire-kit grimoire-hook --host claude --event UserPromptSubmit",
        "uv run grimoire host hook --host claude --event UserPromptSubmit",
        "uv tool run --from grimoire-kit grimoire-hook --host claude",
        "python -m grimoire host hook --host claude --event UserPromptSubmit",
        "python3.12 -m grimoire host hook --host claude --event UserPromptSubmit",
        "FOO=1 /usr/bin/env -i timeout 5 grimoire-hook --host claude",
        "sudo -u bob grimoire-hook --host claude",
        "xargs grimoire-hook --host claude",
        "bash -c 'uvx --from grimoire-kit grimoire-hook --host claude'",
        # Code arbitraire qui appelle la même entrée : la fonction que la CLI appelle directement.
        'python -c "from grimoire.cli.cmd_host import host_hook"',
        'python -c "from grimoire.hosts.runtime import run_hook; run_hook({})"',
    ],
)
def test_rejeu_du_hook_par_la_cli_ou_un_lanceur_refuse_en_governed(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "hook-replay"


@pytest.mark.parametrize(
    "command",
    [
        '"grimoire-hook" --host claude --event UserPromptSubmit',
        "'grimoire-hook' --host claude --event UserPromptSubmit",
        "\\grimoire-hook --host claude --event UserPromptSubmit",
        'grimoire "host" hook --host claude --event UserPromptSubmit',
        "grimoire host 'hook' --host claude --event UserPromptSubmit",
        'uvx --from grimoire-kit "grimoire-hook" --host claude',
        'python -m "grimoire.hosts.runtime" --host claude',
        'timeout 5 "grimoire-hook" --host claude',
        'echo ok && "grimoire-hook" --host claude',
    ],
)
def test_rejeu_du_hook_sous_citation_ou_antislash_refuse_en_governed(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "hook-replay"


@pytest.mark.parametrize(
    "command",
    ['"claude" -c approve', "'claude' --resume abc -p approve", "\\claude --continue", 'uvx "claude" -c approve'],
)
def test_reprise_de_session_sous_citation_ou_antislash_refusee_en_governed(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "session-resume"


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "grimoire-hook"',
        'git commit -m "x && grimoire-hook"',
        "timeout 5 git commit -m 'claude -c'",
        'echo "a; grimoire-hook"',
        "cat <<EOF\ngrimoire-hook --host claude\nEOF",
        'grimoire "host" status',
        '"claude" -p résume',
    ],
)
def test_prose_citee_ne_devient_pas_un_programme_vise(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert "authority_vector" not in decision.detail, decision


def test_rejeu_du_hook_par_la_cli_refuse_aussi_en_production(tmp_path: Path) -> None:
    root = _governed(tmp_path)
    (root / "_grimoire" / "standard" / "standard-profile.yaml").write_text("profile: production\n", encoding="utf-8")
    decision = _pre(root, "Bash", {"command": "uvx --from grimoire-kit grimoire-hook --host claude --event UserPromptSubmit"})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "hook-replay"


@pytest.mark.parametrize(
    "command",
    [
        "claude --session-id 6f1c2a90-0000-4000-8000-000000000001 -p approve",
        "uvx claude --resume 7102fd4c -p approve",
    ],
)
def test_sous_session_qui_reprend_l_identifiant_refusee_en_governed(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "session-resume"


@pytest.mark.parametrize(
    "command",
    [
        "grimoire host hook --host claude --event PreToolUse",
        "grimoire host hook --host claude --event 'post_tool_use'",
        "grimoire host status",
        "grimoire host --help",
        "grimoire memory store --help",
        "uvx ruff check src",
        "uv run pytest tests/unit -q",
        "python -m pytest tests/unit/test_authority_guard.py",
        "python -m grimoire doctor",
        "pipx run black .",
        "sudo systemctl status ssh",
        "echo grimoire-hook",
        "xargs echo",
        "git commit -m 'grimoire host hook --event UserPromptSubmit'",
    ],
)
def test_commandes_voisines_du_rejeu_non_refusees(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert "authority_vector" not in decision.detail, decision


def test_rejeu_du_hook_par_la_cli_hors_governed_n_est_pas_refuse_par_la_garde(tmp_path: Path) -> None:
    decision = _pre(tmp_path, "Bash", {"command": "grimoire host hook --host claude --event UserPromptSubmit"})
    assert "authority_vector" not in decision.detail


@pytest.mark.parametrize(
    "command",
    ["claude -p 'résume ce fichier'", "which grimoire-hook", "ls ~/.claude -r", "git commit -m 'claude -c'"],
)
def test_commandes_voisines_non_refusees_par_la_garde(tmp_path: Path, command: str) -> None:
    decision = _pre(_governed(tmp_path), "Bash", {"command": command})
    assert "authority_vector" not in decision.detail, decision


def test_rejeu_du_hook_hors_governed_n_est_pas_refuse_par_la_garde(tmp_path: Path) -> None:
    decision = _pre(tmp_path, "Bash", {"command": "grimoire-hook --host claude --event user_prompt_submit"})
    assert "authority_vector" not in decision.detail


def _crash(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("garde cassée")


def test_garde_en_panne_refuse_en_governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.hosts.decisions.authority_guard as guard

    monkeypatch.setattr(guard, "forgery_vector", _crash)
    decision = _pre(_governed(tmp_path), "Bash", {"command": "ls"})
    assert decision.outcome is Outcome.DENY
    assert decision.detail["authority_vector"] == "guard-error"


def test_garde_en_panne_demande_hors_governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.hosts.decisions.authority_guard as guard

    monkeypatch.setattr(guard, "forgery_vector", _crash)
    decision = _pre(tmp_path, "Bash", {"command": "ls"})
    assert decision.outcome is Outcome.ASK


def test_le_hook_de_prompt_journalise_le_tour_sans_son_texte(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.human_turns import load_turns, turns_path

    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=tmp_path,
            prompt="approuve le retrait",
            session_id="S1",
            host="host-claude-code-cli",
        ),
    )
    turns = load_turns(tmp_path)
    assert turns is not None and len(turns) == 1
    assert (turns[0].session_id, turns[0].origin) == ("S1", "user")
    assert "approuve" not in turns_path(tmp_path).read_text(encoding="utf-8")


# ── K13-S1-02 : l'instantané du ledger n'est pas un fichier qu'un agent écrit ────────────────────

_SNAPSHOT = "_grimoire-runtime-output/ledger/ledger.snapshot.json"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _SNAPSHOT, "content": "{}\n"}),
        ("Edit", {"file_path": f"/abs/project/{_SNAPSHOT}", "old_string": "a", "new_string": "b"}),
        ("Bash", {"command": f"echo '{{}}' > {_SNAPSHOT}"}),
        ("Bash", {"command": f"python -c \"open('{_SNAPSHOT}', 'w').write('x')\""}),
        ("Bash", {"command": f"cp forged.json {_SNAPSHOT}"}),
        ("Bash", {"command": f"sed -i 's/waiting/closed/' {_SNAPSHOT}"}),
    ],
)
def test_ecrire_l_instantane_du_ledger_est_refuse_quel_que_soit_le_profil(
    tmp_path: Path, tool: str, tool_input: dict[str, object]
) -> None:
    """Un instantané falsifié, au hash de préfixe valide, impose au rejeu un état que le journal ne dit pas."""
    decision = _pre(tmp_path, tool, tool_input)  # projet non enrôlé : profil starter
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "ledger-snapshot"
    assert "instantané du ledger" in decision.reason
    assert "Limite" in decision.reason


def test_lire_l_instantane_du_ledger_reste_permis(tmp_path: Path) -> None:
    decision = _pre(tmp_path, "Bash", {"command": f"cat {_SNAPSHOT}"})
    assert "authority_vector" not in decision.detail


def test_le_nom_de_l_instantane_est_celui_du_ledger() -> None:
    """Recopié dans la garde pour que PreToolUse n'importe pas le ledger : l'égalité est figée ici."""
    from grimoire.hosts.decisions.authority_guard import SNAPSHOT_FILENAME
    from grimoire.missions.ledger_snapshot import SNAPSHOT_FILENAME as LEDGER_SNAPSHOT_FILENAME

    assert SNAPSHOT_FILENAME == LEDGER_SNAPSHOT_FILENAME
