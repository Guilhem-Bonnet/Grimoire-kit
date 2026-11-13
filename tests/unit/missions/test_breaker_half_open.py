"""GAO-disjoncteur-002 : un disjoncteur ouvert par comptage se rouvre seul, une tâche qui boucle est mise à l'écart.

Le constat : un disjoncteur ouvert par trois échecs restait ouvert jusqu'à un ``reset`` humain, et une seule tâche
qui échoue en boucle pouvait l'ouvrir pour tout le monde. Les contrats vérifiés ici :

1. ouvert **par comptage**, il passe **semi-ouvert** après ``breaker.cooldown_minutes`` (défaut 30) : un seul
   lancement est admis ; son succès referme, son échec rouvre et double le délai (plafond 4 h) ;
2. ouvert par ``trip``, il reste fermé à la main : ni le temps ni un succès ne le referment ;
3. une même tâche qui échoue trois fois est mise à l'écart avec sa raison et ne compte que pour **un** échec global ;
   elle sort de la file d'admission, et ``reset`` lève la mise à l'écart ;
4. ``autonomy autopilot status``, ``breaker status`` et la carte d'attention disent l'état (ouvert, semi-ouvert,
   prochaine tentative) et les tâches écartées.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import breaker as brk
from grimoire.missions.breaker import (
    DEFAULT_COOLDOWN_MINUTES,
    MAX_COOLDOWN_MINUTES,
    admit,
    read_breaker,
    record_failure,
    record_success,
    reset,
    trip,
)
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_attention as wa
from grimoire.tools.workspace_reviews import ReviewCache

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Aucune session ni identité héritée du poste ne doit changer la racine du disjoncteur."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


def _open_by_count(root: Path, *, threshold: int = 3, cooldown: int = DEFAULT_COOLDOWN_MINUTES) -> brk.BreakerState:
    """Trois tâches différentes échouent : le comptage global ouvre le disjoncteur (source ``count``)."""
    for n in range(threshold):
        record_failure(
            root, threshold, f"t{n} : session en échec", task_id=f"t{n}", cooldown_minutes=cooldown, now=T0
        )
    state = read_breaker(root)
    assert state.open and state.source == "count" and not state.half_open, state
    return state


# ── 1. Semi-ouvert : une sonde, puis le verdict ─────────────────────────────


def test_un_disjoncteur_de_comptage_passe_semi_ouvert_apres_le_cooldown_et_n_admet_qu_une_sonde(projet: Path) -> None:
    _open_by_count(projet)

    early = admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=29))
    assert not early.allowed and "disjoncteur ouvert" in early.reason and "prochaine tentative" in early.reason
    assert not read_breaker(projet).half_open
    assert "prochaine tentative après" in read_breaker(projet).describe()

    probe = admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30))
    assert probe.allowed and probe.probe and probe.state.half_open
    assert read_breaker(projet).half_open and "SEMI-OUVERT" in read_breaker(projet).describe()

    second = admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=31))
    assert not second.allowed and "semi-ouvert" in second.reason.lower()
    assert read_breaker(projet).half_open  # toujours la même sonde en vol, aucune autre admise


def test_le_succes_de_la_sonde_referme_le_disjoncteur(projet: Path) -> None:
    _open_by_count(projet)
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30)).probe

    state = record_success(projet, now=T0 + timedelta(minutes=31))

    assert not state.open and not state.half_open and state.failures == 0
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=31)).allowed


def test_l_echec_de_la_sonde_rouvre_et_double_le_delai_jusqu_au_plafond(projet: Path) -> None:
    _open_by_count(projet, cooldown=30)
    now = T0 + timedelta(minutes=30)

    for expected in (60, 120, MAX_COOLDOWN_MINUTES, MAX_COOLDOWN_MINUTES):
        assert admit(projet, cooldown_minutes=30, now=now).probe
        state = record_failure(
            projet, 3, "sonde : session en échec", task_id=f"sonde-{expected}", cooldown_minutes=30, now=now
        )
        assert state.open and not state.half_open and state.cooldown_minutes == expected, state
        assert "tentative semi-ouverte échouée" in state.reason

        refused = admit(projet, cooldown_minutes=30, now=now + timedelta(minutes=expected - 1))
        assert not refused.allowed, f"aucune tentative avant le délai doublé ({expected} min)"
        now += timedelta(minutes=expected)


def test_une_sonde_sans_verdict_ne_gele_pas_l_autonomie(projet: Path) -> None:
    """Un lancement admis puis perdu (aucun succès, aucun échec) ne doit pas bloquer le disjoncteur à jamais."""
    _open_by_count(projet)
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30)).probe
    assert not admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=45)).allowed
    retry = admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=61))
    assert retry.allowed and retry.probe


# ── 2. Un trip reste fermé à la main ─────────────────────────────────────────


def test_un_disjoncteur_ouvert_par_trip_ne_passe_jamais_semi_ouvert(projet: Path) -> None:
    trip(projet, "check modifié après la création de GAO-1", source="lot1")

    later = admit(projet, cooldown_minutes=1, now=T0 + timedelta(days=3))

    assert not later.allowed and "disjoncteur ouvert" in later.reason
    assert not read_breaker(projet).half_open
    assert record_success(projet, now=T0 + timedelta(days=3)).open  # un succès ne le referme pas
    assert read_breaker(projet).open


def test_un_trip_prend_la_main_sur_un_disjoncteur_de_comptage_semi_ouvert(projet: Path) -> None:
    """Un verdict forgé pendant la sonde semi-ouverte rend le disjoncteur manuel : plus de sonde, plus de refermeture."""
    _open_by_count(projet)
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30)).probe

    state = trip(projet, "GAO-1 : verdict forgé", source="reviewer")

    assert state.open and state.source == "reviewer" and not state.half_open
    assert not admit(projet, cooldown_minutes=1, now=T0 + timedelta(days=2)).allowed
    assert record_success(projet).open


# ── 3. Mise à l'écart par tâche ──────────────────────────────────────────────


def test_une_tache_qui_echoue_trois_fois_est_mise_a_l_ecart_et_ne_compte_qu_une_fois(projet: Path) -> None:
    for _ in range(3):
        record_failure(projet, 3, "boucle infinie", task_id="GAO-1", now=T0)

    state = read_breaker(projet)
    assert state.failures == 1 and not state.open  # trois échecs de la même tâche = un échec global
    assert state.parked_reason("GAO-1").startswith("3 échec(s) de la tâche")

    record_failure(projet, 3, "boucle infinie", task_id="GAO-1", now=T0)  # écartée : ne compte plus
    assert read_breaker(projet).failures == 1

    # Les autres tâches comptent normalement et ouvrent au seuil : une seule tâche ne peut pas ouvrir pour tout le monde.
    record_failure(projet, 3, "autre", task_id="GAO-2", now=T0)
    record_failure(projet, 3, "encore", task_id="GAO-3", now=T0)
    state = read_breaker(projet)
    assert state.open and state.failures == 3
    assert state.parked_reason("GAO-1")  # la mise à l'écart survit à l'ouverture


def test_la_tache_mise_a_l_ecart_n_est_plus_lancee(projet: Path) -> None:
    from test_launcher import ready, tick, write_policy  # réutilise les doubles du lot 2

    write_policy(projet)  # rôle worker activé
    service = TaskService(projet)
    tid = ready(service, "boucle")
    for _ in range(3):
        record_failure(projet, 3, "boucle", task_id=tid, now=T0)

    report = tick(projet, FakeHost())

    (outcome,) = [o for o in report.launches if o.task_id == tid]
    assert outcome.status == "skipped" and "mise à l'écart" in outcome.detail, outcome
    assert "3 échec(s) de la tâche" in outcome.detail


def test_reset_leve_les_mises_a_l_ecart(projet: Path) -> None:
    for _ in range(3):
        record_failure(projet, 3, "boucle", task_id="GAO-1", now=T0)
    assert read_breaker(projet).parked_reason("GAO-1")

    state = reset(projet, actor="guilhem")

    assert state.parked == () and state.task_failures == ()


# ── 4. L'état se dit (statut et cockpit) ─────────────────────────────────────


def test_le_statut_autopilot_dit_semi_ouvert_et_les_taches_ecartees(projet: Path) -> None:
    _open_by_count(projet)
    for _ in range(3):
        record_failure(projet, 3, "boucle", task_id="GAO-1", now=T0)
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30)).probe

    result = runner.invoke(app, ["autonomy", "autopilot", "status", "--project-root", str(projet)])

    assert result.exit_code == 0, result.output
    assert "semi-ouvert" in result.output.lower()
    assert "GAO-1" in result.output and "mise à l'écart" in result.output.lower()


def test_la_carte_d_attention_dit_semi_ouvert_et_mise_a_l_ecart(projet: Path) -> None:
    _open_by_count(projet)
    for _ in range(3):
        record_failure(projet, 3, "boucle", task_id="GAO-1", now=T0)
    assert admit(projet, cooldown_minutes=30, now=T0 + timedelta(minutes=30)).probe

    def _never_probe(service: str, url: str) -> str:
        raise AssertionError(f"aucun service ne devait être sondé ({service} {url})")

    view = wa.attention_view(
        projet, service_probe=_never_probe, watch=wa.ServiceWatch(),
        pr_reader=lambda _repo, _cwd: [], review_cache=ReviewCache(), pr_wait=1.0,
    )
    first = view["sections"][0]["items"][0]

    assert first["kind"] == wa.BREAKER
    assert "semi-ouvert" in first["title"].lower()
    assert "GAO-1" in first["reason"] and "mise à l'écart" in first["reason"].lower()
    assert first["ref"]["gesture"]["command"] == "grimoire orchestrate breaker reset"


# ── Réglage : cooldown_minutes ───────────────────────────────────────────────


def test_le_delai_est_reglable_et_le_defaut_reste_30(projet: Path) -> None:
    assert load_orchestrator_policy(projet).policy.breaker_cooldown_minutes == DEFAULT_COOLDOWN_MINUTES  # type: ignore[union-attr]

    (projet / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (projet / POLICY).write_text("orchestrator:\n  breaker:\n    cooldown_minutes: 90\n", encoding="utf-8")
    read = load_orchestrator_policy(projet)
    assert read.usable and read.policy is not None and read.policy.breaker_cooldown_minutes == 90


@pytest.mark.parametrize("value", ["0", "-5", "1.5", "'trente'"])
def test_un_delai_invalide_rend_la_politique_illisible(projet: Path, value: str) -> None:
    (projet / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (projet / POLICY).write_text(f"orchestrator:\n  breaker:\n    cooldown_minutes: {value}\n", encoding="utf-8")

    read = load_orchestrator_policy(projet)

    assert not read.usable and "cooldown_minutes" in read.error


def test_une_cle_inconnue_du_bloc_breaker_rend_la_politique_illisible(projet: Path) -> None:
    (projet / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (projet / POLICY).write_text("orchestrator:\n  breaker:\n    cooldow: 5\n", encoding="utf-8")

    read = load_orchestrator_policy(projet)

    assert not read.usable and "cooldow" in read.error


def test_le_tick_d_un_projet_sans_politique_garde_le_comportement_d_avant(projet: Path) -> None:
    """Rôle désactivé : le tick ne lit pas plus le disjoncteur qu'avant, et n'ouvre rien."""
    from test_launcher import ready, tick  # réutilise les doubles du lot 2

    service = TaskService(projet)
    ready(service, "sans politique")
    report = tick(projet, FakeHost())
    assert report.breaker is None and not read_breaker(projet).open
