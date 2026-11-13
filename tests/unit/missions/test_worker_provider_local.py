"""#1154 : un worker peut router vers un fournisseur local en boucle locale, sans clé.

Le constat : ``route_worker`` exigeait une URL ``https://`` et une clé au magasin. Un fournisseur
``provider_type: local`` sur ``http://127.0.0.1`` (Ollama) était donc refusé, et les données qui ne doivent pas
quitter le poste n'avaient aucun worker possible.

Ce que ce fichier prouve :

1. un fournisseur ``local`` en ``http://`` sur ``127.0.0.1``, ``localhost`` ou ``::1`` est routé sans clé ;
2. toute autre URL ``http://`` reste refusée — y compris un hôte hébergé déguisé en adresse locale ;
3. un seul worker à la fois par fournisseur local : le suivant attend au lieu de retomber sur l'hôte ;
4. le motif d'un refus est visible dans ``task show``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.launcher import LAUNCHED_EVENT, WORKER_DEFERRED_EVENT
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskCheck, TaskState, TaskType
from grimoire.missions.service import TaskService
from grimoire.missions.worker_provider import route_denial, route_worker

POLICY = "_grimoire/standard/orchestration-policy.yaml"
REGISTRY = "_grimoire/standard/llm-provider-registry.yaml"
runner = CliRunner()

#: Jeton factice que le worker écrit pour un serveur local : il n'authentifie rien.
_JETON_LOCAL = "local"

_TASK = {
    "id": "T-1",
    "mission_id": "M",
    "title": "t",
    "status": TaskState.READY,
    "type": TaskType.IMPLEMENTATION,
    "risk_profile": RiskProfile.STANDARD,
    "acceptance": ("pytest tests/x.py : le code de sortie vaut 0",),
    "created_at": "2026-11-12T00:00:00Z",
}


def task(data_class: str = "") -> MissionTask:
    return MissionTask(**_TASK, data_class=data_class)


def provider(url: str, *, provider_type: str = "local", provider_id: str = "ollama") -> str:
    return (
        f"  - id: {provider_id}\n"
        "    enabled: true\n"
        f"    provider_type: {provider_type}\n"
        f"    anthropic_base_url: {url}\n"
        "    models: [{id: qwen-local, tier: cheap}]\n"
    )


def write_registry(root: Path, providers: str) -> None:
    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f'$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n{providers}routing: {{default_data_class: ""}}\n',
        encoding="utf-8",
    )


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
        "      provider: ollama\n"
        "      tier: cheap\n"
        "  breaker:\n"
        "    threshold: 3\n",
        encoding="utf-8",
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un HOME isolé : les réglages du worker ne touchent jamais le vrai magasin."""
    root = tmp_path / "home"
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    return root


@pytest.fixture
def projet(home: Path) -> Path:
    return make_repo(home.parent)


def tick(projet: Path, host: FakeHost) -> Any:
    return run_tick(projet, apply=True, exec_=host)


def ready_task(service: TaskService, title: str = "local", *, data_class: str = "") -> str:
    mission = service.ledger.create_mission(title="Local", origin="test", created_by="test")
    created = service.ledger.create_task(
        mission.id,
        title,
        acceptance=("pytest tests/x.py : le code de sortie vaut 0",),
        owner="amelia",
        checks=(TaskCheck(cmd="pytest tests/x.py"),),
        data_class=data_class,
    )
    service.ledger.transition_task(created.id, TaskState.READY)
    return created.id


def events_of(service: TaskService, tid: str, kind: str) -> Any:
    return [event for event in service.ledger.list_events(tid) if event.event_type == kind]


def show(projet: Path, tid: str) -> str:
    result = runner.invoke(app, ["task", "show", tid, "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    return result.output


# ── 1. La boucle locale est admise sans clé ──────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"],
)
def test_un_fournisseur_local_sur_la_boucle_locale_est_route_sans_cle(projet: Path, url: str) -> None:
    write_registry(projet, provider(url))

    route = route_worker(projet, "ollama", "cheap", task())

    assert route is not None, route_denial(projet, "ollama", "cheap", task())
    assert (route.provider, route.model) == ("ollama", "qwen-local")
    settings = json.loads(route.settings.read_text(encoding="utf-8"))
    assert settings["env"]["ANTHROPIC_BASE_URL"] == url
    assert "apiKeyHelper" not in settings  # aucune clé n'est exigée d'un serveur local
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == _JETON_LOCAL  # le serveur local ne l'authentifie pas
    assert settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "qwen-local"


# ── 2. Tout autre http:// reste refusé ───────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20:11434",  # LAN
        "http://10.0.0.5:8080",  # réseau privé
        "http://example.com:80",  # distant
        "http://127.0.0.1.evil.example:11434",  # préfixe qui ressemble à une boucle
        "http://localhost.evil.example:11434",  # suffixe qui ressemble à localhost
        "http://127.0.0.1@evil.example:11434",  # userinfo : l'hôte réel est evil.example
    ],
)
def test_une_url_http_hors_boucle_locale_reste_refusee(projet: Path, url: str) -> None:
    write_registry(projet, provider(url))

    assert route_worker(projet, "ollama", "cheap", task()) is None
    motif = route_denial(projet, "ollama", "cheap", task())
    assert "boucle locale" in motif


def test_un_fournisseur_heberge_en_http_localhost_reste_refuse(projet: Path) -> None:
    """Le type dit que les données sortent : une adresse locale ne le rend pas faux (échec fermé)."""
    write_registry(projet, provider("http://127.0.0.1:11434", provider_type="hosted"))

    assert route_worker(projet, "ollama", "cheap", task()) is None
    assert "URL non admise" in route_denial(projet, "ollama", "cheap", task())


def test_un_fournisseur_local_https_hors_boucle_reste_refuse(projet: Path) -> None:
    """« local » veut dire que rien ne sort : une URL distante, même en https, ne le respecte pas."""
    write_registry(projet, provider("https://api.remote.example/anthropic"))

    assert route_worker(projet, "ollama", "cheap", task()) is None
    assert "URL non admise" in route_denial(projet, "ollama", "cheap", task())


# ── Le lancement de bout en bout ─────────────────────────────────────────────


def test_le_tick_route_une_tache_vers_le_fournisseur_local(projet: Path) -> None:
    write_policy(projet)
    write_registry(projet, provider("http://127.0.0.1:11434"))
    host = FakeHost()
    tid = ready_task(TaskService(projet))

    tick(projet, host)

    (argv,) = host.of("claude", "--bg")
    assert argv[argv.index("--model") + 1] == "qwen-local"
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    (launch,) = events_of(TaskService(projet), tid, LAUNCHED_EVENT)
    assert launch.payload["provider"] == "ollama"


def test_une_classe_qu_aucun_heberge_n_admet_part_chez_le_local(projet: Path) -> None:
    """Le cas du constat : des données qui ne doivent pas sortir trouvent enfin un worker."""
    write_policy(projet)
    write_registry(projet, provider("http://127.0.0.1:11434"))
    host = FakeHost()
    tid = ready_task(TaskService(projet), data_class="secret-projet")

    tick(projet, host)

    (argv,) = host.of("claude", "--bg")
    assert "--settings" in argv
    after = TaskService(projet)
    assert after.require(tid).status is TaskState.CLAIMED
    assert events_of(after, tid, WORKER_DEFERRED_EVENT) == []


# ── 3. Un seul worker à la fois par fournisseur local ────────────────────────


def test_un_seul_worker_a_la_fois_par_fournisseur_local(projet: Path) -> None:
    write_policy(projet)
    write_registry(projet, provider("http://127.0.0.1:11434"))
    host = FakeHost()
    service = TaskService(projet)
    first = ready_task(service, "première")
    second = ready_task(service, "seconde")

    tick(projet, host)

    assert len(host.of("claude", "--bg")) == 1  # le second n'est pas lancé
    after = TaskService(projet)
    assert after.require(first).status is TaskState.CLAIMED
    assert after.require(second).status is TaskState.READY  # il attend, il ne part pas sur l'hôte
    (deferred,) = events_of(after, second, WORKER_DEFERRED_EVENT)
    assert "un seul worker à la fois" in deferred.payload["reason"]
    shown = show(projet, second)
    assert "différée" in shown and "un seul worker à la fois" in shown


# ── 4. Le motif d'un refus est visible dans task show ────────────────────────


def test_le_motif_d_un_refus_est_visible_dans_task_show(projet: Path) -> None:
    write_policy(projet)
    write_registry(projet, provider("http://192.168.1.20:11434"))  # LAN, pas une boucle locale
    host = FakeHost()
    tid = ready_task(TaskService(projet))

    tick(projet, host)

    (argv,) = host.of("claude", "--bg")
    assert "--settings" not in argv  # pas de route : le modèle de l'hôte
    (launch,) = events_of(TaskService(projet), tid, LAUNCHED_EVENT)
    assert "boucle locale" in launch.payload["provider_refused"]
    shown = show(projet, tid)
    assert "repli sur l'hôte" in shown and "boucle locale" in shown
