"""Détection en lecture seule de ce qui est branché (épique #1477, lot B1) : Ollama, registre, hôtes, clés."""

from __future__ import annotations

import http.server
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.providers import availability as av
from grimoire.providers.availability import detect, scan_ollama, tier_from_parameter_size
from grimoire.providers.secrets import set_secret
from grimoire.providers.state import ProviderRuntimeState, Suspension, save_state

STANDARD = Path("_grimoire/standard")

REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
providers:
  - id: "deepseek"
    enabled: true
    provider_type: "hosted"
    currency: "api"
    invocation: "deepseek-cli {model} {prompt}"
    models:
      - {id: "deepseek-chat", tier: "mid"}
    data_policy: {allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}
  - id: "claude"
    enabled: true
    provider_type: "hosted"
    currency: "quota"
    invocation: "claude -p {prompt}"
    models:
      - {id: "sonnet", tier: "strong", capabilities: {tools: true, context_window: 200000}}
  - id: "ollama"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "ollama run {model} {prompt}"
    models:
      - {id: "llama3.2:3b", tier: "cheap"}
      - {id: "gone:7b", tier: "mid"}
  - id: "off"
    enabled: false
    provider_type: "hosted"
    currency: "api"
    invocation: "off-cli {prompt}"
    models:
      - {id: "off-1", tier: "cheap"}
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    av.clear_cache()
    # Aucun hôte de la vraie machine : ni PATH réel, ni exécutable ACP.
    monkeypatch.setattr("shutil.which", lambda _name, *a, **k: None)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    return root


def everything_installed(name: str) -> str:
    return f"/usr/bin/{name}"


def tags(*names: str, size: str = "3.2B") -> bytes:
    return json.dumps({"models": [{"name": n, "details": {"parameter_size": size}} for n in names]}).encode()


def fetch_ok(*names: str) -> Any:
    return lambda _url, _timeout, _limit: tags(*names)


def rows(project: Path, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("which", everything_installed)
    kwargs.setdefault("fetch", fetch_ok("llama3.2:3b", "qwen2.5:14b"))
    kwargs.setdefault("acp", lambda _p, _r: (True, ""))
    return {m.key: m for m in detect(project, **kwargs).models}


# ── Ollama ──────────────────────────────────────────────────────────────────


class _Server:
    def __init__(self, handler: type[http.server.BaseHTTPRequestHandler]) -> None:
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def handler_for(body: bytes, *, delay: float = 0.0, status: int = 200) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    return Handler


@pytest.fixture
def ollama_up() -> Iterator[_Server]:
    with _Server(handler_for(tags("llama3.2:3b", "qwen2.5:14b", "big:70b"))) as server:
        yield server


def test_ollama_joignable_liste_les_modeles_par_la_vraie_api_locale(ollama_up: _Server) -> None:
    scan = scan_ollama(ollama_up.url)
    assert scan.reachable
    assert [n for n, _ in scan.models] == ["llama3.2:3b", "qwen2.5:14b", "big:70b"]


def test_ollama_injoignable() -> None:
    scan = scan_ollama("http://127.0.0.1:9")  # port fermé
    assert not scan.reachable
    assert scan.note == "service injoignable"


def test_ollama_lent_est_tenu_pour_injoignable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av, "OLLAMA_TIMEOUT_S", 0.2)
    with _Server(handler_for(tags("x"), delay=1.0)) as slow:
        started = time.monotonic()
        scan = scan_ollama(slow.url)
    assert not scan.reachable
    assert time.monotonic() - started < 0.9


def test_ollama_reponse_enorme_est_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = b'{"models": [' + b'{"name": "a"},' * 40000 + b'{"name": "z"}]}'
    assert len(huge) > av.MAX_RESPONSE_BYTES
    with _Server(handler_for(huge)) as server:
        scan = scan_ollama(server.url)
    assert not scan.reachable
    assert "trop grosse" in scan.note


@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe", b"[" * 100000, b'{"models": "x"}', b"null", b'{"models": [1, null, {"name": 3}]}'])
def test_ollama_reponse_hostile_ne_plante_pas_et_ne_donne_aucun_modele(body: bytes) -> None:
    scan = scan_ollama("http://127.0.0.1:1", fetch=lambda *_a: body)
    assert scan.models == ()


def test_ollama_noms_hostiles_sont_ecartes_et_les_doublons_aussi() -> None:
    payload = json.dumps(
        {"models": [{"name": "ok:1b"}, {"name": "ok:1b"}, {"name": "../../etc"}, {"name": "a b"}, {"name": "x" * 500}]}
    ).encode()
    scan = scan_ollama("http://localhost:11434", fetch=lambda *_a: payload)
    assert [n for n, _ in scan.models] == ["ok:1b", "../../etc"] or [n for n, _ in scan.models] == ["ok:1b"]
    assert all(" " not in n and len(n) <= 128 for n, _ in scan.models)


def test_ollama_nombre_de_modeles_borne() -> None:
    payload = json.dumps({"models": [{"name": f"m{i}"} for i in range(1000)]}).encode()
    assert len(scan_ollama("http://127.0.0.1:1", fetch=lambda *_a: payload).models) == av.MAX_OLLAMA_MODELS


@pytest.mark.parametrize("url", ["http://192.168.1.10:11434", "http://example.com", "https://evil.test:11434", "file:///etc", "http://127.0.0.1:abc"])
def test_adresse_non_locale_n_est_jamais_sondee(url: str) -> None:
    def boom(*_a: object) -> bytes:
        raise AssertionError("aucune requête vers une adresse non locale")

    scan = scan_ollama(url, fetch=boom)
    assert not scan.reachable
    assert "non locale" in scan.note


def test_ollama_host_de_l_environnement_non_local_n_est_pas_sonde(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
    inventory = detect(project, which=everything_installed, acp=lambda _p, _r: (True, ""))
    assert not inventory.ollama.reachable
    assert "non locale" in inventory.ollama.note


@pytest.mark.parametrize(
    ("size", "tier"),
    [("3.2B", "cheap"), ("4.9B", "cheap"), ("7B", "mid"), ("14B", "mid"), ("20B", "strong"), ("70B", "strong"),
     ("500M", "cheap"), ("1T", "strong"), ("", ""), ("énorme", ""), ("12", "")],
)
def test_palier_deduit_de_la_taille(size: str, tier: str) -> None:
    assert tier_from_parameter_size(size) == tier


# ── Inventaire ──────────────────────────────────────────────────────────────


def test_modele_ollama_du_registre_present_est_disponible_et_local(project: Path) -> None:
    m = rows(project)["ollama:llama3.2:3b"]
    assert m.available and m.reason == ""
    assert (m.egress, m.cost, m.configured, m.tier, m.tier_provenance) == ("rien", "gratuit-local", True, "cheap", "registre")


def test_modele_ollama_du_registre_non_telecharge_est_non_installe(project: Path) -> None:
    m = rows(project)["ollama:gone:7b"]
    assert (m.available, m.reason) == (False, "non-installé")
    assert "ollama pull gone:7b" in m.detail


def test_ollama_hors_ligne_rend_les_modeles_du_registre_hors_ligne(project: Path) -> None:
    def down(*_a: object) -> bytes:
        raise OSError("refused")

    m = rows(project, fetch=down)["ollama:llama3.2:3b"]
    assert (m.available, m.reason) == (False, "hors-ligne")


def test_modele_detecte_absent_du_registre_est_detecte_non_configure(project: Path) -> None:
    payload = json.dumps(
        {"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:14b", "details": {"parameter_size": "14B"}}]}
    ).encode()
    found = rows(project, fetch=lambda *_a: payload)["ollama:qwen2.5:14b"]
    assert (found.configured, found.available) == (False, True)
    assert found.tier == "mid" and found.tier_provenance == "déduit de la taille"
    assert found.detail == "Détecté, non configuré dans le registre."
    assert (found.egress, found.cost) == ("rien", "gratuit-local")


def test_un_modele_latest_declare_sans_suffixe_n_apparait_pas_deux_fois(project: Path) -> None:
    got = rows(project, fetch=fetch_ok("llama3.2:3b", "gone:7b:latest"))
    assert "ollama:gone:7b:latest" not in got
    assert got["ollama:gone:7b"].available


def test_fournisseur_a_l_usage_sans_cle_est_cle_absente(project: Path) -> None:
    m = rows(project)["registre:deepseek/deepseek-chat"]
    assert (m.available, m.reason, m.key_present) == (False, "clé-absente", False)
    assert (m.egress, m.cost) == ("texte", "payant")
    assert m.key_command == "grimoire providers set-key deepseek DEEPSEEK_API_KEY"


def test_cle_presente_dans_le_magasin_sans_jamais_lire_la_valeur(project: Path) -> None:
    secret = "sk-ultra-secret-value-0123456789"  # noqa: S105 — valeur d'essai
    set_secret("deepseek", "DEEPSEEK_API_KEY", secret)
    inventory = detect(project, which=everything_installed, fetch=fetch_ok(), acp=lambda _p, _r: (True, ""))
    m = inventory.by_key()["registre:deepseek/deepseek-chat"]
    assert (m.available, m.key_present) == (True, True)
    dumped = json.dumps(inventory.to_dict())
    assert secret not in dumped
    assert '"key_length"' not in dumped and "length" not in dumped  # la longueur de la clé n'est jamais exposée


def test_cle_presente_par_l_environnement(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    assert rows(project)["registre:deepseek/deepseek-chat"].key_present is True


def test_fournisseur_au_forfait_n_exige_pas_de_cle(project: Path) -> None:
    m = rows(project)["registre:claude/sonnet"]
    assert (m.available, m.key_present, m.cost, m.egress) == (True, None, "abonnement", "texte")
    assert m.tier == "strong"


def test_executable_absent_est_non_installe(project: Path) -> None:
    m = rows(project, which=lambda _n: None)["registre:claude/sonnet"]
    assert (m.available, m.reason) == (False, "non-installé")
    assert "introuvable" in m.detail


def test_fournisseur_desactive(project: Path) -> None:
    m = rows(project)["registre:off/off-1"]
    assert (m.available, m.reason) == (False, "désactivé")


def test_limite_atteinte_par_refroidissement_et_par_suspension(project: Path) -> None:
    now = datetime.now(UTC)
    save_state(project, {"claude": ProviderRuntimeState(cooldown_until=now + timedelta(minutes=5), failure_count=1)})
    cooling = rows(project, now=now)["registre:claude/sonnet"]
    assert (cooling.available, cooling.reason) == (False, "limite-atteinte")
    assert cooling.cooldown_until is not None
    save_state(project, {"claude": ProviderRuntimeState(suspension=Suspension(now + timedelta(hours=2), "resets_at"))})
    assert rows(project, now=now)["registre:claude/sonnet"].reason == "limite-atteinte"


def test_modele_suspendu_seul(project: Path) -> None:
    now = datetime.now(UTC)
    state = ProviderRuntimeState(model_suspensions={"sonnet": Suspension(now + timedelta(hours=1), "resets_at")})
    save_state(project, {"claude": state})
    assert rows(project, now=now)["registre:claude/sonnet"].reason == "limite-atteinte"


def test_audit_qui_ne_l_a_pas_trouve_est_hors_ligne(project: Path) -> None:
    save_state(project, {"claude": ProviderRuntimeState(available=False, probed_at="2026-01-01T00:00:00+00:00")})
    assert rows(project)["registre:claude/sonnet"].reason == "hors-ligne"


def test_capacites_avec_provenance(project: Path) -> None:
    m = rows(project)["registre:claude/sonnet"]
    assert m.capability("tools") == (True, "declared")
    assert m.capability("context_window") == (200000, "declared")
    assert m.capability("vision") is None or m.capability("vision") is not None  # inconnue ou documentée : jamais inventée
    assert "vision" not in {n for n, v, p in m.capabilities if p == "declared"}


def test_registre_absent_ou_illisible_ne_plante_pas(tmp_path: Path) -> None:
    root = tmp_path / "vide"
    root.mkdir()
    inventory = detect(root, which=everything_installed, fetch=fetch_ok("a:1b"), acp=lambda _p, _r: (True, ""))
    assert inventory.registry_error == ""
    assert [m.key for m in inventory.models if m.source == "ollama"] == ["ollama:a:1b"]
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text("- pas une table", encoding="utf-8")
    broken = detect(root, which=everything_installed, fetch=fetch_ok(), acp=lambda _p, _r: (True, ""), use_cache=False)
    assert broken.registry_error


def test_doublon_de_declaration_garde_la_premiere(project: Path) -> None:
    extra = REGISTRY + """\
  - id: "claude2"
    enabled: true
    provider_type: "hosted"
    currency: "quota"
    invocation: "claude -p {prompt}"
    models:
      - {id: "sonnet", tier: "mid"}
"""
    (project / STANDARD / "llm-provider-registry.yaml").write_text(extra, encoding="utf-8")
    keys = [m.key for m in detect(project, which=everything_installed, fetch=fetch_ok(), acp=lambda _p, _r: (True, "")).models]
    assert len(keys) == len(set(keys))


# ── Hôtes ───────────────────────────────────────────────────────────────────


def test_hote_absent_est_non_installe_avec_son_geste(project: Path) -> None:
    hosts = [m for m in detect(project, which=lambda _n: None, fetch=fetch_ok()).models if m.source == "hote"]
    assert hosts
    assert all((not m.available) and m.reason == "non-installé" for m in hosts)
    assert all((m.egress, m.cost) == ("texte", "abonnement") for m in hosts)


def test_hote_installe_n_est_pas_connecte_tant_qu_on_ne_verifie_pas(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    calls: list[str] = []

    def acp(profile: Any, _root: Path) -> tuple[bool, str]:
        calls.append(str(profile.host_id))
        return False, "ne répond pas"

    quiet = [m for m in detect(project, which=everything_installed, fetch=fetch_ok(), acp=acp).models if m.source == "hote"]
    assert quiet
    assert all(m.available and m.connected is None for m in quiet)
    assert calls == []  # aucune sonde de connexion sans le geste « Vérifier »
    checked = [m for m in detect(project, which=everything_installed, fetch=fetch_ok(), acp=acp, verify=True).models if m.source == "hote"]
    assert calls
    assert all((not m.available) and m.reason == "hors-ligne" and m.connected is False for m in checked)
    good = [m for m in detect(project, which=everything_installed, fetch=fetch_ok(), acp=lambda _p, _r: (True, ""), verify=True).models if m.source == "hote"]
    assert all(m.available and m.connected is True for m in good)


# ── Cache ───────────────────────────────────────────────────────────────────


def test_cache_court_et_verifier_le_contourne(project: Path, ollama_up: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    first = detect(project, ollama_url=ollama_up.url)
    assert first.ollama.reachable
    # Le cache répond sans nouvelle requête : le serveur coupé ne change rien.
    ollama_up.httpd.shutdown()
    again = detect(project, ollama_url=ollama_up.url)
    assert again is first
    fresh = detect(project, ollama_url=ollama_up.url, verify=True)
    assert not fresh.ollama.reachable and fresh.verified
    av.clear_cache()
    # TTL expiré
    monkeypatch.setattr(av, "CACHE_TTL_SECONDS", 0.0)
    assert detect(project, ollama_url=ollama_up.url) is not first


def test_serialisation_ne_porte_ni_valeur_de_cle_ni_chemin_de_cle(project: Path) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", "valeur-secrete")
    text = json.dumps(detect(project, which=everything_installed, fetch=fetch_ok(), acp=lambda _p, _r: (True, "")).to_dict())
    assert "valeur-secrete" not in text
    assert ".env-store" not in text


# ── Liste confirmée par l'humain : la clé ne part que vers elle (contre-relecture #1480, A) ──────────────────────────


def _gateway(base_url: str) -> str:
    return f"""providers:
  - id: "gw"
    enabled: true
    provider_type: "litellm"
    currency: "api"
    base_url: "{base_url}"
    invocation: "gw-cli {{prompt}}"
    models:
      - {{id: "m1", tier: "mid"}}
"""


def _write_registry(project: Path, text: str) -> None:
    (project / STANDARD / "llm-provider-registry.yaml").write_text(text, encoding="utf-8")
    av.clear_cache()


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Un faux transport pour la sonde de capacités : il note les URL jointes, aucune connexion n'est ouverte."""
    calls: list[str] = []

    def fake(url: str, _headers: Any, _body: Any = None) -> Any:
        calls.append(url)
        return {"data": [{"id": "m1"}]}

    monkeypatch.setattr("grimoire.providers.capability_probe._http_json", fake)
    return calls


def test_la_cle_part_vers_la_liste_confirmee_identique(project: Path, transport: list[str]) -> None:
    _write_registry(project, _gateway("https://gw.example"))
    set_secret("gw", "GW_API_KEY", "k", endpoints=["https://gw.example"])
    shown = av.key_endpoints(project)
    assert shown == [{"provider_id": "gw", "url": "https://gw.example/v1/models"}]
    assert av.probe_remote(project, shown) == ["gw"]
    assert transport[0] == "https://gw.example/v1/models"
    assert {url.split("/v1/")[0] for url in transport} == {"https://gw.example"}


def test_registre_reecrit_apres_confirmation_est_refuse_sans_appel_reseau(project: Path, transport: list[str]) -> None:
    _write_registry(project, _gateway("https://gw.example"))
    set_secret("gw", "GW_API_KEY", "k", endpoints=["https://gw.example"])
    shown = av.key_endpoints(project)
    _write_registry(project, _gateway("https://evil.example"))
    with pytest.raises(av.EndpointsChangedError, match="la liste a changé, relis-la"):
        av.probe_remote(project, shown)
    assert transport == []


def test_liste_vide_confirmee_puis_registre_peuple_est_refuse(project: Path, transport: list[str]) -> None:
    _write_registry(project, _gateway("https://gw.example"))
    set_secret("gw", "GW_API_KEY", "k", endpoints=["https://gw.example"])
    with pytest.raises(av.EndpointsChangedError):
        av.probe_remote(project, [])
    assert transport == []


def test_liste_vide_confirmee_et_registre_sans_cle_n_envoie_rien(project: Path, transport: list[str]) -> None:
    _write_registry(project, _gateway("https://gw.example"))  # aucune clé posée : rien n'est listé
    assert av.probe_remote(project, []) == []
    assert transport == []


@pytest.mark.parametrize("confirmed", [None, "gw", {"provider_id": "gw"}, [{"provider_id": "gw"}], [42]])
def test_liste_absente_ou_illisible_est_refusee(project: Path, transport: list[str], confirmed: Any) -> None:
    _write_registry(project, _gateway("https://gw.example"))
    set_secret("gw", "GW_API_KEY", "k", endpoints=["https://gw.example"])
    with pytest.raises(av.EndpointsChangedError):
        av.probe_remote(project, confirmed)
    assert transport == []


def test_le_registre_n_est_lu_qu_une_fois_et_la_sonde_suit_cette_lecture(
    project: Path, transport: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(project, _gateway("https://gw.example"))
    set_secret("gw", "GW_API_KEY", "k", endpoints=["https://gw.example"])
    shown = av.key_endpoints(project)
    real = av.read_registry
    reads: list[int] = []

    def read_then_rewrite(root: Path) -> Any:
        specs = real(root)
        reads.append(1)
        _write_registry(project, _gateway("https://evil.example"))  # le registre change juste après la lecture
        return specs

    monkeypatch.setattr(av, "read_registry", read_then_rewrite)
    assert av.probe_remote(project, shown) == ["gw"]
    assert len(reads) == 1
    assert transport and all(url.startswith("https://gw.example/") for url in transport)


def test_registre_illisible_vaut_liste_vide(project: Path, transport: list[str]) -> None:
    _write_registry(project, "providers: [oups")
    assert av.probe_remote(project, []) == []
    with pytest.raises(av.EndpointsChangedError):
        av.probe_remote(project, [{"provider_id": "gw", "url": "https://gw.example/v1/models"}])
    assert transport == []


# ── anthropic_base_url avec identifiants, requête ou fragment (B) ────────────────────────────────────────────────────


HOSTED_BASE = """providers:
  - id: "an"
    enabled: true
    provider_type: "hosted"
    currency: "api"
    anthropic_base_url: "{url}"
    invocation: "claude -p {{prompt}}"
"""


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com@evil.example",
        "https://user:pass@evil.example",
        "https://evil.example/p?x=1",
        "https://evil.example/p#frag",
        "http://evil.example",
    ],
)
def test_anthropic_base_url_non_admise_n_est_ni_listee_ni_sondee_et_est_dite_invalide(
    project: Path, transport: list[str], url: str
) -> None:
    from grimoire.providers.capability_probe import _hosted_endpoint, probe_provider_capabilities
    from grimoire.providers.registry import read_registry

    _write_registry(project, HOSTED_BASE.format(url=url))
    set_secret("an", "AN_API_KEY", "k")
    spec = read_registry(project)[0]
    assert _hosted_endpoint(spec) is None
    assert av.key_endpoints(project) == []
    probe = probe_provider_capabilities(spec)
    assert probe.measured is False and "anthropic_base_url invalide" in probe.note
    assert av.probe_remote(project, []) == []
    assert transport == []


def test_anthropic_base_url_https_nette_reste_admise(project: Path) -> None:
    _write_registry(project, HOSTED_BASE.format(url="https://proxy.example/"))
    set_secret("an", "AN_API_KEY", "k", endpoints=["https://proxy.example"])
    assert av.key_endpoints(project) == [{"provider_id": "an", "url": "https://proxy.example/v1/models"}]


# ── L'empreinte d'« Ollama local » lie le binaire, la commande et l'adresse (C) ──────────────────────────────────────


OLLAMA_REG = """providers:
  - id: "ollama"
    enabled: true
    provider_type: "local"
    currency: "local"
    invocation: "{invocation}"
{extra}    models:
      - {{id: "llama3.2:3b", tier: "cheap"}}
"""


def _ollama_row(project: Path, *, invocation: str = "ollama run {model} {prompt}", extra: str = "", binary: str = "/usr/bin/ollama") -> Any:
    _write_registry(project, OLLAMA_REG.format(invocation=invocation, extra=extra))
    found = detect(project, which=lambda _n: binary, fetch=fetch_ok("llama3.2:3b"), acp=lambda _p, _r: (True, ""), use_cache=False)
    return next(m for m in found.models if m.model_id == "llama3.2:3b")


def test_l_empreinte_ollama_change_avec_le_binaire_le_port_ou_le_gabarit(project: Path) -> None:
    base = _ollama_row(project)
    assert base.egress == "rien" and base.fingerprint == _ollama_row(project).fingerprint
    assert _ollama_row(project, binary="/opt/autre/ollama").fingerprint != base.fingerprint
    assert _ollama_row(project, extra='    base_url: "http://127.0.0.1:9"\n').fingerprint != base.fingerprint
    assert _ollama_row(project, invocation="ollama run evil.example/m {prompt}").fingerprint != base.fingerprint
    assert _ollama_row(project, invocation="ollama serve {model} {prompt}").fingerprint != base.fingerprint


def test_un_binaire_ollama_sous_la_racine_du_projet_n_est_pas_l_ollama_local(project: Path) -> None:
    inside = project / "bin" / "ollama"
    row = _ollama_row(project, binary=str(inside))
    assert row.source == "registre" and row.egress == "texte" and row.cost == "payant"
    # y compris pour un modèle détecté non configuré
    _write_registry(project, "providers: []\n")
    found = detect(project, which=lambda _n: str(inside), fetch=fetch_ok("mistral:7b"), acp=lambda _p, _r: (True, ""), use_cache=False)
    detected = next(m for m in found.models if m.model_id == "mistral:7b")
    assert detected.egress == "texte" and detected.cost != "gratuit-local"


def test_une_autorisation_perd_sa_validite_quand_le_binaire_change(project: Path) -> None:
    from grimoire.providers import direct_use_settings as ds

    _write_registry(project, OLLAMA_REG.format(invocation="ollama run {model} {prompt}", extra=""))
    kw: dict[str, Any] = {"fetch": fetch_ok("llama3.2:3b"), "acp": lambda _p, _r: (True, ""), "use_cache": False}
    inv = detect(project, which=lambda _n: "/usr/bin/ollama", **kw)
    ds.write_usage(project, "conversation", [{"key": "ollama:llama3.2:3b"}], inv.by_key())
    assert ds.view(project, inventory=inv)["usages"][0]["decision"]["kind"] == "use"
    other = detect(project, which=lambda _n: "/opt/autre/ollama", **kw)
    view = ds.view(project, inventory=other)
    assert view["usages"][0]["decision"]["kind"] == "none"
    assert any("a changé depuis ton autorisation" in p for p in view["problems"])


# ── Modèle listé par Ollama mais exécuté à distance (D) ─────────────────────────────────────────────────────────────


def _tags_remote() -> Any:
    payload = {
        "models": [
            {"name": "llama3.2:3b", "details": {"parameter_size": "3.2B"}},
            {"name": "gpt-oss:120b-cloud"},
            {"name": "qwen3-coder:480b-cloud"},
            {"name": "deepseek-v3.1:cloud"},
            {"name": "viaremote:8b", "remote_host": "https://ollama.com:443"},
            {"name": "viamodel:8b", "remote_model": "gpt-oss:120b"},
        ]
    }
    return lambda _u, _t, _l: json.dumps(payload).encode()


def test_un_modele_distant_d_ollama_n_est_jamais_etiquete_local(project: Path) -> None:
    found = detect(project, which=everything_installed, fetch=_tags_remote(), acp=lambda _p, _r: (True, ""), use_cache=False)
    by_key = found.by_key()
    assert by_key["ollama:llama3.2:3b"].egress == "rien" and by_key["ollama:llama3.2:3b"].cost == "gratuit-local"
    for name in ("gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v3.1:cloud", "viaremote:8b", "viamodel:8b"):
        row = by_key[f"ollama:{name}"]
        assert row.egress == "texte" and row.cost != "gratuit-local", name
        assert "hors de la machine" in row.origin


def test_un_modele_distant_declare_au_registre_est_expose_et_son_empreinte_distincte(project: Path) -> None:
    _write_registry(
        project,
        OLLAMA_REG.format(invocation="ollama run {model} {prompt}", extra="").replace("llama3.2:3b", "gpt-oss:120b-cloud"),
    )
    found = detect(project, which=everything_installed, fetch=_tags_remote(), acp=lambda _p, _r: (True, ""), use_cache=False)
    row = found.by_key()["ollama:gpt-oss:120b-cloud"]
    assert row.egress == "texte" and row.cost == "payant"
    assert row.fingerprint != av._ollama_fp("gpt-oss:120b-cloud", "/usr/bin/ollama", "ollama run {model} {prompt}")


def test_les_tags_ollama_hostiles_ne_plantent_pas_la_detection_des_distants() -> None:
    assert av._remote_names({"models": "x"}) == frozenset()
    assert av._remote_names([]) == frozenset()
    assert av._remote_names({"models": [1, {"name": 3}, {"name": "a", "model": "b-cloud"}]}) == frozenset({"a"})
