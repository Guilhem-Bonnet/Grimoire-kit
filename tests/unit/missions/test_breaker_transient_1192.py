"""#1192 : un délai réseau du merger n'ouvre plus le disjoncteur, et un disjoncteur ouvert se voit.

Trois lectures ``gh pr view`` en panne réseau à l'étape ``wait`` : le compteur transitoire monte, le disjoncteur reste
fermé (le comportement d'avant gelait toute l'autonomie jusqu'à un ``reset`` humain). Trois échecs de session
consécutifs l'ouvrent toujours. Ouvert, il apparaît **en tête** de la route d'attention du cockpit, avec le geste
``reset``, et ``grimoire autonomy autopilot status`` le dit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost
from test_merger import REPO, grant, launch_and_work, make_projet, merges, ready_judged, review_ready, tick
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker, record_failure, trip
from grimoire.missions.merger import MERGE_TRANSIENT_EVENT
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_attention as wa
from grimoire.tools.workspace_reviews import ReviewCache


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session héritée du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return make_projet(tmp_path)


@pytest.fixture
def host() -> FakeHost:
    fake = FakeHost()
    fake.repo = REPO
    return fake


def _never_probe(service: str, url: str) -> str:
    raise AssertionError(f"aucun service ne devait être sondé ({service} {url})")


def test_trois_delais_reseau_de_gh_pr_view_au_wait_laissent_le_disjoncteur_ferme(
    projet: Path, host: FakeHost
) -> None:
    """Le cas de l'issue : trois ``gh pr view`` en délai réseau, aucun échec de session, disjoncteur fermé."""
    tid, wt = launch_and_work(projet, host)
    first = review_ready(projet, host, tid, wt)  # READY jugé, CI verte vue une première fois : en attente de confirmation
    assert merges(first) == [("wait", "waiting")], first.merges
    host.gh_broken = True
    host.gh_error = ('Post "https://api.github.com/graphql": dial tcp 140.82.112.6:443: i/o timeout')  # le cas de l'issue

    reports = [tick(projet, host) for _ in range(3)]

    state = read_breaker(projet)
    assert [merges(r)[0] for r in reports] == [("wait", "transient")] * 3, [r.merges for r in reports]
    assert state.failures == 0 and not state.open and state.transients == 3
    transient = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_TRANSIENT_EVENT]
    assert len(transient) == 3 and [e.payload["transients"] for e in transient] == [1, 2, 3]
    assert wt.is_dir()  # la tâche reste dans la file, rejouée au tick suivant


def test_trois_echecs_de_session_consecutifs_ouvrent_toujours_le_disjoncteur(tmp_path: Path) -> None:
    """Le contrat de #876 ne change pas : de vrais échecs de session comptent et ouvrent au seuil."""
    root = tmp_path / "projet"
    root.mkdir()

    states = [record_failure(root, 3, f"tâche {n} : session en échec") for n in range(1, 4)]

    assert [s.open for s in states] == [False, False, True]
    state = read_breaker(root)
    assert state.open and state.failures == 3 and state.transients == 0


def test_un_claude_bg_refuse_compte_au_disjoncteur_meme_si_son_message_parle_de_delai(
    projet: Path, host: FakeHost
) -> None:
    """Un refus de session n'est pas un délai réseau : « délai » dans le message ne le rend pas transitoire."""
    grant(projet)
    tid, wt = launch_and_work(projet, host)
    ready_judged(projet, host, tid, wt)
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "fail"}]
    host.bg_fails = True
    host.bg_error = "claude --bg : délai dépassé, session refusée"

    report = tick(projet, host)

    state = read_breaker(projet)
    assert merges(report) == [("bounce", "failed")], report.merges
    assert state.failures == 1 and state.transients == 0 and not state.open


def test_un_disjoncteur_ouvert_est_en_tete_du_cockpit_et_dit_par_autopilot_status(projet: Path) -> None:
    trip(projet, "3 échecs consécutifs, dernier : GAO-x : session en échec", source="count")

    view = wa.attention_view(
        projet, service_probe=_never_probe, watch=wa.ServiceWatch(),
        pr_reader=lambda _repo, _cwd: [], review_cache=ReviewCache(), pr_wait=1.0,
    )
    first = view["sections"][0]["items"][0]
    assert first["kind"] == wa.BREAKER
    assert "3 échecs consécutifs" in first["reason"]
    assert first["ref"]["gesture"]["command"] == "grimoire orchestrate breaker reset"

    result = CliRunner().invoke(app, ["autonomy", "autopilot", "status", "--project-root", str(projet)])

    assert result.exit_code == 0, result.output
    assert "disjoncteur ouvert" in result.output.lower()
    assert "grimoire orchestrate breaker reset" in result.output
