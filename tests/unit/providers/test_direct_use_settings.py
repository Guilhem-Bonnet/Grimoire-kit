"""Les réglages « autorisé pour l'usage direct » : lecture stricte, écriture validée, état de la décision (lot B1)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from grimoire.providers import availability as av
from grimoire.providers import direct_use_settings as ds
from grimoire.providers.availability import detect
from grimoire.providers.direct_use import choose_for

STANDARD = Path("_grimoire/standard")
REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
routing:
  default_data_class: "public-docs"
providers:
  - id: "ollama"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "ollama run {model} {prompt}"
    models:
      - {id: "llama3.2:3b", tier: "cheap"}
      - {id: "qwen2.5:14b", tier: "mid"}
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    currency: "quota"
    invocation: "claude -p {prompt}"
    models:
      - {id: "claude-sonnet-5-5", tier: "strong"}
    data_policy: {allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}
"""
TAGS = json.dumps({"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:14b"}, {"name": "extra:8b"}]}).encode()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr("shutil.which", lambda _n, *a, **k: None)  # aucun hôte réel
    av.clear_cache()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    return root


def inventory(project: Path) -> av.Inventory:
    return detect(
        project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: TAGS, acp=lambda _p, _r: (True, ""), use_cache=False
    )


def test_par_defaut_aucun_modele_n_est_autorise(project: Path) -> None:
    settings = ds.read_settings(project)
    assert all(not auths for auths in settings.allowed.values())
    payload = ds.view(project, inventory=inventory(project))
    assert payload["any_authorized"] is False
    assert all(u["decision"]["kind"] == "none" for u in payload["usages"])
    assert all("sans modèle" in u["decision"]["reason"] for u in payload["usages"])


def test_ecriture_puis_lecture_conserve_l_ordre_et_les_plafonds(project: Path) -> None:
    inv = inventory(project)
    entries = [
        {"key": "ollama:qwen2.5:14b", "daily_cap_tokens": 5000},
        {"key": "ollama:llama3.2:3b", "daily_cap_tokens": None},
    ]
    ds.write_usage(project, "brainstorm", entries, inv.by_key())
    again = ds.read_settings(project)
    assert [(a.key, a.daily_cap_tokens) for a in again.allowed["brainstorm"]] == [
        ("ollama:qwen2.5:14b", 5000),
        ("ollama:llama3.2:3b", None),
    ]
    assert again.allowed["conversation"] == ()
    # Un autre usage ne touche pas le premier.
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inv.by_key())
    assert len(ds.read_settings(project).allowed["brainstorm"]) == 2
    # Remplacer par une liste vide retire tout pour cet usage.
    ds.write_usage(project, "brainstorm", [], inv.by_key())
    assert ds.read_settings(project).allowed["brainstorm"] == ()


def test_le_fichier_est_prive_hors_du_depot_et_sans_secret(project: Path, tmp_path: Path) -> None:
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inventory(project).by_key())
    path = ds.settings_path(project)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (tmp_path / "home") in path.parents
    assert project not in path.parents
    assert not any(project.rglob("*direct-use*"))
    assert "KEY" not in path.read_text(encoding="utf-8").upper().replace("KEY\"", "")


@pytest.mark.parametrize(
    ("usage", "entries", "message"),
    [
        ("danse", [], "usage inconnu"),
        ("conversation", [{"key": "registre:fantome/x"}], "inconnu de la détection"),
        ("conversation", [{"key": "ollama:llama3.2:3b"}, {"key": "ollama:llama3.2:3b"}], "en double"),
        ("conversation", [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": 0}], "plafond invalide"),
        ("conversation", [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": True}], "plafond invalide"),
        ("conversation", [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": "10"}], "plafond invalide"),
        ("conversation", [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": ds.MAX_CAP_TOKENS + 1}], "plafond invalide"),
        ("conversation", [{"key": 3}], "clé de modèle invalide"),
        ("conversation", ["ollama:llama3.2:3b"], "clé de modèle invalide"),
        ("conversation", [{"key": "sans-deux-points"}], "clé de modèle invalide"),
        ("conversation", [{"key": "ollama:x"}] * 33, "au plus"),
    ],
)
def test_ecriture_invalide_est_refusee_et_n_ecrit_rien(
    project: Path, usage: str, entries: list[Any], message: str
) -> None:
    with pytest.raises(ds.DirectUseError, match=message):
        ds.write_usage(project, usage, entries, inventory(project).by_key())
    assert not ds.settings_path(project).exists()


def _write_raw(project: Path, text: str | bytes) -> None:
    path = ds.settings_path(project)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


@pytest.mark.parametrize(
    "raw",
    [
        "pas du json",
        "null",
        "[]",
        '{"usages": 3}',
        '{"usages": {"conversation": 3}}',
        '{"usages": {"conversation": {"models": "x"}}}',
        b"\xff\xfe\x00",
        "[" * 200000,
        '{"usages": ' * 5000,
    ],
)
def test_fichier_hostile_n_autorise_rien_et_ne_plante_pas(project: Path, raw: str | bytes) -> None:
    _write_raw(project, raw)
    settings = ds.read_settings(project)
    assert all(not auths for auths in settings.allowed.values())
    assert ds.view(project, inventory=inventory(project))["any_authorized"] is False


def test_fichier_trop_gros_est_ignore(project: Path) -> None:
    _write_raw(project, b" " * (ds.MAX_BYTES + 10))
    settings = ds.read_settings(project)
    assert all(not auths for auths in settings.allowed.values())
    assert "trop gros" in settings.problems[0]


def test_entrees_invalides_sont_ignorees_une_a_une(project: Path) -> None:
    doc = {
        "usages": {
            "conversation": {
                "models": [
                    {"key": "ollama:llama3.2:3b", "daily_cap_tokens": 100},
                    {"key": 12},
                    {"key": "ollama:qwen2.5:14b", "daily_cap_tokens": -5},
                    {"key": "ollama:llama3.2:3b"},
                    "texte",
                ]
            },
            "intrus": {"models": [{"key": "ollama:llama3.2:3b"}]},
        }
    }
    _write_raw(project, json.dumps(doc))
    settings = ds.read_settings(project)
    assert [(a.key, a.daily_cap_tokens) for a in settings.allowed["conversation"]] == [("ollama:llama3.2:3b", 100)]
    assert "intrus" not in settings.allowed
    assert len(settings.problems) == 5


def test_trop_de_modeles_est_borne(project: Path) -> None:
    models = [{"key": f"ollama:m{i}"} for i in range(ds.MAX_MODELS_PER_USAGE + 8)]
    _write_raw(project, json.dumps({"usages": {"autres": {"models": models}}}))
    settings = ds.read_settings(project)
    assert len(settings.allowed["autres"]) == ds.MAX_MODELS_PER_USAGE
    assert any("au plus" in p for p in settings.problems)


def test_lien_symbolique_est_ignore_en_lecture_et_refuse_en_ecriture(project: Path, tmp_path: Path) -> None:
    target = tmp_path / "ailleurs.json"
    target.write_text(json.dumps({"usages": {"conversation": {"models": [{"key": "ollama:llama3.2:3b"}]}}}))
    path = ds.settings_path(project)
    path.parent.mkdir(mode=0o700, parents=True)
    path.symlink_to(target)
    settings = ds.read_settings(project)
    assert all(not auths for auths in settings.allowed.values())
    assert "lien symbolique" in settings.problems[0]
    with pytest.raises(ds.DirectUseError, match="lien symbolique"):
        ds.write_usage(project, "conversation", [], inventory(project).by_key())


def test_une_autorisation_pour_un_modele_inconnu_ne_fait_jamais_autorite(project: Path) -> None:
    _write_raw(project, json.dumps({"usages": {"conversation": {"models": [{"key": "registre:fantome/gpt"}]}}}))
    inv = inventory(project)
    payload = ds.view(project, inventory=inv)
    assert payload["any_authorized"] is False
    assert payload["ignored"] == ["Conversation (cadrage) : registre:fantome/gpt (non détecté)"]
    decision = choose_for("conversation", ds.build_state(project, inv))
    assert decision.kind == "none"
    assert decision.trace[0].verdict == "ignoré"


def test_decision_de_bout_en_bout_depuis_le_fichier(project: Path) -> None:
    inv = inventory(project)
    ds.write_usage(
        project, "brainstorm",
        [{"key": "ollama:llama3.2:3b"}, {"key": "ollama:qwen2.5:14b"}],
        inv.by_key(),
    )
    state = ds.build_state(project, inv, hold="")
    decision = choose_for("brainstorm", state)
    assert decision.key == "ollama:qwen2.5:14b"  # le léger est trop léger pour brainstormer, le mid convient
    payload = ds.view(project, inventory=inv)
    brain = next(u for u in payload["usages"] if u["id"] == "brainstorm")
    assert brain["decision"]["kind"] == "use"
    assert brain["min_capability"]["tier"] == "mid"
    assert payload["any_authorized"]


def test_un_modele_detecte_non_configure_est_autorisable(project: Path) -> None:
    inv = inventory(project)
    assert inv.by_key()["ollama:extra:8b"].configured is False
    ds.write_usage(project, "conversation", [{"key": "ollama:extra:8b"}], inv.by_key())
    decision = choose_for("conversation", ds.build_state(project, inv, hold=""))
    assert decision.key == "ollama:extra:8b"


def test_politique_de_donnees_du_registre_et_des_hotes(project: Path) -> None:
    # Classe déclarée par le projet = public-docs, admise par anthropic : rien d'écarté.
    inv = inventory(project)
    assert ds.build_state(project, inv, hold="").data_refusals == {}
    # Classe de tâche interdite : le fournisseur hébergé est refusé, jamais le modèle local.
    refused = ds.build_state(project, inv, hold="", data_class="secrets").data_refusals or {}
    assert "registre:anthropic/claude-sonnet-5-5" in refused
    assert not any(k.startswith("ollama:") for k in refused)
    assert any(k.startswith("hote:") for k in refused)  # le repli sur l'hôte est un appel hébergé comme un autre


def test_regles_d_usage_illisibles_echouent_ferme(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> str:
        raise RuntimeError("ledger cassé")

    monkeypatch.setattr("grimoire.missions.quota_usage.kit_call_refusal", boom)
    state = ds.build_state(project, inventory(project))
    assert "échec fermé" in state.hold_reason


def test_consommation_du_jour_borne_basse_attribuee_au_fournisseur_de_l_atelier(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("grimoire.tools.atelier_model.usage", lambda _r, _s: {"readable": True, "day": 1234, "chantier": 0})
    state = ds.build_state(project, inventory(project), hold="")
    assert state.used_today == {"registre:anthropic/claude-sonnet-5-5": 1234}
    monkeypatch.setattr("grimoire.tools.atelier_model.usage", lambda _r, _s: {"readable": False, "day": 0, "chantier": 0})
    assert ds.build_state(project, inventory(project), hold="").used_today == {}

    def boom(_r: Path, _s: str) -> dict[str, Any]:
        raise OSError("x")

    monkeypatch.setattr("grimoire.tools.atelier_model.usage", boom)
    assert ds.build_state(project, inventory(project), hold="").used_today == {}


def test_plafond_atteint_par_la_consommation_connue(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.tools.atelier_model.usage", lambda _r, _s: {"readable": True, "day": 900, "chantier": 0})
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "registre:anthropic/claude-sonnet-5-5", "daily_cap_tokens": 800}], inv.by_key())
    state = ds.build_state(project, inv, hold="")
    assert "plafond du jour atteint" in choose_for("conversation", state).reason


def test_la_vue_ne_nomme_ni_chemin_de_controle_ni_valeur_de_cle(project: Path) -> None:
    text = json.dumps(ds.view(project, inventory=inventory(project)))
    assert ".grimoire" not in text
    assert "control" not in text


# ── Liaison de l'autorisation : l'empreinte de ce qu'elle désigne ───────────

HOSTED = """\
providers:
  - id: "cloud"
    enabled: true
    provider_type: "hosted"
    currency: "quota"
    invocation: "cloud-cli -p {prompt}"
    models:
      - {id: "m1", tier: "strong"}
    data_policy: {allowed_data_classes: ["public-docs"], forbidden_data_classes: []}
"""


def _registry(project: Path, text: str) -> None:
    (project / STANDARD / "llm-provider-registry.yaml").write_text(
        text + 'routing:\n  default_data_class: "public-docs"\n', encoding="utf-8"
    )
    av.clear_cache()


def test_l_autorisation_enregistre_l_empreinte_et_la_forme_est_exacte(project: Path) -> None:
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inv.by_key())
    stored = json.loads(ds.settings_path(project).read_text(encoding="utf-8"))
    entry = stored["usages"]["conversation"]["models"][0]
    assert entry["key"] == "ollama:llama3.2:3b"
    assert entry["fingerprint"] == av.fingerprint(
        "ollama-local", "llama3.2:3b", os.path.realpath("/usr/bin/ollama"), "run {model} {prompt}", "", ""
    )
    row = inv.by_key()["registre:anthropic/claude-sonnet-5-5"]
    assert row.fingerprint == av.fingerprint(
        "registre", "anthropic", "hosted", "quota", "claude -p {prompt}", "", "", "claude-sonnet-5-5"
    )


def test_registre_reecrit_sous_le_meme_nom_invalide_l_autorisation_puis_le_geste_la_retablit(project: Path) -> None:
    _registry(project, HOSTED)
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "registre:cloud/m1"}], inv.by_key())
    assert ds.view(project, inventory=inv)["usages"][0]["decision"]["kind"] == "use"
    # Le registre est réécrit : même nom de modèle, autre invocation (l'ancienne étiquette ne dit plus la vérité).
    _registry(project, HOSTED.replace("cloud-cli -p {prompt}", "evil-cli {prompt}"))
    inv2 = inventory(project)
    view = ds.view(project, inventory=inv2)
    conversation = view["usages"][0]
    assert conversation["decision"]["kind"] == "none"
    assert "a changé depuis ton autorisation" in conversation["decision"]["reason"]
    assert conversation["decision"]["trace"][0]["verdict"] == "ignoré"
    assert any("a changé depuis ton autorisation" in p for p in view["problems"])
    assert view["any_authorized"] is False
    assert conversation["authorized"][0]["stale"] is True
    # Ré-autoriser par le geste protégé (write_usage) lie l'autorisation à ce que la ligne désigne maintenant.
    ds.write_usage(project, "conversation", [{"key": "registre:cloud/m1"}], inv2.by_key())
    assert ds.view(project, inventory=inv2)["usages"][0]["decision"]["kind"] == "use"


def test_base_url_changee_invalide_aussi(project: Path) -> None:
    _registry(project, HOSTED)
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "registre:cloud/m1"}], inv.by_key())
    _registry(project, HOSTED.replace('invocation:', 'base_url: "https://autre.example"\n    invocation:'))
    assert ds.view(project, inventory=inventory(project))["usages"][0]["decision"]["kind"] == "none"


def test_un_registre_hostile_ne_remplace_jamais_le_vrai_ollama(project: Path, tmp_path: Path) -> None:
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inv.by_key())
    evil = tmp_path / "bin" / "ollama"
    evil.parent.mkdir()
    evil.write_text("#!/bin/sh\n", encoding="utf-8")
    evil.chmod(0o755)
    for provider_type, currency in (("local", "local"), ("hosted", "local"), ("cli", "quota")):
        _registry(
            project,
            f"""providers:
  - id: ollama
    enabled: true
    provider_type: {provider_type}
    currency: {currency}
    invocation: "{evil} run {{model}} {{prompt}}"
    models: [{{id: "llama3.2:3b", tier: strong}}]
""",
        )
        inv2 = inventory(project)
        rows = [m for m in inv2.models if m.model_id == "llama3.2:3b"]
        evil_rows = [m for m in rows if m.source == "registre"]
        assert evil_rows and all(m.egress == "texte" and m.cost != "gratuit-local" for m in evil_rows)  # jamais « local »
        assert any(m.cost == "payant" for m in evil_rows) or currency == "quota"  # monnaie « local » seule : traité comme payant
        decision = ds.view(project, inventory=inv2)["usages"][0]["decision"]
        assert decision["key"] == "ollama:llama3.2:3b"  # le vrai Ollama, jamais l'entrée du registre
        assert inv2.by_key()[decision["key"]].source == "ollama"


def test_deux_entrees_de_meme_nom_gardent_chacune_leur_ligne(project: Path) -> None:
    _registry(project, HOSTED + HOSTED.split("providers:\n")[1].replace('"cloud"', '"cloud"').replace("cloud-cli", "autre-cli"))
    rows = [m for m in inventory(project).models if m.ref == "registre:cloud/m1"]
    assert len(rows) == 2
    assert len({m.key for m in rows}) == 2 and all("#" in m.key for m in rows)
    assert len({m.fingerprint for m in rows}) == 2


def test_doublon_exact_n_est_garde_qu_une_fois(project: Path) -> None:
    _registry(project, HOSTED + HOSTED.split("providers:\n")[1])
    assert len([m for m in inventory(project).models if m.ref == "registre:cloud/m1"]) == 1


def test_ollama_reconnu_exige_type_local_chemin_du_path_et_aucune_adresse_distante() -> None:
    from grimoire.providers.registry import ProviderSpec

    def spec(**kw: object) -> ProviderSpec:
        base: dict[str, Any] = {
            "id": "ollama", "enabled": True, "provider_type": "local", "allowed_capabilities": (), "default_models": (),
            "fallback_order": (), "currency": "local", "invocation": "ollama run {model} {prompt}",
        }
        return ProviderSpec(**{**base, **kw})

    which = lambda _n: "/usr/bin/ollama"  # noqa: E731
    assert av.is_local_ollama(spec(), which)[0]
    assert av.is_local_ollama(spec(invocation="/usr/bin/ollama run {model} {prompt}"), which)[0]
    assert av.is_local_ollama(spec(base_url="http://127.0.0.1:11434"), which)[0]
    assert not av.is_local_ollama(spec(invocation="/tmp/evil/ollama run {prompt}"), which)[0]
    assert not av.is_local_ollama(spec(invocation="/usr/bin/ollama run {prompt}"), lambda _n: None)[0]
    assert not av.is_local_ollama(spec(base_url="https://x.example"), which)[0]
    assert not av.is_local_ollama(spec(anthropic_base_url="https://x.example"), which)[0]
    assert not av.is_local_ollama(spec(provider_type="hosted"), which)[0]
    assert not av.is_local_ollama(spec(invocation="curl http://x {prompt}"), which)[0]


def test_local_non_verifiable_est_etiquete_expose(project: Path) -> None:
    _registry(
        project,
        """providers:
  - id: "binaire"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "binaire {prompt}"
    models: [{id: "b1", tier: mid}]
  - id: "passerelle"
    enabled: true
    provider_type: "local"
    currency: "local"
    base_url: "http://127.0.0.1:4000"
    invocation: "passerelle {prompt}"
    models: [{id: "p1", tier: mid}]
""",
    )
    by = inventory(project).by_key()
    assert (by["registre:binaire/b1"].egress, by["registre:binaire/b1"].cost) == ("texte", "payant")
    assert (by["registre:passerelle/p1"].egress, by["registre:passerelle/p1"].cost) == ("rien", "gratuit-local")


# ── Plafonds non mesurés, durcissement du fichier ───────────────────────────


def test_la_vue_dit_quels_modeles_ont_une_consommation_mesuree(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.tools.atelier_model.usage", lambda _r, _s: {"readable": True, "day": 0, "chantier": 0})
    models = {m["key"]: m for m in ds.view(project, inventory=inventory(project))["inventory"]["models"]}
    assert models["registre:anthropic/claude-sonnet-5-5"]["usage_measured"] is True
    assert models["ollama:llama3.2:3b"]["usage_measured"] is False


def test_dossier_trop_ouvert_est_refuse_en_lecture_et_en_ecriture_sans_correction_silencieuse(project: Path) -> None:
    inv = inventory(project)
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inv.by_key())
    directory = ds.settings_path(project).parent
    directory.chmod(0o755)
    settings = ds.read_settings(project)
    assert all(not auths for auths in settings.allowed.values())
    assert "trop ouvert" in settings.problems[0]
    with pytest.raises(ds.DirectUseError, match="trop ouvert"):
        ds.write_usage(project, "conversation", [], inv.by_key())
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755  # rien n'a été « réparé » en silence


def test_zone_de_controle_en_lien_symbolique_est_refusee(project: Path, tmp_path: Path) -> None:
    from grimoire.missions.mandate import control_dir

    real = tmp_path / "ailleurs"
    real.mkdir(mode=0o700)
    (real / "direct-use").mkdir(mode=0o700)
    control_dir().parent.mkdir(parents=True, exist_ok=True)
    control_dir().symlink_to(real)
    settings = ds.read_settings(project)
    assert "lien symbolique" in settings.problems[0]
    with pytest.raises(ds.DirectUseError, match="lien symbolique"):
        ds.write_usage(project, "conversation", [], inventory(project).by_key())


def test_dossier_direct_use_en_lien_symbolique_est_refuse(project: Path, tmp_path: Path) -> None:
    from grimoire.missions.mandate import control_dir

    real = tmp_path / "ailleurs"
    real.mkdir(mode=0o700)
    control_dir().mkdir(mode=0o700, parents=True)
    (control_dir() / "direct-use").symlink_to(real)
    assert "lien symbolique" in ds.read_settings(project).problems[0]


def test_empreinte_invalide_dans_le_fichier_ignore_l_entree(project: Path) -> None:
    _write_raw(project, json.dumps({"usages": {"autres": {"models": [{"key": "ollama:x", "fingerprint": "pas-hex"}]}}}))
    settings = ds.read_settings(project)
    assert settings.allowed["autres"] == ()
    assert "empreinte invalide" in settings.problems[0]
