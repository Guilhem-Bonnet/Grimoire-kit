"""PR à revoir (P48) — la route d'attention liste les PR ouvertes, l'état de leur CI et la tâche liée.

Aucun test n'appelle GitHub : le lecteur ``gh`` est injecté (un faux qui rend
une liste fixe), ou ``subprocess.run`` est remplacé pour le lecteur par défaut.
Ce que les tests prouvent :

- la route d'attention rend une ligne « PR à revoir » par PR ouverte, avec
  ses checks comptés (verts, échecs, ignorés) et la tâche du ledger liée ;
- un ``gh`` absent, non authentifié ou lent est un « état inconnu » avec sa
  raison, jamais une liste vide ; un projet sans remote n'affiche rien ;
- la lecture ne bloque pas la route : partagée en vol, bornée, mise en cache ;
- ``GET /api/workspace/reviews`` rend la même lecture pour l'écran de revue,
  les plus prêtes d'abord, avec les croisements de fichiers.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_reviews as wr
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

REPO = "exemple/atelier"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wa, "kit_alignment", lambda _root: {})


def _run(check: str, conclusion: str = "", *, name: str = "", status: str = "COMPLETED", url: str = "") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": name or check, "workflowName": "CI", "status": status,
            "conclusion": conclusion, "detailsUrl": url}


def _pr(number: int, title: str, *, branch: str, checks: list[dict[str, Any]], body: str = "",
        files: tuple[str, ...] = ("src/a.py",), draft: bool = False) -> dict[str, Any]:
    return {
        "number": number, "title": title, "url": f"https://github.com/{REPO}/pull/{number}",
        "headRefName": branch, "baseRefName": "main", "isDraft": draft, "author": {"login": "agent"},
        "additions": 10 * number, "deletions": number, "changedFiles": len(files), "body": body,
        "createdAt": "2026-10-20T10:00:00Z", "updatedAt": "2026-10-21T10:00:00Z",
        "statusCheckRollup": checks, "reviewDecision": "REVIEW_REQUIRED",
        "files": [{"path": p, "additions": 1, "deletions": 0} for p in files],
        "closingIssuesReferences": [{"number": 700 + number}],
    }


def _project(root: Path, *, origin: str | None = f"https://github.com/{REPO}.git") -> dict[str, str]:
    """Un dépôt git (remote GitHub facultatif) avec un ledger de deux tâches."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True, capture_output=True)
    service = TaskService(root)
    ids = {
        "branch": service.add("Lot par branche", ("critère",), actor="t", owner="guilhem", ready=True).task.id,
        "body": service.add("Lot cité au corps", ("critère",), actor="t", owner="guilhem", ready=True).task.id,
    }
    return ids


def _prs(ids: dict[str, str]) -> list[dict[str, Any]]:
    return [
        _pr(12, "Lot vert", branch=f"task/{ids['branch']}",
            checks=[_run("tests", "SUCCESS"), _run("lint", "SUCCESS"), _run("rust", "SKIPPED"), _run("n", "NEUTRAL")],
            files=("src/a.py", "tests/test_a.py", "CHANGELOG.md")),
        _pr(15, "Lot en échec", branch="fix/codeql", body=f"Corrige le parseur.\n\nRefs {ids['body']}",
            checks=[_run("tests", "SUCCESS"), _run("CodeQL", "FAILURE", url="https://github.com/x/runs/1")],
            files=("src/a.py",)),
        _pr(16, "Brouillon", branch="wip", checks=[], draft=True),
    ]


class _Reader:
    """Faux ``gh pr list`` : compte ses appels, peut lever ou attendre un signal."""

    def __init__(self, prs: list[dict[str, Any]] | None = None, *, error: Exception | None = None,
                 gate: threading.Event | None = None) -> None:
        self.prs, self.error, self.gate = prs or [], error, gate
        self.calls: list[str] = []

    def __call__(self, repo: str, _cwd: Path) -> list[dict[str, Any]]:
        self.calls.append(repo)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return self.prs


def _items(view: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [i for s in view["sections"] for i in s["items"] if i["kind"] == kind]


# ── Critère 1 : la route d'attention ────────────────────────────────────────


class TestPilote:
    """P84 : sous mandat accept+merge, le relecteur du tick relit les PR de tâches ; elles quittent « À relire »."""

    @staticmethod
    def _view(root: Path, prs: list[dict[str, Any]]) -> dict[str, Any]:
        return wa.attention_view(root, pr_reader=_Reader(prs), review_cache=wr.ReviewCache(), pr_wait=5,
                                 service_probe=lambda *_: "online", watch=wa.ServiceWatch())

    def test_sous_mandat_la_pr_de_tache_est_comptee_a_part(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        monkeypatch.setattr(wa, "_pilot_covers_reviews", lambda _root: True)

        view = self._view(root, _prs(ids))

        assert {i["ref"]["pr"] for i in _items(view, wa.PR_REVIEW)} == {15}  # la PR 12 (task/<id>) est pilotée
        assert view["reviews"][0]["piloted"] == 1
        assert view["reviews"][0]["count"] == 1

    def test_pr_sans_tache_ou_tache_en_attente_humaine_reste_a_relire(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        service = TaskService(root)
        service.claim(ids["branch"], "agent-a", host="host-claude-code-cli")
        service.transition(ids["branch"], TaskState.RUNNING, "agent-a")
        service.ask(ids["branch"], "Fusionner ?", "agent-a")
        monkeypatch.setattr(wa, "_pilot_covers_reviews", lambda _root: True)
        prs = [*_prs(ids), _pr(20, "Bump dépendance", branch="dependabot/pip/x", checks=[_run("tests", "SUCCESS")])]

        view = self._view(root, prs)

        assert {i["ref"]["pr"] for i in _items(view, wa.PR_REVIEW)} == {12, 15, 20}
        assert "piloted" not in view["reviews"][0]

    def test_pr_pilotee_en_echec_de_ci_revient_a_l_humain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        monkeypatch.setattr(wa, "_pilot_covers_reviews", lambda _root: True)
        prs = [_pr(12, "Lot rouge", branch=f"task/{ids['branch']}", checks=[_run("tests", "FAILURE")])]

        view = self._view(root, prs)

        assert {i["ref"]["pr"] for i in _items(view, wa.PR_REVIEW)} == {12}

    def test_sans_mandat_la_liste_est_inchangee(self, tmp_path: Path) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)

        view = self._view(root, _prs(ids))

        assert {i["ref"]["pr"] for i in _items(view, wa.PR_REVIEW)} == {12, 15}
        assert "piloted" not in view["reviews"][0]


class TestAttention:
    def test_les_pr_ouvertes_avec_leur_ci_et_leur_tache(self, tmp_path: Path) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        reader = _Reader(_prs(ids))

        view = wa.attention_view(root, pr_reader=reader, review_cache=wr.ReviewCache(), pr_wait=5,
                                 service_probe=lambda *_: "online", watch=wa.ServiceWatch())

        review = next(s for s in view["sections"] if s["id"] == "review")
        assert review["label"] == "PR à revoir"
        rows = {i["ref"]["pr"]: i for i in _items(view, wa.PR_REVIEW)}
        assert set(rows) == {12, 15}  # le brouillon n'attend pas de revue
        green, failed = rows[12], rows[15]
        assert green["title"] == "#12 Lot vert"
        assert green["ref"]["checks"] | {"failing": []} == {
            "state": "passed", "passed": 2, "failed": 0, "skipped": 2, "pending": 0, "total": 4, "failing": [],
        }
        assert green["ref"]["linked_task"] == ids["branch"]
        assert green["reason"] == f"CI verte : 2 verts, 2 ignorés · tâche {ids['branch']}"
        assert failed["ref"]["checks"]["failed"] == 1
        assert failed["ref"]["checks"]["failing"][0]["name"] == "CodeQL"
        assert failed["ref"]["linked_task"] == ids["body"]
        assert failed["reason"].startswith("1 échec de CI : CodeQL")
        assert green["ref"]["url"] == f"https://github.com/{REPO}/pull/12"
        assert reader.calls == [REPO]
        assert view["counts"][wa.PR_REVIEW] == 2
        assert view["reviews"] == [{"project": view["projects"][0]["slug"], "state": "ok", "detail": "",
                                    "repo": REPO, "count": 2}]

    def test_gh_absent_est_un_etat_inconnu_jamais_une_liste_vide(self, tmp_path: Path) -> None:
        root = tmp_path / "atelier"
        _project(root)
        reader = _Reader(error=wr.ReviewReadError("gh absent : installe GitHub CLI pour lire les PR"))

        view = wa.attention_view(root, pr_reader=reader, review_cache=wr.ReviewCache(), pr_wait=5)

        assert not _items(view, wa.PR_REVIEW)
        probe = [i for i in _items(view, wa.PROBE_FAILED) if i["id"].endswith(":pr")]
        assert len(probe) == 1
        assert probe[0]["reason"] == "état des PR inconnu : gh absent : installe GitHub CLI pour lire les PR"
        assert view["reviews"][0]["state"] == wr.UNKNOWN

    def test_sans_remote_ni_depot_rien_n_est_lu(self, tmp_path: Path) -> None:
        bare, plain = tmp_path / "sans-remote", tmp_path / "pas-git"
        _project(bare, origin=None)
        plain.mkdir()
        reader = _Reader()

        for root in (bare, plain):
            view = wa.attention_view(root, pr_reader=reader, review_cache=wr.ReviewCache(), pr_wait=5)
            assert not _items(view, wa.PR_REVIEW)
            assert not [i for i in _items(view, wa.PROBE_FAILED) if i["id"].endswith(":pr")]
        assert reader.calls == []

    def test_lecture_en_cours_ne_bloque_pas_la_route(self, tmp_path: Path) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        gate = threading.Event()
        reader, cache = _Reader(_prs(ids), gate=gate), wr.ReviewCache()

        try:
            view = wa.attention_view(root, pr_reader=reader, review_cache=cache, pr_wait=0)
            assert view["reviews"][0]["state"] == wr.LOADING
            assert not _items(view, wa.PR_REVIEW)
        finally:
            gate.set()
        again = wa.attention_view(root, pr_reader=reader, review_cache=cache, pr_wait=5)
        assert len(_items(again, wa.PR_REVIEW)) == 2
        assert len(reader.calls) == 1

    def test_deux_projets_du_meme_depot_une_ligne_par_pr(self, tmp_path: Path) -> None:
        one, two = tmp_path / "un", tmp_path / "deux"
        ids = _project(one)
        _project(two)
        reg.save_registry([{"slug": "un", "name": "Un", "path": str(one)},
                           {"slug": "deux", "name": "Deux", "path": str(two)}])
        reader = _Reader(_prs(ids))

        view = wa.attention_view(one, portfolio=True, pr_reader=reader, review_cache=wr.ReviewCache(), pr_wait=5)

        rows = _items(view, wa.PR_REVIEW)
        assert sorted(i["ref"]["pr"] for i in rows) == [12, 15]
        assert {i["project"]["slug"] for i in rows} == {"un"}
        assert len(reader.calls) == 1


# ── La lecture : checks, lien de tâche, gh ──────────────────────────────────


class TestLecture:
    def test_checks_comptes(self) -> None:
        summary = wr.check_summary([
            _run("a", "SUCCESS"), _run("b", "SKIPPED"), _run("c", "NEUTRAL"),
            _run("d", "FAILURE", url="http://pas-https"), _run("e", "TIMED_OUT"), _run("f", status="IN_PROGRESS"),
            {"context": "statut", "state": "SUCCESS"}, {"context": "attente", "state": "PENDING"},
            {"context": "erreur", "state": "ERROR", "targetUrl": "https://ci.exemple/1"}, "pas un check",
        ])
        assert (summary["passed"], summary["skipped"], summary["failed"], summary["pending"]) == (2, 2, 3, 2)
        assert summary["state"] == "failed"
        assert [f["name"] for f in summary["failing"]] == ["d", "e", "erreur"]
        assert summary["failing"][0]["url"] == ""
        assert summary["failing"][2]["url"] == "https://ci.exemple/1"
        assert wr.check_summary(None)["state"] == "none"
        assert wr.check_summary([_run("a", status="QUEUED")])["state"] == "pending"
        assert wr.check_summary([_run("a", "SKIPPED")])["state"] == "passed"

    def test_tache_liee(self) -> None:
        ids = {"GAO-lot-a-001", "GAO-lot-b-002", "bootstrap"}
        assert wr.linked_task({"headRefName": "task/GAO-lot-a-001"}, ids) == "GAO-lot-a-001"
        assert wr.linked_task({"headRefName": "fix/x", "body": "Refs GAO-lot-b-002."}, ids) == "GAO-lot-b-002"
        assert wr.linked_task({"headRefName": "feat/GAO-lot-b-002-suite"}, ids) == ""
        assert wr.linked_task({"headRefName": "x", "title": "bootstrap du lot"}, ids) == ""
        assert wr.linked_task({"headRefName": "task/inconnue", "body": "GAO-autre-009"}, ids) == ""

    def test_raisons(self) -> None:
        def reason(checks: list[dict[str, Any]], task: dict[str, str] | None = None) -> str:
            return wr.review_reason({"checks": wr.check_summary(checks), "task": task})

        assert reason([_run("a", status="IN_PROGRESS"), _run("b", "SUCCESS")]) == \
            "CI en cours : 1 check sur 2 · aucune tâche liée"
        assert reason([]) == "aucun check rapporté · aucune tâche liée"
        assert reason([_run("a", "FAILURE"), _run("b", "FAILURE")], {"id": "T"}) == "2 échecs de CI : a, b · tâche T"
        assert reason([_run("a", "SUCCESS")]) == "CI verte : 1 vert · aucune tâche liée"

    @pytest.mark.parametrize("raw", ["", "pas du json", '{"a": 1}', "[1]"])
    def test_une_reponse_illisible_est_une_erreur(self, raw: str) -> None:
        with pytest.raises(wr.ReviewReadError):
            wr.parse_prs(raw)

    def test_lecteur_par_defaut_gh_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wr.shutil, "which", lambda _name: None)
        with pytest.raises(wr.ReviewReadError, match="gh absent"):
            wr.default_reader(REPO, Path.cwd())

    def test_lecteur_par_defaut_lecture_seule_et_bornee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            seen["argv"], seen["timeout"] = argv, kwargs["timeout"]
            return subprocess.CompletedProcess(argv, 0, '[{"number": 3}]', "")

        monkeypatch.setattr(wr.shutil, "which", lambda _name: "gh")
        monkeypatch.setattr(wr.subprocess, "run", run)
        assert wr.default_reader(REPO, Path.cwd()) == [{"number": 3}]
        assert seen["argv"][:5] == ["gh", "pr", "list", "-R", REPO]
        assert "--state" in seen["argv"] and seen["argv"][seen["argv"].index("--state") + 1] == "open"
        assert not {"merge", "comment", "review", "edit"} & set(seen["argv"])
        assert seen["timeout"] == wr.READ_TIMEOUT_S

    @pytest.mark.parametrize(
        ("outcome", "message"),
        [
            (subprocess.CompletedProcess([], 4, "", "To get started, run: gh auth login"), "non authentifié"),
            (subprocess.CompletedProcess([], 1, "", "HTTP 502"), "a échoué"),
            (subprocess.TimeoutExpired(["gh"], 15), "n'a pas répondu"),
            (OSError("introuvable"), "indisponible"),
        ],
    )
    def test_lecteur_par_defaut_echecs(self, monkeypatch: pytest.MonkeyPatch, outcome: Any, message: str) -> None:
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(wr.shutil, "which", lambda _name: "gh")
        monkeypatch.setattr(wr.subprocess, "run", run)
        with pytest.raises(wr.ReviewReadError, match=message):
            wr.default_reader(REPO, Path.cwd())


# ── Le cache : partagé, borné ───────────────────────────────────────────────


class TestCache:
    def test_une_lecture_en_vol_est_partagee(self) -> None:
        gate = threading.Event()
        reader, cache = _Reader([{"number": 1}], gate=gate), wr.ReviewCache()
        got: list[wr.ReviewRead] = []
        threads = [threading.Thread(target=lambda: got.append(cache.read(REPO, Path.cwd(), reader, wait=5)))
                   for _ in range(3)]
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(10)
        assert len(reader.calls) == 1
        assert [r.state for r in got] == [wr.OK] * 3
        assert cache.read(REPO, Path.cwd(), reader, wait=0).prs == ({"number": 1},)
        assert len(reader.calls) == 1

    def test_un_lecteur_casse_dit_inconnu(self) -> None:
        cache = wr.ReviewCache()
        boom = _Reader(error=RuntimeError("panne"))
        got = cache.read(REPO, Path.cwd(), boom, wait=5)
        assert (got.state, got.detail) == (wr.UNKNOWN, "lecture des PR en échec (RuntimeError)")

    def test_une_lecture_perimee_est_relue(self) -> None:
        reader, cache = _Reader([]), wr.ReviewCache(ttl=0)
        cache.read(REPO, Path.cwd(), reader, wait=5)
        cache.read(REPO, Path.cwd(), reader, wait=5)
        assert len(reader.calls) == 2


# ── L'écran de revue : GET /api/workspace/reviews ───────────────────────────


class TestRoute:
    def test_la_route_rend_les_plus_pretes_d_abord(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "atelier"
        ids = _project(root)
        monkeypatch.setattr(wr, "default_reader", _Reader(_prs(ids)))
        monkeypatch.setattr(wr, "_CACHE", wr.ReviewCache())
        assert f"{PREFIX}reviews" in GET_ROUTES

        view = workspace_get(root, f"{PREFIX}reviews", {})

        assert view["schemaVersion"] == wr.SCHEMA_VERSION
        assert view["state"] == wr.OK
        assert [pr["number"] for pr in view["prs"]] == [12, 15]
        green, failed = view["prs"]
        assert green["task"] == {"id": ids["branch"], "title": "Lot par branche", "status": "ready"}
        assert green["changelog"] is True
        assert [g["kind"] for g in green["files"]] == ["code", "tests", "docs"]
        assert green["overlaps"] == [15] and failed["overlaps"] == [12]
        assert failed["issues"] == [715]
        assert "paths" not in green
        assert view["sources"][0]["drafts"] == 1
        assert green["project"]["slug"] == view["sources"][0]["project"]["slug"]

    def test_la_route_dit_l_etat_inconnu_par_projet(self, tmp_path: Path) -> None:
        good, missing = tmp_path / "atelier", tmp_path / "disparu"
        _project(good)
        reg.save_registry([{"slug": "atelier", "name": "Atelier", "path": str(good)},
                           {"slug": "disparu", "name": "Disparu", "path": str(missing)}])
        reader = _Reader(error=wr.ReviewReadError("gh non authentifié : lance « gh auth login »"))

        view = wr.reviews_view(good, portfolio=True, reader=reader, cache=wr.ReviewCache(), wait=5)

        assert view["state"] == wr.UNKNOWN
        assert view["prs"] == []
        states = {s["project"]["slug"]: (s["state"], s["detail"]) for s in view["sources"]}
        assert states["atelier"] == (wr.UNKNOWN, "gh non authentifié : lance « gh auth login »")
        assert states["disparu"][0] == wr.UNKNOWN

    def test_politique_invalide_dit_inconnu(self, tmp_path: Path) -> None:
        root = tmp_path / "atelier"
        _project(root)
        policy = root / "_grimoire" / "standard" / "orchestration-policy.yaml"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("ci:\n  repository: pas un slug\n", encoding="utf-8")

        got = wr.project_reviews(root, reader=_Reader(), cache=wr.ReviewCache())

        assert got["state"] == wr.UNKNOWN
        assert "repository invalide" in got["detail"]
