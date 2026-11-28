"""Liaison d'une clé au programme lancé, à l'identifiant de modèle et au magasin (contre-vérification de #1491).

La liaison ne retient pas seulement le texte de la commande : elle retient le programme résolu et son contenu ; une
clé n'est jamais remise à un programme situé sous la racine du projet. Un identifiant de modèle ne devient pas une
option. Un refus de liaison ne met pas le fournisseur en refroidissement. Le magasin n'écrit pas à travers un lien.
``HOME`` est isolé, aucun réseau, aucun vrai fournisseur (les « programmes » sont des fichiers de test).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import dispatch as dispatch_mod
from grimoire.missions.dispatch import _run_provider_call
from grimoire.providers import availability as av
from grimoire.providers import capability_probe as cp
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import KeyEndpointError, env_for_provider, list_secrets, set_secret, store_dir

SECRET = "sk-test-valeur-de-test-0004"  # noqa: S105 - valeur factice, jamais une vraie clé
REGISTRY_PATH = Path("_grimoire/standard/llm-provider-registry.yaml")
runner = CliRunner()


def _write(root: Path, invocation: str, *, base_url: str = "https://gw.example", ptype: str = "hosted", pid: str = "gw",
           model: str = "m1") -> None:
    (root / REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(
        f'providers:\n  - id: "{pid}"\n    enabled: true\n    provider_type: "{ptype}"\n    currency: "api"\n'
        f'    base_url: "{base_url}"\n    invocation: "{invocation}"\n    models:\n      - {{id: "{model}", tier: "mid"}}\n'
        'routing: {default_data_class: ""}\n',
        encoding="utf-8",
    )
    av.clear_cache()


def _program(directory: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("GW_API_KEY", raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    envs: list[dict[str, str]] = []

    def fake_run(argv: Any, **kwargs: Any) -> Any:
        envs.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(dispatch_mod.subprocess, "run", fake_run)
    return envs


def set_key(root: Path) -> Any:
    return runner.invoke(
        app, ["providers", "set-key", "gw", "GW_API_KEY", "--project-root", str(root), "--yes"], input=f"{SECRET}\n"
    )


def call(root: Path) -> tuple[Any, ...]:
    spec = read_registry(root)[0]
    return _run_provider_call(root, spec, spec.models[0].id, "go", call_timeout=5)


# ── 1. Le programme lancé, pas seulement le texte de la commande ─────────────────────────────────────────────────────


@pytest.mark.parametrize("invocation", ["./tool.sh {prompt}", "tool.sh {prompt}"])
def test_un_programme_sous_la_racine_du_projet_ne_recoit_jamais_la_cle(
    project: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    _program(project, "tool.sh")
    monkeypatch.setenv("PATH", f"{project}{os.pathsep}{os.environ['PATH']}")
    _write(project, invocation)
    result = set_key(project)
    assert result.exit_code == 1 and list_secrets() == ()
    assert "racine du projet" in result.output and "Valeur pour" not in result.output


def test_un_script_du_projet_lance_par_un_interpreteur_ne_recoit_jamais_la_cle(project: Path) -> None:
    _program(project / "scripts", "x.py", "print(1)\n")
    _write(project, f"{sys.executable} scripts/x.py {{prompt}}")
    result = set_key(project)
    assert result.exit_code == 1 and "racine du projet" in result.output and list_secrets() == ()
    _write(project, f"{sys.executable} -m pkg {{prompt}}")  # `-m` importe depuis le répertoire du projet
    assert set_key(project).exit_code == 1


def test_une_cle_deja_liee_n_est_pas_remise_a_un_script_du_projet_reecrit(
    project: Path, sent: list[dict[str, str]]
) -> None:
    script = _program(project, "tool.sh")
    _write(project, f"{script} {{prompt}}")
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://gw.example"], invocation=f"{script} {{prompt}}")
    code, _out, err, _dur, kind, _sig = call(project)
    assert sent == [] and code != 0 and kind == "key_binding" and "racine du projet" in err and SECRET not in err


def test_un_nom_nu_est_lie_au_chemin_absolu_et_au_contenu_du_programme(
    project: Path, tmp_path: Path, sent: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _program(tmp_path / "bin", "gw-cli")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    _write(project, "gw-cli {prompt}")
    result = set_key(project)
    assert result.exit_code == 0, result.output
    assert str(good) in result.output  # le chemin absolu est montré à la saisie
    assert call(project)[0] == 0 and sent[0]["GW_API_KEY"] == SECRET
    sent.clear()
    planted = _program(tmp_path / "bin2", "gw-cli", "#!/bin/sh\nenv\n")  # un binaire du même nom, plus tôt dans le PATH
    monkeypatch.setenv("PATH", f"{planted.parent}{os.pathsep}{os.environ['PATH']}")
    code, _out, err, _dur, kind, _sig = call(project)
    assert sent == [] and code != 0 and kind == "key_binding" and "set-key gw GW_API_KEY" in err
    monkeypatch.setenv("PATH", f"{good.parent}{os.pathsep}{os.environ['PATH']}")
    assert call(project)[0] == 0  # le programme lié est de nouveau celui du PATH
    sent.clear()
    good.write_text("#!/bin/sh\nenv\n", encoding="utf-8")  # contenu réécrit après la saisie
    code, _out, err, _dur, kind, _sig = call(project)
    assert sent == [] and code != 0 and kind == "key_binding" and SECRET not in err


def test_commandes_illisibles_ou_scripts_introuvables_sont_refuses_et_le_code_inline_est_admis(project: Path) -> None:
    from grimoire.providers.key_command import command_fingerprint, resolve_program

    for template in ('"non fermé', "", f"{sys.executable} introuvable.py {{prompt}}"):
        with pytest.raises(KeyEndpointError, match="résoudre"):
            resolve_program(template, project, "gw")
    assert resolve_program(f"{sys.executable} -c pass {{prompt}}", project, "gw") == (Path(sys.executable).resolve(),)
    first = command_fingerprint(f"{sys.executable} -c pass", project, "gw")
    assert first == command_fingerprint(f"{sys.executable} -c autre", project, "gw")  # le programme, pas le texte


def test_la_lecture_dit_pourquoi_un_programme_n_est_pas_admis(project: Path) -> None:
    from grimoire.tools import workspace_config

    _program(project, "tool.sh")
    _write(project, "./tool.sh {prompt}")
    view = workspace_config.providers_view(project)["providers"][0]["key_binding"]
    assert "racine du projet" in view["program_refusal"] and view["programs"] == []


@pytest.mark.parametrize(
    "template",
    [
        "env FOO=1 prog {prompt}",
        "/usr/bin/env FOO=1 prog {prompt}",
        "uv run x.py {prompt}",
        "uvx tool {prompt}",
        "npx tool {prompt}",
        "pnpm exec tool {prompt}",
        "yarn tool {prompt}",
        "npm exec tool {prompt}",
        "docker run img {prompt}",
        "podman run img {prompt}",
        "nohup prog {prompt}",
        "xargs prog {prompt}",
        "timeout 5 prog {prompt}",
        "nice prog {prompt}",
        "sudo prog {prompt}",
        "doas prog {prompt}",
    ],
)
def test_un_lanceur_generique_est_refuse_tant_que_la_cle_va_a_un_sous_processus(project: Path, template: str) -> None:
    from grimoire.providers.key_command import resolve_program

    with pytest.raises(KeyEndpointError, match="lanceur") as caught:
        resolve_program(template, project, "gw")
    assert "set-key gw GW_API_KEY" in str(caught.value)
    _write(project, template)
    result = set_key(project)
    assert result.exit_code == 1 and list_secrets() == () and "lanceur" in result.output


def test_aucun_argument_ne_designe_un_chemin_existant_sous_la_racine_du_projet(project: Path, tmp_path: Path) -> None:
    from grimoire.providers.key_command import resolve_program

    _program(project, "tool.sh")
    (project / "conf").mkdir()
    (project / "conf" / "x.cfg").write_text("x", encoding="utf-8")
    outside = _program(tmp_path / "dehors", "ok.sh")
    link = tmp_path / "dehors" / "lien"
    link.symlink_to(project / "tool.sh")
    base = f"{sys.executable} -c pass"
    for argument in ("./tool.sh", "conf/x.cfg", "--config=conf/x.cfg", str(project / "tool.sh"), str(link), "conf"):
        with pytest.raises(KeyEndpointError, match="racine du projet"):
            resolve_program(f"{base} {argument} {{prompt}}", project, "gw")
    assert resolve_program(f"{base} {outside} --mode=fast {{model}} {{prompt}}", project, "gw")  # hors projet : admis


@pytest.mark.parametrize("template", ["/bin/sh -c './tool.sh x' {prompt}", "/bin/sh -c 'cat conf/x.cfg' {prompt}"])
def test_un_interpreteur_dont_la_chaine_cite_un_chemin_du_projet_est_refuse(project: Path, template: str) -> None:
    from grimoire.providers.key_command import resolve_program

    _program(project, "tool.sh")
    (project / "conf").mkdir()
    (project / "conf" / "x.cfg").write_text("x", encoding="utf-8")
    with pytest.raises(KeyEndpointError, match="racine du projet"):
        resolve_program(template, project, "gw")


def test_un_programme_introuvable_n_est_pas_lie(project: Path) -> None:
    _write(project, "programme-qui-n-existe-pas {prompt}")
    result = set_key(project)
    assert result.exit_code == 1 and "introuvable" in result.output and list_secrets() == ()


# ── 2. L'identifiant de modèle ne devient pas une option ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", ["-x", "--base-url=https://evil.example", "a b", "é", "x;y", "m\n1"])
def test_un_identifiant_de_modele_hors_forme_n_est_pas_rendu_dans_la_commande(
    project: Path, sent: list[dict[str, str]], model: str
) -> None:
    spec = read_registry(_registry_ok(project))[0]
    code, _out, err, _dur, kind, _sig = _run_provider_call(project, spec, model, "go", call_timeout=5)
    assert sent == [] and code != 0 and kind == "model_id" and "identifiant de modèle" in err
    from grimoire.providers.state import load_state

    dispatch_mod._record_call_failure(project, "gw", model, kind, None)  # ni une panne, ni un refroidissement
    assert "gw" not in load_state(project) or load_state(project)["gw"].failure_count == 0


def test_un_identifiant_de_modele_usuel_reste_admis(project: Path, sent: list[dict[str, str]]) -> None:
    spec = read_registry(_registry_ok(project))[0]
    for model in ("qwen2.5:7b-q4_K_M", "deepseek-ai/DeepSeek-V3", "claude-sonnet-4.6", "gpt-4o@2024"):
        assert _run_provider_call(project, spec, model, "go", call_timeout=5)[0] == 0


def _registry_ok(project: Path) -> Path:
    _write(project, f"{sys.executable} -c pass {{model}} {{prompt}}")
    return project


# ── 3. Une même déclaration, la même clé acceptée sur les quatre chemins ─────────────────────────────────────────────


def test_une_passerelle_declaree_en_v1_est_acceptee_par_la_sonde_verify_remote_le_dispatch_et_la_fabrique(
    project: Path, sent: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.tools.atelier_engines import fabrique as fab

    _write(project, f"{sys.executable} -c pass {{prompt}}", base_url="https://gw.example/v1", ptype="litellm")
    assert set_key(project).exit_code == 0
    seen: list[str] = []
    monkeypatch.setattr(cp, "_http_json", lambda url, headers, body=None: seen.append(url) or {"data": [{"id": "m1"}]})
    spec = read_registry(project)[0]
    assert cp.probe_provider_capabilities(spec).measured is True  # 1. sonde
    shown = av.key_endpoints(project)
    assert shown and av.probe_remote(project, shown) == ["gw"]  # 2. verify-remote
    assert call(project)[0] == 0 and sent[0]["GW_API_KEY"] == SECRET  # 3. dispatch
    monkeypatch.setenv("GW_API_KEY", SECRET)  # la détection ne lit que la présence
    inv = av.detect(project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b'{"models": []}',
                    acp=lambda _p, _r: (True, ""), use_cache=False, ollama_url="http://127.0.0.1:11434")
    row = next(r for r in inv.models if r.provider_id == "gw")
    backend, why = fab._backend(row, project, "un", inv, 3.0, None, None)  # 4. fabrique
    assert backend is not None and why == ""
    assert seen and {u.split("/v1/")[0] for u in seen} == {"https://gw.example"}


# ── 4. Un refus de liaison n'est pas une panne du fournisseur ────────────────────────────────────────────────────────


def test_un_refus_de_liaison_ne_met_pas_le_fournisseur_en_refroidissement(
    project: Path, sent: list[dict[str, str]]
) -> None:
    from grimoire.providers.state import load_state

    cmd = f"{sys.executable} -c pass {{prompt}}"
    _write(project, cmd)
    assert set_key(project).exit_code == 0
    _write(project, f"{sys.executable} -c exit {{prompt}}")  # commande réécrite
    spec = read_registry(project)[0]
    code, _out, err, _dur, kind, _sig = _run_provider_call(project, spec, "m1", "go", call_timeout=5)
    assert kind == "key_binding" and code != 0 and sent == [] and "set-key gw GW_API_KEY" in err
    dispatch_mod._record_call_failure(project, "gw", "m1", kind, None)
    assert "gw" not in load_state(project) or load_state(project)["gw"].failure_count == 0


# ── 5. Le magasin n'écrit pas à travers un lien ──────────────────────────────────────────────────────────────────────


def test_le_magasin_n_ecrit_pas_a_travers_un_lien_sur_l_index_ni_le_fichier_de_cles(tmp_path: Path) -> None:
    set_secret("aa", "AA_API_KEY", SECRET, endpoints=[])
    outside = tmp_path / "dehors"
    outside.mkdir()
    index_target, env_target = outside / "index.json", outside / "zz.env"
    index_target.write_text("{}", encoding="utf-8")
    env_target.write_text("", encoding="utf-8")
    (store_dir() / "index.json").unlink()
    (store_dir() / "index.json").symlink_to(index_target)
    (store_dir() / "zz.env").symlink_to(env_target)
    with pytest.raises(ValueError, match="lien"):
        set_secret("zz", "ZZ_API_KEY", SECRET, endpoints=[])
    assert env_target.read_text(encoding="utf-8") == "" and index_target.read_text(encoding="utf-8") == "{}"
    assert SECRET not in env_target.read_text(encoding="utf-8")


def test_le_magasin_ecrit_atomiquement_en_0600_sans_suivre_de_lien_sur_son_dossier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = set_secret("aa", "AA_API_KEY", SECRET, endpoints=[])
    assert (path.stat().st_mode & 0o777) == 0o600 and ((store_dir() / "index.json").stat().st_mode & 0o777) == 0o600
    assert sorted(p.name for p in store_dir().iterdir()) == ["aa.env", "index.json"]  # aucun fichier temporaire
    real = tmp_path / "ailleurs"
    real.mkdir()
    link = tmp_path / "home2"
    link.mkdir()
    (link / ".env-store").symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    monkeypatch.setenv("USERPROFILE", str(link))
    with pytest.raises(ValueError, match="lien"):
        set_secret("bb", "BB_API_KEY", SECRET, endpoints=[])
    assert list(real.iterdir()) == []


def test_une_cle_liee_au_programme_est_rendue_pour_le_meme_programme(tmp_path: Path) -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://gw.example"], invocation="x {prompt}", command="abc")
    assert env_for_provider("gw", endpoints=["https://gw.example"], invocation="x {prompt}", command="abc")
    with pytest.raises(KeyEndpointError, match="programme"):
        env_for_provider("gw", endpoints=["https://gw.example"], invocation="x {prompt}", command="autre")
