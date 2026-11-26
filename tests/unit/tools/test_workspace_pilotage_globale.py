"""Piloter depuis la vue « Tous les projets » : lecture du pilote et du mandat par projet, gestes sur un projet NOMMÉ.

Aucune route nouvelle pour les gestes : le pilote automatique, le lancement et le chat passent par les routes
d'écriture existantes du projet visé, avec la cible obligatoire (``?project=``), la clé de contrôle et, depuis une page
en portée globale (en-tête ``X-Grimoire-Scope: all``), le refus de toute écriture sans projet. Aucun agent n'y accède.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.autopilot import AutopilotStore
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN
from grimoire.tools import workspace_pilotage
from grimoire.tools.request_guard import SCOPE_HEADER
from grimoire.tools.workspace_routes import PREFIX
from grimoire.tools.workspace_write_guard import GLOBAL_SCOPE_REFUSAL

from .. import _cockpit_http
from .test_workspace_pilotage import _isolated_home, _key, _post, duo  # noqa: F401

AUTOPILOT = f"{PREFIX}autonomy/autopilot"
GRANT = f"{PREFIX}autonomy/mandate/grant"
GLOBAL = {SCOPE_HEADER: "all"}
MANDATE = {"gestures": ["answer", "resume"], "task_types": ["implementation"], "max_sessions_per_day": 10, "until": "+1d"}


def _rows(port: int) -> dict[str, dict[str, Any]]:
    status, body = _cockpit_http.get(port, f"{PREFIX}pilotage?scope=portfolio")
    assert status == 200, body
    return {row["slug"]: row for row in body["projects"]}


def _grant(port: int, slug: str) -> None:
    status, body = _post(port, GRANT, slug, MANDATE)
    assert status == 200, body


def _set(
    port: int, slug: str, on: bool, *, key: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    payload = {"project": slug, "control_key": _key() if key is None else key, "on": on}
    return _cockpit_http.post(port, f"{AUTOPILOT}?project={slug}", payload, headers=headers)


def test_la_vue_globale_lit_le_pilote_et_le_mandat_de_chaque_projet(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    rows = _rows(port)
    assert rows["beta"]["authority"]["error"] == ""
    assert rows["beta"]["authority"]["autopilot"]["active"] is False
    assert rows["beta"]["authority"]["mandate"]["active"] is False

    _grant(port, "beta")
    assert _set(port, "beta", True)[0] == 200
    rows = _rows(port)
    assert rows["beta"]["authority"]["autopilot"]["active"] is True
    assert rows["beta"]["authority"]["mandate"]["active"] is True
    assert rows["alpha"]["authority"]["autopilot"]["active"] is False, "l'autre projet n'est pas touché"
    assert "gestures" not in rows["beta"]["authority"]["mandate"], "ce que le mandat porte ne sort pas"


def test_un_journal_illisible_se_dit_et_n_invente_pas_un_pilote_coupe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: Any, **_k: Any) -> Any:
        raise OSError("journal rompu")

    monkeypatch.setattr(AuthorityJournal, "read", broken)
    view = workspace_pilotage.authority_view(tmp_path)
    assert view["autopilot"] is None and view["mandate"] is None
    assert "journal rompu" not in view["error"], "le message de l'exception ne sort pas, seulement son type"
    assert "OSError" in view["error"]


def test_la_vue_globale_ne_lit_le_journal_d_autorite_qu_une_fois_pour_toutes_les_lignes(
    duo: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    _port, _alpha, _beta = duo
    calls: list[int] = []
    real = AuthorityJournal.read

    def counting(self: AuthorityJournal) -> Any:
        calls.append(1)
        return real(self)

    monkeypatch.setattr(AuthorityJournal, "read", counting)
    view = workspace_pilotage.portfolio_view()
    assert {row["slug"] for row in view["projects"]} == {"alpha", "beta"}
    assert all(row["authority"]["error"] == "" for row in view["projects"])
    assert len(calls) == 1, "une lecture par rafraîchissement, pas une par ligne"


def test_un_journal_illisible_est_lu_une_fois_et_dit_sur_chaque_ligne(
    duo: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    calls: list[int] = []

    def broken(_self: AuthorityJournal) -> Any:
        calls.append(1)
        raise OSError("journal rompu")

    monkeypatch.setattr(AuthorityJournal, "read", broken)
    rows = workspace_pilotage.portfolio_view()["projects"]
    assert len(calls) == 1 and all(r["authority"]["error"] and r["authority"]["autopilot"] is None for r in rows)


@pytest.mark.parametrize("content", [b"{not json\n", b"\xff\xfe" * 10], ids=["json-invalide", "octets-non-utf8"])
def test_un_journal_corrompu_se_dit_lecture_impossible_jamais_coupe(
    duo: tuple[int, Path, Path], content: bytes  # noqa: F811
) -> None:
    port, _alpha, _beta = duo
    _grant(port, "beta")
    AuthorityJournal().path.write_bytes(content)
    for row in _rows(port).values():
        authority = row["authority"]
        assert authority["error"] and authority["autopilot"] is None and authority["mandate"] is None
        assert "/" not in authority["error"] and "\\" not in authority["error"], "aucun chemin local dans l'erreur"


def test_lire_la_vue_globale_n_ecrit_rien_et_un_mandat_echu_se_dit_expire(
    duo: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from datetime import timedelta

    from grimoire.missions import mandate as md

    port, _alpha, _beta = duo
    _grant(port, "beta")
    assert _set(port, "beta", True)[0] == 200
    journal = AuthorityJournal().path
    before = journal.read_bytes()
    real_now = md._now
    monkeypatch.setattr(md, "_now", lambda: real_now() + timedelta(days=3))

    first = _rows(port)["beta"]["authority"]
    second = _rows(port)["beta"]["authority"]
    assert first["mandate"]["active"] is False and first["mandate"]["reason"] == "expired"
    assert first["autopilot"]["active"] is False and second == first
    assert journal.read_bytes() == before, "un GET n'inscrit aucune révocation au journal de la machine"


def test_la_facade_de_lecture_refuse_toute_ecriture() -> None:
    journal = workspace_pilotage._ReadOnceJournal()
    with pytest.raises(PermissionError):
        journal.append({"type": "x"})
    with pytest.raises(PermissionError):
        journal.mark_revoked("m", "r", "t")


def test_allumer_le_pilote_d_un_projet_nomme_ne_touche_que_ce_projet_et_se_journalise(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    _grant(port, "beta")
    status, body = _set(port, "beta", True, headers=GLOBAL)
    assert status == 200 and body["autopilot"] == "on", body
    entries = [e for e in AuthorityJournal().read() if e.get("type") == "autopilot"]
    assert entries and entries[-1]["by"] == ORIGIN_HUMAN and entries[-1]["via"] == "cockpit"
    assert _rows(port)["alpha"]["authority"]["autopilot"]["active"] is False

    assert _set(port, "beta", False)[0] == 200
    assert _rows(port)["beta"]["authority"]["autopilot"]["active"] is False


@pytest.mark.parametrize("key", ["", "mauvaise-cle"])
def test_sans_cle_valide_rien_n_est_ecrit(duo: tuple[int, Path, Path], key: str) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    _grant(port, "beta")
    status, _body = _set(port, "beta", True, key=key)
    assert status == 403
    assert _rows(port)["beta"]["authority"]["autopilot"]["active"] is False


def test_sans_projet_cible_le_geste_est_refuse_depuis_la_vue_globale(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    _grant(port, "beta")
    body = {"on": True, "control_key": _key(), "project": "beta"}
    status, answer = _cockpit_http.post(port, AUTOPILOT, body, headers=GLOBAL)
    assert status == 400 and GLOBAL_SCOPE_REFUSAL in answer["error"]
    assert _rows(port)["beta"]["authority"]["autopilot"]["active"] is False
    # Sans l'en-tête, le refus de l'hôte reste celui d'avant : projet non nommé.
    assert _cockpit_http.post(port, AUTOPILOT, body)[0] == 400


def test_un_projet_non_enrole_ou_un_corps_qui_contredit_l_url_sont_refuses(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    _grant(port, "beta")
    assert _set(port, "inconnu", True)[0] == 404
    status, _ = _cockpit_http.post(
        port, f"{AUTOPILOT}?project=beta", {"on": True, "control_key": _key(), "project": "alpha"},
    )
    assert status == 400
    assert _rows(port)["beta"]["authority"]["autopilot"]["active"] is False


def test_sans_mandat_actif_le_pilote_ne_s_allume_pas(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    status, body = _set(port, "beta", True)
    assert status == 400 and "mandat" in body["error"]


def test_un_agent_ne_peut_pas_allumer_le_pilote(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    from grimoire.missions.mandate import MandateRefusedError

    _port, _alpha, beta = duo
    with pytest.raises(MandateRefusedError):
        AutopilotStore(beta).set(True, origin=ORIGIN_AGENT, via="agent")


def test_aucun_outil_mcp_ni_commande_de_la_console_n_expose_le_pilote_ni_le_lancement_ni_le_chat() -> None:
    from grimoire.mcp import server
    from grimoire.tools import workspace_exec

    words = re.compile(r"autopilot|pilote|launch|lance|chat", re.IGNORECASE)
    names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]
    assert names and [n for n in names if words.search(n)] == []
    assert [k for k in workspace_exec.ALLOWED if words.search(k)] == []
    assert [c["command"] for c in workspace_exec.catalogue() if words.search(c["command"])] == []
    calls = re.compile(r"autonomy/autopilot|_autopilot_set|workspace_launch_apply|workspace_chat")
    offenders = [p.name for p in Path(server.__file__).parent.glob("*.py") if calls.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_lancer_et_ecrire_au_chat_exigent_un_projet_nomme_depuis_la_vue_globale(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    port, _alpha, _beta = duo
    for route, body in ((f"{PREFIX}tasks/GAO-1/launch", {"empreinte": "x"}), (f"{PREFIX}chat/send", {"text": "bonjour"})):
        status, answer = _cockpit_http.post(port, route, body, headers=GLOBAL)
        assert status == 400 and GLOBAL_SCOPE_REFUSAL in answer["error"], route
    # Nommé, le projet est résolu par le registre : une tâche inconnue est un 404, un slug inconnu aussi.
    named, _ = _cockpit_http.post(port, f"{PREFIX}tasks/GAO-1/launch?project=beta", {"empreinte": "x", "project": "beta"}, headers=GLOBAL)
    assert named != 400
    unknown, _ = _cockpit_http.post(port, f"{PREFIX}chat/send?project=inconnu", {"text": "x"}, headers=GLOBAL)
    assert unknown == 404


def test_le_journal_de_la_garde_note_le_projet_de_chaque_geste_de_la_vue_globale(duo: tuple[int, Path, Path]) -> None:  # noqa: F811
    from grimoire.tools.workspace_write_guard import cockpit_journal_path

    port, _alpha, _beta = duo
    _grant(port, "beta")
    _set(port, "beta", True, headers=GLOBAL)
    lines = [json.loads(x) for x in cockpit_journal_path().read_text(encoding="utf-8").splitlines()]
    assert any(e.get("route") == AUTOPILOT and e.get("project") == "beta" and e["verdict"] == "acceptée" for e in lines)
