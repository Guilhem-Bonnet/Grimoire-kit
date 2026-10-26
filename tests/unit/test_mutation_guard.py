"""Garde d'origine et de type de contenu des écritures — atelier et cockpit.

Le contrôle d'hôte ferme le rebinding DNS ; il ne dit rien d'une page tierce
ouverte dans le navigateur de l'utilisateur, qui peut émettre un ``POST``
« simple » (``text/plain``, sans préflight) vers la loopback. Ces tests jouent
cette requête contre un vrai serveur HTTP, pour CHAQUE route d'écriture, sur les
deux hôtes : elle doit être refusée avant toute lecture du corps, et rien ne doit
être écrit sur le disque.

Un test d'énumération échoue si une route d'écriture est ajoutée à l'un des
deux hôtes sans passer par la garde commune.
"""

from __future__ import annotations

import http.client
import inspect
import json
import re
import threading
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import forge_http, project_registry
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX, TASK_ACTIONS

ROOT = Path(__file__).resolve().parents[2]

# ── Routes d'écriture, une requête concrète par route ──────────────────────

#: Littéraux de ``do_POST`` (cockpit) → chemins concrets joués.
COCKPIT_LITERALS: dict[str, list[str]] = {
    "/api/projects/select": ["/api/projects/select"],
    "/api/projects/update": ["/api/projects/update"],
    "/api/projects/add": ["/api/projects/add"],
    "/api/projects/scan": ["/api/projects/scan"],
    "/api/projects/remove": ["/api/projects/remove"],
    "/api/projects/prune": ["/api/projects/prune"],
    "/api/projects/create": ["/api/projects/create"],
    "/api/setup": ["/api/setup"],
    "/api/setup/plan": ["/api/setup/plan"],
    "/api/blueprints/": [
        "/api/blueprints/x/compile",
        "/api/blueprints/x/validate",
        "/api/blueprints/x/simulate",
    ],
    "/api/memory": ["/api/memory"],
}

#: Littéraux de ``do_POST`` (atelier) → chemins concrets joués.
ATELIER_LITERALS: dict[str, list[str]] = {
    "/api/extensions/add": ["/api/extensions/add"],
    "/api/extensions/remove": ["/api/extensions/remove"],
    "/api/setup": ["/api/setup"],
    "/api/setup/plan": ["/api/setup/plan"],
    "/api/projects/select": ["/api/projects/select"],
    "/api/projects/add": ["/api/projects/add"],
    "/api/projects/scan": ["/api/projects/scan"],
    "/api/data/refresh": ["/api/data/refresh"],
    "/api/projects/update": ["/api/projects/update"],
    "/api/features/": ["/api/features/stigmergy-hooks"],
    "/api/blueprints/": [
        "/api/blueprints/x/compile",
        "/api/blueprints/x/validate",
        "/api/blueprints/x/simulate",
    ],
}


def _workspace_paths() -> list[str]:
    """Toute la table de la vue de travail, lue depuis le code et non recopiée."""
    paths = list(POST_ROUTES)
    paths += [f"{PREFIX}tasks/T-1/{a}" for a in TASK_ACTIONS]
    paths += [f"{PREFIX}portfolio/tasks/T-1/{a}" for a in TASK_ACTIONS]
    paths += [
        f"{PREFIX}agents/amelia/fields",
        f"{PREFIX}agents/amelia/skill",
        f"{PREFIX}proposals/p/accept",
        f"{PREFIX}proposals/p/reject",
    ]
    return paths


def _flat(table: dict[str, list[str]]) -> list[str]:
    return [p for paths in table.values() for p in paths] + _workspace_paths()


COCKPIT_POSTS = _flat(COCKPIT_LITERALS)
ATELIER_POSTS = _flat(ATELIER_LITERALS)

# ── Serveurs ───────────────────────────────────────────────────────────────


class Host:
    def __init__(self, httpd: ThreadingHTTPServer, root: Path, slug: str, watched: Path) -> None:
        self.httpd = httpd
        self.port: int = httpd.server_address[1]
        self.root = root
        self.slug = slug
        self.watched = watched

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

    def snapshot(self) -> dict[str, tuple[int, int]]:
        return {
            str(p): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(self.watched.rglob("*"))
            if p.is_file()
        }

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "home"
    (project / ".git").mkdir(parents=True)
    (project / "_grimoire" / "blueprints").mkdir(parents=True)
    (project / "_grimoire" / "overrides").mkdir(parents=True)
    return project


@pytest.fixture
def cockpit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Host]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    project = _project(tmp_path)
    slug = project_registry.register_project(project)
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", slug)
    project_registry.set_selected_slug(slug)
    serve_dir = tmp_path / "serve"
    (serve_dir / "data").mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Host(httpd, project, slug, tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Host]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    project = _project(tmp_path)
    slug = project_registry.register_project(project)
    api = ForgeAPI(project, ROOT, ROOT / "web")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Host(httpd, project, slug, tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()


BODY = json.dumps({"slug": "x", "path": "/tmp/x", "text": "t", "confirm": True}).encode()
JSON = {"Content-Type": "application/json"}
EVIL = "https://evil.example"

#: Requêtes qu'une page tierce sait émettre, avec ce qui les trahit.
HOSTILE: dict[str, dict[str, str]] = {
    "simple-text-plain": {"Content-Type": "text/plain", "Origin": EVIL},
    "simple-form-urlencoded": {"Content-Type": "application/x-www-form-urlencoded", "Origin": EVIL},
    "json-foreign-origin": {**JSON, "Origin": EVIL},
    "json-null-origin": {**JSON, "Origin": "null"},
    "json-cross-site-fetch-metadata": {**JSON, "Sec-Fetch-Site": "cross-site"},
    "json-same-site-fetch-metadata": {**JSON, "Sec-Fetch-Site": "same-site"},
    "text-plain-null-origin": {"Content-Type": "text/plain", "Origin": "null"},
    "no-content-type": {"Origin": EVIL},
}


def _assert_refused(host: Host, method: str, path: str, headers: dict[str, str], label: str) -> None:
    before = host.snapshot()
    code = host.request(method, f"{path}?project={host.slug}", BODY, headers)
    assert code in (403, 415), f"{method} {path} [{label}] a répondu {code}"
    assert host.snapshot() == before, f"{method} {path} [{label}] a écrit sur le disque"



# ── 1. Toutes les routes d'écriture refusent la requête tierce ─────────────


@pytest.mark.parametrize("path", COCKPIT_POSTS)
def test_every_cockpit_post_refuses_a_foreign_page(cockpit: Host, path: str) -> None:
    for label, headers in HOSTILE.items():
        _assert_refused(cockpit, "POST", path, headers, label)


def test_the_cockpit_put_refuses_a_foreign_page(cockpit: Host) -> None:
    for label, headers in HOSTILE.items():
        _assert_refused(cockpit, "PUT", "/api/blueprints/x", headers, label)


@pytest.mark.parametrize("path", ATELIER_POSTS)
def test_every_atelier_post_refuses_a_foreign_page(atelier: Host, path: str) -> None:
    for label, headers in HOSTILE.items():
        _assert_refused(atelier, "POST", path, headers, label)


def test_the_atelier_put_refuses_a_foreign_page(atelier: Host) -> None:
    for label, headers in HOSTILE.items():
        _assert_refused(atelier, "PUT", "/api/blueprints/x", headers, label)


@pytest.mark.parametrize("fixture_name", ["cockpit", "atelier"])
def test_the_origin_must_match_the_port_not_only_the_hostname(
    fixture_name: str, request: pytest.FixtureRequest
) -> None:
    """Un autre service local (autre port) n'est pas l'interface de ce serveur."""
    host: Host = request.getfixturevalue(fixture_name)
    other = host.port + 1 if host.port < 65535 else host.port - 1
    for origin in (
        f"http://127.0.0.1:{other}",
        f"http://localhost:{other}",
        f"https://127.0.0.1:{host.port}",
        "http://127.0.0.1",
        f"http://evil.example:{host.port}",
    ):
        _assert_refused(host, "POST", "/api/projects/select", {**JSON, "Origin": origin}, origin)


@pytest.mark.parametrize("fixture_name", ["cockpit", "atelier"])
def test_a_json_body_is_required_even_from_the_same_origin(
    fixture_name: str, request: pytest.FixtureRequest
) -> None:
    host: Host = request.getfixturevalue(fixture_name)
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        code = host.request(
            "POST", "/api/projects/select", BODY, {"Content-Type": ctype, "Origin": host.origin}
        )
        assert code == 415, f"{ctype} accepté"
    assert host.request("POST", "/api/projects/select", BODY, {"Origin": host.origin}) == 415


# ── 2. L'interface légitime continue de passer ─────────────────────────────


@pytest.mark.parametrize("fixture_name", ["cockpit", "atelier"])
def test_the_product_pages_still_pass(fixture_name: str, request: pytest.FixtureRequest) -> None:
    host: Host = request.getfixturevalue(fixture_name)
    ok_body = json.dumps({"slug": host.slug}).encode()
    variants = [
        {**JSON},  # outil local, curl, tests
        {**JSON, "Origin": host.origin},
        {**JSON, "Origin": f"http://localhost:{host.port}"},
        {**JSON, "Origin": host.origin, "Sec-Fetch-Site": "same-origin"},
        {"Content-Type": "application/json; charset=utf-8", "Origin": host.origin},
        {**JSON, "Sec-Fetch-Site": "none"},
    ]
    for headers in variants:
        code = host.request("POST", "/api/projects/select", ok_body, headers)
        assert code == 200, f"{headers} refusé ({code})"


@pytest.mark.parametrize("fixture_name", ["cockpit", "atelier"])
def test_a_bodyless_post_from_the_same_origin_passes_the_guard(
    fixture_name: str, request: pytest.FixtureRequest
) -> None:
    """Le type de contenu ne se réclame que d'un corps : ``refresh`` part sans."""
    host: Host = request.getfixturevalue(fixture_name)
    code = host.request("POST", "/api/projects/scan", None, {"Origin": host.origin})
    assert code not in (403, 415)


# ── 3. Aucune route d'écriture sans garde ──────────────────────────────────


def _literals(func: Any) -> set[str]:
    return set(re.findall(r'"(/api/[^"]*)"', inspect.getsource(func)))


def test_every_cockpit_post_literal_is_covered_by_the_matrix() -> None:
    """Une route ajoutée à ``do_POST`` sans ligne dans ``COCKPIT_LITERALS`` échoue ici."""
    from grimoire.tools.cockpit_registry_routes import REGISTRY_ROUTES

    seen = (_literals(cmd_cockpit._CockpitHandler.do_POST) | set(REGISTRY_ROUTES)) - {"/api/"}
    assert seen <= set(COCKPIT_LITERALS), f"route non couverte : {sorted(seen - set(COCKPIT_LITERALS))}"


def test_every_atelier_post_literal_is_covered_by_the_matrix() -> None:
    handler = make_handler(ForgeAPI.__new__(ForgeAPI))
    seen = _literals(handler.do_POST) - {"/api/"}
    assert seen <= set(ATELIER_LITERALS), f"route non couverte : {sorted(seen - set(ATELIER_LITERALS))}"


def test_no_unguarded_write_method_exists() -> None:
    """Toute méthode d'écriture HTTP ajoutée à un hôte doit être listée ici,
    donc testée par la matrice ci-dessus, et appeler la garde commune."""
    allowed = {"do_GET", "do_HEAD", "do_POST", "do_PUT"}
    atelier = make_handler(ForgeAPI.__new__(ForgeAPI))
    for cls in (cmd_cockpit._CockpitHandler, atelier):
        methods = {n for n in dir(cls) if n.startswith("do_")}
        assert methods <= allowed, f"{cls.__name__} : méthode non couverte {sorted(methods - allowed)}"
        for name in ("do_POST", "do_PUT"):
            source = inspect.getsource(getattr(cls, name))
            assert "_guard_mutation()" in source, f"{cls.__name__}.{name} n'appelle pas la garde"


def test_both_hosts_share_one_guard() -> None:
    """Deux copies de la règle divergeraient : c'est ce qui a laissé le cockpit ouvert."""
    from grimoire.tools import request_guard

    assert hasattr(request_guard, "check_mutation")
    for module in (forge_http, cmd_cockpit):
        assert "request_guard" in inspect.getsource(module), f"{module.__name__} ne l'utilise pas"
