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
        api = type("Api", (), {"project_root": tmp_path, "ui_dir": WEB, "status": lambda self: {"ok": True}})()
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


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/shell.js",
        "/workspace/shell.css",
        "/workspace/icons.svg",
        "/__grimoire/session.js",
        "/kanban.html",  # redirection vers la coque
        "/n-existe-pas.html",  # page d'erreur 404
        "/api/status",
    ],
)
def test_toute_reponse_porte_nosniff_et_une_page_html_la_politique(port: int, path: str) -> None:
    _code, headers, _body = _get(port, path)
    assert headers["x-content-type-options"] == "nosniff", (path, headers)
    if headers.get("content-type", "").startswith("text/html"):
        assert headers["content-security-policy"] == CSP_STRICT, (path, headers)


def test_une_page_d_erreur_html_du_cockpit_porte_la_politique_stricte() -> None:
    """`http.server` fabrique ses pages 404/501 : sans en-têtes de la bibliothèque, elles n'en auraient aucun."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(WEB)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("PATCH", "/workspace/index.html")  # 501 : méthode non gérée
        resp = conn.getresponse()
        error_body = resp.read()
        conn.close()
        assert resp.status == 501
        assert b"<style" not in error_body, "la page d'erreur de la bibliothèque standard porte un <style> en 3.14"
        assert resp.getheader("Content-Type", "").startswith("text/html")
        assert resp.getheader("Content-Security-Policy") == CSP_STRICT
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("page", ["/workspace/index.html", "/kanban.html?legacy=1", "/index.html"])
def test_une_page_html_n_est_pas_mise_en_cache(port: int, page: str) -> None:
    assert _get(port, page)[1]["cache-control"] == "no-store"


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
    # Une balise citée dans un commentaire HTML ou une chaîne JavaScript n'est pas une balise : rien à marquer.
    quoted, _ = html_page(
        b"<html><head></head><body><!-- <script>x</script> --><script src='/a.js'></script>"
        b"<script type='text/plain'>var s = '<style>';</script></body></html>"
    )
    assert (
        quoted.count(b"nonce=") == 3
    )  # le script du jeton, `/a.js` et le `<script>` de données ; ni `<style>` ni commentaire
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


_REGEX_AFTER_WORD = {"return", "typeof", "case", "do", "else", "in", "of", "void", "delete", "throw", "new"}


def tokenize(js: str) -> tuple[str, str]:
    """`(code, littéraux)` : le JavaScript sans ses commentaires, et ce même texte où tout ce qui n'est pas dans une
    chaîne ou un gabarit est blanchi (lignes gardées).

    Un suivi d'état, pas un test sur le début de ligne : une ligne de code qui commence par `*` n'est pas un
    commentaire. Un littéral d'expression régulière (`/'/`, `/["`]/g`) est reconnu comme tel : son guillemet n'ouvre
    pas de chaîne et ne décale pas le suivi du reste du fichier.
    """
    code: list[str] = []
    lits: list[str] = []
    i, n = 0, len(js)
    quote = ""  # ' " ` quand on est dans un littéral

    def emit(text: str, literal: bool) -> None:
        code.append(text)
        lits.append(text if literal else "".join("\n" if ch == "\n" else " " for ch in text))

    def regex_allowed() -> bool:
        tail = "".join(code).rstrip()
        if not tail:
            return True
        if tail[-1] in "(,=:[!&|?{};+-*%<>~^":
            return True
        word = tail.split()[-1] if tail[-1].isalpha() else ""
        return word.split("(")[-1].split(";")[-1] in _REGEX_AFTER_WORD

    while i < n:
        c = js[i]
        two = js[i : i + 2]
        if quote:
            emit(c, True)
            if c == "\\" and i + 1 < n:
                emit(js[i + 1], True)
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
        elif two == "//":
            while i < n and js[i] != "\n":
                i += 1
        elif two == "/*":
            end = js.find("*/", i + 2)
            end = n if end < 0 else end + 2
            emit("\n" * js.count("\n", i, end), False)
            i = end
        elif c == "/" and regex_allowed():
            j, in_class = i + 1, False
            while j < n and js[j] != "\n":
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "[":
                    in_class = True
                elif js[j] == "]":
                    in_class = False
                elif js[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and js[j].isalpha():
                j += 1
            emit(js[i:j], False)
            i = j
        else:
            if c in "'\"`":
                quote = c
                emit(c, True)
            else:
                emit(c, False)
            i += 1
    return "".join(code), "".join(lits)


def strip_comments(js: str) -> str:
    return tokenize(js)[0]


# Les formes qui contournent `script-src`/`style-src` : feuille ou balise fabriquée, attribut style ou gestionnaire
# posé par texte (gabarit, setAttribute, clé d'un helper d'éléments), évaluation de chaîne. Aucune ne réclame de
# guillemet : `style=${css}`, `style = "…"` et `onerror=${f}` sont vus aussi.
_FORBIDDEN_JS = {
    "balise <style> ou <script> dans un littéral": r"<\s*(?:style|script)\b",
    "feuille ou script fabriqué": r"createElement\(\s*['\"`](?:style|script)['\"`]",
    "setAttribute style ou on*": r"setAttribute\(\s*['\"`](?:style|on\w+)['\"`]|setAttributeNS\([^,]*,\s*['\"`](?:style|on\w+)['\"`]",
    "createAttribute style ou on*": r"createAttribute\(\s*['\"`](?:style|on\w+)['\"`]",
    "cssText": r"\bcssText\b",
    "attribut style= (gabarit)": r"(?<!\bconst )(?<!\blet )(?<!\bvar )(?<![\w.$-])style\s*=(?!=)",
    "clé style d'un helper d'éléments": r"(?<![\w.$-])['\"`]?style['\"`]?\s*:",
    "gestionnaire on*= (gabarit)": r"(?<![\w.$-])on[a-z]+\s*=(?![=>])",
    "eval": r"(?<![\w.])eval\s*\(",
    "new Function": r"new\s+Function\s*\(",
    "minuterie à chaîne": r"set(?:Timeout|Interval)\(\s*['\"`]",
    "document.write": r"document\.write",
    "adoptedStyleSheets": r"adoptedStyleSheets|new\s+CSSStyleSheet",
    "URL javascript:": r"javascript:",
}
_FORBIDDEN_RE = {label: re.compile(pattern, re.IGNORECASE) for label, pattern in _FORBIDDEN_JS.items()}


#: Ces motifs n'ont de sens que dans le texte d'un littéral (un gabarit HTML) : `onOpen = null` est du code.
_IN_LITERAL = {
    "balise <style> ou <script> dans un littéral",
    "attribut style= (gabarit)",
    "gestionnaire on*= (gabarit)",
    "URL javascript:",
}


def violations(js: str, labels: tuple[str, ...] | None = None) -> list[tuple[int, str]]:
    """`(ligne, motif)` de chaque contournement trouvé dans `js`, commentaires exclus."""
    code, literals = tokenize(js)
    texts = {False: code, True: literals}
    found = []
    for label, regex in _FORBIDDEN_RE.items():
        if labels is not None and label not in labels:
            continue
        text = texts[label in _IN_LITERAL]
        for match in regex.finditer(text):
            found.append((text.count("\n", 0, match.start()) + 1, label))
    return sorted(found)


@pytest.mark.parametrize("label", sorted(_FORBIDDEN_JS))
def test_le_javascript_de_la_coque_ne_contourne_pas_la_politique(label: str) -> None:
    offenders = []
    for path in _workspace_files(".js"):
        text = path.read_text(encoding="utf-8")
        lines = strip_comments(text).splitlines()
        for number, _seen in violations(text, (label,)):
            offenders.append(f"{path.relative_to(WEB)}:{number}: {lines[number - 1].strip()[:100]}")
    assert not offenders, "\n".join(offenders)


# Les pages historiques et vitrine tolèrent des `style=` (leur politique a `style-src-attr`) mais aucune balise
# <style>/<script> fabriquée sans nonce, aucun gestionnaire en attribut, aucune évaluation de chaîne.
_LEGACY_LABELS = (
    "feuille ou script fabriqué",
    "setAttribute style ou on*",
    "createAttribute style ou on*",
    "eval",
    "new Function",
    "minuterie à chaîne",
    "document.write",
    "URL javascript:",
)


def test_le_javascript_des_pages_historiques_ne_fabrique_pas_de_style_ni_de_gestionnaire() -> None:
    offenders = []
    handler = re.compile(r"\son[a-z]+\s*=\s*[\"'$]", re.IGNORECASE)  # `onclick="…"`, `onerror=${f}` dans un gabarit
    for path in sorted(WEB.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        lines = strip_comments(text).splitlines()
        offenders += [
            f"{path.name}: gestionnaire en attribut: {m.group(0)}" for m in handler.finditer("\n".join(lines))
        ]
        for number, label in violations(text, _LEGACY_LABELS):
            if label == "feuille ou script fabriqué" and "'script'" in lines[number - 1] and "src" in text:
                continue  # un <script src> même origine (project-picker.js) : permis par `script-src 'self'`
            offenders.append(f"{path.name}:{number}: {label}: {lines[number - 1].strip()[:90]}")
    assert not offenders, "\n".join(offenders)


# Le test du test : chaque forme qui échappait à l'ancien garde doit être vue ; les écritures permises, jamais.
_DETECTED = [
    "el.innerHTML = '<style>.a{}</style>';",
    "el.insertAdjacentHTML('beforeend', `<STYLE>.a{}</STYLE>`);",
    "el.innerHTML = '<script>f()</script>';",
    'el.setAttribute("STYLE", "color:red");',
    "el.setAttribute('onclick', 'f()');",
    "el.setAttributeNS(null, 'style', 'color:red');",
    "el.setAttributeNS('http://www.w3.org/1999/xhtml', `ONCLICK`, 'f()');",
    "const a = document.createAttribute('style');",
    'document.createAttribute("onerror");',
    "const re = /'/; el.setAttribute('style', 'a:b');",  # le `'` de la regex ne décale pas le suivi
    'const re = /["`]/g; const h = `<style>x</style>`;',
    "el.setAttribute(`ONERROR`, 'f()');",
    "const h = `<p style=${css}>x</p>`;",
    'const h = `<p style = "color:red">x</p>`;',
    'const h = `<div>\nstyle="color:red">\n</div>`;',
    "const h = `<img src=x onerror=${f}>`;",
    'const h = `<img src=x onload = "f()">`;',
    "const total = base\n  * `<style>`;",
    "document.head.append(document.createElement('style'));",
    "x.style.cssText = 'a:b';",
    "h(a, { style: 'margin:0' });",
    "eval('1')",
    "new Function('return 1')",
    "setTimeout('f()', 1)",
]
_PERMITTED = [
    "el.style.left = '3px';",
    "const style = getComputedStyle(root);",
    "audio.onended = resolve;",
    "const q = s.replace(/'/g, ''); el.style.top = '0';",  # un littéral regex avec apostrophe
    "const ok = a / b; const t = 'x' / 2; el.style.left = '0';",
    "el.setAttribute('data-style', '1'); el.setAttributeNS(null, 'class', 'a');",
    '// <style> style="x" onclick=f() eval(x)',
    "/* <script> setAttribute('style', x) */ const a = 1;",
    '/**\n * style="x"\n */',
    "const url = 'https://example.org/a//b'; el.style.top = '0';",
    "el.classList.add('a'); el.addEventListener('click', f);",
    "const ok = a === b && c => d;",
]


@pytest.mark.parametrize("snippet", _DETECTED)
def test_le_garde_statique_voit_chaque_forme_de_contournement(snippet: str) -> None:
    assert violations(snippet), snippet


@pytest.mark.parametrize("snippet", _PERMITTED)
def test_le_garde_statique_laisse_passer_les_ecritures_permises(snippet: str) -> None:
    assert violations(snippet) == [], snippet


def test_html_page_ne_recoit_que_des_fichiers_du_kit() -> None:
    """Invariant de `html_page` : seul le contenu d'un fichier statique lui est passé (voir sa docstring)."""
    root = Path(__file__).resolve().parents[2] / "src" / "grimoire"
    callers = {}
    for path in root.rglob("*.py"):
        if path.name == "page_security.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "html_page(" in line and not line.lstrip().startswith(("#", "from ", "import ")):
                callers[f"{path.relative_to(root).as_posix()}:{number}"] = line.strip()
    assert sorted(path.split(":")[0] for path in callers) == ["cli/cmd_cockpit.py", "tools/forge_http.py"], callers
    allowed = ("= html_page(target.read_bytes())", "= html_page(body)")
    assert all(any(form in line for form in allowed) for line in callers.values()), callers
    # `body` n'est, dans forge_http, que `target.read_bytes()` : un fichier résolu sous `ui_dir`.
    forge = (root / "tools" / "forge_http.py").read_text(encoding="utf-8")
    assert "body = target.read_bytes()" in forge


# `_InlineScan.handle_starttag` est un rappel de `HTMLParser` : aucun code du dépôt ne l'appelle par son nom. Ces tests
# l'exercent par ce nom (doctrine #275 : un accesseur public a un appelant ou un test) et couvrent ses cas.
def test_inline_scan_marque_une_balise_en_majuscules_et_ignore_celle_qui_a_un_nonce() -> None:
    from grimoire.tools.page_security import _InlineScan

    scan = _InlineScan()
    scan.feed("<HTML><BODY>")
    scan.handle_starttag("script", [("src", "/a.js")])
    scan.handle_starttag("script", [("nonce", "deja")])
    scan.handle_starttag("style", [])
    assert [tag for _line, _col, tag in scan.tags] == ["script", "style"], "le script qui a un nonce n'est pas remarqué"
    assert scan.inline is True


def test_inline_scan_ne_voit_pas_une_balise_dans_un_commentaire_et_voit_un_script_en_ligne() -> None:
    from grimoire.tools.page_security import _InlineScan

    scan = _InlineScan()
    scan.feed("<!-- <script>x</script> --><p>ok</p><script src='/a.js'></script>")
    assert [tag for _line, _col, tag in scan.tags] == ["script"]  # celui du fichier, pas celui du commentaire
    assert scan.inline is False  # `src` : rien d'en ligne
    scan.handle_starttag("script", [])  # un script sans `src`, ou un attribut on*/style, est du contenu en ligne
    assert scan.inline is True
    other = _InlineScan()
    other.handle_starttag("p", [("onclick", "f()")])
    assert other.inline is True
    third = _InlineScan()
    third.handle_starttag("p", [("style", "color:red")])
    assert third.inline is True
