"""Une clé d'API est liée au point d'accès pour lequel elle a été saisie.

Classe du défaut : une clé était retrouvée par l'identifiant du fournisseur, pas par le point d'accès auquel elle
était destinée ; si la déclaration du fournisseur changeait d'adresse après la saisie, la clé pouvait partir vers une
adresse que l'humain n'avait pas approuvée. Désormais le magasin retient, avec la clé, une empreinte du point d'accès
normalisé ; à chaque usage l'appelant donne le point d'accès visé et la clé n'est pas rendue si l'empreinte diffère.

``HOME`` est isolé sur ``tmp_path`` ; la valeur de clé est un jeton de test ; aucun réseau n'est ouvert (faux transport).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import mandate as md
from grimoire.providers import availability as av
from grimoire.providers import capability_probe as cp
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import (
    KeyEndpointError,
    env_for_provider,
    list_secrets,
    normalize_endpoint,
    set_secret,
    store_dir,
    unbound_secrets,
)
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX

SECRET = "sk-test-valeur-de-test-0001"  # noqa: S105 - valeur factice, jamais une vraie clé
STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"
GW = "https://gw.example/anthropic"
EVIL = "https://evil.example/anthropic"
runner = CliRunner()


def _provider(url: str | None, *, pid: str = "gw", field: str = "anthropic_base_url", ptype: str = "hosted") -> str:
    declared = f'    {field}: "{url}"\n' if url is not None else ""
    return (
        f'  - id: "{pid}"\n    enabled: true\n    provider_type: "{ptype}"\n    currency: "api"\n'
        f'{declared}    invocation: "{pid}-cli {{model}} {{prompt}}"\n'
        '    models:\n      - {id: "m1", tier: "mid"}\n'
    )


def _write(root: Path, *providers: str) -> None:
    (root / REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(
        'providers:\n' + "".join(providers) + 'routing: {default_data_class: ""}\n', encoding="utf-8"
    )
    av.clear_cache()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for var in ("GW_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def enter_key(root: Path, pid: str = "gw", var: str = "GW_API_KEY") -> None:
    """La saisie d'un humain au terminal : le geste d'autorité existant, avec la valeur masquée."""
    result = runner.invoke(app, ["providers", "set-key", pid, var, "--project-root", str(root)], input=f"{SECRET}\n")
    assert result.exit_code == 0, result.output


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str]]]:
    """Un faux transport pour la sonde : il note l'URL et les en-têtes, aucune connexion n'est ouverte."""
    calls: list[tuple[str, dict[str, str]]] = []

    def fake(url: str, headers: Any, _body: Any = None) -> Any:
        calls.append((url, dict(headers)))
        return {"data": [{"id": "m1"}]}

    monkeypatch.setattr(cp, "_http_json", fake)
    return calls


# ── Normalisation : aucune fausse alerte ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://gw.example/anthropic", "HTTPS://GW.Example/anthropic"),
        ("https://gw.example/anthropic", "https://gw.example:443/anthropic"),
        ("https://gw.example/anthropic", "https://gw.example/anthropic/"),
        ("https://gw.example", "https://gw.example/v1"),
        ("https://gw.example", "  https://gw.example/  "),
        ("http://localhost:11434", "http://LOCALHOST:11434/"),
        ("http://127.0.0.1", "http://127.0.0.1:80"),
    ],
)
def test_la_normalisation_ne_provoque_pas_de_faux_refus(a: str, b: str) -> None:
    assert normalize_endpoint(a) == normalize_endpoint(b) != ""


@pytest.mark.parametrize(
    "url",
    [
        "https://gw.example/a",
        "https://gw.example:8443/anthropic",
        "http://localhost:11434/anthropic",
    ],
)
def test_une_adresse_differente_a_une_empreinte_differente(url: str) -> None:
    assert normalize_endpoint(url) != normalize_endpoint(GW)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@gw.example",
        "https://user@gw.example",
        "http://gw.example",
        "ftp://gw.example",
        "https://gw.example?x=1",
        "https://gw.example#frag",
        "https://gw.example:0",
        "https://gw.example:notaport",
        "",
        "gw.example",
    ],
)
def test_une_adresse_non_admise_n_a_pas_d_empreinte(url: str) -> None:
    assert normalize_endpoint(url) == ""


# ── Le magasin : liaison, refus, migration ───────────────────────────────────────────────────────────────────────────


def test_la_cle_est_rendue_pour_l_adresse_saisie_sous_toute_ecriture_equivalente() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    for same in (GW, "HTTPS://Gw.Example:443/anthropic/", f"{GW}/v1"):
        assert env_for_provider("gw", endpoints=[same]) == {"GW_API_KEY": SECRET}


def test_la_cle_n_est_pas_rendue_pour_une_autre_adresse_et_le_message_ne_montre_ni_cle_ni_adresse() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    with pytest.raises(KeyEndpointError) as caught:
        env_for_provider("gw", endpoints=["https://evil.example/secret-path"])
    text = str(caught.value)
    assert "l'adresse de ce fournisseur a changé depuis la saisie de la clé" in text
    assert "saisis-la de nouveau pour l'approuver" in text
    for forbidden in (SECRET, "evil.example", "secret-path"):
        assert forbidden not in text


def test_l_index_ne_garde_qu_une_empreinte_jamais_l_adresse() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    index = (store_dir() / "index.json").read_text(encoding="utf-8")
    assert "gw.example" not in index and SECRET not in index
    assert json.loads(index)["entries"][0]["endpoints"]


def test_une_adresse_non_admise_a_l_usage_est_refusee_sans_etre_montree() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    for bad in ("https://u:motdepasse@gw.example/anthropic", "http://gw.example/anthropic"):
        with pytest.raises(KeyEndpointError) as caught:
            env_for_provider("gw", endpoints=[bad])
        assert "non admise" in str(caught.value)
        for forbidden in ("gw.example", "motdepasse", SECRET):
            assert forbidden not in str(caught.value)


def test_la_saisie_refuse_une_adresse_non_admise() -> None:
    with pytest.raises(ValueError, match="non admise") as caught:
        set_secret("gw", "GW_API_KEY", SECRET, endpoints=["https://u:p@gw.example"])
    assert "gw.example" not in str(caught.value)
    assert list_secrets() == ()  # rien n'a été écrit


def test_la_boucle_locale_en_http_est_admise_quand_elle_a_ete_saisie() -> None:
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=["http://127.0.0.1:4000"])
    assert env_for_provider("gw", endpoints=["http://127.0.0.1:4000/"]) == {"GW_API_KEY": SECRET}


def test_une_cle_saisie_sans_adresse_declaree_n_est_valable_nulle_part() -> None:
    set_secret("mycli", "MYCLI_API_KEY", SECRET, endpoints=[])
    assert env_for_provider("mycli", endpoints=[]) == {"MYCLI_API_KEY": SECRET}
    with pytest.raises(KeyEndpointError):
        env_for_provider("mycli", endpoints=[GW])


def test_ancienne_cle_sans_empreinte_seulement_vers_le_defaut_officiel() -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)  # une entrée d'avant la liaison
    for official in ("https://api.deepseek.com", "https://API.deepseek.com:443/", "https://api.deepseek.com/v1"):
        assert env_for_provider("deepseek", endpoints=[official]) == {"DEEPSEEK_API_KEY": SECRET}
    for other in (GW, "https://api.deepseek.com/anthropic", "https://api.deepseek.com.evil.example"):
        with pytest.raises(KeyEndpointError) as caught:
            env_for_provider("deepseek", endpoints=[other])
        assert "saisis-la de nouveau pour l'approuver" in str(caught.value)
    with pytest.raises(KeyEndpointError):
        env_for_provider("deepseek", endpoints=[])  # une adresse inconnue n'est pas le défaut officiel


def test_ancienne_cle_d_un_fournisseur_sans_defaut_officiel_exige_une_nouvelle_saisie() -> None:
    set_secret("gw", "GW_API_KEY", SECRET)
    with pytest.raises(KeyEndpointError):
        env_for_provider("gw", endpoints=[GW])


def test_sans_cle_posee_rien_n_est_rendu_ni_refuse() -> None:
    assert env_for_provider("never", endpoints=[GW]) == {}


def test_les_cles_non_liees_sont_listees_et_ressaisir_les_lie() -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    assert [e.provider for e in unbound_secrets()] == ["deepseek"]
    assert {e.provider: e.bound for e in list_secrets()} == {"deepseek": False, "gw": True}
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET, endpoints=["https://api.deepseek.com"])
    assert unbound_secrets() == ()


# ── La saisie (CLI et cockpit) lie la clé à la déclaration du registre ───────────────────────────────────────────────


def test_la_saisie_au_terminal_lie_la_cle_a_l_adresse_du_registre(project: Path) -> None:
    _write(project, _provider(GW))
    enter_key(project)
    assert env_for_provider("gw", endpoints=[GW]) == {"GW_API_KEY": SECRET}
    with pytest.raises(KeyEndpointError):
        env_for_provider("gw", endpoints=[EVIL])


def test_la_saisie_du_cockpit_lie_la_cle_a_l_adresse_du_registre(project: Path) -> None:
    _write(project, _provider(GW))
    body = {
        "provider_id": "gw",
        "env_var": "GW_API_KEY",
        "value": SECRET,
        "control_key": md.ensure_control_key().read_text(encoding="utf-8").strip(),
    }
    POST_ROUTES[f"{PREFIX}providers/set-key"](project, body)
    assert env_for_provider("gw", endpoints=[GW]) == {"GW_API_KEY": SECRET}
    with pytest.raises(KeyEndpointError):
        env_for_provider("gw", endpoints=[EVIL])


def test_une_saisie_pour_un_fournisseur_documente_lie_aussi_son_defaut_officiel(project: Path) -> None:
    _write(project, _provider("https://api.deepseek.com/anthropic", pid="deepseek"))
    enter_key(project, "deepseek", "DEEPSEEK_API_KEY")
    for url in ("https://api.deepseek.com/anthropic", "https://api.deepseek.com"):
        assert env_for_provider("deepseek", endpoints=[url])
    with pytest.raises(KeyEndpointError):
        env_for_provider("deepseek", endpoints=[EVIL])


def test_la_saisie_refuse_une_adresse_declaree_non_admise(project: Path) -> None:
    _write(project, _provider("http://gw.example"))
    result = runner.invoke(
        app, ["providers", "set-key", "gw", "GW_API_KEY", "--project-root", str(project)], input=f"{SECRET}\n"
    )
    assert result.exit_code == 1
    assert "non admise" in result.output and SECRET not in result.output and "gw.example" not in result.output
    assert list_secrets() == ()


def test_la_liste_des_cles_dit_lesquelles_ne_sont_pas_liees() -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    result = runner.invoke(app, ["providers", "secrets", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)[0]["bound"] is False
    assert SECRET not in result.output
    table = runner.invoke(app, ["providers", "secrets"])
    assert "non lié" in table.output.lower() or "non liée" in table.output.lower()


def test_doctor_signale_les_cles_non_liees(project: Path) -> None:
    from grimoire.cli.doctor_keys import apply_key_binding_check

    results: list[dict[str, Any]] = []
    apply_key_binding_check(results, project, fmt="json", console=Console(quiet=True))
    assert results == []  # aucune clé : rien n'est dit
    set_secret("gw", "GW_API_KEY", SECRET, endpoints=[GW])
    apply_key_binding_check(results, project, fmt="json", console=Console(quiet=True))
    assert results[-1]["level"] == "ok"
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    apply_key_binding_check(results, project, fmt="json", console=Console(quiet=True))
    assert results[-1]["level"] == "warn" and "deepseek" in results[-1]["detail"]
    assert SECRET not in json.dumps(results)


def test_les_adresses_visees_et_approuvees_suivent_la_declaration(project: Path) -> None:
    from grimoire.providers.key_endpoint import binding_endpoints, key_targets

    _write(
        project,
        _provider(GW),
        _provider("https://api.deepseek.com/anthropic", pid="deepseek"),
        _provider(None, pid="plain"),
    )
    gw, deepseek, plain = read_registry(project)
    assert key_targets(gw) == (GW,)
    assert key_targets(deepseek) == ("https://api.deepseek.com/anthropic",)
    assert key_targets(plain) == ()  # ni adresse déclarée ni défaut officiel
    assert binding_endpoints(project, "gw") == (GW,)
    assert binding_endpoints(project, "deepseek") == ("https://api.deepseek.com/anthropic", "https://api.deepseek.com")
    assert binding_endpoints(project, "plain") == ()
    assert binding_endpoints(project, "anthropic") == ("https://api.anthropic.com",)  # absent du registre : l'officiel
    assert binding_endpoints(project, "inconnu") == ()


def test_un_fournisseur_documente_sans_adresse_declaree_vise_son_defaut_officiel(project: Path) -> None:
    from grimoire.providers.key_endpoint import key_targets

    _write(project, _provider(None, pid="deepseek"))
    assert key_targets(read_registry(project)[0]) == ("https://api.deepseek.com",)


def test_un_registre_illisible_ne_declare_rien(project: Path) -> None:
    from grimoire.providers.key_endpoint import binding_endpoints

    (project / REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    (project / REGISTRY_PATH).write_text("providers: [: pas du yaml", encoding="utf-8")
    assert binding_endpoints(project, "deepseek") == ("https://api.deepseek.com",)


def test_doctor_imprime_le_controle_hors_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from grimoire.cli.doctor_keys import apply_key_binding_check

    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    apply_key_binding_check([], project, fmt="text", console=Console(width=200))
    assert "WARN" in capsys.readouterr().out


# ── Chemin d'envoi 1 : la sonde de capacités (hébergé, passerelle) ───────────────────────────────────────────────────


def test_sonde_hebergee_registre_reecrit_apres_la_saisie_la_cle_ne_part_pas(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider(GW))
    enter_key(project)
    _write(project, _provider(EVIL))
    probe = cp.probe_provider_capabilities(read_registry(project)[0])
    assert probe.measured is False and "a changé" in probe.note
    assert transport == [] and SECRET not in probe.note


def test_sonde_hebergee_adresse_inchangee_la_cle_part_vers_elle(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider(GW))
    enter_key(project)
    probe = cp.probe_provider_capabilities(read_registry(project)[0])
    assert probe.measured is True
    assert [url for url, _ in transport] == ["https://gw.example/anthropic/v1/models"]
    assert transport[0][1]["x-api-key"] == SECRET


def test_sonde_passerelle_registre_reecrit_apres_la_saisie_la_cle_ne_part_pas(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider("https://gw.example", field="base_url", ptype="litellm"))
    enter_key(project)
    _write(project, _provider("https://evil.example", field="base_url", ptype="litellm"))
    probe = cp.probe_provider_capabilities(read_registry(project)[0])
    assert probe.measured is False and "a changé" in probe.note
    assert transport == []


def test_sonde_passerelle_adresse_inchangee_fonctionne(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider("https://gw.example", field="base_url", ptype="litellm"))
    enter_key(project)
    assert cp.probe_provider_capabilities(read_registry(project)[0]).measured is True
    assert {url.split("/v1/")[0] for url, _ in transport} == {"https://gw.example"}


def test_sonde_documentee_ancienne_cle_part_vers_le_defaut_officiel_seulement(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider("https://api.deepseek.com", pid="deepseek"))
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)  # sans empreinte
    assert cp.probe_provider_capabilities(read_registry(project)[0]).measured is True
    assert transport[0][0] == "https://api.deepseek.com/models"


def test_sonde_ancienne_cle_et_passerelle_exigent_une_nouvelle_saisie(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider("https://gw.example", field="base_url", ptype="litellm"))
    set_secret("gw", "GW_API_KEY", SECRET)  # sans empreinte
    probe = cp.probe_provider_capabilities(read_registry(project)[0])
    assert probe.measured is False and "saisis-la de nouveau" in probe.note
    assert transport == []


# ── Chemin d'envoi 2 : « Vérifier les connexions » (verify-remote) ───────────────────────────────────────────────────


def test_verify_remote_registre_reecrit_apres_la_saisie_rien_n_est_propose_ni_envoye(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider(GW))
    enter_key(project)
    assert av.key_endpoints(project) == [{"provider_id": "gw", "url": "https://gw.example/anthropic/v1/models"}]
    _write(project, _provider(EVIL))
    assert av.key_endpoints(project) == []  # la page ne montre pas une adresse que la clé n'a jamais approuvée
    assert av.probe_remote(project, []) == []
    assert transport == []


def test_verify_remote_adresse_inchangee_fonctionne(
    project: Path, transport: list[tuple[str, dict[str, str]]]
) -> None:
    _write(project, _provider(GW))
    enter_key(project)
    shown = av.key_endpoints(project)
    assert av.probe_remote(project, shown) == ["gw"]
    assert transport[0][0] == "https://gw.example/anthropic/v1/models"


# ── Chemin d'envoi 3 : l'assistant de l'Atelier (API Messages, adresse fixe) ─────────────────────────────────────────


def test_atelier_une_cle_liee_ailleurs_n_est_pas_rendue_une_ancienne_cle_l_est() -> None:
    from grimoire.tools import atelier_model

    set_secret("anthropic", "ANTHROPIC_API_KEY", SECRET, endpoints=[GW])
    assert atelier_model.api_key() == ""
    set_secret("anthropic", "ANTHROPIC_API_KEY", SECRET)  # ancienne : défaut officiel seulement
    assert atelier_model.api_key() == SECRET
    set_secret("anthropic", "ANTHROPIC_API_KEY", SECRET, endpoints=["https://api.anthropic.com"])
    assert atelier_model.api_key() == SECRET


# ── Rien de la clé dans un message ni un journal ─────────────────────────────────────────────────────────────────────


def test_la_cle_n_apparait_dans_aucun_message_ni_journal(
    project: Path,
    transport: list[tuple[str, dict[str, str]]],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    _write(project, _provider(GW))
    enter_key(project)
    _write(project, _provider(EVIL))
    probe = cp.probe_provider_capabilities(read_registry(project)[0])
    with pytest.raises(KeyEndpointError) as caught:
        env_for_provider("gw", endpoints=[EVIL])
    out = capsys.readouterr()
    for text in (probe.note, str(caught.value), caplog.text, out.out, out.err, repr(caught.value)):
        assert SECRET not in text
