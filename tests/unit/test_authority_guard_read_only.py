"""#1173 — une lecture ou une mention de la zone de contrôle ne révoque plus le mandat.

Six lignes d'attendu, un cas par ligne, et pour chacun l'état du mandat après la commande :

1. lecture seule de la zone de contrôle (``cat``, ``tail``, …) : refusée, mandat intact ;
2. lecture seule de l'instantané du ledger : autorisée, mandat intact ;
3. simple mention du chemin (heredoc, chaîne, argument de texte) : ni refus ni révocation ;
4. commande composée dont aucune partie n'écrit dans la zone : pas de refus ;
5. écriture avérée (redirection, ``tee``, ``sed -i``, ``cp``/``mv``, ``python`` en ``w``/``a``) : refus et
   révocation ;
6. nature incertaine : refus sans révocation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.events import HookEvent
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN

Z = "~/.grimoire/control"
SNAPSHOT = "_grimoire-runtime-output/ledger/ledger.snapshot.json"


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
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PROJECT_DIR",
        "GRIMOIRE_UNATTENDED",
    ):
        monkeypatch.delenv(var, raising=False)
    return root


def _project(root: Path) -> Path:
    (root / "_grimoire").mkdir(parents=True, exist_ok=True)
    MandateStore(root).grant(
        MandateRequest.build(("accept",), ("implementation",), 5, "+1h"), origin=ORIGIN_HUMAN, via="test"
    )
    assert MandateStore(root).state().active
    return root


def _run(project: Path, command: str, *, session: str = "S-1173"):  # type: ignore[no-untyped-def]
    return run_decision(
        "grimoire.tool-policy",
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=project,
            tool_name="Bash",
            tool_input={"command": command},
            session_id=session,
        ),
    )


# ── 1. Lecture de la zone : refusée (la clé y vit), mandat intact ───────────────


@pytest.mark.parametrize(
    "command",
    [
        f"cat {Z}/cockpit-key",
        f"tail -n 20 {Z}/mandates.authority.jsonl",
        f"head -n 5 {Z}/host-contracts.json",
        f"grep x {Z}/cockpit-key",
        f"ls {Z}",
        f"find {Z} -maxdepth 1 -name x",
        f"wc -l {Z}/host-contracts.json",
        f"jq . {Z}/mandates.authority.jsonl",
        f"python3 -c \"print(open('{Z}/mandates.authority.jsonl').read())\"",
        f"tail -n 50 {Z}/mandates.authority.jsonl | head",
    ],
)
def test_une_lecture_de_la_zone_est_refusee_sans_revoquer(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "control-zone", decision
    assert decision.detail["authority_revokes"] is False, decision
    assert "mandat n'est pas révoqué" in decision.reason, decision
    assert MandateStore(proj).state().active, command


# ── 2. Lecture de l'instantané du ledger : autorisée, mandat intact ─────────────


@pytest.mark.parametrize(
    "command",
    [
        f"cat {SNAPSHOT}",
        f"jq . {SNAPSHOT}",
        f"head -c 200 {SNAPSHOT}",
        f"python3 -c \"import json; print(len(json.load(open('{SNAPSHOT}'))))\"",
    ],
)
def test_une_lecture_de_l_instantane_est_autorisee(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is not Outcome.DENY, decision
    assert MandateStore(proj).state().active, command


# ── 3. Simple mention : ni refus ni révocation ─────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        f"git commit -m 'cite {Z}'",
        f"echo '{Z}'",
        f"cat > notes.md <<'EOF'\nle fichier {Z}/cockpit-key est refusé\nEOF",
        f"gh issue create --title t --body-file - <<'EOF'\nle fichier {Z}/cockpit-key est refusé\nEOF",
        "grep -rn '.grimoire/control' src",
        f"python3 -c \"print('{Z}')\"",
    ],
)
def test_une_mention_ne_est_ni_refusee_ni_revoquee(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is not Outcome.DENY, decision
    assert "authority_vector" not in decision.detail or decision.detail["authority_vector"] != "control-zone", decision
    assert MandateStore(proj).state().active, command


# ── 4. Commande composée sans écriture dans la zone : pas de refus ─────────────


@pytest.mark.parametrize(
    "command",
    [
        "grimoire task claim --help; grep -r foo src",
        "grimoire task claim --help; grep -rn '.grimoire/control' src",
        f"git status; echo '{Z}'",
        "echo ok && grep -rn '.grimoire/control' src",
    ],
)
def test_une_commande_composee_sans_ecriture_n_est_pas_refusee(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is not Outcome.DENY, decision
    assert MandateStore(proj).state().active, command


# ── 5. Écriture avérée : refus et révocation ───────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        f"echo x > {Z}/host-contracts.json",
        f"echo x >> {Z}/mandates.authority.jsonl",
        f"tee {Z}/host-contracts.json < /dev/null",
        f"sed -i 's/a/b/' {Z}/host-contracts.json",
        f"cp /tmp/forged {Z}/host-contracts.json",
        f"mv /tmp/forged {Z}/host-contracts.json",
        f"python3 -c \"open('{Z}/x', 'w').write('x')\"",
        f"python3 -c \"open('{Z}/x', 'a').write('x')\"",
    ],
)
def test_une_ecriture_prouvee_est_refusee_et_revoque(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "control-zone", decision
    assert decision.detail["authority_revokes"] is True, decision
    assert decision.detail["authority_revoked"] is True, decision
    assert "Le mandat a été révoqué" in decision.reason, decision
    assert not MandateStore(proj).state().active, command


# ── 6. Nature incertaine : refus sans révocation ───────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        f'F={Z}/x; echo hi >> "$F"',
        f"echo `cat {Z}/x`",
        f"echo 'echo x > {Z}/y' | bash",
        f"eval 'echo x > {Z}/y'",
        f"sudo -u root tee {Z}/host-contracts.json",
        f"curl -o {Z}/x http://example.invalid",
        f"python3 -c \"open('{Z}/x', m).write('x')\"",
    ],
)
def test_une_nature_incertaine_est_refusee_sans_revoquer(tmp_path: Path, command: str) -> None:
    proj = _project(tmp_path / "proj")
    decision = _run(proj, command)
    assert decision.outcome is Outcome.DENY, decision
    assert decision.detail["authority_vector"] == "control-zone", decision
    assert decision.detail["authority_revokes"] is False, decision
    assert decision.detail["authority_revoked"] is False, decision
    assert MandateStore(proj).state().active, command
