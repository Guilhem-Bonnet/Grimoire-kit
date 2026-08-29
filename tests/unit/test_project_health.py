"""Ce que le portefeuille dit d'un projet doit venir du disque.

Trois affirmations, trois sources vérifiables : l'alignement kit vient des
digests de contenu, les flows des blueprints présents, l'activité des journaux
d'événements et du board. Aucune ne doit pouvoir répondre « oui » par défaut.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.tools import project_health as ph

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / "_grimoire").mkdir(parents=True)
    return root


# ── Alignement kit ───────────────────────────────────────────────────────────


def test_a_project_without_kit_files_is_not_declared_up_to_date(project: Path) -> None:
    """Sans rien à comparer, « à jour » serait une affirmation gratuite."""
    kit = ph.kit_alignment(project)
    assert kit["scaffolded"] is False
    assert kit["upToDate"] is False
    assert kit["aligned"] is None
    assert kit["installed"]


def test_project_written_files_are_not_counted_as_behind(project: Path) -> None:
    """Une personnalisation n'est pas un retard.

    Compter les fichiers inconnus du catalogue comme « en retard »
    transformerait chaque override en alerte permanente.
    """
    kit_dir = project / "_grimoire" / "kit"
    kit_dir.mkdir(parents=True)
    (kit_dir / "a-moi.md").write_text("écrit par le projet\n", encoding="utf-8")

    kit = ph.kit_alignment(project)
    assert kit["projectOwned"] == 1
    assert kit["behind"] == 0
    assert kit["upToDate"] is False, "aucun fichier reconnu : rien ne prouve l'alignement"


def test_a_superseded_revision_reads_as_behind(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """En retard = le kit connaît une révision plus récente du *même chemin*."""
    kit_dir = project / "_grimoire" / "kit"
    kit_dir.mkdir(parents=True)
    (kit_dir / "outil.py").write_text("ancienne révision\n", encoding="utf-8")

    ph._newest_version_by_path.cache_clear()
    monkeypatch.setattr(ph, "load_catalog", lambda: {
        "d-vieux": {"version": "3.10.0", "path": "framework/outil.py"},
        "d-neuf": {"version": "3.30.0", "path": "framework/outil.py"},
    })
    monkeypatch.setattr(
        ph, "shipped_by_kit", lambda _p: {"version": "3.10.0", "path": "framework/outil.py"}
    )

    kit = ph.kit_alignment(project)
    assert kit["behind"] == 1
    assert kit["upToDate"] is False
    assert "outil.py" in kit["behindFiles"][0]


def test_an_unchanged_file_is_not_behind_just_because_it_is_old(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le piège que la première version de ce module n'a pas vu.

    Un fichier inchangé depuis 3.32.0 est à jour dans un kit 3.34.2. Comparer
    au numéro de version installée déclarait « 37 fichiers en retard » sur un
    projet qu'on venait tout juste de mettre à jour.
    """
    kit_dir = project / "_grimoire" / "kit"
    kit_dir.mkdir(parents=True)
    (kit_dir / "stable.md").write_text("inchangé depuis longtemps\n", encoding="utf-8")

    ph._newest_version_by_path.cache_clear()
    monkeypatch.setattr(ph, "_installed_kit_version", lambda: "3.34.2")
    monkeypatch.setattr(ph, "load_catalog", lambda: {
        "d": {"version": "3.32.0", "path": "framework/stable.md"},
    })
    monkeypatch.setattr(
        ph, "shipped_by_kit", lambda _p: {"version": "3.32.0", "path": "framework/stable.md"}
    )

    kit = ph.kit_alignment(project)
    assert kit["behind"] == 0
    assert kit["upToDate"] is True
    assert kit["aligned"] == "3.32.0"


def test_version_ordering_survives_a_non_numeric_chunk() -> None:
    assert ph._version_key("3.34.2") > ph._version_key("3.9.0")
    assert ph._version_key("3.34.2rc1") >= ph._version_key("3.34.2")
    assert ph._version_key("") == (0,)


# ── Flows ────────────────────────────────────────────────────────────────────


def test_flows_are_the_blueprints_actually_present(project: Path) -> None:
    assert ph.flows(project) == []

    bp_dir = project / "_grimoire" / "blueprints"
    bp_dir.mkdir(parents=True)
    (bp_dir / "revue.blueprint.json").write_text(
        json.dumps({
            "id": "revue", "name": "Revue gouvernée",
            "nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"from": "a", "to": "b"}],
            "meta": {"validated": True, "compiledAt": 1234},
        }),
        encoding="utf-8",
    )
    (bp_dir / "casse.blueprint.json").write_text("{ pas du json", encoding="utf-8")

    found = ph.flows(project)
    assert [f["id"] for f in found] == ["revue"], "un blueprint illisible ne casse pas la liste"
    assert found[0]["nodes"] == 2
    assert found[0]["edges"] == 1
    assert found[0]["validated"] is True


# ── Activité ─────────────────────────────────────────────────────────────────


def _write_event(project: Path, stamp: str, **fields: object) -> None:
    log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, **fields}) + "\n")


def test_a_project_with_no_trace_is_not_active(project: Path) -> None:
    """L'absence de preuve n'est pas une preuve d'activité — ni l'inverse."""
    act = ph.activity(project)
    assert act["active"] is False
    assert act["lastEventAt"] is None
    assert act["ageMinutes"] is None


def test_a_fresh_trace_makes_the_project_active(project: Path) -> None:
    now = datetime.now(UTC)
    _write_event(project, (now - timedelta(minutes=2)).isoformat(), action="blueprint.compile")

    act = ph.activity(project, now=now)
    assert act["active"] is True
    assert act["ageMinutes"] == pytest.approx(2.0, abs=0.5)
    assert act["lastEventLabel"] == "blueprint.compile"
    assert act["lastEventSource"] == "hook-runtime"


def test_an_old_trace_does_not_make_the_project_active(project: Path) -> None:
    """Une session d'hier ne doit pas s'afficher comme un run en cours."""
    now = datetime.now(UTC)
    _write_event(project, (now - timedelta(hours=26)).isoformat(), action="task-finish")

    act = ph.activity(project, now=now)
    assert act["active"] is False
    assert act["ageMinutes"] > ph.ACTIVE_WINDOW_MINUTES
    assert act["lastEventAt"] is not None, "l'ancienneté se dit, elle ne se cache pas"


def test_an_unparsable_log_line_is_ignored_not_fatal(project: Path) -> None:
    log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("pas du json\n{\"ts\": \"pas une date\"}\n", encoding="utf-8")
    assert ph.activity(project)["lastEventAt"] is None


def test_in_flight_tasks_come_from_the_project_board(project: Path) -> None:
    """« Où il en est » est ce que le projet déclare, pas ce qu'on devine."""
    board = project / "_grimoire" / "standard"
    board.mkdir(parents=True)
    board.joinpath("task-board.yaml").write_text(
        "tasks:\n"
        "  - task_id: en-cours\n    title: Le sujet du moment\n    status: in_progress\n"
        "  - task_id: revue\n    title: En revue\n    status: review\n"
        "  - task_id: fini\n    title: Terminé\n    status: accepted\n",
        encoding="utf-8",
    )
    in_flight = ph.activity(project)["inFlight"]
    assert [t["id"] for t in in_flight] == ["en-cours", "revue"]
    assert in_flight[0]["title"] == "Le sujet du moment"


# ── Vue agrégée ──────────────────────────────────────────────────────────────


def test_project_health_reports_the_three_surfaces(project: Path) -> None:
    health = ph.project_health(project)
    assert set(health) == {"projectRoot", "kit", "flows", "activity"}
    assert health["projectRoot"] == str(project.resolve())


# ── L'atelier est une activité du projet ────────────────────────────────────


def test_work_done_in_the_atelier_counts_as_activity(project: Path) -> None:
    """Un projet qu'on manipule dans l'atelier ne doit pas afficher « aucune trace ».

    Les mutations servies sont journalisées dans un fichier que la télémétrie
    du runtime ne référence pas : sans lui, installer une extension ou aligner
    le projet ne se voyait nulle part.
    """
    ledger = project / "_grimoire-runtime-output" / "hook-runtime" / "serve-mutations.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ledger.write_text(
        json.dumps({"ts": now.isoformat(), "source": "serve", "action": "project.update"}) + "\n",
        encoding="utf-8",
    )

    act = ph.activity(project, now=now)
    assert act["active"] is True
    assert act["lastEventSource"] == "atelier"
    assert act["lastEventLabel"] == "project.update"


def test_a_huge_log_is_not_read_whole(project: Path) -> None:
    """Ces journaux ne font que grossir — 14 Mo sur cette machine, pour une
    seule ligne utile. On ne lit que la fin."""
    log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    filler = json.dumps({"ts": (now - timedelta(days=9)).isoformat(), "action": "vieux"})
    recent = json.dumps({"ts": (now - timedelta(minutes=3)).isoformat(), "action": "récent"})
    with log.open("w", encoding="utf-8") as f:
        for _ in range(20_000):
            f.write(filler + "\n")
        f.write(recent + "\n")
    assert log.stat().st_size > ph._TAIL_BYTES * 4, "le journal doit dépasser la fenêtre"

    act = ph.activity(project, now=now)
    assert act["lastEventLabel"] == "récent"
    assert act["active"] is True


def test_the_tail_window_drops_a_line_cut_in_half(project: Path) -> None:
    """La découpe en octets tombe au milieu d'une ligne : elle est écartée."""
    log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * (ph._TAIL_BYTES + 500) + "\n", encoding="utf-8")
    assert ph._tail_lines(log) == [], "la ligne tronquée ne doit pas être rendue"


def test_a_short_log_is_read_entirely(project: Path) -> None:
    log = project / "_grimoire-runtime-output" / "hook-runtime" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("une\ndeux\n", encoding="utf-8")
    assert ph._tail_lines(log) == ["une", "deux"], "rien ne doit être perdu sur un petit journal"


# ── Runs : le kit tenait déjà le registre, personne ne le lisait ────────────


def _kernel(project: Path) -> object:
    from grimoire.runtime.kernel import RuntimeKernel

    return RuntimeKernel(project / "_grimoire-runtime-output" / "runtime")


def _ctx(run_id: str) -> object:
    from grimoire.runtime.schemas import ExecutionContext

    return ExecutionContext(
        run_id=run_id, mission_id="m", task_id="t", workflow_instance_id="",
        actor_id="dev", host_id="poste", risk_profile="standard",
    )


def test_a_project_that_never_ran_has_no_runs(project: Path) -> None:
    assert ph.runs(project) == []
    assert ph.activity(project)["running"] is False


def test_a_checkpointed_run_reports_its_current_step(project: Path) -> None:
    """« Où il se trouve » est littéral : l'étape nommée par le dernier checkpoint."""
    kernel, ctx = _kernel(project), _ctx("run-A")
    wfi = kernel.create_instance(ctx, recipe_id="recipe.revue")
    kernel.start(wfi.id, ctx)
    kernel.checkpoint(wfi.id, ctx, step_id="analyse",
                      completed_steps=["lecture"], pending_steps=["revue", "preuve"])

    live = ph.runs(project)
    assert len(live) == 1
    assert live[0]["recipe"] == "recipe.revue"
    assert live[0]["step"] == "analyse"
    assert live[0]["completedSteps"] == 1
    assert live[0]["pendingSteps"] == 2
    assert live[0]["silent"] is False
    assert ph.activity(project)["running"] is True


def test_a_completed_run_leaves_the_in_flight_list(project: Path) -> None:
    kernel, ctx = _kernel(project), _ctx("run-A")
    wfi = kernel.create_instance(ctx, recipe_id="recipe.revue")
    kernel.start(wfi.id, ctx)
    assert ph.runs(project)
    kernel.complete(wfi.id, ctx)
    assert ph.runs(project) == []


def test_a_killed_run_stops_counting_as_running(project: Path) -> None:
    """Un processus tué n'écrit jamais son statut terminal.

    Son instance reste « running » indéfiniment : sans borne de fraîcheur, le
    portefeuille afficherait une exécution en cours pour toujours. Constaté en
    interrompant un vrai run.
    """
    kernel, ctx = _kernel(project), _ctx("run-B")
    wfi = kernel.create_instance(ctx, recipe_id="recipe.abandonnee")
    kernel.start(wfi.id, ctx)

    later = datetime.now(UTC) + timedelta(minutes=ph.RUN_SILENT_AFTER_MINUTES + 5)
    live = ph.runs(project, now=later)
    assert len(live) == 1, "le run reste visible : il n'a pas disparu, il s'est tu"
    assert live[0]["silent"] is True
    assert live[0]["silentForMinutes"] > ph.RUN_SILENT_AFTER_MINUTES
    assert ph.activity(project, now=later)["running"] is False


def test_instances_are_read_whole_not_by_the_tail(project: Path) -> None:
    """``instances.jsonl`` est réécrit intégralement à chaque sauvegarde.

    Le lire par la fin, comme un journal append-only, ferait disparaître des
    exécutions dès que le fichier dépasse la fenêtre.
    """
    runtime = project / "_grimoire-runtime-output" / "runtime"
    runtime.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    rows = [
        json.dumps({
            "id": f"wfi-{i}", "recipe_id": "r", "mission_id": "m", "task_id": "t",
            "status": "running", "created_at": now, "run_id": f"run-{i}",
            "x": "p" * 400,  # de quoi dépasser la fenêtre de queue
        })
        for i in range(400)
    ]
    (runtime / "instances.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert (runtime / "instances.jsonl").stat().st_size > ph._TAIL_BYTES

    assert len(ph.runs(project)) == 400
