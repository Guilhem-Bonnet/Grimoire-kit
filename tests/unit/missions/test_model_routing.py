"""Le choix de modèle d'un worker : défauts par classe, trace au ledger, montée en gamme après un refus de relecture."""

from __future__ import annotations

from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from test_launcher import _to_review, ready, ready_v0, tick, write_policy
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.launcher import LAUNCHED_EVENT, WORKER_RESUMED_EVENT, launched_sessions, resume_worker
from grimoire.missions.model_routing import (
    REASON_DEFAULT_CLASS,
    REASON_HOST_DEFAULT,
    REASON_POLICY_CLASS,
    REASON_POLICY_MODEL,
    REASON_POLICY_TIER,
    REASON_SENSITIVE_PATH,
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


def _renvoi(projet: Path, host: FakeHost, tid: str, launch: dict) -> None:  # type: ignore[type-arg]
    TaskService(projet).ledger.transition_task(tid, TaskState.RUNNING, actor_id="reviewer")
    assert resume_worker(
        TaskService(projet), host, "claude", tid, launch, "corrige", permission_mode="acceptEdits", escalate=True
    ).returncode == 0


def test_un_refus_de_relecture_remonte_d_une_marche_plafonnee_et_tracee(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = ready_v0(service, "mécanique")
    tick(projet, host)
    _to_review(projet, tid)
    tick(projet, host)
    _renvoi(projet, host, tid, launched_sessions(TaskService(projet))[tid])
    argv = host.of("claude", "--bg")[-1]  # session neuve : `--resume` ne reçoit pas de modèle
    assert "--resume" not in argv and argv[argv.index("--model") + 1] == "sonnet"
    (resumed,) = [e for e in TaskService(projet).ledger.list_events(tid) if e.event_type == WORKER_RESUMED_EVENT]
    assert resumed.payload["routing"] == {
        "class": "V0", "tier": "mid", "model": "sonnet", "reason": "step_up", "detail": "cheap → mid",
    }
    assert launched_sessions(TaskService(projet))[tid]["model"] == "sonnet"
    shown = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert "montée en gamme : sonnet (palier mid, classe V0) — montée en gamme après un refus de relecture (cheap → mid)" in shown.output
    # deuxième refus : le palier fort, puis plus rien ne monte
    _to_review(projet, tid)
    tick(projet, host)
    _renvoi(projet, host, tid, launched_sessions(TaskService(projet))[tid])
    assert launched_sessions(TaskService(projet))[tid]["model"] == "opus"
    _to_review(projet, tid)
    tick(projet, host)
    _renvoi(projet, host, tid, launched_sessions(TaskService(projet))[tid])
    assert launched_sessions(TaskService(projet))[tid]["model"] == "opus"
    assert len([e for e in TaskService(projet).ledger.list_events(tid) if e.event_type == WORKER_RESUMED_EVENT and e.payload.get("routing")]) == 2


def test_sans_escalade_la_reprise_garde_le_modele(projet: Path, service: TaskService, host: FakeHost) -> None:
    tid = ready_v0(service, "mécanique")
    tick(projet, host)
    launch = launched_sessions(TaskService(projet))[tid]
    _to_review(projet, tid)
    tick(projet, host)
    TaskService(projet).ledger.transition_task(tid, TaskState.RUNNING, actor_id="reviewer")
    resume_worker(TaskService(projet), host, "claude", tid, launch, "reprends", permission_mode="acceptEdits")
    argv = host.of("claude", "--bg", "--resume")[-1]
    assert "--model" not in argv  # la conversation reprise garde son modèle
    (resumed,) = [e for e in TaskService(projet).ledger.list_events(tid) if e.event_type == WORKER_RESUMED_EVENT]
    assert "routing" not in resumed.payload


def test_un_lancement_ancien_sans_trace_se_deduit_du_modele() -> None:
    found = step_up({"model": "haiku", "provider": ""}, _TABLE.__getitem__)
    assert found is not None and (found.model, found.previous_tier, found.trace.klass) == ("sonnet", "cheap", "")


@pytest.mark.parametrize(
    "launch",
    [
        {"model": "opus"},  # déjà fort
        {"model": "haiku", "provider": "deepseek"},  # route de fournisseur
        {"model": "autre"},  # palier inconnu
        {"model": "haiku", "routing": {"tier": "cheap", "reason": REASON_POLICY_MODEL}},  # modèle imposé
    ],
)
def test_rien_ne_monte_quand_le_palier_est_inconnu_impose_ou_deja_fort(launch: dict) -> None:  # type: ignore[type-arg]
    assert step_up(launch, _TABLE.__getitem__) is None


def test_rien_ne_monte_sans_modele_pour_le_palier_suivant() -> None:
    assert step_up({"model": "haiku", "routing": {"tier": "cheap"}}, {"cheap": "haiku"}.get) is None  # type: ignore[arg-type,return-value]


def test_describe_routing_sans_modele_ni_detail() -> None:
    assert describe_routing({"reason": "inconnue"}) == "modèle par défaut de l'hôte — inconnue"
