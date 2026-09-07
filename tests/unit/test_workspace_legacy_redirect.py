"""Basculement, pas 2 (ADR-006) — la vue de travail devient la page par défaut.

Les cinq lots de la vue de travail sont mergés : ``grimoire serve`` et
``grimoire cockpit serve`` ouvrent maintenant ``workspace/index.html``. Les 14
pages historiques restent servies (l'ADR ne supprime rien avant le pas 3), mais
les dix pages *outil* redirigent vers l'espace qui les remplace — sauf
``?legacy=1``, la sortie de secours explicite. Les quatre pages vitrine, hors
périmètre, ne redirigent jamais.

Deux hôtes, une table (``grimoire.tools.workspace_legacy``) : ce module la
prouve sur les deux serveurs, comme ``test_workspace_routes.py`` le fait pour
les lectures ``/api/workspace/``.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.workspace_legacy import LEGACY_PAGES

ROOT = Path(__file__).resolve().parents[2]


def _get(port: int, path: str) -> tuple[int, str, str | None]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — loopback de test
            return resp.status, resp.read().decode("utf-8", errors="replace"), resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers.get("Location")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Pour observer le 302 lui-même plutôt que la page qu'il pointe."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get_no_follow(port: int, path: str) -> tuple[int, str | None]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with _OPENER.open(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location")


# ── Atelier (`grimoire serve`) ───────────────────────────────────────────────


@pytest.fixture
def atelier_server(tmp_path: Path) -> Any:
    project = tmp_path / "servi"
    (project / "_grimoire").mkdir(parents=True)
    api = ForgeAPI(project, ROOT, ROOT / "web")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_une_page_outil_redirige_vers_son_espace(atelier_server: int) -> None:
    code, location = _get_no_follow(atelier_server, "/atelier.html")
    assert code == 302
    assert location == "/workspace/index.html#piloter"


def test_legacy_1_sert_encore_l_ancienne_page(atelier_server: int) -> None:
    code, location = _get_no_follow(atelier_server, "/atelier.html?legacy=1")
    assert code == 200
    assert location is None
    code, body, _ = _get(atelier_server, "/atelier.html?legacy=1")
    assert code == 200
    assert "<html" in body.lower()


def test_une_page_vitrine_ne_redirige_jamais(atelier_server: int) -> None:
    for page in ("index.html", "demo.html", "anatomy.html", "game-ui.html"):
        code, location = _get_no_follow(atelier_server, f"/{page}")
        assert code == 200, f"{page} : {code}"
        assert location is None, f"{page} a redirigé vers {location}"


def test_toutes_les_pages_outil_de_la_table_redirigent(atelier_server: int) -> None:
    """Balaie `LEGACY_PAGES` entière — pas seulement `atelier.html` — pour
    qu'un espace mal orthographié dans la table se voie ici."""
    for page, space in LEGACY_PAGES.items():
        code, location = _get_no_follow(atelier_server, f"/{page}")
        assert (code, location) == (302, f"/workspace/index.html#{space}"), page


# ── Cockpit (`grimoire cockpit serve`) ───────────────────────────────────────


@pytest.fixture
def cockpit_server() -> Any:
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(ROOT / "web"))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_le_cockpit_redirige_portfolio_vers_piloter(cockpit_server: int) -> None:
    code, location = _get_no_follow(cockpit_server, "/portfolio.html")
    assert code == 302
    assert location == "/workspace/index.html#piloter"


def test_le_cockpit_honore_legacy_1(cockpit_server: int) -> None:
    code, location = _get_no_follow(cockpit_server, "/portfolio.html?legacy=1")
    assert code == 200
    assert location is None


def test_le_cockpit_ne_redirige_pas_la_vitrine(cockpit_server: int) -> None:
    code, location = _get_no_follow(cockpit_server, "/index.html")
    assert code == 200
    assert location is None


def test_la_racine_ouvre_la_coque_et_index_reste_vitrine() -> None:
    """`/` ouvre l'espace Piloter ; `index.html` demandé explicitement reste la
    page vitrine, hors périmètre du basculement (spec §7)."""
    from grimoire.tools.workspace_legacy import legacy_redirect_target

    assert legacy_redirect_target("") == "piloter"
    assert legacy_redirect_target("index.html") is None
