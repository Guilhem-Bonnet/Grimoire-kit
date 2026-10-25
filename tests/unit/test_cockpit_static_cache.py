"""Les fichiers statiques du cockpit se revalident à chaque chargement.

Sans ``Cache-Control``, un navigateur garde d'anciens modules JS/CSS après une
mise à jour du kit : la page HTML (``no-store``) est neuve, ses scripts ne le
sont pas, et l'interface montre l'ancienne version.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from grimoire.cli import cmd_cockpit


@pytest.fixture
def cockpit_base(tmp_path: Path):
    """Sert le site comme le cockpit : copie de site puis handler statique."""
    serve = tmp_path / "serve"
    cmd_cockpit._sync_site(serve)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(serve)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _head(base: str, rel: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
    req = urllib.request.Request(f"{base}/{rel}", headers=headers or {})  # noqa: S310 - serveur de test local
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - serveur de test local
            resp.read()
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}


@pytest.mark.parametrize("rel", ["workspace/shell.js", "workspace/spaces/executer.js", "workspace/shell.css"])
def test_un_module_du_cockpit_se_revalide_a_chaque_chargement(cockpit_base: str, rel: str) -> None:
    status, headers = _head(cockpit_base, rel)
    assert status == 200
    assert headers.get("cache-control") == "no-cache", headers


def test_les_icones_gardent_une_minute_de_fraicheur(cockpit_base: str) -> None:
    """``no-cache`` sur icons.svg coûtait une requête par rendu d'icône ; une
    minute suffit à reprendre les icônes d'une mise à jour."""
    status, headers = _head(cockpit_base, "workspace/icons.svg")
    assert status == 200
    assert headers.get("cache-control") == "max-age=60", headers


def test_une_revalidation_sans_changement_rend_un_304(cockpit_base: str) -> None:
    status, headers = _head(cockpit_base, "workspace/shell.js")
    assert status == 200
    status, _ = _head(cockpit_base, "workspace/shell.js", {"If-Modified-Since": headers["last-modified"]})
    assert status == 304


def test_la_page_garde_no_store(cockpit_base: str) -> None:
    status, headers = _head(cockpit_base, "workspace/index.html")
    assert status == 200
    assert headers.get("cache-control") == "no-store"
