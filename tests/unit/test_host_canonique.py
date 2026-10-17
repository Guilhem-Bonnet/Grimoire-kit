"""Issue #711 — une seule graphie de l'hôte Claude Code dans le ledger.

Rouge avant : ``current_session`` rendait ``claude-code-cli`` alors que les
hooks écrivent ``host-claude-code-cli`` (``HostId``) ; ``RESUME_COMMANDS`` ne
connaissait que la seconde, donc la carte et ``task_show`` n'affichaient aucune
commande de reprise pour une session rattachée par ``task attach``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.standard_state import current_session
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.session_link import resume_command, session_fields
from grimoire.tools import workspace_portfolio as wp

CANON = HostId.CLAUDE_CODE_CLI.value
ALIAS_TABLE = {
    "host-claude-code-cli": CANON,
    "claude-code-cli": CANON,
    "claude": CANON,
    "Claude": CANON,
    "claude-code": CANON,
    "Claude-Code": CANON,
    "  claude-code  ": CANON,
    "host-github-copilot": "host-github-copilot",
    "host-codex": "host-codex",
    "": "",
    "inconnu": "inconnu",
}


@pytest.mark.parametrize(("raw", "expected"), sorted(ALIAS_TABLE.items()))
def test_table_de_verite_des_alias(raw: str, expected: str) -> None:
    from grimoire.missions.session_link import canonical_host

    assert canonical_host(raw) == expected


@pytest.mark.parametrize("host", [CANON, "claude-code-cli", "claude", "claude-code", "Claude"])
def test_meme_commande_sur_les_trois_surfaces(host: str) -> None:
    attendu = "claude --resume abc-123"
    assert resume_command(host, "abc-123") == attendu
    claim = SimpleNamespace(session_id="abc-123", session_host=host)
    assert session_fields(SimpleNamespace(claim=claim))["resume_command"] == attendu
    assert wp.resume_command("abc-123", host) == attendu


def test_l_environnement_de_session_rend_la_graphie_canonique() -> None:
    assert current_session({"CLAUDE_CODE_SESSION_ID": "abc-123"}) == ("abc-123", CANON)


def test_un_rattachement_ecrit_la_graphie_canonique_meme_si_l_appelant_donne_l_alias(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission(title="m", origin="t", created_by="a")
    task = ledger.create_task(mission.id, "t", acceptance=("c",))
    ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    ledger.claim_task(task.id, "claude", "local")
    attached = ledger.attach_session(task.id, "abc-123", session_host="claude-code-cli")
    assert attached.claim is not None and attached.claim.session_host == CANON


def test_un_ledger_a_l_ancienne_graphie_reste_lisible_et_affiche_la_commande(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    mission = ledger.create_mission(title="m", origin="t", created_by="a")
    task = ledger.create_task(mission.id, "t", acceptance=("c",))
    ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    ledger.claim_task(task.id, "claude", "local")
    ledger.attach_session(task.id, "abc-123", session_host=CANON)
    # Événement déjà écrit à l'ancienne graphie : on réécrit le journal à la main.
    for path in (tmp_path / "ledger").rglob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(f'"session_host": "{CANON}"', '"session_host": "claude-code-cli"'), encoding="utf-8")
    relu = MissionLedger(tmp_path / "ledger").get_task(task.id)
    assert relu is not None and relu.claim is not None
    assert relu.claim.session_host in {"claude-code-cli", CANON}
    fields = session_fields(relu)
    assert fields["resume_command"] == "claude --resume abc-123"
    assert fields["host"] == CANON


def test_une_seule_table_d_alias_pour_l_hote() -> None:
    """S1-03 : ``canonical_host`` et ``resolve_host`` ne peuvent plus diverger."""
    from grimoire.hosts.capabilities import HOST_ALIASES as TABLE_CLI
    from grimoire.hosts.capabilities import resolve_host
    from grimoire.missions import session_link

    assert not hasattr(session_link, "HOST_ALIASES")
    for alias in ("claude", "claude-code", "claude-code-cli", "host-claude-code-cli", "Claude"):
        assert resolve_host(alias) is HostId.CLAUDE_CODE_CLI, alias
    # tout alias de la CLI se canonise vers la valeur de son HostId
    for alias, host in TABLE_CLI.items():
        assert session_link.canonical_host(alias) == host.value


@pytest.mark.parametrize("host", ["claude", "claude-code", "Claude", "claude-code-cli", "host-claude-code-cli"])
def test_la_commande_de_reprise_du_portefeuille_couvre_tous_les_alias_claude(host: str) -> None:
    assert wp.resume_command("abc", host) == "claude --resume abc"
    assert resume_command(host, "abc") == "claude --resume abc"
