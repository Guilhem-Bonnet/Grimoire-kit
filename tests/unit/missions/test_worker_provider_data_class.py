"""Le repli du worker sur le modèle de l'hôte respecte la classe de données.

Constat : quand ``route_worker`` refuse la route vers le fournisseur configuré, le worker partait en silence sur le
modèle de l'hôte — lui aussi un modèle hébergé. Une classe que **aucun** fournisseur hébergé activé n'admet ferme
désormais ce repli : la tâche retourne en ``ready``, son motif est au ledger (``task.worker_deferred``), dans
``task show`` et dans l'attention du tick, sans question à l'humain. Dans les autres cas le repli reste possible et
son motif (``provider_refused``) devient visible dans ``task show``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.launcher import LAUNCHED_EVENT, WORKER_DEFERRED_EVENT
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.schemas import TaskCheck, TaskState
from grimoire.missions.service import TaskService
from grimoire.providers.data_policy import hosted_fallback_refusal
from grimoire.providers.secrets import set_secret

POLICY = "_grimoire/standard/orchestration-policy.yaml"
REGISTRY = "_grimoire/standard/llm-provider-registry.yaml"
runner = CliRunner()

_EXT = """\
  - id: ext
    enabled: true
    provider_type: hosted
    anthropic_base_url: https://ext.example/anthropic
    models: [{id: ext-cheap, tier: cheap}]
    data_policy:
      allowed_data_classes: [project-source]
      forbidden_data_classes: [secret-projet]
"""
#: Un second fournisseur hébergé qui, lui, admet la classe : le repli n'est pas globalement interdit.
_OUVERT = """\
  - id: ouvert
    enabled: true
    provider_type: hosted
    models: [{id: ouvert-mid, tier: mid}]
    data_policy:
      allowed_data_classes: [project-source, secret-projet]
      forbidden_data_classes: []
"""
#: Un fournisseur local n'a pas de data_policy : il n'admet aucune classe hébergée à lui seul.
_LOCAL = """\
  - id: ici
    enabled: true
    provider_type: local
    models: [{id: ici-mid, tier: mid}]
"""


def write_policy(root: Path) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n"
        "  roles:\n"
        "    worker:\n"
        "      enabled: true\n"
        "      wip: 3\n"
        "      sessions_per_day: 5\n"
        "      permission_mode: acceptEdits\n"
        "      provider: ext\n"
        "      tier: cheap\n"
        "  breaker:\n"
        "    threshold: 3\n",
        encoding="utf-8",
    )


def write_registry(root: Path, providers: str, *, default: str = "") -> None:
    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f'$schema: "grimoire-llm-provider-registry/v1"\n'
        f"providers:\n{providers}"
        f'routing: {{default_data_class: "{default}"}}\n',
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = make_repo(tmp_path)
    write_policy(root)
    return root


def ready_task(service: TaskService, *, data_class: str = "") -> str:
    mission = service.ledger.create_mission(title="Repli", origin="test", created_by="test")
    task = service.ledger.create_task(
        mission.id,
        "une tâche V0",
        acceptance=("pytest tests/x.py : le code de sortie vaut 0",),
        owner="amelia",
        checks=(TaskCheck(cmd="pytest tests/x.py"),),
        data_class=data_class,
    )
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def tick(projet: Path, host: FakeHost) -> Any:
    return run_tick(projet, apply=True, exec_=host)


def events_of(service: TaskService, tid: str, kind: str) -> Any:
    return [event for event in service.ledger.list_events(tid) if event.event_type == kind]


def show(projet: Path, tid: str) -> str:
    result = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    return result.output


# ── La règle pure ─────────────────────────────────────────────────────────────


def test_sans_classe_declaree_le_repli_reste_permis(projet: Path) -> None:
    write_registry(projet, _EXT)
    assert hosted_fallback_refusal(projet, "") == ""


def test_une_classe_admise_par_un_fournisseur_heberge_laisse_le_repli(projet: Path) -> None:
    write_registry(projet, _EXT + _OUVERT)
    assert hosted_fallback_refusal(projet, "secret-projet") == ""


def test_une_classe_qu_aucun_heberge_n_admet_ferme_le_repli(projet: Path) -> None:
    write_registry(projet, _EXT)
    motif = hosted_fallback_refusal(projet, "secret-projet")
    assert "secret-projet" in motif and "aucun fournisseur hébergé" in motif


def test_un_fournisseur_local_ne_rend_pas_la_classe_hebergee(projet: Path) -> None:
    write_registry(projet, _EXT + _LOCAL)
    assert "aucun fournisseur hébergé" in hosted_fallback_refusal(projet, "secret-projet")


def test_la_classe_du_projet_compte_pour_le_repli(projet: Path) -> None:
    write_registry(projet, _EXT, default="secret-projet")
    assert "aucun fournisseur hébergé" in hosted_fallback_refusal(projet, "")


# ── Le lanceur ────────────────────────────────────────────────────────────────


def test_le_worker_ne_se_rabat_pas_sur_l_hote_et_dit_pourquoi(projet: Path) -> None:
    write_registry(projet, _EXT)
    set_secret("ext", "EXT_API_KEY", "sk-test")
    host = FakeHost()
    service = TaskService(projet)
    tid = ready_task(service, data_class="secret-projet")

    report = tick(projet, host)

    assert host.of("claude", "--bg") == []  # aucun repli silencieux sur le modèle de l'hôte
    after = TaskService(projet)
    assert after.require(tid).status is TaskState.READY
    assert after.require(tid).waiting is None  # différée, jamais une question bloquante
    assert events_of(after, tid, "task.question_asked") == []
    (deferred,) = events_of(after, tid, WORKER_DEFERRED_EVENT)
    assert "aucun fournisseur hébergé" in deferred.payload["reason"]
    assert any(tid in ligne and "repli sur le modèle de l'hôte refusé" in ligne for ligne in report.attention)
    assert "différée" in show(projet, tid)


def test_un_repli_permis_reste_possible_et_son_motif_est_visible(projet: Path) -> None:
    write_registry(projet, _EXT + _OUVERT)
    set_secret("ext", "EXT_API_KEY", "sk-test")
    host = FakeHost()
    service = TaskService(projet)
    tid = ready_task(service, data_class="secret-projet")

    tick(projet, host)

    (argv,) = host.of("claude", "--bg")
    assert "--settings" not in argv  # le modèle de l'hôte, pas celui du fournisseur refusé
    after = TaskService(projet)
    assert after.require(tid).status is TaskState.CLAIMED
    (launch,) = events_of(after, tid, LAUNCHED_EVENT)
    assert "interdite" in launch.payload["provider_refused"]
    shown = show(projet, tid)
    assert "repli sur l'hôte" in shown and "interdite" in shown


def test_sans_classe_declaree_le_comportement_actuel_est_garde(projet: Path) -> None:
    write_registry(projet, _EXT)
    set_secret("ext", "EXT_API_KEY", "sk-test")
    host = FakeHost()
    service = TaskService(projet)
    tid = ready_task(service)

    tick(projet, host)

    assert len(host.of("claude", "--bg")) == 1
    after = TaskService(projet)
    (launch,) = events_of(after, tid, LAUNCHED_EVENT)
    assert "aucune classe de données" in launch.payload["provider_refused"]
    shown = show(projet, tid)
    assert "repli sur l'hôte" in shown and "aucune classe de données" in shown
