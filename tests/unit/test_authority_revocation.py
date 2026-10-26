"""#982 : un refus dans la zone de contrôle révoque le mandat, sauf mention prouvée inoffensive.

Le mandat du kit est tombé deux fois le 2026-10-26 sur une commande qui ne faisait que *citer* la zone (message de
commit par heredoc, corps d'issue). Un premier correctif reconnaissait les *écritures* par analyse de texte : une
revue adversariale a montré qu'il échouait ouvert (variable, ``$(…)``, ``| bash``, préfixe ``sudo -u``, écrivain
non listé, ``cd`` hors projet). La charge est inversée : on révoque **sauf** si la mention passe une liste blanche
stricte. Le refus, lui, ne change pas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.decisions import authority_guard as ag
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.hosts.events import HookEvent
from grimoire.missions import mandate as md
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN

Z = "~/.grimoire/control"
J = "mandates.authority.jsonl"
H = "host-contracts.json"
runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un HOME de test qui porte la zone ; le répertoire courant est un dossier de travail ordinaire."""
    root = tmp_path / "home"
    (root / ".grimoire" / "control").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.chdir(work)
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    return root


def _vector(command: str):  # type: ignore[no-untyped-def]
    return ag.forgery_vector(classify_tool("Bash", {"command": command}))


# ── A. Mentions prouvées inoffensives : refusées par prudence, jamais révoquées ──────────────────

HARMLESS = [
    f'git commit -m "fix(hosts): refus de lecture de {Z}/cockpit-key"',
    f"git commit -q -F - <<'EOF'\nfix: le journal {Z}/{J}\nEOF",
    f"git add a.py && git commit --message='{Z}' && git push",
    f'git -C /srv/repo commit -m "voir {Z}"',
    f"git tag -a v1 -m '{Z}'",
    f"cat > notes/body.md <<'EOF'\nLa zone {Z} est protégée.\nEOF\ngh issue create --title t --body-file notes/body.md",
    f'gh issue create --title t --body "écrire > {Z}/{H} est refusé"',
    f'gh pr create --title "zone {Z}" --body x && git push',
    f"gh pr comment 12 --body-file - <<'EOF'\n{Z}/{J}\nEOF",
    "grep -rn '.grimoire/control' src > hits.txt",
    f"ls {Z} && git status",
    f"cd /srv/repo && git commit -m '{Z}'",
]


@pytest.mark.parametrize("command", HARMLESS)
def test_a_harmless_mention_does_not_revoke(command: str) -> None:
    assert ag.is_harmless_mention(command), command
    vector = _vector(command)
    assert vector is None or (vector.revokes is False and vector.revokes_all is False), command


def test_a_harmless_mention_is_still_refused() -> None:
    vector = _vector(HARMLESS[1])
    assert vector is not None and vector.code == "control-zone" and vector.revokes is False
    assert "pas révoqué" in vector.what


# ── Points 1 à 8 de la revue : refusés ET révoqués ──────────────────────────────────────────────

REVOKED = {
    # 1. une variable porte le chemin
    "var": f'F={Z}/{J}; echo x >> "$F"',
    "export": f"export F={Z}/{J}\necho x >> $F",
    "split": f"D={Z}; N=mandates; echo x >> $D/$N.authority.jsonl",
    # 2. une apostrophe en commentaire fait avaler la ligne suivante
    "comment": f"echo ok # don't\necho x > {Z}/{J}",
    "comment-commit": f"git commit -m x # don't\ncp a {Z}/{J}",
    # 3. substitution de commande
    "subst": f'echo "$(echo x > {Z}/{J})"',
    "backtick": f"echo `echo x > {Z}/{J}`",
    # 4. texte envoyé à un shell
    "pipe-bash": f"echo 'echo x > {Z}/{J}' | bash",
    "bash-procsub": f"bash <(echo 'echo x > {Z}/{J}')",
    "source": f"source <(echo 'cp a {Z}/{J}')",
    "eval": f"eval 'echo x > {Z}/{J}'",
    # 5. préfixes avec options
    "env-i": f"env -i tee {Z}/{J}",
    "sudo-u": f"sudo -u root tee {Z}/{J}",
    "sudo-n": f"sudo -n cp a {Z}/{J}",
    "nice": f"nice -n 5 cp a {Z}/{J}",
    "time-p": f"time -p cp a {Z}/{J}",
    "command-p": f"command -p cp a {Z}/{J}",
    "flock": f"flock /tmp/l cp a {Z}/{J}",
    "strace": f"strace -o /dev/null cp a {Z}/{J}",
    "env-S": f"env -S 'tee {Z}/{J}'",
    # 6. code interprété avec une écriture hors liste
    "py-subprocess": f"python3 -c \"import subprocess; subprocess.run(['cp', 'a', '{Z}/{J}'])\"",
    "py-shell": f"python3 -c \"import subprocess; subprocess.run('echo x > {Z}/{J}', shell=True)\"",
    "py-fileinput": f"python3 -c \"import fileinput; fileinput.input('{Z}/{J}', inplace=True)\"",
    "py-logging": f"python3 -c \"import logging; logging.FileHandler('{Z}/{J}')\"",
    "py-sqlite": f"python3 -c \"import sqlite3; sqlite3.connect('{Z}/{J}')\"",
    "node-open": f"node -e \"require('fs').openSync('{Z}/{J}', 'a')\"",
    # 7. écrivains non reconnus
    "curl-o": f"curl -o {Z}/{J} http://x",
    "wget-O": f"wget -O {Z}/{J} http://x",
    "wget-P": f"wget -P {Z} http://x",
    "sort-o": f"sort -o {Z}/{J} a",
    "sponge": f"echo x | sponge {Z}/{J}",
    "find-fprint": f"find / -maxdepth 0 -fprint {Z}/x",
    "awk": f"awk 'BEGIN{{print 1 > \"{Z}/x\"}}'",
    "sed-w": f"sed -n 'w {Z}/x' /etc/passwd",
    "perl-open": f"perl -e 'open(F,\">>{Z}/{J}\")'",
    # écritures ordinaires : toujours révoquées
    "redirect": f"echo x > {Z}/{H}",
    "cp": f"cp /tmp/forged {Z}/{H}",
    "cd-zone": f"cd {Z} && echo x > y",
    "echo-mention": f'echo "{Z}"',
    "git-other-sub": f"git clone /src {Z}/x",
    "gh-api": f"gh api repos/x --input {Z}/{J}",
    "unclosed": f"echo 'a > {Z}/{J}",
}


@pytest.mark.parametrize("command", REVOKED.values(), ids=REVOKED.keys())
def test_what_is_not_proven_harmless_revokes(command: str) -> None:
    vector = _vector(command)
    if vector is not None:  # un appel classé en lecture seule n'est pas un refus de cette garde
        assert vector.code == "control-zone" and vector.revokes is True, command
    assert not ag.is_harmless_mention(command), command


@pytest.mark.parametrize(
    "command", [REVOKED[k] for k in ("var", "comment", "subst", "pipe-bash", "sudo-u", "py-subprocess", "curl-o")]
)
def test_the_review_bypasses_are_refused_and_revoked_end_to_end(tmp_path: Path, command: str) -> None:
    """Un exemple de chaque famille de contournement, par l'entrée réelle du frein."""
    proj = _project(tmp_path / "proj")
    decision = _pre(proj, {"command": command})
    assert decision.outcome is Outcome.DENY, command
    assert not MandateStore(proj).state().active, command


# ── B. Échec fermé : une exception révoque, et le refus reste un refus ───────────────────────────


def _project(root: Path, *, grant: bool = True) -> Path:
    (root / "_grimoire").mkdir(parents=True, exist_ok=True)
    if grant:
        MandateStore(root).grant(
            MandateRequest.build(("accept",), ("implementation",), 5, "+1h"), origin=ORIGIN_HUMAN, via="test"
        )
        assert MandateStore(root).state().active
    return root


def _pre(root: Path, tool_input: dict[str, object], *, tool: str = "Bash", session: str = "S1"):  # type: ignore[no-untyped-def]
    return run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name=tool, tool_input=tool_input, session_id=session
        ),
    )


def test_a_parser_error_revokes_and_still_denies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts.decisions import authority_guard

    def boom(_command: str) -> list[object]:
        raise RuntimeError("panne du lecteur")

    monkeypatch.setattr(authority_guard, "_parse_strict", boom)
    assert ag.is_harmless_mention(HARMLESS[0]) is False
    proj = _project(tmp_path / "proj")  # aucun profil déclaré : pas governed
    decision = _pre(proj, {"command": HARMLESS[0]})
    assert decision.outcome is Outcome.DENY and decision.detail["authority_revokes"] is True
    assert not MandateStore(proj).state().active


def test_a_targeted_revocation_error_falls_back_to_every_mandate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("panne de la révocation ciblée")

    monkeypatch.setattr(md, "revoke_session_mandates", boom)
    proj, other = _project(tmp_path / "proj"), _project(tmp_path / "other")
    decision = _pre(proj, {"command": REVOKED["redirect"]})
    assert decision.outcome is Outcome.DENY
    assert not MandateStore(proj).state().active and not MandateStore(other).state().active


def test_a_revocation_that_fails_entirely_never_softens_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("journal en panne")

    monkeypatch.setattr(md, "revoke_session_mandates", boom)
    monkeypatch.setattr(md, "revoke_every_mandate", boom)
    decision = _pre(_project(tmp_path / "proj"), {"command": REVOKED["redirect"]})
    assert decision.outcome is Outcome.DENY


# ── C. Le journal ou la clé visés : tous les mandats tombent ─────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Bash", {"command": f"echo x >> {Z}/{J}"}),
        ("Bash", {"command": f"cp /tmp/k {Z}/cockpit-key"}),
        ("Bash", {"command": f"echo x >> {Z}/mand*"}),
        ("Bash", {"command": f"D={Z}; echo x > $D/y"}),
        ("Write", {"file_path": f"{Z}/{J}", "content": "{}"}),
    ],
)
def test_aiming_at_the_mandate_journal_or_key_revokes_every_mandate(
    tmp_path: Path, tool: str, tool_input: dict[str, object]
) -> None:
    proj, other = _project(tmp_path / "proj"), _project(tmp_path / "other")
    _pre(proj, tool_input, tool=tool)
    assert not MandateStore(proj).state().active and not MandateStore(other).state().active


def test_another_zone_file_revokes_only_the_session_project(tmp_path: Path) -> None:
    proj, other = _project(tmp_path / "proj"), _project(tmp_path / "other")
    _pre(proj, {"command": REVOKED["cp"]})
    assert not MandateStore(proj).state().active and MandateStore(other).state().active


# ── D. Point 8 : projet inconnu, sans mandat, session inscrite ailleurs ──────────────────────────


def test_a_session_outside_any_project_revokes_every_mandate(tmp_path: Path) -> None:
    proj, other = _project(tmp_path / "proj"), _project(tmp_path / "other")
    elsewhere = tmp_path / "elsewhere"  # le cwd d'un `cd` hors projet : pas de _grimoire/
    elsewhere.mkdir()
    _pre(elsewhere, {"command": REVOKED["cp"]})
    assert not MandateStore(proj).state().active and not MandateStore(other).state().active


def test_a_project_without_mandate_revokes_every_mandate(tmp_path: Path) -> None:
    proj = _project(tmp_path / "proj")
    forge = _project(tmp_path / "forge", grant=False)
    _pre(forge, {"command": REVOKED["cp"]})
    assert not MandateStore(proj).state().active


def test_the_mandate_where_the_session_is_registered_falls_too(tmp_path: Path) -> None:
    kit, forge, third = _project(tmp_path / "kit"), _project(tmp_path / "forge"), _project(tmp_path / "third")
    with md.acting_as_orchestrator():
        MandateStore(kit).register_session("S-kit", "worker", "T-1")
    _pre(forge, {"command": REVOKED["cp"]}, session="S-kit")
    assert not MandateStore(kit).state().active and not MandateStore(forge).state().active
    assert MandateStore(third).state().active


def test_a_task_worktree_session_revokes_its_project_mandate(tmp_path: Path) -> None:
    main, other = _project(tmp_path / "kit"), _project(tmp_path / "other")
    gitdir = main / ".git" / "worktrees" / "t-1"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt" / "t-1"
    (worktree / "_grimoire").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    _pre(worktree, {"command": REVOKED["cp"]})
    assert not MandateStore(main).state().active and MandateStore(other).state().active


# ── E. La cause au statut : programme et zone, jamais les arguments ─────────────────────────────

_SECRET = "ghp_" + "a" * 36


def test_the_status_says_what_revoked_without_any_argument(tmp_path: Path) -> None:
    proj = _project(tmp_path / "proj")
    _pre(proj, {"command": f"GH_TOKEN={_SECRET} cp --token hunter2 /tmp/forged {Z}/{H}"}, session="S-982")
    state = MandateStore(proj).state()
    cause = state.to_dict()["revocation"]
    assert cause["reason"] == md.REVOKE_PROTECTED_WRITE and cause["ts"]
    assert cause["session_id"] == "S-982" and cause["tool"] == "Bash" and cause["program"] == "cp"
    assert cause["target"].startswith("control-zone") and Z in cause["target"]
    assert "command" not in cause
    dumped = json.dumps(state.to_dict())
    assert _SECRET not in dumped and "hunter2" not in dumped and "/tmp/forged" not in dumped

    text = runner.invoke(app, ["autonomy", "mandate", "status", "--project-root", str(proj)])
    assert text.exit_code == 0, text.output
    assert "par la session S-982 (Bash)" in text.output and "cp → control-zone" in text.output
    assert _SECRET not in text.output and "hunter2" not in text.output
    raw = runner.invoke(app, ["--output", "json", "autonomy", "mandate", "status", "--project-root", str(proj)])
    assert raw.exit_code == 0, raw.output
    assert json.loads(raw.output)["revocation"]["program"] == "cp"


def test_revocation_cause_keeps_only_the_program() -> None:
    cause = md.revocation_cause("S", "Bash", "TOKEN=s3cr3t /usr/bin/tee --x y", "control-zone", "écrit (~/z)")
    assert cause == {"session_id": "S", "tool": "Bash", "program": "tee", "target": "control-zone ~/z"}


def test_a_manual_revocation_keeps_its_time_and_no_cause(tmp_path: Path) -> None:
    proj = _project(tmp_path / "proj")
    MandateStore(proj).revoke(md.REVOKE_MANUAL, "fin de la nuit")
    cause = MandateStore(proj).state().to_dict()["revocation"]
    assert cause["reason"] == md.REVOKE_MANUAL and cause["ts"] and "program" not in cause and "session_id" not in cause
