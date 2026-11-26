"""Atelier de spec, lot 4 (#1197, #1342) : le document vivant (trace par exigence) et « Valider et découper ».

Un HOME par test : le journal d'autorité (validations de la MOA) est celui du test. La validation passe par
``approve_requirement`` avec le tour humain simulé, exactement comme les tests de ``grimoire cadrage approve`` ; aucune
route du cockpit ne la fait, et un test le prouve.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import cadrage_trace
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_trace as live

P = "/api/workspace/"
EXIGENCES = """# Exigences

- REQ-001 [Must] : dicter une idée au micro
  - la transcription apparaît en moins de 2 secondes
- REQ-002 (Should) : repli sur le navigateur
  - `pytest tests/test_repli.py`
- REQ-003 (Could) : historique des dictées
- REQ-004 (Won't) : traduction automatique
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def human(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le tour humain attesté au terminal, simulé (comme les tests de `cadrage approve`)."""
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    data = {"project": {"name": "p"}, "governance": {"roles": {"moa": "Ada"}, "requirement_gate": "warn"}}
    (root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    wa.set_atelier(root, True)
    return root


def _chantier(root: Path, text: str = EXIGENCES) -> str:
    made = new_chantier(root, "Dicter une idée")
    (root / "_grimoire" / "cadrage" / made.slug / "04-exigences.md").write_text(text, encoding="utf-8")
    return made.slug


def _approve(root: Path, slug: str, req: str) -> None:
    rg.approve_requirement(root, f"{slug}/{req}", actor="ada")


def _get(root: Path, slug: str, leaf: str) -> dict[str, Any]:
    return dict(wa.get(root, f"{P}atelier/chantiers/{slug}/{leaf}", {}))


def _post(root: Path, slug: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, f"{P}atelier/chantiers/{slug}/decoupage", body)


def _tasks(root: Path) -> list[Any]:
    return TaskService(root).list_tasks()


def _row(view: dict[str, Any], req: str) -> dict[str, Any]:
    return dict(next(r for r in view["requirements"] if r["id"] == req))


# ── drapeau ─────────────────────────────────────────────────────────────────


def test_flag_off_every_lot4_route_is_404(project: Path) -> None:
    slug = _chantier(project)
    wa.set_atelier(project, False)
    for leaf in ("trace", "decoupage"):
        with pytest.raises(FileNotFoundError):
            _get(project, slug, leaf)
    assert _post(project, slug, {"confirm": True, "digest": "x"})[0] == 404
    assert _tasks(project) == []


def test_unknown_chantier_and_slug_are_404(project: Path) -> None:
    for slug in ("inconnu", "..", "a/b"):
        code, _ = wa.post_http(project, f"{P}atelier/chantiers/{slug}/decoupage", {"confirm": True, "digest": "x"})
        assert code == 404
    with pytest.raises(FileNotFoundError):
        _get(project, "inconnu", "trace")


# ── lecture : la trace ──────────────────────────────────────────────────────


def test_trace_gives_each_requirement_its_state_tasks_gaps_and_command(project: Path, human: None) -> None:
    slug = _chantier(project)
    view = _get(project, slug, "trace")
    assert view["schema"] == live.SCHEMA and view["moa"] == {"declared": True, "name": "Ada", "problem": ""}
    assert [r["id"] for r in view["requirements"]] == ["REQ-001", "REQ-002", "REQ-003"]  # le Won't n'est pas planifié
    first = _row(view, "REQ-001")
    assert (first["state"], first["label"], first["validated"], first["tasks"]) == ("proposee", "proposée", False, [])
    assert first["command"] == f"grimoire cadrage approve REQ-001 --chantier {slug}"
    assert first["revalidate"] is False and first["drift"] is None and first["files"] == 0
    assert any(g["kind"] == "requirement-without-criterion" for g in _row(view, "REQ-003")["gaps"])
    assert view["summary"] == {
        "requirements": 3, "must": 1, "must_validated": 0, "gaps": 1, "modified_since_validation": [],
    }

    _approve(project, slug, "REQ-001")
    view = _get(project, slug, "trace")
    row = _row(view, "REQ-001")
    assert (row["state"], row["validated"], row["validated_by"], row["command"]) == ("validee", True, "Ada", "")
    assert [g["kind"] for g in row["gaps"]] == ["validated-without-task"]
    assert view["summary"]["must_validated"] == 1

    service = TaskService(project)
    added = service.add("Dictée", ("ça marche",), requirement=f"{slug}/REQ-001")
    row = _row(_get(project, slug, "trace"), "REQ-001")
    assert [(t["id"], t["status"]) for t in row["tasks"]] == [(added.task.id, "proposed")]
    assert row["gaps"] == [] and row["state"] == "validee"


def test_trace_reports_an_edited_requirement_as_modified_since_validation(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8").replace("moins de 2 secondes", "moins d'une seconde"), encoding="utf-8")
    view = _get(project, slug, "trace")
    row = _row(view, "REQ-001")
    assert (row["state"], row["revalidate"], row["validated"]) == ("proposee", True, False)
    assert row["drift"]["validated_by"] == "Ada" and "critères" in row["drift"]["what"]
    assert row["command"].startswith("grimoire cadrage approve REQ-001")
    assert view["summary"]["modified_since_validation"] == ["REQ-001"]


def test_default_chantier_command_has_no_chantier_option(project: Path) -> None:
    assert live.approve_command("REQ-007", "principal") == "grimoire cadrage approve REQ-007"
    assert live.approve_command("mon-chantier/REQ-007", "mon-chantier") == (
        "grimoire cadrage approve REQ-007 --chantier mon-chantier"
    )
    cadrage = project / "_grimoire" / "cadrage"
    cadrage.mkdir(parents=True, exist_ok=True)
    (cadrage / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    view = _get(project, "principal", "trace")
    assert _row(view, "REQ-001")["command"] == "grimoire cadrage approve REQ-001"


def test_trace_is_built_once_per_request(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _chantier(project)
    calls: list[str] = []
    real = cadrage_trace.build_trace

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append("build")
        return real(*args, **kwargs)

    monkeypatch.setattr(cadrage_trace, "build_trace", counting)
    _get(project, slug, "trace")
    assert calls == ["build"]


def test_trace_with_an_unreadable_journal_says_so_instead_of_failing(project: Path) -> None:
    slug = _chantier(project)
    journal = AuthorityJournal()
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text("pas du json\n", encoding="utf-8")
    view = _get(project, slug, "trace")
    assert any("journal" in n for n in view["notes"]) and not _row(view, "REQ-001")["validated"]


def test_chantier_without_requirements_has_an_empty_trace(project: Path) -> None:
    made = new_chantier(project, "Vide")
    view = _get(project, made.slug, "trace")
    assert view["requirements"] == [] and view["summary"]["requirements"] == 0
    preview = _get(project, made.slug, "decoupage")
    assert preview["ready"] is False and preview["create"] == []
    code, body = _post(project, made.slug, {"confirm": True, "digest": "x"})
    assert code == 400 and "aucune exigence" in body["error"]


# ── découpage : aperçu et écriture ──────────────────────────────────────────


def test_preview_is_read_only_and_names_creations_existing_and_skipped(project: Path, human: None) -> None:
    slug = _chantier(project)
    before = _tasks(project)
    preview = _get(project, slug, "decoupage")
    assert preview["ready"] is False
    assert preview["blocked_by"][0]["id"] == "REQ-001" and "non validée" in preview["blocked_by"][0]["reason"]
    _approve(project, slug, "REQ-001")
    TaskService(project).add("Déjà là", ("ok",), extra={"cadrage": "plan", "covers": [f"{slug}/REQ-002"]})
    preview = _get(project, slug, "decoupage")
    assert preview["ready"] is True and preview["blocked_by"] == [] and preview["applied"] is False
    assert [c["requirement"] for c in preview["create"]] == ["REQ-001"]
    assert preview["create"][0]["criteria"] == ["la transcription apparaît en moins de 2 secondes"]
    assert preview["existing"][0]["requirement"] == "REQ-002"
    skipped = {s["id"]: s["why"] for s in preview["skipped"]}
    assert set(skipped) == {"REQ-003", "REQ-004"}
    assert "sans critère" in skipped["REQ-003"] and "hors périmètre" in skipped["REQ-004"]
    assert any(q["requirement"] == "REQ-003" and q["blocking"] for q in preview["questions"])
    assert len(_tasks(project)) == len(before) + 1  # seulement celle que le test a ajoutée soi-même


def test_apply_requires_confirmation_digest_and_validated_musts(project: Path, human: None) -> None:
    slug = _chantier(project)
    assert _post(project, slug, {})[0] == 400
    assert _post(project, slug, {"confirm": "oui", "digest": "x"})[0] == 400
    assert _post(project, slug, {"confirm": True})[0] == 400
    digest = _get(project, slug, "decoupage")["digest"]
    code, body = _post(project, slug, {"confirm": True, "digest": digest})
    assert code == 409 and body["reason"] == "exigences-non-validees" and "REQ-001" in body["error"]
    assert _tasks(project) == []


def test_apply_creates_one_task_per_requirement_in_the_state_plan_gives_and_replays_without_duplicates(
    project: Path, human: None
) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    digest = _get(project, slug, "decoupage")["digest"]
    code, body = _post(project, slug, {"confirm": True, "digest": digest})
    assert code == 200 and body["already"] is False
    assert sorted(c["requirement"] for c in body["created"]) == ["REQ-001", "REQ-002"]
    tasks = [t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "plan"]
    assert len(tasks) == 2
    assert all(t.status.value in ("proposed", "ready") and t.claim is None for t in tasks)  # aucun travail lancé
    by_req = {(t.extra or {})["requirement"]: t for t in tasks}
    assert by_req[f"{slug}/REQ-001"].acceptance == ("la transcription apparaît en moins de 2 secondes",)
    assert by_req[f"{slug}/REQ-001"].priority == "high"
    # Rejouer : l'ancienne empreinte (un autre onglet) est périmée, rien n'est créé ; l'aperçu à jour rend « déjà fait ».
    code, body = _post(project, slug, {"confirm": True, "digest": digest})
    assert code == 409 and body["reason"] == "apercu-perime"
    code, body = _post(project, slug, {"confirm": True, "digest": _get(project, slug, "decoupage")["digest"]})
    assert code == 200 and body["already"] is True and body["created"] == []
    assert len([t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "plan"]) == 2
    assert _row(_get(project, slug, "trace"), "REQ-001")["tasks"][0]["id"] == by_req[f"{slug}/REQ-001"].id


def test_apply_with_an_outdated_preview_is_409_and_writes_nothing(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    digest = _get(project, slug, "decoupage")["digest"]
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8") + "- REQ-005 (Should) : nouvelle\n  - `pytest tests/x.py`\n", encoding="utf-8")
    code, body = _post(project, slug, {"confirm": True, "digest": digest})
    assert code == 409 and body["reason"] == "apercu-perime"
    assert [t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "plan"] == []


def test_two_simultaneous_confirmations_create_each_task_once(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    digest = _get(project, slug, "decoupage")["digest"]
    codes: list[int] = []

    def run() -> None:
        codes.append(_post(project, slug, {"confirm": True, "digest": digest})[0])

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(codes) == [200, 409]  # le second voit un plan déjà écrit : son empreinte est périmée
    assert len([t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "plan"]) == 2


def test_apply_blocked_when_a_must_changed_after_its_validation(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8").replace("2 secondes", "3 secondes"), encoding="utf-8")
    preview = _get(project, slug, "decoupage")
    assert preview["ready"] is False and "modifiée depuis sa validation" in preview["blocked_by"][0]["reason"]
    assert _post(project, slug, {"confirm": True, "digest": preview["digest"]})[0] == 409


def test_without_a_declared_moa_nothing_is_ready(project: Path) -> None:
    slug = _chantier(project)
    (project / "project-context.yaml").write_text(yaml.safe_dump({"project": {"name": "p"}}), encoding="utf-8")
    wa.set_atelier(project, True)
    preview = _get(project, slug, "decoupage")
    assert preview["ready"] is False and "aucune MOA" in preview["blocked_by"][0]["reason"]
    assert _get(project, slug, "trace")["moa"]["declared"] is False


def test_without_any_must_the_découpage_is_ready_and_a_questions_only_plan_is_idempotent(project: Path) -> None:
    slug = _chantier(project, "# Exigences\n\n- REQ-001 (Could) : une idée\n")
    preview = _get(project, slug, "decoupage")
    assert preview["ready"] is True and preview["create"] == [] and preview["questions"]
    first = _post(project, slug, {"confirm": True, "digest": preview["digest"]})
    assert _post(project, slug, {"confirm": True, "digest": "forgé"})[0] == 409  # même sans tâche : toujours contrôlée
    second = _post(project, slug, {"confirm": True, "digest": _get(project, slug, "decoupage")["digest"]})
    assert first[0] == second[0] == 200 and first[1]["created"] == [] == second[1]["created"]
    asked = [t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "questions"]
    assert len(asked) == 1  # l'ancre des questions n'est pas dupliquée


# ── l'autorité : un agent ne valide pas ─────────────────────────────────────


def test_there_is_no_validation_route_and_an_agent_cannot_validate(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _chantier(project)
    journal = AuthorityJournal()
    for leaf in ("exigences/REQ-001/valider", "exigences/REQ-001", "validation", "valider", "approve", "trace/valider"):
        for body in ({}, {"origin": "human", "confirm": True}):
            code, _ = wa.post_http(project, f"{P}atelier/chantiers/{slug}/{leaf}", body)
            assert code == 404, leaf
    assert not journal.path.exists()  # rien n'a été scellé

    # Le chemin du kit refuse un agent, avant toute lecture, puis sans tour humain attesté.
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    with pytest.raises(rg.RequirementRefusedError, match="exécution déclarée sans humain"):
        rg.approve_requirement(project, f"{slug}/REQ-001", actor="agent")
    monkeypatch.delenv("GRIMOIRE_UNATTENDED")
    with pytest.raises(rg.RequirementRefusedError) as refused:  # sous pytest : pas de terminal interactif
        rg.approve_requirement(project, f"{slug}/REQ-001", actor="agent")
    assert refused.value.args and "tour humain non attesté" in str(refused.value)
    assert not journal.path.exists()
    assert _row(_get(project, slug, "trace"), "REQ-001")["validated"] is False


def test_the_mcp_server_offers_the_agent_no_way_to_validate_or_split() -> None:
    from grimoire.mcp import atelier_tools

    names: list[str] = []

    def tool(_decorator: Any) -> Any:
        def register(function: Any) -> Any:
            names.append(function.__name__)
            return function

        return register

    atelier_tools.register(tool, lambda **_: None)
    assert names == ["atelier_suggest"]
    assert not hasattr(atelier_tools, "approve") and not hasattr(live, "approve_requirement")


def test_a_task_in_progress_moves_the_requirement_to_en_cours(project: Path, human: None) -> None:
    from grimoire.missions.schemas import TaskState

    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    service = TaskService(project)
    task = service.add("Dictée", ("ça marche",), requirement=f"{slug}/REQ-001", owner="ada").task
    service.transition(task.id, TaskState.READY, "ada")
    service.claim(task.id, "ada")
    row = _row(_get(project, slug, "trace"), "REQ-001")
    assert (row["state"], row["label"]) == ("en-cours", "en cours")
    assert row["tasks"][0]["status"] == "claimed"


def test_preview_says_when_a_hand_made_task_is_linked_but_not_counted_as_coverage(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    TaskService(project).add("À la main", ("ok",), requirement=f"{slug}/REQ-001")
    create = {c["requirement"]: c for c in _get(project, slug, "decoupage")["create"]}
    assert len(create["REQ-001"]["already_linked"]) == 1 and create["REQ-002"]["already_linked"] == []


def test_the_default_chantier_links_do_not_pick_other_chantiers_tasks(project: Path, human: None) -> None:
    cadrage = project / "_grimoire" / "cadrage"
    cadrage.mkdir(parents=True, exist_ok=True)
    (cadrage / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    TaskService(project).add("Ailleurs", ("ok",), requirement="autre/REQ-001")
    create = {c["requirement"]: c for c in _get(project, "principal", "decoupage")["create"]}
    assert create["REQ-001"]["already_linked"] == []


def test_criteria_rewritten_after_the_preview_are_not_created_unseen(project: Path) -> None:
    text = "# Exigences\n\n- REQ-002 (Should) : repli navigateur\n  - le repli marche hors ligne\n"
    slug = _chantier(project, text)
    preview = _get(project, slug, "decoupage")
    assert preview["create"][0]["criteria"] == ["le repli marche hors ligne"]  # le texte est dans l'aperçu
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(text.replace("hors ligne", "sans revue"), encoding="utf-8")
    code, body = _post(project, slug, {"confirm": True, "digest": preview["digest"]})
    assert code == 409 and body["reason"] == "apercu-perime"
    assert _tasks(project) == []
    # La priorité ou les questions qui seraient posées comptent aussi dans l'empreinte.
    path.write_text(text.replace("(Should)", "(Could)"), encoding="utf-8")
    assert _get(project, slug, "decoupage")["digest"] != preview["digest"]


def _worker(root: str, slug: str, digest: str, barrier: Any, queue: Any, cli: bool) -> None:
    from grimoire.missions.cadrage_plan import apply_locked, build_plan

    barrier.wait()
    path = Path(root)
    if cli:  # le chemin de `grimoire cadrage plan --yes`
        service = TaskService(path)
        plan = build_plan(path, service, slug, "")
        queue.put(len(apply_locked(path, service, plan, "cli").proposals))
    else:
        queue.put(wa.post_http(path, f"{P}atelier/chantiers/{slug}/decoupage", {"confirm": True, "digest": digest})[0])


def test_two_processes_split_the_same_chantier_without_duplicates(project: Path) -> None:
    import multiprocessing

    lines = ["# Exigences", ""] + [f"- REQ-{i:03d} (Should) : e{i}\n  - c{i}" for i in range(1, 21)]
    slug = _chantier(project, "\n".join(lines) + "\n")
    digest = _get(project, slug, "decoupage")["digest"]
    ctx = multiprocessing.get_context("fork")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(str(project), slug, digest, barrier, queue, cli)) for cli in (False, True)
    ]
    for proc in procs:
        proc.start()
    results = [queue.get(timeout=60) for _ in procs]
    for proc in procs:
        proc.join(30)
    planned = [t for t in _tasks(project) if (t.extra or {}).get("cadrage") == "plan"]
    assert len(planned) == 20 and len({(t.extra or {})["requirement"] for t in planned}) == 20, results


def test_a_held_lock_answers_503_and_writes_nothing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import cadrage_plan
    from grimoire.missions.cadrage_plan import plan_lock

    slug = _chantier(project)
    digest = _get(project, slug, "decoupage")["digest"]
    monkeypatch.setattr(cadrage_plan, "LOCK_TIMEOUT_S", 0.2)
    with plan_lock(project, slug, timeout=5):
        code, body = _post(project, slug, {"confirm": True, "digest": digest})
    assert code == 503 and "verrouillé" in body["error"]
    assert _tasks(project) == []


def test_the_trace_is_kept_while_nothing_changes_and_rebuilt_when_it_does(
    project: Path, human: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _chantier(project)
    calls: list[int] = []
    real = cadrage_trace.build_trace
    monkeypatch.setattr(cadrage_trace, "build_trace", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    _get(project, slug, "trace")
    _get(project, slug, "trace")
    assert len(calls) == 1  # rien n'a changé : une seule construction
    _approve(project, slug, "REQ-001")  # une validation (journal) : reconstruite aussitôt
    assert _row(_get(project, slug, "trace"), "REQ-001")["validated"] is True and len(calls) == 2
    TaskService(project).add("Dictée", ("ok",), requirement=f"{slug}/REQ-001")  # le ledger bouge
    assert _row(_get(project, slug, "trace"), "REQ-001")["task_count"] == 1 and len(calls) == 3
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8").replace("2 secondes", "3 secondes"), encoding="utf-8")
    assert _row(_get(project, slug, "trace"), "REQ-001")["revalidate"] is True and len(calls) == 4
    monkeypatch.setattr(live, "CACHE_TTL_S", 0.0)  # au-delà du délai, relue même sans changement
    _get(project, slug, "trace")
    assert len(calls) == 5


def test_long_chantiers_get_lean_rows_and_the_detail_on_demand(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "DETAIL_MAX", 2)
    slug = _chantier(project, "# Exigences\n\n" + "".join(f"- REQ-{i:03d} (Should) : e{i}\n  - c{i}\n" for i in range(1, 5)))
    TaskService(project).add("Une", ("ok",), requirement=f"{slug}/REQ-003")
    view = _get(project, slug, "trace")
    lean = _row(view, "REQ-003")
    assert lean["detail"] is False and lean["tasks"] == [] and lean["task_count"] == 1
    full = dict(wa.get(project, f"{P}atelier/chantiers/{slug}/trace", {"req": ["REQ-003"]}))
    assert _row(full, "REQ-003")["detail"] is True and len(_row(full, "REQ-003")["tasks"]) == 1
    assert _row(full, "REQ-004")["detail"] is False


def test_requirement_ids_are_ascii_digits_only(project: Path) -> None:
    text = "# Exigences\n\n- REQ-001 (Should) : vrai\n  - c\n- REQ-００２ (Should) : sosie\n  - c\n- REQ-٣ (Should) : arabe\n  - c\n"
    slug = _chantier(project, text)
    view = _get(project, slug, "trace")
    assert [r["id"] for r in view["requirements"]] == ["REQ-001"]
    from grimoire.core.cadrage_requirements import REQ_ID

    assert REQ_ID.search("REQ-００２") is None and REQ_ID.search("REQ-٣") is None and REQ_ID.search("REQ-002")


def test_no_other_method_or_path_validates_or_splits(project: Path) -> None:
    slug = _chantier(project)
    base = f"{P}atelier/chantiers/{slug}"
    body = json.dumps({"origin": "human", "confirm": True, "digest": "x"}).encode()
    for leaf in ("decoupage", "trace", "exigences/REQ-001", "exigences/REQ-001/valider", "valider", "approve"):
        assert wa.put_http(project, f"{base}/{leaf}", body)[0] in (404, 400), leaf
    for leaf in ("trace", "exigences/REQ-001/approve", "exigences", "validations", "REQ-001/valider"):
        assert wa.post_http(project, f"{base}/{leaf}", {"confirm": True})[0] == 404, leaf
    assert not AuthorityJournal().path.exists() and _tasks_or_none(project) == []


def _tasks_or_none(root: Path) -> list[Any]:
    service = TaskService(root)
    return service.list_tasks() if service.has_ledger else []


def _cli_plan(root: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    return CliRunner().invoke(app, ["cadrage", "plan", str(root), *args])


def test_cli_writes_nothing_when_the_cadrage_changes_between_the_plan_and_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.core.cadrage import CADRAGE_DIR, scaffold
    from grimoire.missions import cadrage_plan as cp

    root = tmp_path / "cli"
    scaffold(root, project_name="demo")
    path = root / CADRAGE_DIR / "04-exigences.md"
    path.write_text("# Exigences\n\n- REQ-002 (Should) : repli\n  - le repli marche hors ligne\n", encoding="utf-8")
    real, calls = cp.build_plan, []

    def racing(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:  # la reconstruction sous verrou : le fichier a changé pendant l'invite
            path.write_text(path.read_text(encoding="utf-8").replace("hors ligne", "sans revue"), encoding="utf-8")
        return real(*args, **kwargs)

    monkeypatch.setattr(cp, "build_plan", racing)
    result = _cli_plan(root, "--yes")
    assert result.exit_code != 0 and "a changé depuis l'aperçu" in result.output and "rien n'est écrit" in result.output
    assert not TaskService(root).has_ledger or TaskService(root).list_tasks() == []


def test_cli_nominal_plan_still_writes_what_it_showed(tmp_path: Path) -> None:
    from grimoire.core.cadrage import CADRAGE_DIR, scaffold

    root = tmp_path / "cli"
    scaffold(root, project_name="demo")
    (root / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    result = _cli_plan(root, "--yes")
    assert "tâche(s) créée(s)" in result.output
    planned = [t for t in TaskService(root).list_tasks() if (t.extra or {}).get("cadrage") == "plan"]
    assert len(planned) == 2 and "le fichier s'ouvre" not in result.output


def test_apply_locked_refuses_a_changed_plan_and_cockpit_shares_the_digest(project: Path) -> None:
    from grimoire.missions.cadrage_plan import PlanChangedError, apply_locked, build_plan, plan_digest

    slug = _chantier(project)
    service = TaskService(project)
    shown = build_plan(project, service, slug, "")
    assert live._digest(shown) == plan_digest(shown) == _get(project, slug, "decoupage")["digest"]
    path = project / "_grimoire" / "cadrage" / slug / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8").replace("2 secondes", "9 secondes"), encoding="utf-8")
    with pytest.raises(PlanChangedError):
        apply_locked(project, service, shown, "cli")
    assert _tasks_or_none(project) == []


def test_the_trace_payload_is_json_serialisable(project: Path, human: None) -> None:
    slug = _chantier(project)
    _approve(project, slug, "REQ-001")
    for view in (_get(project, slug, "trace"), _get(project, slug, "decoupage")):
        assert json.loads(json.dumps(view)) == view
