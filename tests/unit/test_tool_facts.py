"""Tests for :mod:`grimoire.hosts.decisions.tool_facts` — the read/write split.

Regression for issue #643: two shapes of command wrote or deleted while being
classified read-only, so they never consumed ``per_session.max_writes`` and
left no mutation trace in the evidence pack.

1. ``find`` is a blanket read-only leading command, but ``-delete``/``-exec``
   (and siblings) turn it into a mutation or a destructive act; only
   ``-exec``/``rm`` was ever caught, via the destructive-pattern scan, not the
   read-only classifier itself.
2. :func:`_strip_heredoc_bodies` erased a heredoc's body starting right after
   the opening ``<<TAG``, on the same line — which also erases any
   redirection (``> file``, ``>> file``) that followed ``<<TAG`` on that same
   opening line, before :data:`_WRITE_REDIRECTION_RE` ever sees it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.decisions import classify_tool, command_surface
from grimoire.hosts.decisions.tool_facts import is_read_only_command

# ── find: -delete, -exec ────────────────────────────────────────────────────


def test_find_delete_is_not_read_only() -> None:
    assert is_read_only_command('find . -name "*.pyc" -delete') is False


def test_find_exec_rm_is_destructive() -> None:
    """Non-regression: already caught today, via the ``rm`` destructive pattern."""
    facts = classify_tool("Bash", {"command": "find . -exec rm -rf {} +"})
    assert facts.destructive_reason


def test_find_without_a_mutating_primary_stays_read_only() -> None:
    assert is_read_only_command('find . -name "*.py"') is True


# ── heredoc: redirection on the opening line must survive body-stripping ───


@pytest.mark.parametrize("redirect", [">", ">>"])
def test_heredoc_with_redirection_is_not_read_only(redirect: str) -> None:
    command = f"cat <<EOF {redirect} /etc/motd\nhello\nEOF"
    assert is_read_only_command(command) is False


@pytest.mark.parametrize("redirect", [">", ">>"])
def test_heredoc_body_strip_keeps_the_opening_line_redirection(redirect: str) -> None:
    command = f"cat <<EOF {redirect} /etc/motd\nhello\nEOF"
    surface = command_surface(command)
    assert f"{redirect} /etc/motd" in surface
    assert "hello" not in surface


def test_heredoc_without_redirection_documenting_a_dangerous_command_stays_read_only() -> None:
    """The case :func:`command_surface` exists to protect: data, not an action."""
    command = "cat <<EOF\nrm -rf /\nEOF"
    assert is_read_only_command(command) is True
    facts = classify_tool("Bash", {"command": command})
    assert not facts.destructive_reason


# ── non-regression: the lot-B commands issue #643 says must not move ───────


def test_plain_read_commands_stay_read_only() -> None:
    assert is_read_only_command("cat notes.md") is True
    assert is_read_only_command('grep -rn "kubectl delete" docs/') is True


def test_commit_message_quoting_a_dangerous_shape_is_not_destructive() -> None:
    facts = classify_tool("Bash", {"command": 'git commit -m "fix rm -rf bug"'})
    assert not facts.destructive_reason


def test_pipeline_through_tee_is_not_read_only() -> None:
    assert is_read_only_command("cat f | tee out") is False


# ── issue #875 : les lectures courantes ne sont pas des écritures ──────────


@pytest.mark.parametrize(
    "command",
    [
        "cd x && grep -rn y .",
        "sed -n 1,20p f",
        "awk '{print $1}' f | sort | uniq -c",
        "git grep -n x origin/main -- src",
        "command -v gh",
        "git -C repo log --oneline",
        "git remote get-url origin",
        "git branch --show-current",
        "cat f | cut -d: -f1 | tr a-z A-Z | jq .",
        "for f in a b; do cat $f; done",
        "gh pr view 12",
        "git diff --stat",
        "git log --oneline -3",
        "git show HEAD:README.md",
    ],
)
def test_lectures_courantes_sont_read_only(command: str) -> None:
    assert is_read_only_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/a/b/ f",
        "sed --in-place s/a/b/ f",
        "sed -n 's/a/b/w out' f",
        "awk -i inplace '{print}' f",
        "awk '{print > \"out\"}' f",
        "cd x && rm f",
        "sort -o out f",
        "sort f --output=out",
        "uniq in out",
        "cd x && grep y . > out",
        "for f in a b; do rm $f; done",
        "git branch -d topic",
        "git remote add o url",
        "git diff --output=out",
        "git diff --output out",
        "git log --output=out",
        "git log -p --oneline --output=/tmp/x",
        "git show HEAD --output=out",
        "git show --outpu=out HEAD",
        "git -C repo diff --out=out",
        "git format-patch -1 -o out",
        "git format-patch -1 --output-directory=out",
        "git format-patch -1 --stdout",
        "git archive -o out.tar HEAD",
        "git archive --output=out.tar HEAD",
        "git -c core.pager=./x log",  # un -c peut poser un pager, un alias ou un hook : jamais une lecture
        "git -c diff.external=./x diff",
        "git --git-dir=.git log",
        "git diff -- --output=out",
        "ls & rm f",
        "sed p f & rm f",
        "cat f |& rm f",
    ],
)
def test_formes_qui_ecrivent_restent_des_mutations(command: str) -> None:
    assert is_read_only_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "sed -n '1w out' f",
        "sed -n '$w out' f",
        "sed -n '1,2w out' f",
        "sed -n 'w/dev/stdout' f",
        "sed -e 1w -e out f",
        "sed --expression=w\\ out f",
        "sed 'wout' f",
        "sed '1e rm x' f",
        "sed 'e rm x'",
        "sed --in-pl s/a/b/ f",
        "sed -f script f",
        "awk 'BEGIN{ \"rm x\" | getline }'",
        "awk '{ \"rm f\" | getline }'",
        "awk -f p.awk",
        "gawk -f /dev/stdin",
        "awk '$1 > 3' f",
        "sort --compress-program=sh g",
        "git remote -v add x y",
        "git remote -v rm origin",
        "echo $(rm f)",
        "echo `rm f`",
        "cat $(rm f)",
        'echo "$(rm y)"',
        'for f in "$(rm y)"; do cat; done',
        'cd "$(rm y)"',
    ],
)
def test_sed_awk_sort_remote_substitutions_sont_des_mutations(command: str) -> None:
    """Liste blanche stricte : chacune de ces formes écrit ou exécute malgré son verbe de lecture."""
    assert is_read_only_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "sed -n 1,20p f",
        "sed -n '/foo/,/bar/p' f",
        "sed -E 's/a+/b/g' f",
        "sed -e 's/a/b/;s/c/d/gI' f",
        "sed -n '$p' f",
        "sed '/x/!d' f",
        "git remote",
        "git remote -v",
        "git remote --verbose",
        "git remote get-url --push origin",
        "echo '$(not run)'",
        "grep -n 'a`b' f",
    ],
)
def test_formes_de_la_liste_blanche_restent_des_lectures(command: str) -> None:
    assert is_read_only_command(command) is True


# ── Zone protégée ~/.grimoire/control/ (#886 d, #876 lot 1) ────────────────

_ZONE = "/home/u/.grimoire/control"
_JOURNAL = f"{_ZONE}/mandates.authority.jsonl"
_PROOF = f"{_ZONE}/host-contracts.json"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": "{}\n"}),
        ("Edit", {"file_path": _PROOF, "old_string": "false", "new_string": "true"}),
        ("NotebookEdit", {"notebook_path": f"{_ZONE}/x.ipynb", "new_source": "x"}),
        ("Write", {"file_path": "~/.grimoire/control/host-contracts.json", "content": "{}"}),
        ("Bash", {"command": f"echo '{{}}' >> {_PROOF}"}),
        ("Bash", {"command": "echo '{}' > $HOME/.grimoire/control/mandates.authority.jsonl"}),
        ("Bash", {"command": f"cp /tmp/forged.json {_PROOF}"}),
        ("Bash", {"command": f"mv /tmp/forged {_JOURNAL}"}),
        ("Bash", {"command": f"tee {_PROOF} < /tmp/forged"}),
        ("Bash", {"command": f"sed -i 's/false/true/' {_PROOF}"}),
        ("Bash", {"command": f"python -c \"open('{_PROOF}', 'w').write('{{}}')\""}),
        ("Bash", {"command": "cd ~/.grimoire && echo '{}' > control/host-contracts.json"}),
        ("Bash", {"command": "rm -rf ~/.grimoire/control"}),
    ],
)
def test_an_agent_write_in_the_control_zone_is_a_forgery_vector(tool: str, tool_input: dict[str, object]) -> None:
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    vector = forgery_vector(classify_tool(tool, tool_input))
    assert vector is not None and vector.governed_only is False
    assert vector.code in ("control-zone", "journal")


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": "{}\n"}),
        ("Edit", {"file_path": _PROOF, "old_string": "a", "new_string": "b"}),
        ("Bash", {"command": f"echo ok >> {_PROOF}"}),
    ],
)
def test_the_brake_denies_a_control_zone_write_in_any_profile(tmp_path, tool: str, tool_input: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import HookInput, Outcome, run_decision
    from grimoire.hosts.events import HookEvent

    decision = run_decision(
        "grimoire.tool-policy",
        HookInput(event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, tool_name=tool, tool_input=tool_input, session_id="S1"),
    )
    assert decision.outcome is Outcome.DENY
    assert decision.detail["authority_vector"] in ("control-zone", "journal")
    assert "Limite" in decision.reason  # la limite résiduelle est dite à chaque refus


def test_a_control_zone_refusal_revokes_the_mandates(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import HookInput, run_decision
    from grimoire.hosts.events import HookEvent
    from grimoire.missions.mandate import MandateRequest, MandateStore
    from grimoire.missions.schemas import ORIGIN_HUMAN

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    store = MandateStore(project)
    store.grant(MandateRequest.build(("accept",), ("implementation",), 1, "+1h"), origin=ORIGIN_HUMAN, via="test")
    assert store.state().active
    run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=project, tool_name="Write",
            tool_input={"file_path": _JOURNAL, "content": "{}"}, session_id="S1",
        ),
    )
    assert not store.state().active


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [("Read", {"file_path": f"{_ZONE}/cockpit-key"}), ("Bash", {"command": f"cat {_ZONE}/cockpit-key"})],
)
def test_the_cockpit_key_cannot_be_read_by_an_agent(tool: str, tool_input: dict[str, object]) -> None:
    assert classify_tool(tool, tool_input).secret_target.endswith("cockpit-key")


def test_reading_the_control_zone_proof_stays_allowed() -> None:
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    assert forgery_vector(classify_tool("Bash", {"command": f"cat {_PROOF}"})) is None
    assert forgery_vector(classify_tool("Read", {"file_path": _PROOF})) is None


# ── Évasions de la zone protégée : chemins déguisés, répertoire courant, liens, globs ───────────


@pytest.fixture
def real_home(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Un HOME réel (le sien, pas ``/home/u``) : les formes qui se résolvent sur le disque s'éprouvent dessus."""
    home = tmp_path / "home"
    (home / ".grimoire" / "control").mkdir(parents=True)
    (home / ".grimoire" / "other").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)
    return home


_ZONE_COMMANDS = [
    "echo x > ~/.grimoire/x/../control/y",
    "echo x > ${HOME}/.grimoire/./control/y",
    "echo x > $HOME/.grimoire//control/y",
    "echo x > ~/.grimoire/other/../control/y",
    "cd ~/.grimoire && echo x > control/y",
    "cd ~/.grimoire; echo x > control/y",
    "cd ~/.grimoire && cd control && echo x > y",
    "cd && cd .grimoire && echo x > control/y",
    "cd ~ && echo x > .grimoire/control/y",
    "echo x > ~/.grimoire/'control'/y",
    'echo x > ~/.grimoire/con"trol"/y',
    "echo x > ~/.grimoire/contr*/y",
    "echo x > ~/.grimoire/*/y",
    "tar -C ~/.grimoire -xf /tmp/a control/y",
    "printf '{}' | tee ~/.grimoire/control/y",
    "install -m 600 /tmp/f ~/.grimoire/control/y",
    "rsync -a /tmp/f ~/.grimoire/control/y",
    "dd if=/tmp/f of=$HOME/.grimoire/control/y",
    "dd if=/tmp/f of=${HOME}/.grimoire/control/y",
    "ln -sf /tmp/f ~/.grimoire/control/y",
    "truncate -s 0 ~/.grimoire/control/y",
    "chmod 777 ~/.grimoire/control/y",
    "ln -s ~/.grimoire /tmp/elsewhere",
    "ln -s ~ /tmp/elsewhere",
    "mv ~/.grimoire /tmp/elsewhere",
    "mv /tmp/elsewhere ~/.grimoire",
    "rm -rf ~/.grimoire",
    "python3 -c \"import pathlib; pathlib.Path.home().joinpath('.grimoire', 'control', 'y').write_text('1')\"",
    "python3 -c \"from pathlib import Path; (Path.home() / '.grimoire' / 'control' / 'y').write_text('1')\"",
]


@pytest.mark.parametrize("command", _ZONE_COMMANDS)
def test_disguised_control_zone_writes_are_a_forgery_vector(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    vector = forgery_vector(classify_tool("Bash", {"command": command}))
    assert vector is not None and vector.code == "control-zone", command


def test_a_symlink_made_elsewhere_does_not_hide_the_control_zone(real_home, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    (tmp_path / "zg").symlink_to(real_home / ".grimoire")
    (tmp_path / "zc").symlink_to(real_home / ".grimoire" / "control")
    for path in (f"{tmp_path}/zg/control/y", f"{tmp_path}/zc/y", f"{tmp_path}/zc"):
        write = forgery_vector(classify_tool("Write", {"file_path": path, "content": "x"}))
        shell = forgery_vector(classify_tool("Bash", {"command": f"echo x > {path}"}))
        assert write is not None and write.code == "control-zone", path
        assert shell is not None and shell.code == "control-zone", path


@pytest.mark.parametrize(
    "path", ["~/.grimoire/x/../control/y", "$HOME/.grimoire/./control/y", "${HOME}/.grimoire/other/../control/y"]
)
def test_disguised_control_zone_paths_of_the_write_tools_are_refused(real_home, path: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("NotebookEdit", "notebook_path")):
        vector = forgery_vector(classify_tool(tool, {key: path, "content": "x", "old_string": "a", "new_string": "b", "new_source": "x"}))
        assert vector is not None and vector.code == "control-zone", (tool, path)


@pytest.mark.parametrize(
    "command",
    [
        "echo x > ~/.grimoire/other/y",
        "cd ~/.grimoire && echo x > other/y",
        "echo x > ./control/y",  # un dossier `control` du projet, pas celui de ~/.grimoire
        "echo x > src/control/y",
        "cp ~/.grimoire/other/a ~/.grimoire/other/b",
        "echo x > ~/.grimoire/controller/y",
    ],
)
def test_other_paths_are_not_taken_for_the_control_zone(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    assert forgery_vector(classify_tool("Bash", {"command": command})) is None, command


# ── Écriture caractérisée vs mention : seule la première révoque (#982) ─────────────────────────
# Le mandat du kit est tombé deux fois le 2026-10-26 sur une commande qui ne faisait que *citer* la zone : un
# message de commit par heredoc, un corps d'issue écrit dans le scratchpad. Le refus prudent reste ; la
# révocation, qui coupe toute l'autonomie jusqu'à un tour humain, exige une écriture caractérisée.


@pytest.mark.parametrize("command", _ZONE_COMMANDS)
def test_disguised_control_zone_writes_are_characterized_and_revoke(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector, writes_control_zone

    vector = forgery_vector(classify_tool("Bash", {"command": command}))
    assert vector is not None and vector.code == "control-zone" and vector.revokes is True, command
    assert writes_control_zone(command), command


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": _JOURNAL, "content": "{}\n"}),
        ("Edit", {"file_path": _PROOF, "old_string": "false", "new_string": "true"}),
        ("Bash", {"command": f"echo '{{}}' >> {_PROOF}"}),
        ("Bash", {"command": f"cp /tmp/forged.json {_PROOF}"}),
        ("Bash", {"command": f"mv /tmp/forged {_JOURNAL}"}),
        ("Bash", {"command": f"tee -a {_PROOF} < /tmp/forged"}),
        ("Bash", {"command": f"sed -i 's/false/true/' {_PROOF}"}),
        ("Bash", {"command": f"python -c \"open('{_PROOF}', 'w').write('{{}}')\""}),
        ("Bash", {"command": f"python3 - <<'EOF'\nwith open('{_JOURNAL}', 'a') as fh:\n    fh.write('x')\nEOF"}),
        ("Bash", {"command": f"rm -f {_JOURNAL}"}),
        ("Bash", {"command": f"truncate -s 0 {_JOURNAL}"}),
        ("Bash", {"command": f"install -m 600 /tmp/k {_ZONE}/cockpit-key"}),
        ("Bash", {"command": f"cp -t {_ZONE} /tmp/cockpit-key"}),
        ("Bash", {"command": f"bash -c \"echo x > {_PROOF}\""}),
        ("Bash", {"command": f"timeout 5 cp /tmp/x {_PROOF}"}),
    ],
)
def test_a_characterized_control_zone_write_revokes(tool: str, tool_input: dict[str, object]) -> None:
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    vector = forgery_vector(classify_tool(tool, tool_input))
    assert vector is not None and vector.code == "control-zone" and vector.revokes is True, tool_input


_MENTIONS = [
    'git commit -m "fix(hosts): refus de lecture de ~/.grimoire/control/cockpit-key et du journal"',
    "git commit -q -F - <<'EOF'\nfix: le journal ~/.grimoire/control/mandates.authority.jsonl\nEOF",
    "cat > notes/body.md <<'EOF'\nLa zone ~/.grimoire/control est protégée.\nEOF\ngh issue create --title t --body-file notes/body.md",
    'gh issue create --title t --body "écrire > ~/.grimoire/control/host-contracts.json est refusé"',
    'gh pr create --title t --body "voir ~/.grimoire/control" && git push',
    "printf '%s\\n' 'voir ~/.grimoire/control/cockpit-key' > notes.md",
    'echo "cp x ~/.grimoire/control/y" >> notes.md',
    "sed -i 's|~/.grimoire/control|<zone>|' notes.md",
    "python3 - <<'EOF'\nprint('~/.grimoire/control')\nEOF",
    "grep -rn '.grimoire/control' src > hits.txt",
]


@pytest.mark.parametrize("command", _MENTIONS)
def test_a_mention_of_the_control_zone_does_not_revoke(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions.authority_guard import forgery_vector, writes_control_zone

    vector = forgery_vector(classify_tool("Bash", {"command": command}))
    assert vector is None or vector.revokes is False, command
    assert not writes_control_zone(command), command


def test_a_mention_is_still_refused_prudently(real_home) -> None:  # type: ignore[no-untyped-def]
    """Le refus prudent reste (le risque d'une voie d'écriture non reconnue) ; il dit que le mandat tient."""
    from grimoire.hosts.decisions.authority_guard import forgery_vector

    vector = forgery_vector(classify_tool("Bash", {"command": _MENTIONS[1]}))
    assert vector is not None and vector.code == "control-zone" and vector.revokes is False
    assert "pas révoqué" in vector.what


def _grant_on(root: Path) -> object:
    from grimoire.missions.mandate import MandateRequest, MandateStore
    from grimoire.missions.schemas import ORIGIN_HUMAN

    root.mkdir(parents=True, exist_ok=True)
    store = MandateStore(root)
    store.grant(MandateRequest.build(("accept",), ("implementation",), 1, "+1h"), origin=ORIGIN_HUMAN, via="test")
    assert store.state().active
    return store


def _pre_tool(root: Path, tool: str, tool_input: dict[str, object], session: str = "S1"):  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import HookInput, run_decision
    from grimoire.hosts.events import HookEvent

    return run_decision(
        "grimoire.tool-policy",
        HookInput(event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name=tool, tool_input=tool_input, session_id=session),
    )


@pytest.mark.parametrize("command", _MENTIONS[:5])
def test_the_brake_refuses_a_mention_but_keeps_the_mandate(real_home, tmp_path, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import Outcome

    store = _grant_on(tmp_path / "proj")
    decision = _pre_tool(tmp_path / "proj", "Bash", {"command": command})
    assert store.state().active, command  # type: ignore[attr-defined]
    assert decision.outcome is Outcome.DENY and decision.detail["authority_revokes"] is False


@pytest.mark.parametrize(
    "command",
    [
        "echo '{}' > ~/.grimoire/control/mandates.authority.jsonl",
        "cp /tmp/forged.json ~/.grimoire/control/host-contracts.json",
        "python3 -c \"open('/x/.grimoire/control/host-contracts.json', 'w').write('{}')\"",
    ],
)
def test_the_brake_revokes_on_a_characterized_write_and_says_why(real_home, tmp_path, command: str) -> None:  # type: ignore[no-untyped-def]
    from grimoire.hosts.decisions import Outcome

    store = _grant_on(tmp_path / "proj")
    decision = _pre_tool(tmp_path / "proj", "Bash", {"command": command}, session="S-982")
    assert decision.outcome is Outcome.DENY and decision.detail["authority_revokes"] is True
    state = store.state()  # type: ignore[attr-defined]
    assert not state.active and state.reason == "revoked"
    cause = state.to_dict()["revocation"]
    assert cause["reason"] == "protected_write" and cause["session_id"] == "S-982" and cause["tool"] == "Bash"
    assert cause["command"].split()[0] in ("echo", "cp", "python3") and cause["ts"]


def test_the_revocation_only_hits_the_session_project(real_home, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Une session du projet A ne révoque pas le mandat du projet B ; une session sans mandat n'en révoque aucun."""
    a, b, forge = tmp_path / "kit", tmp_path / "other", tmp_path / "forge"
    store_a, store_b = _grant_on(a), _grant_on(b)
    forge.mkdir()
    write = {"command": "cp /tmp/forged ~/.grimoire/control/host-contracts.json"}
    _pre_tool(forge, "Bash", write)
    assert store_a.state().active and store_b.state().active  # type: ignore[attr-defined]
    _pre_tool(a, "Bash", write)
    assert not store_a.state().active and store_b.state().active  # type: ignore[attr-defined]


def test_a_task_worktree_session_revokes_its_project_mandate(real_home, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Le worker d'une tâche tourne dans un worktree lié : il agit sous le mandat du worktree principal."""
    main, other = tmp_path / "kit", tmp_path / "other"
    store_main, store_other = _grant_on(main), _grant_on(other)
    gitdir = main / ".git" / "worktrees" / "t-1"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt" / "t-1"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    _pre_tool(worktree, "Write", {"file_path": str(real_home / ".grimoire" / "control" / "cockpit-key"), "content": "k"})
    assert not store_main.state().active and store_other.state().active  # type: ignore[attr-defined]


# ── Lecture de la clé du cockpit et du journal du mandat : jokers, répertoire courant, liens ────
# La clé, lue puis rejouée avec le jeton de session que le cockpit injecte dans ses pages, accorde un mandat ;
# le journal porte les identifiants de session que le principal `orchestrator` doit prouver.


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.grimoire/control/cockpit-k*",
        "cat ~/.grimoire/control/cockp?t-key",
        "cat ~/.grimoire/control/*",
        "cat ~/.grimoire/*/cockpit-key",
        "cd ~/.grimoire/control && cat cock*",
        "cd ~/.grimoire && cat control/c*",
        "cat ~/.grimoire/other/../control/cockpit-ke[y]",
        "cat ${HOME}/.grimoire/./control/cockpit-key",
        "cat $HOME/.grimoire/control/mandates.authority.jsonl",
        "head -1 ~/.grimoire/control/mandates.*",
    ],
)
def test_the_control_key_and_the_mandate_journal_cannot_be_read_by_any_form(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    assert classify_tool("Bash", {"command": command}).secret_target, command


def test_a_symlink_to_the_control_key_is_still_the_key(real_home, tmp_path) -> None:  # type: ignore[no-untyped-def]
    key = real_home / ".grimoire" / "control" / "cockpit-key"
    key.write_text("k\n", encoding="utf-8")
    (tmp_path / "k").symlink_to(key)
    assert classify_tool("Read", {"file_path": str(tmp_path / "k")}).secret_target
    assert classify_tool("Bash", {"command": f"cat {tmp_path}/k"}).secret_target


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.grimoire/control/host-contracts.json",
        "cat ~/.grimoire/other/x",
        "cd ~/.grimoire/other && cat x*",
        "cat ./control/cockpit-notes",  # un dossier `control` du projet
    ],
)
def test_other_reads_stay_allowed(real_home, command: str) -> None:  # type: ignore[no-untyped-def]
    assert not classify_tool("Bash", {"command": command}).secret_target, command
