"""#876, lot 1, verrou 15 : l'octroi du mandat par le cockpit exige le jeton de session ET la clé de contrôle.

Le jeton est injecté dans toute page HTML servie sur la loopback, un processus local le lit : il ne suffit pas.
Joué contre un vrai serveur HTTP, pour que le 403 vienne du transport et de la route, pas d'un appel direct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.missions import mandate as md
from grimoire.missions.mandate import MandateStore
from grimoire.tools import request_guard
from grimoire.tools.request_guard import SESSION_HEADER

from .test_mutation_guard import JSON, Host, cockpit  # noqa: F401

pytestmark = pytest.mark.no_session_token

GRANT = "/api/workspace/autonomy/mandate/grant"
BODY = {"gestures": ["accept"], "task_types": ["implementation"], "max_sessions_per_day": 2, "until": "+1h"}


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "mandate-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _post(host: Host, body: dict[str, object], *, token: bool) -> int:
    headers = {**JSON, **({SESSION_HEADER: request_guard.session_token()} if token else {})}
    return host.request("POST", GRANT, json.dumps(body).encode(), headers)


def test_the_session_token_alone_never_grants_a_mandate(cockpit: Host) -> None:  # noqa: F811
    key_path = md.ensure_control_key()
    for body in (BODY, {**BODY, "control_key": ""}, {**BODY, "control_key": "faux"}, {**BODY, "control_key": None}):
        assert _post(cockpit, body, token=True) == 403
    assert MandateStore(cockpit.root).state().reason == md.NO_MANDATE
    assert key_path.is_file()


def test_the_control_key_alone_never_grants_a_mandate(cockpit: Host) -> None:  # noqa: F811
    key = md.ensure_control_key().read_text(encoding="utf-8").strip()
    assert _post(cockpit, {**BODY, "control_key": key}, token=False) == 403
    assert MandateStore(cockpit.root).state().reason == md.NO_MANDATE


def test_the_token_and_the_control_key_together_grant_it(cockpit: Host) -> None:  # noqa: F811
    key = md.ensure_control_key().read_text(encoding="utf-8").strip()
    assert _post(cockpit, {**BODY, "control_key": key}, token=True) == 200
    assert MandateStore(cockpit.root).state().active
