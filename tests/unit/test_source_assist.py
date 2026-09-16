"""Suggestions par un petit modèle local, derrière l'IntelliSense (issue #280, voie 2).

Un faux serveur HTTP local joue le rôle d'Ollama (``/api/tags``,
``/api/ps``, ``/api/generate``) — jamais le vrai binaire de la machine.
Chaque refus décrit dans la spécification a son test : opt-in absent, Ollama
injoignable, modèle absent de ``ollama list``, délai dépassé, et — le
garde-fou central — un identifiant inventé par le modèle marqué « inconnu »
plutôt que pris pour argent comptant. Section « chargement » (issue #450) :
un modèle présent dans ``ollama list`` mais absent de ``ollama ps``
(``/api/ps``) est un chargement en cours, jamais un dépassement de délai.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.tools import source_assist
from grimoire.tools.workspace_api import WorkspacePathError

_PROJECT_CONTEXT = """\
project:
  name: "demo"
source:
  assist:
    model: "{model}"
    allow_lan: {allow_lan}
"""


def _write_project(root: Path, *, model: str = "", allow_lan: bool = False) -> None:
    text = _PROJECT_CONTEXT.format(model=model, allow_lan="true" if allow_lan else "false")
    (root / "project-context.yaml").write_text(text, encoding="utf-8")
    (root / "_grimoire" / "overrides" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "overrides" / "agents" / "demo.md").write_text("---\nname: demo\n---\n", encoding="utf-8")


class _FakeOllama(http.server.BaseHTTPRequestHandler):
    """``/api/tags``, ``/api/ps`` et ``/api/generate`` — les routes Ollama sondées."""

    #: Réponse renvoyée par ``/api/generate`` — ajustée par chaque test.
    generate_response: str = "une suggestion sans surprise."
    #: Délai simulé (s) avant de répondre à ``/api/generate``.
    generate_delay: float = 0.0
    #: Modèles vus par ``/api/tags`` (``ollama list``).
    models: tuple[str, ...] = ("qwen3-coder:30b",)
    #: Modèles résidents en mémoire, vus par ``/api/ps`` (issue #450) — résident
    #: par défaut, pour ne pas changer le comportement des tests qui ne
    #: portent pas sur le chargement.
    running: tuple[str, ...] = ("qwen3-coder:30b",)
    #: Corps JSON de chaque appel ``/api/generate`` reçu — inspecté par les
    #: tests du préchauffage (``prompt: ""``, ``keep_alive: "30m"``).
    generate_calls: ClassVar[list[dict[str, Any]]] = []

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            body = json.dumps({"models": [{"name": m} for m in self.models]}).encode("utf-8")
            self._send(200, body)
            return
        if self.path == "/api/ps":
            body = json.dumps({"models": [{"name": m} for m in self.running]}).encode("utf-8")
            self._send(200, body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self.send_response(404)
            self.end_headers()
            return
        if self.generate_delay:
            time.sleep(self.generate_delay)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        with contextlib.suppress(ValueError):
            _FakeOllama.generate_calls.append(json.loads(raw))
        body = json.dumps({"response": self.generate_response}).encode("utf-8")
        self._send(200, body)

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _drain_warm_up(timeout: float = 6.0) -> None:
    """Attend qu'aucun préchauffage (:func:`source_assist._trigger_warm_up`)
    ne reste en vol avant de réinitialiser l'état partagé de ``_FakeOllama``
    ni d'arrêter le serveur.

    Régression #529 : ``_trigger_warm_up`` démarre un thread démon que ni
    ``assist_status`` ni le test appelant ne joignent — un test qui déclenche
    un chargement (``running = ()``) puis rend la main avant que ce thread
    n'ait fini laissait le thread écrire dans ``_FakeOllama.generate_calls``
    (classvar partagée entre tous les tests) pendant le test SUIVANT, une
    fois la liste réinitialisée par CETTE fixture : la course produisait
    tantôt 1 tantôt 2 entrées vues par le test suivant (``assert 2 == 1``
    observé en CI, jamais localement). ``_WARM_UP_INFLIGHT`` (retiré dans le
    ``finally`` de ``_warm_up_model`` une fois l'appel terminé, avec ou sans
    succès) est le seul signal disponible sans changer le code de
    production : l'attendre vide ici garantit que le thread a fini d'écrire
    avant que le prochain test ne parte d'un état propre.
    """
    deadline = time.monotonic() + timeout
    while source_assist._WARM_UP_INFLIGHT and time.monotonic() < deadline:
        time.sleep(0.02)


@pytest.fixture
def fake_ollama() -> Any:
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        _drain_warm_up()
        _FakeOllama.generate_response = "une suggestion sans surprise."
        _FakeOllama.generate_delay = 0.0
        _FakeOllama.models = ("qwen3-coder:30b",)
        _FakeOllama.running = ("qwen3-coder:30b",)
        _FakeOllama.generate_calls = []
        server.shutdown()
        thread.join(timeout=2)


def _body(**kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": "_grimoire/overrides/agents/demo.md",
        "text": "---\nname: demo\nuse_when: \n---\n",
        "intent": "draft-body",
        "position": {"line": 2, "col": 0},
    }
    payload.update(kwargs)
    return payload


# ── Opt-in ───────────────────────────────────────────────────────────────────


def test_opt_in_absent_est_un_refus_nomme(tmp_path: Path) -> None:
    _write_project(tmp_path, model="")

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False
    assert "source.assist.model" in result["reason"]


def test_assist_enabled_model_lit_project_context(tmp_path: Path) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    assert source_assist.assist_enabled_model(tmp_path) == "qwen3-coder:30b"

    _write_project(tmp_path, model="")
    assert source_assist.assist_enabled_model(tmp_path) == ""


def test_projet_sans_config_lisible_desactive_l_assistance(tmp_path: Path) -> None:
    # Pas de project-context.yaml du tout — même tolérance que agents_view.
    (tmp_path / "_grimoire" / "overrides" / "agents").mkdir(parents=True, exist_ok=True)

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False


# ── Garde de chemin — avant même l'opt-in ──────────────────────────────────


def test_chemin_hors_etage_refuse(tmp_path: Path) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")

    with pytest.raises(WorkspacePathError):
        source_assist.assist_view(tmp_path, _body(path="ailleurs/x.md"))


def test_intention_inconnue_refusee(tmp_path: Path) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")

    with pytest.raises(ValueError, match="intention inconnue"):
        source_assist.assist_view(tmp_path, _body(intent="invente"))


# ── URL non locale — doctrine « aucune donnée hors de la machine » ─────────


def test_url_ollama_non_locale_sans_allow_lan_est_un_refus_nomme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b", allow_lan=False)
    # TEST-NET-1 (RFC 5737) : jamais routable, jamais loopback — et jamais
    # contactée puisque le garde doit refuser avant toute tentative réseau.
    monkeypatch.setenv("OLLAMA_HOST", "http://192.0.2.10:11434")

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False
    assert "n'est pas locale" in result["reason"]
    assert "source.assist.allow_lan" in result["reason"]


def test_url_ollama_non_locale_avec_allow_lan_appelle_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    """``allow_lan: true`` lève le garde — l'appel atteint bien Ollama ensuite.

    Le faux serveur écoute sur une adresse de bouclage réelle (aucun réseau
    d'essai n'a de machine distante à disposition en CI) ; ``_is_local_url``
    est neutralisé pour ce test précis afin de simuler une URL réellement
    non locale sans dépendre d'une topologie réseau — c'est la même fonction
    que le test ci-dessus exerce en conditions réelles.
    """
    _write_project(tmp_path, model="qwen3-coder:30b", allow_lan=True)
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(source_assist, "_is_local_url", lambda _url: False)

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is True
    assert result["model"] == "qwen3-coder:30b"


# ── Ollama absent ou injoignable ────────────────────────────────────────────


def test_ollama_injoignable_est_un_refus_discret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    # Un port fermé (personne n'écoute) plutôt qu'une adresse arbitraire : le
    # refus doit venir d'une connexion refusée, pas d'une résolution DNS.
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False
    assert "injoignable" in result["reason"]


def test_modele_absent_de_ollama_list_est_un_refus_nomme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="modele-jamais-installe:1b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False
    assert "modele-jamais-installe:1b" in result["reason"]
    assert "qwen3-coder:30b" in result["reason"]


# ── Délai dépassé ────────────────────────────────────────────────────────────


def test_delai_depasse_est_un_refus_discret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(source_assist, "ASSIST_TIMEOUT_S", 0.2)
    _FakeOllama.generate_delay = 1.5

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is False
    assert "n'a pas répondu" in result["reason"]


# ── Suggestion acceptée, identifiant inconnu marqué ─────────────────────────


def test_suggestion_disponible_sans_identifiant_marque(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.generate_response = "Cet agent répond aux demandes de configuration réseau."

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is True
    assert result["model"] == "qwen3-coder:30b"
    assert result["suggestion"] == "Cet agent répond aux demandes de configuration réseau."
    assert result["unknown"] == []


def test_agent_invente_par_le_modele_est_marque_inconnu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    # `demo` est le seul agent installé (voir _write_project) — `fantome` n'existe
    # nulle part : le modèle l'invente, la vérification doit le voir.
    _FakeOllama.generate_response = 'Route la demande vers <agent tag="fantome">.'

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is True
    assert len(result["unknown"]) == 1
    assert result["unknown"][0]["text"] == "fantome"
    assert result["unknown"][0]["kind"] == "agent"


def test_chemin_mort_invente_par_le_modele_est_marque_inconnu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.generate_response = "Voir `_grimoire/kit/agents/n-existe-pas.md` pour le détail."

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is True
    kinds = {u["kind"] for u in result["unknown"]}
    assert "path" in kinds


# ── Intention explain-diagnostic ────────────────────────────────────────────


def test_explain_diagnostic_transmet_le_diagnostic_au_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.generate_response = "Ce chemin n'existe pas dans le projet."

    result = source_assist.assist_view(
        tmp_path,
        _body(
            intent="explain-diagnostic",
            diagnostic={"family": "dead-path", "message": "chemin cité mais introuvable : x"},
        ),
    )

    assert result["available"] is True
    assert result["intent"] == "explain-diagnostic"


# ── Statut (GET) — jamais de coût, jamais d'appel à /api/generate ──────────


def test_status_opt_in_absent(tmp_path: Path) -> None:
    _write_project(tmp_path, model="")

    status = source_assist.assist_status(tmp_path)

    assert status == {
        "enabled": False,
        "model": "",
        "available": False,
        "reason": "assistance désactivée : définissez `source.assist.model` dans project-context.yaml",
    }


def test_status_enabled_mais_ollama_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")

    status = source_assist.assist_status(tmp_path)

    assert status["enabled"] is True
    assert status["available"] is False


def test_status_pret_ne_contacte_jamais_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")

    status = source_assist.assist_status(tmp_path)

    assert status == {
        "enabled": True,
        "model": "qwen3-coder:30b",
        "available": True,
        "loading": False,
        "reason": None,
    }
    assert _FakeOllama.generate_calls == []  # `/api/generate` jamais appelé par une simple lecture de statut


# ── Chargement du modèle (issue #450) ───────────────────────────────────────
#
# Un modèle connu de `ollama list` (`/api/tags`) mais absent de `ollama ps`
# (`/api/ps`) est en cours de chargement par Ollama : ni `assist_status` ni
# `assist_view` ne doivent le confondre avec une panne ou un dépassement de
# délai de 10 s.


def test_status_modele_non_resident_indique_le_chargement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.running = ()  # présent dans `ollama list`, absent de `ollama ps`

    status = source_assist.assist_status(tmp_path)

    assert status["enabled"] is True
    assert status["available"] is False
    assert status["loading"] is True
    assert "chargement" in status["reason"]
    assert "n'a pas répondu" not in status["reason"]


def test_status_non_resident_declenche_un_chargement_sans_generer_de_texte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    """``assist_status`` déclenche le chargement lui-même — jamais une génération.

    La requête de préchauffage tourne en tâche de fond (elle ne doit pas
    ralentir la réponse HTTP) : ce test l'attend explicitement avant de
    vérifier son contenu.
    """
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.running = ()

    source_assist.assist_status(tmp_path)

    deadline = time.monotonic() + 2.0
    while not _FakeOllama.generate_calls and time.monotonic() < deadline:
        time.sleep(0.02)

    assert len(_FakeOllama.generate_calls) == 1
    assert _FakeOllama.generate_calls[0]["prompt"] == ""
    assert _FakeOllama.generate_calls[0]["keep_alive"] == "30m"
    assert _FakeOllama.generate_calls[0]["model"] == "qwen3-coder:30b"


def test_assist_view_modele_non_resident_repond_immediatement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    """Le premier appel pendant un chargement répond tout de suite — jamais après 10 s.

    ``generate_delay`` est réglé bien au-delà du délai normal : si le code
    appelait quand même ``/api/generate`` pour produire une suggestion, ce
    test le verrait dépasser sa borne de 2 s.
    """
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.running = ()
    _FakeOllama.generate_delay = 5.0

    start = time.monotonic()
    result = source_assist.assist_view(tmp_path, _body())
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    assert result["available"] is False
    assert "charge" in result["reason"]
    assert "n'a pas répondu" not in result["reason"]


def test_assist_view_modele_resident_repond_normalement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    """Un modèle déjà résident garde le chemin existant — aucune régression."""
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    _FakeOllama.generate_response = "suggestion normale, modèle déjà chargé."

    result = source_assist.assist_view(tmp_path, _body())

    assert result["available"] is True
    assert result["suggestion"] == "suggestion normale, modèle déjà chargé."


def test_api_ps_indisponible_ne_bloque_pas_un_ancien_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ollama: http.server.HTTPServer
) -> None:
    """``/api/ps`` absent (Ollama trop ancien) : résidence inconnue traitée comme résidente.

    Sans cette tolérance, un Ollama qui ne connaît pas ``/api/ps`` afficherait
    « chargement » indéfiniment plutôt que de laisser l'appel réel décider.
    """
    _write_project(tmp_path, model="qwen3-coder:30b")
    port = fake_ollama.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(source_assist, "_probe_running_models", lambda _base_url: None)

    status = source_assist.assist_status(tmp_path)
    result = source_assist.assist_view(tmp_path, _body())

    assert status["available"] is True
    assert status["loading"] is False
    assert result["available"] is True


# ── Corps requis ─────────────────────────────────────────────────────────────


def test_texte_manquant_refuse(tmp_path: Path) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")

    body = _body()
    del body["text"]
    with pytest.raises(ValueError, match="`text` requis"):
        source_assist.assist_view(tmp_path, body)
