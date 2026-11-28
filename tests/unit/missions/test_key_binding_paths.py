"""Les chemins d'envoi d'une clé côté missions : appel du fournisseur, worker compatible Anthropic, worker opencode.

Chaque chemin donne le point d'accès visé au magasin ; une clé saisie pour une autre adresse n'est rendue à aucun
d'eux (faux transport : ``subprocess.run`` et ``spawn`` sont des doubles, aucun réseau, aucun processus).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import dispatch as dispatch_mod
from grimoire.missions.dispatch import _run_provider_call
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.worker_host import (
    OpencodeRoute,
    OpencodeWorkerHost,
    SessionSpec,
    opencode_route,
)
from grimoire.missions.worker_provider import route_denial, route_worker, settings_path
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import set_secret

SECRET = "sk-test-valeur-de-test-0002"  # noqa: S105 - valeur factice, jamais une vraie clé
REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")
GW = "https://gw.example/anthropic"
EVIL = "https://evil.example/anthropic"
runner = CliRunner()

_TASK = {
    "id": "T-1",
    "mission_id": "M",
    "title": "t",
    "status": TaskState.READY,
    "type": TaskType.IMPLEMENTATION,
    "risk_profile": RiskProfile.STANDARD,
    "acceptance": ("pytest tests/x.py : le code de sortie vaut 0",),
    "created_at": "2026-11-12T00:00:00Z",
    "data_class": "project-source",
}


def _provider(url: str, pid: str = "gw") -> str:
    return (
        f'  - id: "{pid}"\n    enabled: true\n    provider_type: "hosted"\n    currency: "api"\n'
        f'    anthropic_base_url: "{url}"\n    invocation: "{sys.executable} -c pass {{model}} {{prompt}}"\n'
        '    models:\n      - {id: "m1", tier: "cheap"}\n'
        '    data_policy: {allowed_data_classes: ["project-source"], forbidden_data_classes: []}\n'
    )


def _write(root: Path, url: str, pid: str = "gw") -> None:
    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f'providers:\n{_provider(url, pid)}routing: {{default_data_class: ""}}\n', encoding="utf-8"
    )


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _write(root, GW)
    result = runner.invoke(
        app, ["providers", "set-key", "gw", "GW_API_KEY", "--project-root", str(root), "--yes"], input=f"{SECRET}\n"
    )
    assert result.exit_code == 0, result.output
    return root


# ── Appel du fournisseur par `task dispatch` (la clé part dans l'environnement du sous-processus) ────────────────────


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    envs: list[dict[str, str]] = []

    def fake_run(argv: Any, **kwargs: Any) -> Any:
        envs.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(dispatch_mod.subprocess, "run", fake_run)
    return envs


def test_dispatch_adresse_inchangee_la_cle_est_dans_l_environnement(project: Path, sent: list[dict[str, str]]) -> None:
    provider = read_registry(project)[0]
    code, *_ = _run_provider_call(project, provider, "m1", "go", call_timeout=5)
    assert code == 0 and sent[0]["GW_API_KEY"] == SECRET


def test_dispatch_registre_reecrit_apres_la_saisie_la_cle_n_est_rendue_a_aucun_sous_processus(
    project: Path, sent: list[dict[str, str]]
) -> None:
    _write(project, EVIL)
    provider = read_registry(project)[0]
    code, out, err, _dur, kind, _signal = _run_provider_call(project, provider, "m1", "go", call_timeout=5)
    assert sent == [] and code != 0 and kind == "key_binding"  # le sous-processus n'est même pas lancé
    assert "a changé" in err and SECRET not in out + err and "evil.example" not in err


def test_dispatch_ancienne_cle_sans_empreinte_vers_une_adresse_personnalisee_est_refusee(
    tmp_path: Path, sent: list[dict[str, str]]
) -> None:
    root = tmp_path / "p2"
    root.mkdir()
    _write(root, "https://api.deepseek.com/anthropic", "deepseek")
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)  # d'avant la liaison
    code, *_ = _run_provider_call(root, read_registry(root)[0], "m1", "go", call_timeout=5)
    assert sent == [] and code != 0
    _write(root, "https://api.deepseek.com", "deepseek")  # même l'adresse officielle : la commande vient du registre
    code, *_ = _run_provider_call(root, read_registry(root)[0], "m1", "go", call_timeout=5)
    assert sent == [] and code != 0  # une clé ancienne n'est jamais remise à une commande du registre avant ressaisie
    result = runner.invoke(
        app, ["providers", "set-key", "deepseek", "DEEPSEEK_API_KEY", "--project-root", str(root), "--yes"], input=f"{SECRET}\n"
    )
    assert result.exit_code == 0
    code, *_ = _run_provider_call(root, read_registry(root)[0], "m1", "go", call_timeout=5)
    assert code == 0 and sent[0]["DEEPSEEK_API_KEY"] == SECRET


# ── Worker compatible Anthropic (réglages ``apiKeyHelper`` écrits au moment de la route) ─────────────────────────────


def test_worker_adresse_inchangee_la_route_est_ouverte_et_pointe_vers_cette_adresse(project: Path) -> None:
    route = route_worker(project, "gw", "cheap", MissionTask(**_TASK))
    assert route is not None
    assert GW in route.settings.read_text(encoding="utf-8")


def test_worker_registre_reecrit_apres_la_saisie_aucune_route_aucun_reglage(project: Path) -> None:
    _write(project, EVIL)
    assert route_worker(project, "gw", "cheap", MissionTask(**_TASK)) is None
    assert not settings_path("gw").exists()
    why = route_denial(project, "gw", "cheap", MissionTask(**_TASK))
    assert "a changé" in why and SECRET not in why and "evil.example" not in why


def test_session_de_l_arbitre_registre_reecrit_apres_la_saisie_aucun_reglage(project: Path) -> None:
    from grimoire.missions.worker_provider import model_settings

    model, settings = model_settings(project, "gw", "cheap")
    assert model == "m1" and settings is not None
    settings_path("gw").unlink()
    _write(project, EVIL)
    assert model_settings(project, "gw", "cheap") == ("", None)
    assert not settings_path("gw").exists()


def test_un_fournisseur_local_ignore_une_cle_liee_ailleurs(tmp_path: Path) -> None:
    root = tmp_path / "p4"
    root.mkdir()
    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        'providers:\n  - id: "ollama"\n    enabled: true\n    provider_type: "local"\n'
        '    anthropic_base_url: "http://127.0.0.1:11434"\n    models: [{id: "q", tier: "cheap"}]\n',
        encoding="utf-8",
    )
    set_secret("ollama", "OLLAMA_API_KEY", SECRET, endpoints=[GW])  # un reste d'une autre déclaration
    route = route_worker(root, "ollama", "cheap", MissionTask(**_TASK))
    assert route is not None
    assert "apiKeyHelper" not in route.settings.read_text(encoding="utf-8")  # aucune clé ne part vers le serveur local


# ── Worker opencode (la clé passe par l'environnement du processus détaché) ──────────────────────────────────────────

_OC = OpencodeRoute("gw", "deepseek", "m", key_var="GW_API_KEY", base_url="https://api.deepseek.com", source="policy")


def test_opencode_plan_une_cle_liee_ailleurs_n_est_pas_rendue(tmp_path: Path) -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    plan = OpencodeWorkerHost().plan(
        SessionSpec(name="n", prompt="p", permission_mode="acceptEdits", opencode=_OC, kit_home=tmp_path / "kit")
    )
    assert isinstance(plan, str) and "a changé" in plan and SECRET not in plan


def test_opencode_plan_adresse_officielle_la_cle_part_dans_l_environnement(tmp_path: Path) -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://api.deepseek.com"])
    plan = OpencodeWorkerHost().plan(
        SessionSpec(name="n", prompt="p", permission_mode="acceptEdits", opencode=_OC, kit_home=tmp_path / "kit")
    )
    assert not isinstance(plan, str) and plan.environ()["GW_API_KEY"] == SECRET


def test_opencode_route_une_cle_liee_ailleurs_ferme_la_route(tmp_path: Path) -> None:
    root = tmp_path / "p3"
    root.mkdir()
    _write(root, "https://api.deepseek.com/anthropic", "deepseek")
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET, endpoints=[EVIL])
    task = MissionTask(**_TASK)
    route, why = opencode_route(root, "deepseek", "cheap", task, model="m1")
    assert route is None and "a changé" in why and SECRET not in why
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET, endpoints=["https://api.deepseek.com"])
    route, why = opencode_route(root, "deepseek", "cheap", task, model="m1")
    assert route is not None, why
