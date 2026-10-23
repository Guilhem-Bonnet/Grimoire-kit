"""Route d'attention — ``attention_order`` et ``GET /api/workspace/attention`` (P22).

Ce que la liste « À traiter » ignorait (revue UX, B1) : checkpoints non
décidés, revues refusées par un gate, tâches bloquées, consignes non
accusées, propositions, claims que plus aucune session ne tient, sondes en
échec. Et ce qu'elle perdait : un projet dont la sonde expirait sortait de la
liste. Ici, un projet illisible est un élément avec sa raison, jamais omis.
"""

from __future__ import annotations

import copy
import json
import socket
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.memory import health_cache
from grimoire.memory.backends.base import BackendStatus
from grimoire.memory.manager import MemoryManager
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import flow_runs
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_attention as wa
from grimoire.tools.memory_setup import (
    SERVICE_FAILED,
    SERVICE_ONLINE,
    SERVICE_STOPPED,
    SERVICE_UNKNOWN,
    service_state,
)
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "attention" / "cards.json"
STANDARD = Path("_grimoire/standard")
RUNS = Path("_grimoire-output/.runs")
FLOWS = Path("_grimoire-runtime-output/flows")

#: La clôture d'une tâche relue exige un pack de preuve : sans lui, la revue est bloquée.
GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["evidence_pack"]
profile_strictness:
  governed: hard_fail
"""

#: Les huit raisons que la route doit recenser (critère 2), en plus des questions posées.
EIGHT_KINDS = {
    wa.CHECKPOINT, wa.REVIEW_BLOCKED, wa.TASK_BLOCKED, wa.DIRECTIVE_UNACKED,
    wa.PROPOSAL, wa.CLAIM_ORPHANED, wa.KIT_BEHIND, wa.PROBE_FAILED,
}


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)


def _ids(section: dict[str, Any]) -> list[str]:
    return [item["id"] for item in section["items"]]


def _items(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for section in view["sections"] for item in section["items"]]


def _kinds(view: dict[str, Any], slug: str | None = None) -> set[str]:
    return {i["kind"] for i in _items(view) if slug is None or i["project"]["slug"] == slug}


# ── attention_order : fonction pure, sur fixture ─────────────────────────────


class TestAttentionOrder:
    @pytest.fixture
    def fixture(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return data

    def test_l_ordre_de_la_fixture(self, fixture: dict[str, Any]) -> None:
        sections = wa.attention_order(fixture["cards"])

        assert {s["id"]: _ids(s) for s in sections} == fixture["expected"]
        assert [s["id"] for s in sections] == ["decide", "unblock", "repair", "maintain", "resolved", "other"]
        assert all(s["count"] == len(s["items"]) for s in sections)

    def test_chaque_element_dans_une_seule_section(self, fixture: dict[str, Any]) -> None:
        sections = wa.attention_order(fixture["cards"])

        seen = Counter(item_id for s in sections for item_id in _ids(s))
        assert set(seen) == {c["id"] for c in fixture["cards"]}
        assert set(seen.values()) == {1}

    def test_ordre_stable_et_entree_intacte(self, fixture: dict[str, Any]) -> None:
        cards = fixture["cards"]
        before = copy.deepcopy(cards)

        first = wa.attention_order(cards)
        again = wa.attention_order(copy.deepcopy(cards))

        assert first == again
        assert cards == before

    def test_une_egalite_garde_l_ordre_d_arrivee(self, fixture: dict[str, Any]) -> None:
        cards = fixture["cards"]
        i = next(k for k, c in enumerate(cards) if c["id"] == "task_blocked:beta:t2")
        j = next(k for k, c in enumerate(cards) if c["id"] == "task_blocked:alpha:t6")
        swapped = list(cards)
        swapped[i], swapped[j] = swapped[j], swapped[i]

        unblock = {s["id"]: _ids(s) for s in wa.attention_order(swapped)}["unblock"]

        assert unblock == [
            "review_blocked:alpha:t3", "task_blocked:alpha:t6", "task_blocked:beta:t2",
            "directive_unacked:alpha:t5", "claim_orphaned:beta:t4",
        ]

    def test_chaque_type_connu_a_une_seule_section(self) -> None:
        kinds = [kind for _, _, section_kinds in wa.SECTIONS for kind in section_kinds]
        assert len(kinds) == len(set(kinds))
        assert set(kinds) == set(wa.KINDS)

    def test_sans_element_les_sections_restent_et_autre_n_apparait_pas(self) -> None:
        sections = wa.attention_order([])
        assert [s["id"] for s in sections] == [s[0] for s in wa.SECTIONS]
        assert all(s["items"] == [] for s in sections)


# ── La route : flotte et projet ─────────────────────────────────────────────


def _task(service: TaskService, title: str, *, running: bool = True) -> str:
    tid = service.add(title, ("critère",), actor="t", owner="guilhem", ready=True).task.id
    if running:
        service.claim(tid, "agent-a", host="host-claude-code-cli")
        service.transition(tid, TaskState.RUNNING, "agent-a")
    return tid


def _journal(root: Path, session_id: str, *, task_id: str = "", updated: datetime, ended: bool = False) -> None:
    path = root / RUNS / f"session-{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1, "session_id": session_id, "started_at": updated.isoformat(),
        "updated_at": updated.isoformat(), "task_id": task_id, "rules": {},
    }
    if ended:
        data["ended_at"] = updated.isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")


def _memory_config(root: Path, name: str, *, qdrant_url: str = "http://127.0.0.1:6333") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: qdrant-server\n  qdrant_url: {qdrant_url}\n",
        encoding="utf-8",
    )


def _alpha(root: Path, now: datetime) -> dict[str, str]:
    """Un projet où attendent un checkpoint, une question, une revue, une
    tâche bloquée, une consigne, un claim orphelin et une proposition."""
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "evidence-gates.yaml").write_text(GATES, encoding="utf-8")
    (root / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    service = TaskService(root)
    ids = {
        "question": _task(service, "Question : fusionner la branche"),
        "review": _task(service, "Revue : relire le module"),
        "blocked": _task(service, "Bloquée : attend une clé"),
        "directive": _task(service, "Consigne : changer de cap"),
        "orphan": _task(service, "Orpheline : session finie"),
        "calm": _task(service, "Tranquille : rien à signaler", running=False),
    }
    service.ask(ids["question"], "Fusionner la PR #12 ?", "agent-a", kind="merge", options=("oui", "non"))
    service.transition(ids["review"], TaskState.NEEDS_VERIFICATION, "agent-a")
    service.transition(ids["blocked"], TaskState.BLOCKED, "agent-a", reason="attend la clé d'API du fournisseur")
    service.comment(ids["directive"], "Passe d'abord par les tests.", "guilhem", kind="directive", origin=ORIGIN_HUMAN)
    _journal(root, "sess-finie", task_id=ids["orphan"], updated=now - timedelta(minutes=5), ended=True)

    from grimoire.proposals import create_manual_proposal

    create_manual_proposal(root, slug="relecteur-migrations", specialty="relecture des migrations",
                           artifact_type="agent")
    (root / FLOWS).mkdir(parents=True)
    for run_id, created in (("run-attend", "2026-10-21T09:00:00+00:00"), ("run-fini", "2026-10-22T09:00:00+00:00")):
        (root / FLOWS / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "blueprint_id": "project-upgrade", "created_at": created,
        }), encoding="utf-8")
    return ids


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Alpha (tout ce qui attend), Beta (sonde mémoire en échec), Gamma (dossier
    disparu), Delta (ledger abîmé)."""
    now = datetime.now(UTC)
    a, b, d = tmp_path / "projet-a", tmp_path / "projet-b", tmp_path / "projet-d"
    ids = _alpha(a, now)
    _memory_config(b, "Beta")
    health_cache.set_status(b, {"state": "unavailable", "error": "connexion refusée par qdrant",
                                "resolvedBackend": "qdrant-server", "entries": None})
    ledger = d / "_grimoire-runtime-output" / "ledger"
    ledger.mkdir(parents=True)
    # Une ligne qui n'est pas du JSON est sautée au rejeu ; un événement bien
    # formé mais incomplet fait lever `from_dict` : c'est ce ledger-là qui est illisible.
    (ledger / "events.jsonl").write_text('{"event_type": "task.created", "payload": {}}\n', encoding="utf-8")
    reg.save_registry([
        {"slug": "projet-a", "name": "Alpha", "path": str(a)},
        {"slug": "projet-b", "name": "Beta", "path": str(b)},
        {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        {"slug": "projet-d", "name": "Delta", "path": str(d)},
    ])

    # Le statut vivant d'un run vient du moteur ; la fixture en fixe deux.
    def _status(_root: Path, runs: list[dict[str, Any]]) -> None:
        for run in runs:
            waiting = run["runId"] == "run-attend"
            run["status"] = "checkpointed" if waiting else "completed"
            run["currentNode"] = "destructive" if waiting else None

    monkeypatch.setattr(flow_runs, "_attach_live_status", _status)
    behind_root = a.resolve()

    def _kit(root: Path) -> dict[str, Any]:
        late = root.resolve() == behind_root
        return {"scaffolded": True, "catalogAvailable": True, "upToDate": not late,
                "behind": 3 if late else 0, "behindFiles": ["_grimoire/kit/a.md"] if late else []}

    monkeypatch.setattr(wa, "kit_alignment", _kit)
    return {"a": a, "b": b, "d": d, "ids": ids, "now": now, "root": tmp_path}


def _never_probe(service: str, url: str) -> str:
    raise AssertionError(f"aucun service ne devait être sondé ({service} {url})")


class TestRouteFleet:
    def test_la_flotte_recense_chaque_raison(self, fleet: dict[str, Any]) -> None:
        view = wa.attention_view(fleet["root"], portfolio=True, now=fleet["now"],
                                 service_probe=_never_probe, watch=wa.ServiceWatch())

        assert view["schemaVersion"] == wa.SCHEMA_VERSION
        assert view["scope"] == "portfolio"
        assert _kinds(view) >= EIGHT_KINDS
        alpha = {(i["kind"], i["ref"].get("task_id") or i["ref"].get("run_id") or i["ref"].get("slug"))
                 for i in _items(view) if i["project"]["slug"] == "projet-a"}
        ids = fleet["ids"]
        assert {
            (wa.QUESTION, ids["question"]),
            (wa.REVIEW_BLOCKED, ids["review"]),
            (wa.TASK_BLOCKED, ids["blocked"]),
            (wa.DIRECTIVE_UNACKED, ids["directive"]),
            (wa.CLAIM_ORPHANED, ids["orphan"]),
            (wa.CHECKPOINT, "run-attend"),
            (wa.PROPOSAL, "relecteur-migrations"),
        } <= alpha
        assert (wa.CHECKPOINT, "run-fini") not in alpha
        assert not any(i["ref"].get("task_id") == ids["calm"] for i in _items(view))
        assert wa.KIT_BEHIND in _kinds(view, "projet-a")
        assert _kinds(view, "projet-b") == {wa.PROBE_FAILED}
        assert view["count"] == sum(view["counts"].values())

    def test_les_raisons_sont_concretes(self, fleet: dict[str, Any]) -> None:
        view = wa.attention_view(fleet["root"], portfolio=True, now=fleet["now"],
                                 service_probe=_never_probe, watch=wa.ServiceWatch())
        by_kind = {i["kind"]: i for i in _items(view) if i["project"]["slug"] in ("projet-a", "projet-b")}

        assert by_kind[wa.TASK_BLOCKED]["reason"] == "attend la clé d'API du fournisseur"
        assert "evidence_pack" in by_kind[wa.REVIEW_BLOCKED]["reason"]
        assert by_kind[wa.REVIEW_BLOCKED]["ref"]["refusals"]
        assert by_kind[wa.DIRECTIVE_UNACKED]["ref"]["unread"] == 1
        assert by_kind[wa.CLAIM_ORPHANED]["reason"] == "la session a déclaré sa fin, le claim reste posé"
        assert by_kind[wa.QUESTION]["reason"] == "Fusionner la PR #12 ?"
        assert by_kind[wa.CHECKPOINT]["ref"]["node"] == "destructive"
        assert by_kind[wa.KIT_BEHIND]["ref"]["behind"] == 3
        assert by_kind[wa.PROBE_FAILED]["reason"] == "sonde mémoire en échec : connexion refusée par qdrant"

    def test_un_projet_par_slug_et_pour_le_projet_servi(self, fleet: dict[str, Any]) -> None:
        one = wa.attention_view(fleet["root"], portfolio=True, slug="projet-a", now=fleet["now"],
                                service_probe=_never_probe, watch=wa.ServiceWatch())
        served = wa.attention_view(fleet["a"], now=fleet["now"], service_probe=_never_probe,
                                   watch=wa.ServiceWatch())

        for view in (one, served):
            assert {i["project"]["slug"] for i in _items(view)} == {"projet-a"}
            assert _kinds(view) >= EIGHT_KINDS - {wa.PROBE_FAILED}
            assert [p["slug"] for p in view["projects"]] == ["projet-a"]
        assert served["scope"] == "project"
        assert [s["id"] for s in one["sections"]] == [s["id"] for s in served["sections"]]
        assert [_ids(s) for s in one["sections"]] == [_ids(s) for s in served["sections"]]

    def test_un_slug_inconnu_est_un_refus(self, fleet: dict[str, Any]) -> None:
        with pytest.raises(FileNotFoundError):
            wa.attention_view(fleet["root"], portfolio=True, slug="projet-z")

    def test_la_route_est_dans_la_table_et_lit_ses_parametres(
        self, fleet: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(wa, "_WATCH", wa.ServiceWatch())
        assert f"{PREFIX}attention" in GET_ROUTES

        fleet_view = workspace_get(fleet["a"], f"{PREFIX}attention", {"scope": ["portfolio"]})
        one = workspace_get(fleet["a"], f"{PREFIX}attention", {"scope": ["portfolio"], "slug": ["projet-b"]})
        served = workspace_get(fleet["a"], f"{PREFIX}attention", {})

        assert len(fleet_view["projects"]) == 4
        assert [p["slug"] for p in one["projects"]] == ["projet-b"]
        assert served["scope"] == "project"
        assert served["probed"] is False


class TestUnreadableProject:
    def test_un_projet_illisible_apparait_avec_sa_raison(self, fleet: dict[str, Any]) -> None:
        view = wa.attention_view(fleet["root"], portfolio=True, now=fleet["now"],
                                 service_probe=_never_probe, watch=wa.ServiceWatch())

        gamma = [i for i in _items(view) if i["project"]["slug"] == "projet-c"]
        delta = [i for i in _items(view) if i["project"]["slug"] == "projet-d"]
        assert [(i["kind"], i["reason"]) for i in gamma] == [
            (wa.UNREADABLE, f"dossier introuvable : {fleet['root'] / 'n-existe-pas'}")
        ]
        assert [i["kind"] for i in delta] == [wa.UNREADABLE]
        assert delta[0]["reason"].startswith("ledger illisible : ")
        rows = {p["slug"]: p for p in view["projects"]}
        assert set(rows) == {"projet-a", "projet-b", "projet-c", "projet-d"}
        assert rows["projet-c"]["state"] == rows["projet-d"]["state"] == "unreadable"
        assert view["summary"] == {"projects": 4, "readable": 2}
        repair = next(s for s in view["sections"] if s["id"] == "repair")
        assert {i["project"]["slug"] for i in repair["items"] if i["kind"] == wa.UNREADABLE} == {
            "projet-c", "projet-d",
        }

    def test_un_chemin_absent_du_registre_est_nomme(self, tmp_path: Path) -> None:
        reg.save_registry([{"slug": "sans-chemin", "name": "Sans chemin", "path": ""}])

        view = wa.attention_view(tmp_path, portfolio=True, service_probe=_never_probe, watch=wa.ServiceWatch())

        assert [(i["kind"], i["reason"]) for i in _items(view)] == [(wa.UNREADABLE, "chemin absent du registre")]

    def test_une_source_qui_casse_devient_une_sonde_en_echec(
        self, fleet: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_root: Path) -> dict[str, Any]:
            raise OSError("catalogue illisible")

        monkeypatch.setattr(wa, "kit_alignment", _boom)

        view = wa.attention_view(fleet["a"], now=fleet["now"], service_probe=_never_probe, watch=wa.ServiceWatch())

        failed = [i for i in _items(view) if i["kind"] == wa.PROBE_FAILED]
        assert [i["reason"] for i in failed] == ["kit illisible : catalogue illisible"]
        assert wa.REVIEW_BLOCKED in _kinds(view)


# ── Services requis : anti-bruit et résolution ───────────────────────────────


def _scripted(states: list[str]) -> tuple[Callable[[str, str], str], list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def _probe(service: str, url: str) -> str:
        calls.append((service, url))
        return states[len(calls) - 1]

    return _probe, calls


def _service_items(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in _items(view) if i["kind"] in (wa.SERVICE, wa.SERVICE_RESOLVED)]


class TestRequiredServices:
    T0 = datetime(2026, 10, 23, 14, 30, tzinfo=UTC)

    def _live_project(self, tmp_path: Path) -> Path:
        root = tmp_path / "projet-vivant"
        _memory_config(root, "Vivant", qdrant_url="http://127.0.0.1:6333")
        _journal(root, "sess-vivante", updated=self.T0 - timedelta(minutes=1))
        reg.save_registry([{"slug": "projet-vivant", "name": "Vivant", "path": str(root)}])
        return root

    @pytest.mark.parametrize(("state", "word"), [
        (SERVICE_STOPPED, "arrêté"), (SERVICE_FAILED, "en échec"), (SERVICE_UNKNOWN, "inconnu"),
    ])
    def test_deux_sondes_negatives_puis_resolution_avec_l_heure(
        self, tmp_path: Path, state: str, word: str
    ) -> None:
        root = self._live_project(tmp_path)
        probe, calls = _scripted([state, state, SERVICE_ONLINE])
        watch = wa.ServiceWatch()

        def view(minutes: int) -> dict[str, Any]:
            return wa.attention_view(root, now=self.T0 + timedelta(minutes=minutes),
                                     service_probe=probe, watch=watch)

        first = view(0)
        second = view(1)
        third = view(2)

        assert _service_items(first) == []
        [alert] = _service_items(second)
        assert alert["kind"] == wa.SERVICE
        assert alert["title"] == f"qdrant {word}"
        assert alert["reason"] == "requis (session vivante) — 2 sondes négatives consécutives depuis 14:30 UTC"
        assert alert["since"] == self.T0.isoformat()
        assert alert["ref"]["state"] == state
        [resolved] = _service_items(third)
        assert resolved["kind"] == wa.SERVICE_RESOLVED
        assert resolved["reason"] == "résolu automatiquement à 14:32 UTC"
        assert third["count"] == 0
        assert [i["id"] for i in next(s for s in third["sections"] if s["id"] == "resolved")["items"]] == [
            resolved["id"]
        ]
        assert calls == [("qdrant", "http://127.0.0.1:6333")] * 3

    def test_une_tache_en_cours_suffit(self, tmp_path: Path) -> None:
        root = tmp_path / "projet-tache"
        _memory_config(root, "Tâche")
        _task(TaskService(root), "En cours : sans journal de session")
        reg.save_registry([{"slug": "projet-tache", "name": "Tâche", "path": str(root)}])
        probe, _ = _scripted([SERVICE_STOPPED, SERVICE_STOPPED])
        watch = wa.ServiceWatch()

        wa.attention_view(root, now=self.T0, service_probe=probe, watch=watch)
        view = wa.attention_view(root, now=self.T0 + timedelta(minutes=1), service_probe=probe, watch=watch)

        [alert] = [i for i in _items(view) if i["kind"] == wa.SERVICE]
        assert alert["reason"].startswith("requis (tâche en cours)")

    def test_une_seule_sonde_negative_ne_dit_rien(self, tmp_path: Path) -> None:
        root = self._live_project(tmp_path)
        probe, _ = _scripted([SERVICE_STOPPED, SERVICE_ONLINE, SERVICE_STOPPED])
        watch = wa.ServiceWatch()

        views = [wa.attention_view(root, now=self.T0 + timedelta(minutes=m), service_probe=probe, watch=watch)
                 for m in range(3)]

        assert all(_service_items(v) == [] for v in views)

    def test_deux_lectures_rapprochees_ne_font_qu_une_sonde(self, tmp_path: Path) -> None:
        root = self._live_project(tmp_path)
        probe, calls = _scripted([SERVICE_STOPPED, SERVICE_STOPPED])
        watch = wa.ServiceWatch()

        wa.attention_view(root, now=self.T0, service_probe=probe, watch=watch)
        view = wa.attention_view(root, now=self.T0 + timedelta(seconds=1), service_probe=probe, watch=watch)

        assert len(calls) == 1
        assert _service_items(view) == []

    def test_un_projet_au_repos_n_est_pas_sonde(self, tmp_path: Path) -> None:
        root = tmp_path / "projet-repos"
        _memory_config(root, "Repos")
        _journal(root, "sess-ancienne", updated=self.T0 - timedelta(hours=3))
        reg.save_registry([{"slug": "projet-repos", "name": "Repos", "path": str(root)}])
        watch = wa.ServiceWatch()

        for minutes in range(3):
            view = wa.attention_view(root, now=self.T0 + timedelta(minutes=minutes),
                                     service_probe=_never_probe, watch=watch)
            assert _service_items(view) == []


class TestServiceState:
    def test_rien_n_ecoute_est_arrete_et_un_port_ouvert_est_en_ligne(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            assert service_state("qdrant", f"http://127.0.0.1:{port}") == SERVICE_ONLINE
        # Sous Windows, un refus local n'arrive qu'après les relances SYN (~2 s) :
        # un délai large garde le test sur « refusé » et non sur « délai dépassé ».
        assert service_state("qdrant", f"http://127.0.0.1:{port}", timeout=5.0) == SERVICE_STOPPED

    def test_une_url_illisible_est_inconnue(self) -> None:
        assert service_state("qdrant", "http://[::1") == SERVICE_UNKNOWN


# ── Sonde mémoire expirée ───────────────────────────────────────────────────


class _SlowBackend:
    """Backend dont la sonde dépasse le délai du manager (même patron que P09)."""

    def health_check(self) -> BackendStatus:
        import time

        time.sleep(0.6)
        return BackendStatus(backend="qdrant-server", healthy=True, entries=42)


class TestExpiredMemoryProbe:
    def test_un_projet_dont_la_sonde_expire_figure_avec_sa_raison(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "projet-lent"
        _memory_config(root, "Lent", qdrant_url="http://qdrant.lan:6333")
        reg.save_registry([{"slug": "projet-lent", "name": "Lent", "path": str(root)}])

        def _from_config(cls: type, cfg: Any, *, project_root: Path | None = None) -> Any:
            return MemoryManager(_SlowBackend(), project_name=cfg.project.name, backend_id="qdrant-server")  # type: ignore[arg-type]

        monkeypatch.setattr(MemoryManager, "from_config", classmethod(_from_config))

        probed = wa.attention_view(root, probe=True, service_probe=_never_probe, watch=wa.ServiceWatch())
        cached = wa.attention_view(tmp_path, portfolio=True, service_probe=_never_probe, watch=wa.ServiceWatch())

        for view in (probed, cached):
            [item] = [i for i in _items(view) if i["project"]["slug"] == "projet-lent"]
            assert item["kind"] == wa.PROBE_FAILED
            assert item["reason"].startswith("état inconnu, sonde expirée")
            assert item["ref"]["backend"] == "qdrant-server"
        assert probed["probed"] is True
        repair = next(s for s in cached["sections"] if s["id"] == "repair")
        assert [i["project"]["slug"] for i in repair["items"]] == ["projet-lent"]
