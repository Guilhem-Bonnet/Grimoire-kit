"""Le choix de modèle d'un worker : défauts par classe, trace au ledger, montée en gamme après un refus de relecture."""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from test_launcher import _to_review, ready, ready_v0, tick, write_policy
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import launcher
from grimoire.missions.breaker import trip
from grimoire.missions.launcher import (
    LAUNCHED_EVENT,
    WORKER_RESUMED_EVENT,
    ExecResult,
    launched_sessions,
    resume_worker,
)
from grimoire.missions.model_routing import (
    REASON_DEFAULT_CLASS,
    REASON_HOST_DEFAULT,
    REASON_POLICY_CLASS,
    REASON_POLICY_MODEL,
    REASON_POLICY_TIER,
    REASON_SENSITIVE_PATH,
    RoutingTrace,
    class_tier,
    describe_routing,
    step_up,
    tier_for,
)
from grimoire.missions.orchestrator_policy import WorkerRole, load_orchestrator_policy
from grimoire.missions.schemas import TaskCheck, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.verifiability import Verifiability

runner = CliRunner()
_TABLE = {"cheap": "haiku", "mid": "sonnet", "strong": "opus"}
_DEFAUTS = WorkerRole(default_routing=True)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def ready_v1(service: TaskService, title: str) -> str:
    """Un verdict mécanique sans commande déclarée : V1."""
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="Lot", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=("pytest tests/x.py : le code de sortie vaut 0",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _launch(service: TaskService, tid: str) -> dict:  # type: ignore[type-arg]
    (event,) = [e for e in service.ledger.list_events(tid) if e.event_type == LAUNCHED_EVENT]
    return dict(event.payload)


# ── tier_for ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("klass", "tier"), [(Verifiability.V0, "cheap"), (Verifiability.V1, "mid"), (Verifiability.V2, "strong")]
)
def test_sans_reglage_le_palier_suit_la_classe(klass: Verifiability, tier: str) -> None:
    assert class_tier(klass) == tier
    assert tier_for(WorkerRole(default_routing=True), klass) == (tier, REASON_DEFAULT_CLASS)


def test_chaque_reglage_explicite_garde_son_comportement() -> None:
    v0, v1 = Verifiability.V0, Verifiability.V1
    assert tier_for(WorkerRole(), v1) == ("", REASON_HOST_DEFAULT)
    assert tier_for(WorkerRole(tier="mid"), v0) == ("mid", REASON_POLICY_TIER)
    assert tier_for(WorkerRole(tier="mid", model_by_class=True), v0) == ("cheap", REASON_POLICY_CLASS)
    assert tier_for(WorkerRole(tier="mid", model_by_class=True), v1) == ("mid", REASON_POLICY_TIER)
    assert tier_for(WorkerRole(model="opus", tier="mid"), v1) == ("mid", REASON_POLICY_MODEL)
    assert tier_for(WorkerRole(default_routing=True), v0, sensitive=True) == ("strong", REASON_SENSITIVE_PATH)


def test_la_politique_sans_reglage_de_modele_active_les_defauts(projet: Path) -> None:
    role = load_orchestrator_policy(projet).policy.worker  # type: ignore[union-attr]
    assert role.default_routing and role.sensitive_route == "strong"


@pytest.mark.parametrize("extra", ["      tier: mid\n", "      model: opus\n", "      model_by_class: false\n", "      provider: x\n"])
def test_un_reglage_explicite_ecarte_les_defauts(projet: Path, extra: str) -> None:
    write_policy(projet, extra=extra)
    policy = load_orchestrator_policy(projet).policy
    if policy is None:  # un fournisseur inconnu rend la politique illisible : pas de défaut non plus
        return
    assert not policy.worker.default_routing and policy.worker.sensitive_route == "off"


def test_sensitive_route_explicite_prime_sur_le_defaut(projet: Path) -> None:
    write_policy(projet, extra="      sensitive_route: off\n")
    role = load_orchestrator_policy(projet).policy.worker  # type: ignore[union-attr]
    assert role.default_routing and role.sensitive_route == "off"


# ── le lancement : modèle et trace ───────────────────────────────────────────


def test_sans_reglage_v0_part_au_modele_economique_et_le_dit(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready_v0(service, "mécanique")
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert argv[argv.index("--model") + 1] == "haiku"
    payload = _launch(service, tid)
    assert payload["routing"] == {"class": "V0", "tier": "cheap", "model": "haiku", "reason": REASON_DEFAULT_CLASS}
    shown = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert "routage : haiku (palier cheap, classe V0) — défaut du kit pour cette classe" in shown.output, shown.output


def test_sans_reglage_v1_mid_et_v2_strong(projet: Path, service: TaskService, host: FakeHost) -> None:
    v1, v2 = ready_v1(service, "à relire"), ready(service, "à juger")
    tick(projet, host)
    assert _launch(service, v1)["model"] == "sonnet" and _launch(service, v1)["routing"]["tier"] == "mid"
    assert _launch(service, v2)["model"] == "opus" and _launch(service, v2)["routing"]["tier"] == "strong"


def test_un_projet_a_palier_explicite_ne_change_pas(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, extra="      tier: mid\n")
    tid = ready(service, "à juger")
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert _launch(service, tid)["routing"]["reason"] == REASON_POLICY_TIER


def test_un_projet_model_by_class_false_garde_le_modele_de_l_hote(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, extra="      model_by_class: false\n")
    tid = ready_v0(service, "mécanique")
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert "--model" not in argv
    assert _launch(service, tid)["routing"] == {"class": "V0", "tier": "", "model": "", "reason": REASON_HOST_DEFAULT}
    shown = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert "modèle par défaut de l'hôte" in shown.output


def test_un_chemin_sensible_part_au_modele_fort_sans_reglage(projet: Path, service: TaskService, host: FakeHost) -> None:
    mission = service.ledger.create_mission(title="Lot", origin="test")
    task = service.ledger.create_task(
        mission.id, "garde", acceptance=("pytest tests/x.py : le code de sortie vaut 0",),
        description="Durcir `src/grimoire/missions/launcher.py`.", checks=(TaskCheck(cmd="pytest tests/x.py"),),
    )
    service.ledger.transition_task(task.id, TaskState.READY)
    tick(projet, host)
    (argv,) = host.of("claude", "--bg")
    assert argv[argv.index("--model") + 1] == "opus"
    assert _launch(service, task.id)["routing"]["reason"] == REASON_SENSITIVE_PATH


# ── la montée en gamme ───────────────────────────────────────────────────────


def _bg_neuves(host: FakeHost) -> list[list[str]]:
    return [a for a in host.of("claude", "--bg") if "--resume" not in a]


def _worker_en_revue(projet: Path, host: FakeHost, service: TaskService, *, v0: bool = True) -> str:
    """Une tâche lancée, rendue en revue et son worker arrêté (preuve d'arrêt au ledger)."""
    tid = ready_v0(service, "mécanique") if v0 else ready(service, "à juger")
    tick(projet, host)
    _to_review(projet, tid)
    tick(projet, host)
    return tid


def _renvoi(projet: Path, tid: str, host: object, *, escalate: bool = True) -> ExecResult:
    launch = launched_sessions(TaskService(projet))[tid]
    TaskService(projet).ledger.transition_task(tid, TaskState.RUNNING, actor_id="reviewer")
    return resume_worker(
        TaskService(projet), host, "claude", tid, launch, "corrige", permission_mode="acceptEdits", escalate=escalate  # type: ignore[arg-type]
    )


def _modele_courant(projet: Path, tid: str) -> str:
    return str(launched_sessions(TaskService(projet))[tid]["model"])


def test_un_refus_de_relecture_remonte_d_une_marche_plafonnee_et_tracee(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    write_policy(projet, per_day=9)
    tid = _worker_en_revue(projet, host, service)
    assert _renvoi(projet, tid, host).returncode == 0
    argv = _bg_neuves(host)[-1]  # session neuve : `--resume` ne reçoit pas de modèle
    assert argv[argv.index("--model") + 1] == "sonnet"
    (resumed,) = [e for e in TaskService(projet).ledger.list_events(tid) if e.event_type == WORKER_RESUMED_EVENT]
    assert resumed.payload["routing"] == {
        "class": "V0", "tier": "mid", "model": "sonnet", "reason": "step_up", "detail": "cheap → mid",
    }
    assert _modele_courant(projet, tid) == "sonnet"
    shown = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert shown.output.count("montée en gamme après un refus de relecture") == 1, shown.output
    assert "routage : sonnet (palier mid, classe V0) — montée en gamme après un refus de relecture (cheap → mid)" in shown.output
    for attendu in ("opus", "opus"):  # deuxième refus : le palier fort, puis plus rien ne monte
        _to_review(projet, tid)
        tick(projet, host)
        _renvoi(projet, tid, host)
        assert _modele_courant(projet, tid) == attendu
    assert len(_bg_neuves(host)) == 3  # le lancement d'origine, puis deux montées seulement


def test_sans_escalade_la_reprise_garde_le_modele(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = _worker_en_revue(projet, host, service)
    _renvoi(projet, tid, host, escalate=False)
    argv = host.of("claude", "--bg", "--resume")[-1]
    assert "--model" not in argv and len(_bg_neuves(host)) == 1


@pytest.mark.parametrize("extra", ["      tier: cheap\n", "      model: haiku\n", "      model_by_class: true\n      tier: cheap\n"])
def test_d1_un_projet_qui_a_regle_son_modele_ne_monte_jamais(projet: Path, service: TaskService, host: FakeHost, extra: str) -> None:
    write_policy(projet, extra=extra)
    tid = _worker_en_revue(projet, host, service, v0=False)
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1 and host.of("claude", "--bg", "--resume")
    assert not [e for e in TaskService(projet).ledger.list_events(tid) if e.payload.get("routing", {}).get("reason") == "step_up"]


def test_d1_la_politique_est_relue_a_la_reprise(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = _worker_en_revue(projet, host, service)
    write_policy(projet, extra="      tier: cheap\n")  # réglée entre le lancement et le refus
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1


def test_d2_un_lancement_ancien_sous_modele_impose_ne_monte_pas(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, extra="      model: sonnet\n")
    tid = _worker_en_revue(projet, host, service, v0=False)
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1 and _modele_courant(projet, tid) == "sonnet"


def test_d3_jamais_de_descente_le_palier_est_celui_du_modele_reel() -> None:
    routed = {"model": "opus", "routing": {"class": "V2", "tier": "cheap", "model": "opus", "reason": "router"}}
    assert step_up(_DEFAUTS, routed, _TABLE.__getitem__) is None
    assert step_up(_DEFAUTS, {"model": "autre"}, _TABLE.__getitem__) is None
    assert step_up(_DEFAUTS, {"model": ""}, _TABLE.__getitem__) is None
    assert step_up(_DEFAUTS, {"model": "haiku", "provider": "deepseek"}, _TABLE.__getitem__) is None
    found = step_up(_DEFAUTS, {"model": "haiku"}, _TABLE.__getitem__)
    assert found is not None and (found.model, found.previous_tier, found.trace.klass) == ("sonnet", "cheap", "")


def test_d3_une_table_ou_deux_paliers_partagent_un_modele_monte_au_premier_different() -> None:
    table = {"cheap": "sonnet", "mid": "sonnet", "strong": "opus"}
    found = step_up(_DEFAUTS, {"model": "sonnet", "routing": {"class": "V1"}}, table.__getitem__)
    assert found is not None and (found.model, found.tier, found.trace.klass) == ("opus", "strong", "V1")
    assert step_up(_DEFAUTS, {"model": "haiku"}, {"cheap": "haiku", "mid": "", "strong": ""}.__getitem__) is None


def test_d1_d2_step_up_lui_meme_refuse_un_projet_qui_a_regle_son_modele() -> None:
    for role in (WorkerRole(), WorkerRole(tier="cheap"), WorkerRole(model="sonnet"), WorkerRole(default_routing=True, model="sonnet")):
        assert step_up(role, {"model": "sonnet"}, _TABLE.__getitem__) is None


def test_d4_le_plafond_de_sessions_du_jour_borne_la_montee(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, per_day=1)
    tid = _worker_en_revue(projet, host, service)
    assert _renvoi(projet, tid, host).returncode == 0
    assert len(_bg_neuves(host)) == 1 and host.of("claude", "--bg", "--resume")  # comme sur main : reprise sans montée


def test_d4_le_mandat_et_le_disjoncteur_bornent_la_montee(
    projet: Path, service: TaskService, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_policy(projet, per_day=9)
    tid = _worker_en_revue(projet, host, service)
    monkeypatch.setattr(launcher, "mandate_precheck", lambda *_a: "plafond de mandat")
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1
    monkeypatch.undo()
    _to_review(projet, tid)
    tick(projet, host)
    trip(projet, "panne")
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1


def test_d5_une_montee_qui_echoue_se_rabat_sur_la_reprise_ordinaire(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, per_day=9)
    tid = _worker_en_revue(projet, host, service)

    def exec_(argv, cwd, env):  # type: ignore[no-untyped-def]
        if "--bg" in argv and "--resume" not in argv:
            return ExecResult(1, "", "claude : échec")
        return host(argv, cwd, env)

    result = _renvoi(projet, tid, exec_)
    assert result.returncode == 0 and host.of("claude", "--bg", "--resume")
    assert _modele_courant(projet, tid) == "haiku"


def test_d7_une_session_neuve_sans_identifiant_est_un_echec_pas_une_session_non_attestee(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    write_policy(projet, per_day=9)
    tid = _worker_en_revue(projet, host, service)

    def exec_(argv, cwd, env):  # type: ignore[no-untyped-def]
        if "--bg" in argv and "--resume" not in argv:
            return ExecResult(0, "rien d'utile\n")
        return host(argv, cwd, env)

    assert _renvoi(projet, tid, exec_).returncode == 0
    assert host.of("claude", "--bg", "--resume") and _modele_courant(projet, tid) == "haiku"


def test_une_session_de_montee_non_attestee_est_arretee_puis_reprise_ordinaire(
    projet: Path, service: TaskService, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_policy(projet, per_day=9)
    tid = _worker_en_revue(projet, host, service)
    monkeypatch.setattr(launcher, "attest_session", lambda *_a: ("inscription refusée", "x"))
    assert _renvoi(projet, tid, host).returncode == 0
    assert host.of("claude", "stop") and host.of("claude", "--bg", "--resume")
    assert _modele_courant(projet, tid) == "haiku"


def test_d6_pas_de_session_neuve_tant_que_l_ancienne_n_est_pas_arretee(projet: Path, service: TaskService, host: FakeHost) -> None:
    write_policy(projet, per_day=9)
    tid = ready_v0(service, "mécanique")
    tick(projet, host)
    _to_review(projet, tid)  # le tick n'a pas encore arrêté la session
    _renvoi(projet, tid, host)
    assert len(_bg_neuves(host)) == 1 and host.of("claude", "--bg", "--resume")


def test_le_detail_de_trace_est_borne_et_sans_caractere_de_controle() -> None:
    hostile = RoutingTrace("V0", "cheap", "x", "step_up", "a\x1b[2J\n" + "b" * 5000).to_dict()
    assert len(hostile["detail"]) <= 300 and "\x1b" not in hostile["detail"] and "\n" not in hostile["detail"]
    line = describe_routing({"model": "m\x1b[2J", "reason": "r\n" * 500, "detail": "d" * 5000, "tier": "t\x00"})
    assert len(line) < 600 and all(c.isprintable() for c in line)
    assert describe_routing({"reason": "inconnue"}) == "modèle par défaut de l'hôte — inconnue"
