"""W1-10 — le jeton de session est exigé sur toute écriture, cockpit et atelier."""

from __future__ import annotations

import http.client
import json

import pytest

from grimoire.tools import request_guard
from grimoire.tools.request_guard import SESSION_HEADER, check_mutation, new_session_token
from tests.unit.test_mutation_guard import JSON, Host, atelier, cockpit  # noqa: F401

pytestmark = pytest.mark.no_session_token

BODY = json.dumps({"slug": "x"}).encode()


class _H(dict[str, str]):
    def get(self, name: str, default: str | None = None, /) -> str | None:  # type: ignore[override]
        return super().get(name, default)


def _hosts(request: pytest.FixtureRequest, name: str) -> Host:
    host: Host = request.getfixturevalue(name)
    return host


@pytest.mark.parametrize("name", ["cockpit", "atelier"])
def test_sans_jeton_une_ecriture_est_refusee(name: str, request: pytest.FixtureRequest) -> None:
    host = _hosts(request, name)
    for origin in ({}, {"Origin": host.origin}):
        assert host.request("POST", "/api/projects/select", BODY, {**JSON, **origin}) == 403
    assert host.request("PUT", "/api/blueprints/x", BODY, {**JSON}) == 403


@pytest.mark.parametrize("name", ["cockpit", "atelier"])
def test_sans_origin_mais_avec_jeton_valide_elle_passe(
    name: str, request: pytest.FixtureRequest
) -> None:
    host = _hosts(request, name)
    ok = json.dumps({"slug": host.slug}).encode()
    headers = {**JSON, SESSION_HEADER: request_guard.session_token()}
    assert host.request("POST", "/api/projects/select", ok, headers) == 200


@pytest.mark.parametrize("name", ["cockpit", "atelier"])
def test_un_jeton_d_un_autre_demarrage_est_refuse(
    name: str, request: pytest.FixtureRequest
) -> None:
    host = _hosts(request, name)
    old = request_guard.session_token()
    new_session_token()  # le serveur « redémarre »
    assert host.request("POST", "/api/projects/select", BODY, {**JSON, SESSION_HEADER: old}) == 403
    assert host.request("POST", "/api/projects/select", BODY, {**JSON, SESSION_HEADER: ""}) == 403


@pytest.mark.parametrize("name", ["cockpit", "atelier"])
def test_le_jeton_est_valide_mais_l_origine_etrangere_reste_refusee(
    name: str, request: pytest.FixtureRequest
) -> None:
    host = _hosts(request, name)
    headers = {**JSON, SESSION_HEADER: request_guard.session_token(), "Origin": "https://evil.example"}
    assert host.request("POST", "/api/projects/select", BODY, headers) == 403


@pytest.mark.parametrize("name", ["cockpit", "atelier"])
def test_la_page_servie_porte_le_jeton(name: str, request: pytest.FixtureRequest) -> None:
    host = _hosts(request, name)
    path = "/workspace/index.html"
    served = host.watched / "serve" / "workspace"
    if served.parent.is_dir():
        served.mkdir(exist_ok=True)
        (served / "index.html").write_text("<!doctype html><head></head><body>x</body>")
    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        page = resp.read().decode("utf-8")
    finally:
        conn.close()
    assert resp.status == 200
    assert request_guard.session_token() in page
    assert SESSION_HEADER in page


def test_la_comparaison_est_en_temps_constant() -> None:
    import inspect

    assert "hmac.compare_digest" in inspect.getsource(request_guard._token_ok)
    tok = request_guard.session_token()
    assert check_mutation(_H({SESSION_HEADER: tok}), 1, with_body=False) is None
    assert check_mutation(_H({SESSION_HEADER: tok + "x"}), 1, with_body=False) == (
        403,
        "jeton de session requis",
    )
