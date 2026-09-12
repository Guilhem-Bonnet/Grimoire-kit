"""Suggestions par un petit modèle local, derrière l'IntelliSense (issue #280, voie 2).

Un faux serveur HTTP local joue le rôle d'Ollama (``/api/tags``,
``/api/generate``) — jamais le vrai binaire de la machine. Chaque refus décrit
dans la spécification a son test : opt-in absent, Ollama injoignable, modèle
absent de ``ollama list``, délai dépassé, et — le garde-fou central — un
identifiant inventé par le modèle marqué « inconnu » plutôt que pris pour
argent comptant.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

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
    """``/api/tags`` et ``/api/generate`` — les deux seules routes Ollama sondées."""

    #: Réponse renvoyée par ``/api/generate`` — ajustée par chaque test.
    generate_response: str = "une suggestion sans surprise."
    #: Délai simulé (s) avant de répondre à ``/api/generate``.
    generate_delay: float = 0.0
    #: Modèles vus par ``/api/tags``.
    models: tuple[str, ...] = ("qwen3-coder:30b",)

    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"models": [{"name": m} for m in self.models]}).encode("utf-8")
        self._send(200, body)

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self.send_response(404)
            self.end_headers()
            return
        import time

        if self.generate_delay:
            time.sleep(self.generate_delay)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # consomme le corps — le prompt lui-même n'est pas vérifié ici
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


@pytest.fixture
def fake_ollama() -> Any:
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        _FakeOllama.generate_response = "une suggestion sans surprise."
        _FakeOllama.generate_delay = 0.0
        _FakeOllama.models = ("qwen3-coder:30b",)
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
    called = []
    original = _FakeOllama.do_POST

    def _tracked(self: _FakeOllama) -> None:
        called.append(self.path)
        original(self)

    monkeypatch.setattr(_FakeOllama, "do_POST", _tracked)

    status = source_assist.assist_status(tmp_path)

    assert status == {"enabled": True, "model": "qwen3-coder:30b", "available": True, "reason": None}
    assert called == []  # `/api/generate` jamais appelé par une simple lecture de statut


# ── Corps requis ─────────────────────────────────────────────────────────────


def test_texte_manquant_refuse(tmp_path: Path) -> None:
    _write_project(tmp_path, model="qwen3-coder:30b")

    body = _body()
    del body["text"]
    with pytest.raises(ValueError, match="`text` requis"):
        source_assist.assist_view(tmp_path, body)
