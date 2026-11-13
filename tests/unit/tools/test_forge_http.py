"""Caractérisation du transport HTTP de l'atelier — ``forge_http.make_handler``.

Ces tests figent, par de vraies requêtes sur la loopback, les branches du
handler qu'aucun test n'exerçait avant son découpage : flux SSE, routes
blueprint, extensions, bascule de fonctionnalité, service de fichiers, et la
traduction des exceptions en codes HTTP. Ils ont été écrits et passés sur le
code d'avant le découpage, puis rejoués tels quels après.

L'API est un double qui consigne ses appels : ce fichier teste le transport
(quelle méthode reçoit quoi, quel code et quel corps repartent, quelle trace
gouvernée est écrite) ; le métier de ``ForgeAPI`` a ses propres tests.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.tools import forge_http
from grimoire.tools.ext_manager import ExtensionError
from grimoire.tools.forge_http import make_handler
from grimoire.tools.workspace_routes import WORKSPACE_UNHANDLED


class _Api:
    """Double de ``ForgeAPI`` : consigne chaque appel, rend ou lève ce qu'on lui a préparé."""

    def __init__(self, root: Path, ui_dir: Path | None) -> None:
        self.project_root = root
        self.ui_dir = ui_dir
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}
        self.failures: dict[str, BaseException] = {}

    def __getattr__(self, name: str) -> Any:
        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if name in self.failures:
                raise self.failures[name]
            return self.answers.get(name, {"called": name})

        return _call

    def called(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(args, kwargs) for seen, args, kwargs in self.calls if seen == name]


class _Host:
    def __init__(self, api: _Api, port: int) -> None:
        self.api = api
        self.port = port

    def request(self, method: str, path: str, payload: Any = None) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            headers = {} if payload is None else {"Content-Type": "application/json"}
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
        code, _headers, body = self.request(method, path, payload)
        return code, json.loads(body.decode("utf-8"))

    def mutations(self) -> list[dict[str, Any]]:
        ledger = self.api.project_root / "_grimoire-runtime-output" / "hook-runtime" / "serve-mutations.jsonl"
        if not ledger.is_file():
            return []
        entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        for entry in entries:
            assert entry.pop("ts"), "chaque trace est horodatée"
        return entries


def _serve(api: _Api) -> Any:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))  # type: ignore[arg-type]
    # Le flux SSE ne rend la main qu'à la rupture du tube : ne pas l'attendre à la fermeture.
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def host(tmp_path: Path) -> Any:
    api = _Api(tmp_path / "projet", None)
    api.project_root.mkdir()
    httpd = _serve(api)
    yield _Host(api, httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def site(tmp_path: Path) -> Any:
    ui = tmp_path / "ui"
    (ui / "sous").mkdir(parents=True)
    (ui / "index.html").write_text("<html><head></head><body>accueil</body></html>", encoding="utf-8")
    (ui / "sous" / "index.html").write_text("<html><head></head><body>sous-page</body></html>", encoding="utf-8")
    (ui / "style.css").write_bytes(b"body { color: red }")
    (ui / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "secret.txt").write_text("hors du site", encoding="utf-8")
    api = _Api(tmp_path / "projet", ui)
    api.project_root.mkdir()
    httpd = _serve(api)
    yield _Host(api, httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


# ── GET : routes propres à l'atelier et traduction des exceptions ────────────


def test_get_des_routes_blueprint(host: _Host) -> None:
    assert host.json("GET", "/api/blueprints/mon-bp/diff") == (200, {"called": "blueprint_diff"})
    assert host.json("GET", "/api/blueprints/mon-bp") == (200, {"called": "blueprint_get"})
    assert host.api.called("blueprint_diff") == [(("mon-bp",), {})]
    assert host.api.called("blueprint_get") == [(("mon-bp",), {})]


def test_get_des_routes_projets_et_donnees(host: _Host) -> None:
    assert host.json("GET", "/api/status") == (200, {"called": "status"}), "la table commune passe d'abord"
    assert host.json("GET", "/api/projects") == (200, {"called": "projects_view"})
    assert host.json("GET", "/api/fs/browse?path=/tmp/x") == (200, {"called": "browse_view"})
    assert host.json("GET", "/api/fs/browse") == (200, {"called": "browse_view"})
    assert host.json("GET", "/api/data/status") == (200, {"called": "data_status"})
    assert host.api.called("browse_view") == [(("/tmp/x",), {}), ((None,), {})]


@pytest.mark.parametrize(
    ("failure", "code", "message"),
    [
        (FileNotFoundError("le-fichier"), 404, "introuvable : le-fichier"),
        (PermissionError("pas ici"), 403, "pas ici"),
        (ValueError("valeur refusée"), 400, "valeur refusée"),
        (json.JSONDecodeError("json cassé", "doc", 0), 400, "json cassé: line 1 column 1 (char 0)"),
    ],
)
def test_get_traduit_les_exceptions(host: _Host, failure: Exception, code: int, message: str) -> None:
    host.api.failures["projects_view"] = failure
    assert host.json("GET", "/api/projects") == (code, {"error": message})


def test_get_sans_ui_annonce_l_api(host: _Host) -> None:
    assert host.json("GET", "/nimporte/quoi.html") == (200, {"grimoire": "serve", "hint": "API disponible sous /api/"})


# ── SSE ──────────────────────────────────────────────────────────────────────


def test_le_flux_sse_ne_rend_que_les_lignes_ajoutees_apres_l_ouverture(host: _Host, tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    journal.write_text('{"avant": 1}\n', encoding="utf-8")
    host.api.answers["event_files"] = [("runtime", journal)]

    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    try:
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream"
        assert resp.getheader("Cache-Control") == "no-cache"
        assert resp.fp.readline() == b": keepalive\n"
        assert resp.fp.readline() == b"\n"

        with journal.open("a", encoding="utf-8") as f:
            f.write('{"apres": 2}\n\n   \n')

        assert resp.fp.readline() == b"event: runtime\n"
        assert resp.fp.readline() == b'data: {"apres": 2}\n'
        assert resp.fp.readline() == b"\n"
        assert resp.fp.readline() == b": keepalive\n", "ni l'historique ni les lignes vides ne sont émis"
    finally:
        conn.close()


# ── POST : extensions ────────────────────────────────────────────────────────


def test_post_extension_add(host: _Host) -> None:
    host.api.answers["extension_add"] = SimpleNamespace(
        extension_id="ext-a", version="1.2.3", copied=("a.md", "b.md"), skipped=("c.md",)
    )
    code, body = host.json("POST", "/api/extensions/add", {"source": "ext-a"})

    assert (code, body) == (200, {"installed": "ext-a", "version": "1.2.3", "copied": ["a.md", "b.md"], "skipped": ["c.md"]})
    assert host.api.called("extension_add") == [(("ext-a",), {})]
    assert host.mutations() == [{"source": "serve", "action": "extension.add", "id": "ext-a", "version": "1.2.3"}]


def test_post_extension_add_sans_source_passe_une_chaine_vide(host: _Host) -> None:
    host.api.failures["extension_add"] = ExtensionError("extension introuvable")
    assert host.json("POST", "/api/extensions/add", {}) == (422, {"error": "extension introuvable"})
    assert host.api.called("extension_add") == [(("",), {})]
    assert host.mutations() == [], "un refus n'est pas tracé comme une mutation"


def test_post_extension_remove(host: _Host) -> None:
    assert host.json("POST", "/api/extensions/remove", {"id": "ext-a"}) == (200, {"removed": "ext-a"})
    assert host.api.called("extension_remove") == [(("ext-a",), {})]
    assert host.mutations() == [{"source": "serve", "action": "extension.remove", "id": "ext-a"}]

    assert host.json("POST", "/api/extensions/remove", {}) == (200, {"removed": None})
    assert host.api.called("extension_remove")[-1] == (("",), {})


# ── POST : setup, projets, données ───────────────────────────────────────────


@pytest.mark.parametrize("route", ["/api/setup", "/api/setup/plan"])
def test_post_setup_transmet_le_corps(host: _Host, route: str) -> None:
    assert host.json("POST", route, {"archetype": "minimal"}) == (200, {"called": "setup_plan"})
    assert host.api.called("setup_plan") == [(({"archetype": "minimal"},), {})]
    assert host.mutations() == []


def test_post_des_routes_projets(host: _Host) -> None:
    assert host.json("POST", "/api/projects/add", {"path": "/p"}) == (200, {"called": "project_add"})
    assert host.json("POST", "/api/projects/select", {"slug": "s", "path": "/p"}) == (200, {"called": "select_project"})
    assert host.json("POST", "/api/projects/scan", {"root": "/r", "depth": "2"}) == (200, {"called": "project_scan"})
    assert host.json("POST", "/api/projects/scan", {}) == (200, {"called": "project_scan"})
    assert host.json("POST", "/api/data/refresh") == (200, {"called": "data_refresh"})

    assert host.api.called("project_add") == [(("/p",), {})]
    assert host.api.called("select_project") == [((), {"slug": "s", "path": "/p"})]
    assert host.api.called("project_scan") == [(("/r", 2), {}), (("", forge_http.DEFAULT_SCAN_DEPTH), {})]
    assert host.api.called("data_refresh") == [((), {})]
    assert host.mutations() == [], "ouvrir, enrôler ou scanner n'est pas tracé dans le projet servi"


def test_post_project_update_ne_trace_que_l_ecriture_reelle(host: _Host) -> None:
    host.api.answers["project_update"] = {"ok": True, "path": "/cible"}

    assert host.json("POST", "/api/projects/update", {"project": "p", "path": "/x"}) == (200, {"ok": True, "path": "/cible"})
    assert host.json("POST", "/api/projects/update", {"slug": "s", "confirm": "true"})[0] == 200
    assert host.mutations() == [], "seul `confirm: true` (booléen) écrit"

    assert host.json("POST", "/api/projects/update", {"confirm": True})[0] == 200
    assert host.api.called("project_update") == [
        ((), {"dry_run": True, "slug": "p", "path": "/x"}),
        ((), {"dry_run": True, "slug": "s", "path": ""}),
        ((), {"dry_run": False, "slug": "", "path": ""}),
    ]
    assert host.mutations() == [{"source": "serve", "action": "project.update", "path": "/cible", "ok": True}]


# ── POST : fonctionnalités ───────────────────────────────────────────────────


def test_post_feature_toggle(host: _Host) -> None:
    assert host.json("POST", "/api/features/ma-feature", {"enabled": 1}) == (200, {"called": "feature_toggle"})
    assert host.json("POST", "/api/features/ma-feature", {}) == (200, {"called": "feature_toggle"})
    assert host.api.called("feature_toggle") == [(("ma-feature", True), {}), (("ma-feature", False), {})]
    assert host.mutations() == [
        {"source": "serve", "action": "feature.toggle", "id": "ma-feature", "enabled": True},
        {"source": "serve", "action": "feature.toggle", "id": "ma-feature", "enabled": False},
    ]


def test_post_feature_inconnue_est_un_404_sans_trace(host: _Host) -> None:
    host.api.failures["feature_toggle"] = KeyError("fantome")
    assert host.json("POST", "/api/features/fantome", {"enabled": True}) == (404, {"error": "feature inconnue : fantome"})
    assert host.mutations() == []


# ── POST : blueprints ────────────────────────────────────────────────────────


def test_post_blueprint_validate_prend_le_corps_sinon_le_blueprint_enregistre(host: _Host) -> None:
    host.api.answers["blueprint_get"] = {"id": "enregistre"}

    assert host.json("POST", "/api/blueprints/bp/validate", {"id": "soumis"}) == (200, {"called": "blueprint_lint"})
    assert host.api.called("blueprint_get") == [], "un corps fourni dispense de relire le fichier"

    assert host.json("POST", "/api/blueprints/bp/validate") == (200, {"called": "blueprint_lint"})
    assert host.api.called("blueprint_get") == [(("bp",), {})]
    assert host.api.called("blueprint_lint") == [(({"id": "soumis"},), {}), (({"id": "enregistre"},), {})]
    assert host.mutations() == []


def test_post_blueprint_simulate_et_son_injection_de_panne(host: _Host) -> None:
    host.api.answers["blueprint_get"] = {"id": "enregistre"}
    bp = {"id": "soumis"}

    assert host.json("POST", "/api/blueprints/bp/simulate", bp) == (200, {"called": "blueprint_simulate"})
    assert host.json("POST", "/api/blueprints/bp/simulate?injectNode=n1", bp)[0] == 200
    assert host.json("POST", "/api/blueprints/bp/simulate?injectNode=n2&injectClass=timeout")[0] == 200
    assert host.json("POST", "/api/blueprints/bp/simulate?injectClass=timeout", bp)[0] == 200

    assert host.api.called("blueprint_simulate") == [
        ((bp,), {"inject_failure": None}),
        ((bp,), {"inject_failure": {"nodeId": "n1", "class": "unknown"}}),
        (({"id": "enregistre"},), {"inject_failure": {"nodeId": "n2", "class": "timeout"}}),
        ((bp,), {"inject_failure": None}),
    ]
    assert host.mutations() == []


def test_post_blueprint_compile_trace_l_artefact(host: _Host) -> None:
    host.api.answers["blueprint_compile"] = {"artifact": ".github/prompts/bp.prompt.md", "ok": True}

    code, body = host.json("POST", "/api/blueprints/bp/compile", {"id": "soumis"})

    assert (code, body) == (200, {"artifact": ".github/prompts/bp.prompt.md", "ok": True})
    assert host.api.called("blueprint_compile") == [(({"id": "soumis"},), {})]
    assert host.mutations() == [
        {"source": "serve", "action": "blueprint.compile", "id": "bp", "artifact": ".github/prompts/bp.prompt.md"}
    ]


def test_post_blueprint_introuvable(host: _Host) -> None:
    host.api.failures["blueprint_get"] = FileNotFoundError("bp")
    assert host.json("POST", "/api/blueprints/bp/compile") == (404, {"error": "introuvable : bp"})
    assert host.api.called("blueprint_compile") == []
    assert host.mutations() == []


# ── POST : vue de travail, routes inconnues, exceptions ──────────────────────


def test_post_workspace_delegue_et_trace_la_route(host: _Host, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Path, str, dict[str, Any]]] = []

    def _workspace_post(root: Path, path: str, body: dict[str, Any]) -> Any:
        seen.append((root, path, body))
        return {"fait": True}

    monkeypatch.setattr(forge_http, "workspace_post", _workspace_post)

    assert host.json("POST", "/api/workspace/tasks/T-1/claim?x=1", {"actor": "moi"}) == (200, {"fait": True})
    assert seen == [(host.api.project_root, "/api/workspace/tasks/T-1/claim", {"actor": "moi"})]
    assert host.mutations() == [{"source": "serve", "action": "workspace.post", "route": "tasks/T-1/claim"}]


def test_post_workspace_non_reconnue_est_un_404_sans_trace(host: _Host, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forge_http, "workspace_post", lambda *_a: WORKSPACE_UNHANDLED)
    assert host.json("POST", "/api/workspace/inconnue", {}) == (404, {"error": "route inconnue"})
    assert host.mutations() == []


def test_post_et_put_refusent_une_route_inconnue(host: _Host) -> None:
    assert host.json("POST", "/api/nulle-part", {}) == (404, {"error": "route inconnue"})
    assert host.json("POST", "/api/blueprints/bp/autre", {}) == (404, {"error": "route inconnue"})
    assert host.json("PUT", "/api/nulle-part", {}) == (404, {"error": "route inconnue"})
    assert host.api.calls == []


@pytest.mark.parametrize(
    ("failure", "code", "message"),
    [
        (ExtensionError("extension refusée"), 422, "extension refusée"),
        (FileNotFoundError("le-fichier"), 404, "introuvable : le-fichier"),
        (PermissionError("pas ici"), 403, "pas ici"),
        (ValueError("valeur refusée"), 400, "valeur refusée"),
    ],
)
def test_post_traduit_les_exceptions(host: _Host, failure: Exception, code: int, message: str) -> None:
    host.api.failures["project_add"] = failure
    assert host.json("POST", "/api/projects/add", {"path": "/p"}) == (code, {"error": message})


def test_post_refuse_un_corps_qui_n_est_pas_du_json(host: _Host) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    try:
        conn.request("POST", "/api/projects/add", body=b"{pas du json", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()
    assert resp.status == 400
    assert body["error"].startswith("Expecting property name")
    assert host.api.calls == []


# ── PUT ──────────────────────────────────────────────────────────────────────


def test_put_blueprint_enregistre_et_trace(host: _Host) -> None:
    assert host.json("PUT", "/api/blueprints/bp", {"id": "bp"}) == (200, {"called": "blueprint_put"})
    assert host.api.called("blueprint_put") == [(("bp", {"id": "bp"}), {})]
    assert host.mutations() == [{"source": "serve", "action": "blueprint.put", "id": "bp"}]


def test_put_traduit_une_valeur_refusee(host: _Host) -> None:
    host.api.failures["blueprint_put"] = ValueError("identifiant invalide")
    assert host.json("PUT", "/api/blueprints/bp", {"id": "bp"}) == (400, {"error": "identifiant invalide"})
    assert host.mutations() == []


# ── Trace gouvernée ──────────────────────────────────────────────────────────


def test_une_trace_gouvernee_impossible_ne_bloque_pas_la_mutation(host: _Host) -> None:
    """Fail-open : un dossier de trace inscriptible n'est pas une condition de la réponse."""
    blocker = host.api.project_root / "_grimoire-runtime-output" / "hook-runtime"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("un fichier là où la trace attend un dossier", encoding="utf-8")

    assert host.json("PUT", "/api/blueprints/bp", {"id": "bp"}) == (200, {"called": "blueprint_put"})
    assert blocker.is_file()


# ── Service de fichiers ──────────────────────────────────────────────────────


def test_statique_sert_l_accueil_et_l_index_d_un_dossier(site: _Host) -> None:
    code, headers, body = site.request("GET", "/?legacy=1")
    assert (code, headers["content-type"]) == (200, "text/html")
    assert b"accueil" in body
    assert headers["content-length"] == str(len(body))

    code, headers, body = site.request("GET", "/sous")
    assert (code, headers["content-type"]) == (200, "text/html")
    assert b"sous-page" in body


def test_statique_injecte_le_jeton_dans_le_html_seulement(site: _Host) -> None:
    from grimoire.tools.request_guard import inject_session_token

    source = (site.api.ui_dir / "index.html").read_bytes()
    assert site.request("GET", "/index.html")[2] == inject_session_token(source)
    assert inject_session_token(source) != source, "prémisse : le jeton modifie la page"

    code, headers, body = site.request("GET", "/style.css")
    assert (code, headers["content-type"], body) == (200, "text/css", b"body { color: red }")

    code, headers, body = site.request("GET", "/blob.bin")
    assert (code, headers["content-type"], body) == (200, "application/octet-stream", b"\x00\x01\x02")


def test_statique_refuse_de_sortir_du_site(site: _Host) -> None:
    assert site.json("GET", "/../secret.txt") == (403, {"error": "chemin refusé"})
    assert site.json("GET", "/absent.html") == (404, {"error": "introuvable"})


def test_statique_redirige_la_racine_vers_la_coque_sauf_sortie_de_secours(site: _Host) -> None:
    code, headers, body = site.request("GET", "/")
    assert (code, headers["location"], headers["content-length"], body) == (302, "/workspace/index.html#piloter", "0", b"")
    assert "content-type" not in headers

    assert site.request("GET", "/?legacy=1")[0] == 200


def test_donnees_absentes_est_un_404_qui_dit_comment_les_generer(site: _Host) -> None:
    site.api.answers["data_file"] = None
    site.api.answers["data_status"] = {"state": "absent"}

    assert site.json("GET", "/data/memory.json") == (
        404,
        {
            "error": "couche de données absente pour ce projet",
            "hint": "POST /api/data/refresh régénère la couche du projet servi",
            "data": {"state": "absent"},
        },
    )
    assert site.api.called("data_file") == [(("memory.json",), {})]


def test_donnees_generees_sont_servies_sans_cache(site: _Host, tmp_path: Path) -> None:
    generated = tmp_path / "memory.json"
    generated.write_bytes(b'{"store": 7}')
    site.api.answers["data_file"] = generated

    code, headers, body = site.request("GET", "/data/projects/p/memory.json")

    assert (code, body) == (200, b'{"store": 7}')
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert site.api.called("data_file") == [(("projects/p/memory.json",), {})]


# ── La fabrique ──────────────────────────────────────────────────────────────


def test_chaque_appel_rend_une_classe_liee_a_son_api(tmp_path: Path) -> None:
    """Deux ateliers dans le même processus ne se partagent pas leur API."""
    one, two = _Api(tmp_path / "a", None), _Api(tmp_path / "b", None)
    one.project_root.mkdir()
    two.project_root.mkdir()
    first, second = _serve(one), _serve(two)
    try:
        assert first.RequestHandlerClass is not second.RequestHandlerClass
        assert first.RequestHandlerClass.__name__ == "Handler"
        _Host(one, first.server_address[1]).json("GET", "/api/projects")
        _Host(two, second.server_address[1]).json("GET", "/api/data/status")
        assert [name for name, _a, _k in one.calls] == ["projects_view"]
        assert [name for name, _a, _k in two.calls] == ["data_status"]
    finally:
        for httpd in (first, second):
            httpd.shutdown()
            httpd.server_close()
