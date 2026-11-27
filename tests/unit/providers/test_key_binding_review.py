"""Liaison d'une clé à son point d'accès, suite (relecture adversariale de #1491).

Une cible vide ne laisse pas passer une clé liée ; la commande d'invocation entre dans la liaison ; la saisie montre
ce qu'elle approuve ; la normalisation ne confond pas deux chemins ; un index mal formé n'arrête pas la ressaisie ;
un refus ne relance rien en boucle ; `doctor` dit les clés non liées. ``HOME`` est isolé, aucun réseau.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import dispatch as dispatch_mod
from grimoire.missions import mandate as md
from grimoire.missions.dispatch import _run_provider_call, run_dispatch
from grimoire.missions.service import TaskService
from grimoire.providers import availability as av
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import (
    KeyEndpointError,
    env_for_provider,
    list_secrets,
    normalize_endpoint,
    remove_secret,
    set_secret,
    store_dir,
)
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX

SECRET = "sk-test-valeur-de-test-0003"  # noqa: S105 - valeur factice, jamais une vraie clé
STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"
GW = "https://gw.example/anthropic"
CMD = "gw-cli {model} {prompt}"
runner = CliRunner()


def _provider(
    url: str | None = GW, *, pid: str = "gw", ptype: str = "hosted", invocation: str | None = CMD, field: str = "anthropic_base_url"
) -> str:
    declared = f'    {field}: "{url}"\n' if url is not None else ""
    command = f'    invocation: "{invocation}"\n' if invocation is not None else ""
    return (
        f'  - id: "{pid}"\n    enabled: true\n    provider_type: "{ptype}"\n    currency: "api"\n'
        f'{declared}{command}    models:\n      - {{id: "m1", tier: "mid"}}\n'
    )


def _write(root: Path, *providers: str) -> None:
    (root / REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(
        "providers:\n" + "".join(providers) + 'routing: {default_data_class: ""}\n', encoding="utf-8"
    )
    av.clear_cache()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("GW_API_KEY", raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def enter(root: Path, pid: str = "gw", var: str = "GW_API_KEY") -> Any:
    return runner.invoke(
        app, ["providers", "set-key", pid, var, "--project-root", str(root), "--yes"], input=f"{SECRET}\n"
    )


# ── 1. Une cible vide ne laisse pas passer une clé liée ──────────────────────────────────────────────────────────────


def test_une_cible_vide_n_est_pas_valable_pour_une_cle_liee_a_une_adresse() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://gw.example"])
    with pytest.raises(KeyEndpointError):
        env_for_provider("gw", endpoints=[])


def test_registre_reecrit_en_local_sans_adresse_la_cle_n_est_remise_a_aucune_commande(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project, _provider())
    assert enter(project).exit_code == 0
    _write(project, _provider(None, ptype="local"))  # plus aucune adresse déclarée : cible vide
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(dispatch_mod.subprocess, "run", lambda *a, **k: sent.append(dict(k["env"])))
    code, _out, err, _dur, kind, _sig = _run_provider_call(project, read_registry(project)[0], "m1", "go", call_timeout=5)
    assert sent == [] and code != 0 and kind == "error" and SECRET not in err


# ── 2. La commande d'invocation entre dans la liaison ────────────────────────────────────────────────────────────────


def test_la_cle_n_est_remise_qu_a_la_commande_approuvee() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW], invocation=CMD)
    assert env_for_provider("gw", endpoints=[GW], invocation=CMD) == {"GW_API_KEY": SECRET}
    assert env_for_provider("gw", endpoints=[GW]) == {"GW_API_KEY": SECRET}  # envoi HTTP par le kit : pas concerné
    for other in ("evil-cli {prompt}", ""):
        with pytest.raises(KeyEndpointError) as caught:
            env_for_provider("gw", endpoints=[GW], invocation=other)
        text = str(caught.value)
        assert "invocation" in text and "saisis-la de nouveau pour l'approuver" in text
        assert "set-key gw GW_API_KEY" in text
        assert SECRET not in text and "evil-cli" not in text


def test_une_cle_sans_empreinte_d_invocation_n_est_jamais_remise_a_une_commande_du_registre() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])  # liée à l'adresse seulement
    with pytest.raises(KeyEndpointError) as caught:
        env_for_provider("gw", endpoints=[GW], invocation=CMD)
    assert "invocation" in str(caught.value) and "saisis-la de nouveau" in str(caught.value)
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)  # ancienne : officielle, mais sans empreinte d'invocation
    with pytest.raises(KeyEndpointError):
        env_for_provider("deepseek", endpoints=["https://api.deepseek.com"], invocation="deepseek-cli {prompt}")
    assert env_for_provider("deepseek", endpoints=["https://api.deepseek.com"], invocation="")  # aucune commande déclarée


def test_adresse_inchangee_invocation_reecrite_la_cle_n_est_remise_a_aucun_sous_processus(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project, _provider())
    assert enter(project).exit_code == 0
    sent: list[dict[str, str]] = []

    def fake_run(argv: Any, **kwargs: Any) -> Any:
        import subprocess

        sent.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(dispatch_mod.subprocess, "run", fake_run)
    code, *_ = _run_provider_call(project, read_registry(project)[0], "m1", "go", call_timeout=5)
    assert code == 0 and sent[0]["GW_API_KEY"] == SECRET
    sent.clear()
    _write(project, _provider(invocation="evil-cli {model} {prompt}"))
    code, _out, err, _dur, kind, _sig = _run_provider_call(project, read_registry(project)[0], "m1", "go", call_timeout=5)
    assert sent == [] and code != 0 and kind == "error" and "invocation" in err and SECRET not in err


# ── 3. La saisie montre ce qu'elle approuve ──────────────────────────────────────────────────────────────────────────


def test_set_key_montre_les_adresses_et_la_commande_avant_de_demander_la_cle_puis_confirme(project: Path) -> None:
    _write(project, _provider("HTTPS://GW.Example:443/anthropic/"))
    args = ["providers", "set-key", "gw", "GW_API_KEY", "--project-root", str(project)]
    refused = runner.invoke(app, args, input="n\n")
    assert refused.exit_code == 1 and list_secrets() == ()
    assert "https://gw.example/anthropic" in refused.output and CMD in refused.output
    assert "Valeur pour" not in refused.output  # la clé n'a pas été demandée
    accepted = runner.invoke(app, args, input=f"y\n{SECRET}\n")
    assert accepted.exit_code == 0, accepted.output
    assert env_for_provider("gw", endpoints=[GW], invocation=CMD) == {"GW_API_KEY": SECRET}
    assert SECRET not in accepted.output


def test_set_key_non_interactif_affiche_la_meme_liste(project: Path) -> None:
    _write(project, _provider())
    result = enter(project)
    assert result.exit_code == 0 and GW in result.output and CMD in result.output and SECRET not in result.output


def test_set_key_refuse_avant_la_saisie_une_adresse_non_admise_sans_la_montrer(project: Path) -> None:
    _write(project, _provider("https://u:motdepasse@gw.example"))
    result = enter(project)
    assert result.exit_code == 1 and list_secrets() == ()
    assert "non admise" in result.output and "motdepasse" not in result.output and "Valeur pour" not in result.output


def test_la_route_du_cockpit_rend_la_liste_liee_et_la_lecture_dit_ce_qui_serait_lie(project: Path) -> None:
    from grimoire.tools import workspace_config

    _write(project, _provider())
    shown = workspace_config.providers_view(project)["providers"][0]["key_binding"]
    assert shown == {"endpoints": [GW], "invocation": CMD}
    body = {
        "provider_id": "gw",
        "env_var": "GW_API_KEY",
        "value": SECRET,
        "control_key": md.ensure_control_key().read_text(encoding="utf-8").strip(),
    }
    result = POST_ROUTES[f"{PREFIX}providers/set-key"](project, body)
    assert result["bound"] == {"endpoints": [GW], "invocation": CMD}
    assert SECRET not in json.dumps(result)


# ── 4. La normalisation ne confond pas deux chemins ──────────────────────────────────────────────────────────────────


def test_v1_final_distingue_deux_services_sauf_pour_l_adresse_officielle() -> None:
    assert normalize_endpoint("https://gw.example") != normalize_endpoint("https://gw.example/v1")
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://gw.example"])
    with pytest.raises(KeyEndpointError):
        env_for_provider("gw", endpoints=["https://gw.example/v1"])
    set_secret("moonshot", "MOONSHOT_API_KEY", SECRET, endpoints=["https://api.moonshot.ai"])
    assert env_for_provider("moonshot", endpoints=["https://api.moonshot.ai/v1"])  # l'officielle, avec ou sans /v1
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    assert env_for_provider("deepseek", endpoints=["https://api.deepseek.com/v1/"], invocation="")


# ── 5. Un index mal formé n'arrête pas la ressaisie ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("entries", [[42, "x", {"provider": "gw"}, {"env_var": "A"}], "pas une liste", None])
def test_un_index_mal_forme_n_arrete_ni_la_saisie_ni_le_retrait(entries: Any) -> None:
    store_dir().mkdir(parents=True)
    (store_dir() / "index.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    assert [e.provider for e in list_secrets()] == ["gw"]
    assert env_for_provider("gw", endpoints=[GW]) == {"GW_API_KEY": SECRET}
    remove_secret("gw", "GW_API_KEY")
    assert list_secrets() == ()


# ── 6. Un refus ne relance rien en boucle ; doctor dit les clés non liées ────────────────────────────────────────────


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return f"{Path(sys.executable).as_posix()} {path.as_posix()} {{prompt}} --model {{model}}"


def test_un_refus_de_liaison_est_une_seule_tentative_claire_sans_relance(
    project: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    marker = 'from pathlib import Path\nPath("marker.txt").write_text("done", encoding="utf-8")\n'
    approved = _script(tmp_path, "approved.py", marker)
    rewritten = _script(tmp_path, "rewritten.py", marker)

    def registry(command: str) -> str:
        return (
            f'  - id: "anthropic"\n    enabled: true\n    provider_type: "hosted"\n    currency: "quota"\n'
            f'    invocation: "{command}"\n    allowed_capabilities: ["chat", "code"]\n'
            '    data_policy: {allowed_data_classes: ["project-source"]}\n'
            '    models:\n      - {id: "opus", tier: "mid"}\n'
        )

    _write(project, registry(approved))
    assert enter(project, "anthropic", "ANTHROPIC_API_KEY").exit_code == 0
    _write(project, registry(rewritten))
    (project / STANDARD / "llm-provider-registry.yaml").write_text(
        (project / REGISTRY_PATH).read_text(encoding="utf-8").replace(
            'routing: {default_data_class: ""}', 'routing: {default_data_class: "project-source"}'
        ),
        encoding="utf-8",
    )
    service = TaskService(project, Path("_grimoire-runtime-output/ledger"))
    mission = service.ledger.create_mission("Démo liaison", origin="test")
    task = service.ledger.create_task(
        mission.id, "Tâche déléguée", acceptance=("la suite de tests passe",), owner="amelia"
    )
    caplog.set_level(logging.WARNING)
    report = run_dispatch(service, task.id, checks=("test -f marker.txt",))
    assert not (project / "marker.txt").exists()  # la commande réécrite n'a rien reçu ni rien lancé
    refused = [a for a in report.attempts if a.provider == "anthropic"]
    assert len(refused) == 1 and refused[0].verdict == "error"  # une tentative, pas de relance en boucle
    assert "providers set-key anthropic ANTHROPIC_API_KEY" in caplog.text and SECRET not in caplog.text


def test_doctor_affiche_le_controle_des_cles_non_liees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.hosts.contract_probe.installed_version", lambda *_a, **_k: "9.9.9")
    project = tmp_path / "doctor-project"
    project.mkdir()
    (project / "project-context.yaml").write_text("project:\n  name: t\n", encoding="utf-8")
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)  # d'avant la liaison
    result = runner.invoke(app, ["doctor", str(project), "-o", "json"])
    check = next(c for c in json.loads(result.output)["checks"] if c["name"] == "provider_keys_bound")
    assert check["level"] == "warn" and "deepseek/DEEPSEEK_API_KEY" in check["detail"]
    assert "providers set-key" in check["detail"] and SECRET not in result.output
