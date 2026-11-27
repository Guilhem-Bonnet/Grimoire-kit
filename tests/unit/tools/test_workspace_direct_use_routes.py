"""Routes de la page « Modèles autorisés » (épique #1477, lot B1) : lecture, écriture sous clé de contrôle, « Vérifier ».

Jouées contre un vrai serveur HTTP du cockpit (garde de requête comprise). Aucun appel de modèle ni service distant :
la détection d'Ollama est simulée, les hôtes sont absents, la sonde de capacités distante est remplacée par un espion.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions import mandate as md
from grimoire.providers import availability as av
from grimoire.providers import direct_use_settings as ds
from grimoire.providers.registry import read_registry
from grimoire.tools import project_registry
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX, workspace_post

from .. import _cockpit_http

DIRECT = f"{PREFIX}providers/direct-use"
VERIFY = f"{PREFIX}providers/direct-use/verify"
PROVIDERS = f"{PREFIX}providers"
REGISTRY_PATH = Path("_grimoire/standard/llm-provider-registry.yaml")
REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
providers:
  - id: "ollama"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "ollama run {model} {prompt}"
    models:
      - {id: "llama3.2:3b", tier: "cheap"}
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    currency: "api"
    invocation: "claude -p {prompt}"
    models:
      - {id: "claude-sonnet-5-5", tier: "strong"}
"""
TAGS = json.dumps(
    {"models": [{"name": "llama3.2:3b"}, {"name": "extra:8b", "details": {"parameter_size": "8B"}}]}
).encode()


def _key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr("shutil.which", lambda _n, *a, **k: None)
    monkeypatch.setattr(av, "_fetch", lambda _u, _t, _l: TAGS)
    av.clear_cache()


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Path]]:
    root = tmp_path / "home-project"
    (root / ".git").mkdir(parents=True)
    (root / REGISTRY_PATH).parent.mkdir(parents=True)
    (root / REGISTRY_PATH).write_text(REGISTRY, encoding="utf-8")
    project_registry.save_registry([{"name": "Home", "path": str(root.resolve()), "slug": "home"}])
    project_registry.set_selected_slug("home")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_SLUG", "home")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_PATH", root.resolve())
    cmd_cockpit._API_CACHE.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], root.resolve()
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


def _get(port: int) -> Any:
    status, body = _cockpit_http.get(port, f"{DIRECT}?project=home")
    assert status == 200, body
    return body


def _post(port: int, route: str, body: dict[str, Any], *, key: str | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    payload = {"project": "home", **body}
    if key is not None:
        payload["control_key"] = key
    return _cockpit_http.post(port, f"{route}?project=home", payload, headers=headers)


def _authorized(root: Path) -> dict[str, list[tuple[str, int | None]]]:
    settings = ds.read_settings(root)
    return {u: [(a.key, a.daily_cap_tokens) for a in auths] for u, auths in settings.allowed.items() if auths}


def test_les_routes_sont_declarees_dans_les_tables_du_cockpit() -> None:
    assert DIRECT in GET_ROUTES and DIRECT in POST_ROUTES and VERIFY in POST_ROUTES


# ── Lecture ─────────────────────────────────────────────────────────────────


def test_la_lecture_montre_l_inventaire_et_aucun_modele_autorise_par_defaut(server: tuple[int, Path]) -> None:
    port, _root = server
    body = _get(port)
    assert body["any_authorized"] is False
    keys = {m["key"]: m for m in body["inventory"]["models"]}
    assert keys["ollama:llama3.2:3b"]["state"] == "disponible"
    assert keys["ollama:extra:8b"]["configured"] is False
    assert keys["registre:anthropic/claude-sonnet-5-5"]["reason"] == "clé-absente"
    assert keys["registre:anthropic/claude-sonnet-5-5"]["key_command"] == "grimoire providers set-key anthropic ANTHROPIC_API_KEY"
    assert [u["id"] for u in body["usages"]] == ["conversation", "brainstorm", "redaction", "autres"]


def test_la_lecture_n_ecrit_rien_et_ne_sonde_aucun_service_distant(
    server: tuple[int, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, root = server
    calls: list[str] = []
    monkeypatch.setattr("grimoire.providers.capability_probe.probe_capabilities", lambda *a, **k: calls.append("probe"))
    before = sorted(str(p) for p in root.rglob("*"))
    _get(port)
    assert calls == []
    assert sorted(str(p) for p in root.rglob("*")) == before
    assert not ds.settings_path(root).exists()


# ── Écriture : clé de contrôle ──────────────────────────────────────────────

BODY = {"usage": "conversation", "models": [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": 1000}]}


def test_ecriture_refusee_sans_cle_de_controle_meme_avec_la_garde_de_requete_satisfaite(
    server: tuple[int, Path],
) -> None:
    port, root = server
    status, body = _post(port, DIRECT, BODY, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert status == 403, body
    assert "geste d'humain" in body["error"]
    assert _authorized(root) == {}


@pytest.mark.parametrize("key", ["", "fausse", " ", "0" * 64])
def test_ecriture_refusee_avec_une_cle_fausse(server: tuple[int, Path], key: str) -> None:
    port, root = server
    _key()  # la vraie clé existe
    status, _body = _post(port, DIRECT, BODY, key=key)
    assert status == 403
    assert _authorized(root) == {}


def test_ecriture_refusee_depuis_une_origine_tierce_meme_avec_la_cle(server: tuple[int, Path]) -> None:
    port, root = server
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        status, _body = _post(port, DIRECT, BODY, key=_key(), headers=headers)
        assert status in (403, 415)
    status, _ = _cockpit_http.request(
        port, "POST", f"{DIRECT}?project=home", data=json.dumps({**BODY, "control_key": _key()}).encode(),
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert status in (403, 415)
    assert _authorized(root) == {}


def test_un_texte_dicte_ne_se_confirme_pas_meme_avec_la_cle(server: tuple[int, Path]) -> None:
    port, root = server
    status, body = _post(port, DIRECT, {**BODY, "dictation": True}, key=_key())
    assert status == 400
    assert "ne se dicte pas" in body["error"]
    assert _authorized(root) == {}


def test_ecriture_acceptee_avec_la_cle_puis_relue(server: tuple[int, Path]) -> None:
    port, root = server
    status, body = _post(port, DIRECT, BODY, key=_key())
    assert status == 200, body
    assert _authorized(root) == {"conversation": [("ollama:llama3.2:3b", 1000)]}
    assert body["any_authorized"] is True
    conv = next(u for u in body["usages"] if u["id"] == "conversation")
    assert conv["decision"]["kind"] == "use"
    assert conv["decision"]["key"] == "ollama:llama3.2:3b"
    # Relue par la route de lecture, sans clé.
    again = _get(port)
    assert next(u for u in again["usages"] if u["id"] == "conversation")["authorized"][0]["daily_cap_tokens"] == 1000
    # Réordonner, plafonner, lever un plafond : même geste, même clé.
    reorder = {"usage": "conversation", "models": [{"key": "ollama:extra:8b"}, {"key": "ollama:llama3.2:3b"}]}
    assert _post(port, DIRECT, reorder, key=_key())[0] == 200
    assert _authorized(root)["conversation"] == [("ollama:extra:8b", None), ("ollama:llama3.2:3b", None)]


@pytest.mark.parametrize(
    "body",
    [
        {"usage": "danse", "models": []},
        {"usage": "conversation"},
        {"usage": "conversation", "models": "x"},
        {"usage": "conversation", "models": [{"key": "registre:fantome/x"}]},
        {"usage": "conversation", "models": [{"key": "ollama:llama3.2:3b", "daily_cap_tokens": -1}]},
    ],
)
def test_ecriture_invalide_avec_la_cle_est_un_400_qui_n_ecrit_rien(server: tuple[int, Path], body: dict[str, Any]) -> None:
    port, root = server
    status, _ = _post(port, DIRECT, body, key=_key())
    assert status == 400
    assert _authorized(root) == {}


def test_un_corps_qui_nomme_un_autre_projet_est_refuse(server: tuple[int, Path]) -> None:
    _port, root = server
    for route in (DIRECT, VERIFY, PROVIDERS):
        with pytest.raises(ValueError, match="visait"):
            workspace_post(root, route, {"project": "ailleurs", "control_key": _key(), **BODY})
    assert _authorized(root) == {}


# ── « Vérifier maintenant » ─────────────────────────────────────────────────


def test_verifier_sonde_a_la_demande_seulement_et_sans_cle_de_controle(
    server: tuple[int, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _root = server
    calls: list[str] = []
    monkeypatch.setattr("grimoire.providers.capability_probe.probe_capabilities", lambda *a, **k: calls.append("probe"))
    status, body = _post(port, VERIFY, {})
    assert status == 200, body
    assert calls == ["probe"]
    assert body["inventory"]["verified"] is True


def test_verifier_survit_a_une_sonde_qui_echoue(server: tuple[int, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    port, _root = server

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("réseau")

    monkeypatch.setattr("grimoire.providers.capability_probe.probe_capabilities", boom)
    status, body = _post(port, VERIFY, {})
    assert status == 200, body


def test_verifier_refuse_une_origine_tierce(server: tuple[int, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    port, _root = server
    calls: list[str] = []
    monkeypatch.setattr("grimoire.providers.capability_probe.probe_capabilities", lambda *a, **k: calls.append("probe"))
    status, _ = _post(port, VERIFY, {}, headers={"Origin": "https://evil.example"})
    assert status == 403
    assert calls == []


# ── Ajouter les modèles Ollama détectés (route d'écriture des fournisseurs) ─


def test_ajouter_un_modele_ollama_detecte_exige_la_cle_et_ecrit_au_registre(server: tuple[int, Path]) -> None:
    port, root = server
    body = {"action": "add-local", "models": ["extra:8b"]}
    status, _ = _post(port, PROVIDERS, body)
    assert status == 403
    assert [m.id for p in read_registry(root) if p.id == "ollama" for m in p.models] == ["llama3.2:3b"]
    status, answer = _post(port, PROVIDERS, body, key=_key())
    assert status == 200, answer
    assert answer["added"] == ["extra:8b"]
    ollama = next(p for p in read_registry(root) if p.id == "ollama")
    assert {m.id: m.tier for m in ollama.models} == {"llama3.2:3b": "cheap", "extra:8b": "mid"}  # 8B : déduit mid
    # La clé du modèle ne change pas : une autorisation donnée avant l'ajout reste valable.
    assert _post(port, DIRECT, {"usage": "autres", "models": [{"key": "ollama:extra:8b"}]}, key=_key())[0] == 200
    # Idempotent : rien de plus à ajouter.
    again = _post(port, PROVIDERS, body, key=_key())[1]
    assert again["added"] == []


def test_ajouter_refuse_un_modele_non_detecte_ou_une_liste_invalide(server: tuple[int, Path]) -> None:
    port, root = server
    for models in (["fantome:1b"], [], "x", [3], None):
        status, _ = _post(port, PROVIDERS, {"action": "add-local", "models": models}, key=_key())
        assert status == 400
    assert [m.id for p in read_registry(root) if p.id == "ollama" for m in p.models] == ["llama3.2:3b"]


def test_ajouter_cree_le_fournisseur_local_s_il_manque(server: tuple[int, Path]) -> None:
    port, root = server
    (root / REGISTRY_PATH).write_text(REGISTRY.split('  - id: "ollama"')[0] + '  - id: "anthropic"\n    enabled: true\n    provider_type: "hosted"\n    currency: "api"\n    invocation: "claude -p {prompt}"\n', encoding="utf-8")
    status, answer = _post(port, PROVIDERS, {"action": "add-local", "models": ["extra:8b", "llama3.2:3b"]}, key=_key())
    assert status == 200, answer
    created = next(p for p in read_registry(root) if p.id == "ollama")
    assert (created.provider_type, created.currency, created.enabled) == ("local", "local", True)
    assert {m.id for m in created.models} == {"extra:8b", "llama3.2:3b"}


def test_ajouter_sans_registre_est_un_400(server: tuple[int, Path]) -> None:
    port, root = server
    (root / REGISTRY_PATH).unlink()
    status, body = _post(port, PROVIDERS, {"action": "add-local", "models": ["extra:8b"]}, key=_key())
    assert status == 400
    assert "standard init" in body["error"]
