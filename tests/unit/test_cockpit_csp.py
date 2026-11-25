"""Politique de sécurité des pages du cockpit (#1366) : en-têtes sur les deux serveurs, et page sans rien d'en ligne.

Deux gardes :

* HTTP — de vrais serveurs (``grimoire cockpit serve`` et l'atelier ``grimoire serve``) servent le vrai
  dossier ``web/`` ; toute réponse HTML porte la Content-Security-Policy, ``nosniff`` et ``no-referrer`` ;
* statique — la coque (``web/workspace``) ne contient ni script en ligne, ni ``<style>``, ni ``style=``, ni
  gestionnaire ``on*=``, et son JavaScript ne fabrique ni feuille de style, ni attribut ``style``, ni
  ``eval``. Sans cela, la politique stricte casserait la page sans qu'un test unitaire le voie.
"""

from __future__ import annotations

import http.client
import re
import threading
from functools import partial
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools.forge_http import make_handler
from grimoire.tools.page_security import CSP_STRICT, csp_with_nonce, html_page
from grimoire.tools.request_guard import SESSION_SCRIPT_PATH

WEB = Path(__file__).resolve().parents[2] / "web"
WORKSPACE = WEB / "workspace"

STRICT_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "media-src 'self' blob: data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
)


def _get(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def _directive(policy: str, name: str) -> str:
    for part in policy.split(";"):
        part = part.strip()
        if part == name or part.startswith(name + " "):
            return part
    raise AssertionError(f"directive {name!r} absente de {policy!r}")


def _assert_headers(headers: dict[str, str]) -> str:
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    policy = headers["content-security-policy"]
    assert "unsafe-eval" not in policy
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
    assert "'unsafe-inline'" not in _directive(policy, "style-src")
    return policy


@pytest.fixture(params=["cockpit", "atelier"])
def port(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    if request.param == "cockpit":
        handler: Any = partial(cmd_cockpit._CockpitHandler, directory=str(WEB))
    else:
        api = type("Api", (), {"project_root": tmp_path, "ui_dir": WEB})()
        handler = make_handler(api)  # type: ignore[arg-type]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


# ── HTTP ─────────────────────────────────────────────────────────────────────


def test_la_coque_recoit_la_politique_stricte(port: int) -> None:
    code, headers, body = _get(port, "/workspace/index.html")
    assert code == 200
    policy = _assert_headers(headers)
    assert policy == CSP_STRICT
    for directive in STRICT_DIRECTIVES:
        assert directive in policy
    # Le jeton arrive par un script externe : rien d'en ligne n'est injecté dans la coque.
    assert f'<script src="{SESSION_SCRIPT_PATH}"></script>'.encode() in body
    assert b"nonce" not in body


@pytest.mark.parametrize("page", ["/kanban.html?legacy=1", "/memory.html?legacy=1", "/index.html", "/demo.html"])
def test_les_pages_historiques_et_vitrine_recoivent_un_nonce(port: int, page: str) -> None:
    code, headers, body = _get(port, page)
    assert code == 200
    policy = _assert_headers(headers)
    nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", policy)
    assert nonce, policy
    assert _directive(policy, "style-src") == f"style-src 'self' 'nonce-{nonce.group(1)}'"
    # Seuls les attributs style= restent permis ; jamais de style en ligne en balise sans nonce.
    assert _directive(policy, "style-src-attr") == "style-src-attr 'unsafe-inline'"
    for tag, attrs in _tags(body.decode("utf-8")):
        if tag in {"script", "style"}:
            assert attrs.get("nonce") == nonce.group(1), (tag, attrs)
    for directive in (*STRICT_DIRECTIVES[:1], *STRICT_DIRECTIVES[3:]):
        assert directive in policy


def test_le_nonce_change_a_chaque_reponse(port: int) -> None:
    first = _get(port, "/kanban.html?legacy=1")[1]["content-security-policy"]
    second = _get(port, "/kanban.html?legacy=1")[1]["content-security-policy"]
    assert first != second


def test_le_relais_fetch_est_un_fichier_sans_jeton(port: int) -> None:
    from grimoire.tools.request_guard import session_token

    code, headers, body = _get(port, SESSION_SCRIPT_PATH)
    assert code == 200
    assert headers["content-type"].startswith("text/javascript")
    assert headers["x-content-type-options"] == "nosniff"
    assert session_token().encode() not in body, "le jeton ne se lit que dans la page, jamais dans un script"
    assert b"X-Grimoire-Session" in body


def test_un_dossier_sans_index_n_est_pas_liste(port: int) -> None:
    code, _headers, body = _get(port, "/workspace/spaces/")
    assert code == 404
    assert b"Directory listing" not in body


def test_html_page_choisit_la_politique_d_apres_le_contenu() -> None:
    _, strict = html_page(b"<html><head></head><body><p>rien</p><script src='/a.js'></script></body></html>")
    assert dict(strict)["Content-Security-Policy"] == CSP_STRICT
    for inline in (b"<script>1</script>", b"<style>p{}</style>", b'<p style="x:y">', b'<a onclick="f()">'):
        _, headers = html_page(b"<html><head></head><body>" + inline + b"</body></html>")
        policy = dict(headers)["Content-Security-Policy"]
        nonce = re.search(r"'nonce-([^']+)'", policy)
        assert nonce
        assert policy == csp_with_nonce(nonce.group(1))
    # Un nonce déjà présent n'est pas doublé.
    marked, _ = html_page(b'<html><head></head><script nonce="x">1</script></html>')
    assert marked.count(b'nonce="x"') == 1 and marked.count(b"nonce=") == 2  # + le script du jeton


# ── Statique : la coque ne contient rien d'en ligne ──────────────────────────


def _workspace_files(*suffixes: str) -> list[Path]:
    return sorted(p for p in WORKSPACE.rglob("*") if p.suffix in suffixes and "fonts" not in p.parts)


class _Tags(HTMLParser):
    """Les balises d'un document (commentaires ignorés) avec leurs attributs."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.append((tag, dict(attrs)))


def _tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _Tags()
    parser.feed(html)
    return parser.found


def test_la_page_de_la_coque_n_a_rien_d_en_ligne() -> None:
    tags = _tags((WORKSPACE / "index.html").read_text(encoding="utf-8"))
    scripts = [attrs for tag, attrs in tags if tag == "script"]
    assert all(attrs.get("src") for attrs in scripts), "script en ligne"
    assert not [t for t, _ in tags if t == "style"], "<style> en ligne"
    for tag, attrs in tags:
        assert "style" not in attrs, f"attribut style= sur <{tag}>"
        assert not [name for name in attrs if name.startswith("on")], f"gestionnaire on*= sur <{tag}>"
        for name in ("href", "src", "action"):
            value = (attrs.get(name) or "").lower()
            assert not value.startswith(("javascript:", "http:", "https:")), f"{name}={value} sur <{tag}>"
    first = scripts[0]
    assert first.get("src") == "./theme-boot.js"
    assert "defer" not in first and "async" not in first and first.get("type") != "module", (
        "bloquant, avant le premier paint"
    )


def test_chaque_feuille_de_la_coque_est_liee_par_la_page() -> None:
    tags = _tags((WORKSPACE / "index.html").read_text(encoding="utf-8"))
    linked = {
        Path(attrs["href"] or "").as_posix().removeprefix("./")
        for tag, attrs in tags
        if tag == "link" and attrs.get("rel") == "stylesheet"
    }
    on_disk = {p.relative_to(WORKSPACE).as_posix() for p in _workspace_files(".css")}
    # source.css est chargée à la demande par l'espace Source (`<link>` posé par JS : permis par `style-src 'self'`).
    assert on_disk - {"spaces/source.css"} == linked


_FORBIDDEN_JS = {
    "feuille <style> fabriquée": r"createElement\(\s*['\"](?:style|script)['\"]",
    "attribut style posé en texte": r"setAttribute\(\s*['\"]style['\"]",
    "cssText": r"\bcssText\b",
    "style= dans un gabarit HTML": r"\sstyle=[\"'\\]",
    "eval": r"(?<![\w.])eval\s*\(",
    "new Function": r"new\s+Function\s*\(",
    "minuterie à chaîne": r"set(?:Timeout|Interval)\(\s*['\"`]",
    "document.write": r"document\.write",
    "gestionnaire on*= dans un gabarit": r"\son[a-z]+=[\"']",
    "adoptedStyleSheets": r"adoptedStyleSheets|new\s+CSSStyleSheet",
    "URL javascript:": r"javascript:",
}


@pytest.mark.parametrize("label", sorted(_FORBIDDEN_JS))
def test_le_javascript_de_la_coque_ne_contourne_pas_la_politique(label: str) -> None:
    pattern = re.compile(_FORBIDDEN_JS[label])
    offenders = []
    for path in _workspace_files(".js"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(WEB)}:{number}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders)
