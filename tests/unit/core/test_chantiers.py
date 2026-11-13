"""Lot 0 de l'Atelier de spec (#1200) — plusieurs chantiers par projet, sans rien changer au cadrage existant."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.core.cadrage import CADRAGE_DIR, cadrage_dir, scaffold, status
from grimoire.core.chantiers import (
    link_mission,
    list_chantiers,
    new_chantier,
    reachable_states,
    set_state,
    slugify,
)
from grimoire.core.exceptions import GrimoireValidationError
from grimoire.missions.cadrage_plan import apply_plan, build_plan, covers_of
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage plan")

runner = CliRunner()

EXIGENCES = """## Fonctionnelles

- REQ-001 [Must] : {titre}
  - `pytest tests/test_{n}.py`
"""


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
                "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX"):
        monkeypatch.delenv(var, raising=False)


def _exigences(root: Path, chantier: str | None, titre: str, n: str) -> None:
    (cadrage_dir(root, chantier) / "04-exigences.md").write_text(
        EXIGENCES.format(titre=titre, n=n), encoding="utf-8", newline="\n",
    )


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ── modèle ───────────────────────────────────────────────────────────────────


def test_slugify_is_ascii_lowercase_and_bounded() -> None:
    assert slugify("Atelier de spec — Été !") == "atelier-de-spec-ete"
    assert len(slugify("x" * 100)) == 48


def test_new_chantier_writes_five_templates_and_chantier_yaml(tmp_path: Path) -> None:
    c = new_chantier(tmp_path, "Atelier de spec", mission="M-1")
    assert (c.slug, c.etat, c.mission, c.progress) == ("atelier-de-spec", "idee", "M-1", "0/5")
    directory = tmp_path / CADRAGE_DIR / "atelier-de-spec"
    assert len(list(directory.glob("0*.md"))) == 5 and (directory / "chantier.yaml").is_file()
    assert c.cree and c.cree == c.modifie


def test_reserved_duplicate_and_invalid_slugs_are_refused(tmp_path: Path) -> None:
    with pytest.raises(GrimoireValidationError, match="réservé"):
        new_chantier(tmp_path, "x", slug="principal")
    new_chantier(tmp_path, "Un chantier")
    with pytest.raises(GrimoireValidationError, match="existe déjà"):
        new_chantier(tmp_path, "Un chantier")  # jamais « un-chantier-2 » en silence
    for bad in ("Majuscule", "../evil", "a" * 49, "é"):
        with pytest.raises(GrimoireValidationError, match="slug invalide"):
            new_chantier(tmp_path, "t", slug=bad)
    with pytest.raises(GrimoireValidationError, match="titre"):
        new_chantier(tmp_path, "  ")


def test_allowed_and_refused_transitions(tmp_path: Path) -> None:
    new_chantier(tmp_path, "A")
    assert set_state(tmp_path, "a", "valide").etat == "valide"  # vers l'avant, en sautant des états
    with pytest.raises(GrimoireValidationError, match="états atteignables depuis valide : en-cours, livre, archive"):
        set_state(tmp_path, "a", "idee")
    new_chantier(tmp_path, "B")
    set_state(tmp_path, "b", "pret-a-valider")
    assert set_state(tmp_path, "b", "en-discussion").etat == "en-discussion"  # le seul recul permis
    assert set_state(tmp_path, "b", "archive").etat == "archive"  # archive depuis n'importe où
    with pytest.raises(GrimoireValidationError, match="aucun"):
        set_state(tmp_path, "b", "idee")
    with pytest.raises(GrimoireValidationError, match="état inconnu"):
        set_state(tmp_path, "a", "nimporte")
    with pytest.raises(GrimoireValidationError, match="chantier inconnu"):
        set_state(tmp_path, "absent", "valide")
    assert reachable_states("livre") == ("archive",) and reachable_states("archive") == ()


def test_link_mission(tmp_path: Path) -> None:
    new_chantier(tmp_path, "A")
    assert link_mission(tmp_path, "a", "M-7").mission == "M-7"
    assert link_mission(tmp_path, "a", "").mission == ""


def test_list_orders_by_modification_and_survives_a_corrupt_yaml(tmp_path: Path) -> None:
    assert list_chantiers(tmp_path) == []
    new_chantier(tmp_path, "Ancien")
    new_chantier(tmp_path, "Récent")
    new_chantier(tmp_path, "Cassé")
    base = tmp_path / CADRAGE_DIR
    (base / "casse" / "chantier.yaml").write_text("slug: [oups\n", encoding="utf-8")
    import os

    os.utime(base / "ancien" / "chantier.yaml", (1_000, 1_000))
    os.utime(base / "recent" / "chantier.yaml", (2_000, 2_000))
    os.utime(base / "casse" / "chantier.yaml", (500, 500))
    listed = list_chantiers(tmp_path)
    assert [c.slug for c in listed] == ["recent", "ancien", "casse"]
    assert listed[2].error and "illisible" in listed[2].error
    with pytest.raises(GrimoireValidationError, match="illisible"):
        set_state(tmp_path, "casse", "valide")


def test_default_chantier_is_read_from_files_without_writing(tmp_path: Path) -> None:
    assert status(tmp_path)["initialized"] is False
    scaffold(tmp_path, project_name="demo")
    before = _fingerprint(tmp_path)
    [default] = list_chantiers(tmp_path)
    assert (default.slug, default.etat, default.default) == ("principal", "idee", True)
    (tmp_path / CADRAGE_DIR / "01-brief.md").write_text(
        (tmp_path / CADRAGE_DIR / "01-brief.md").read_text(encoding="utf-8").replace(
            "> À compléter — le problème.", "Un vrai problème."), encoding="utf-8")
    assert list_chantiers(tmp_path)[0].etat == "en-discussion"
    assert not (tmp_path / CADRAGE_DIR / "chantier.yaml").exists()
    assert before.keys() == _fingerprint(tmp_path).keys()
    # Action explicite : c'est elle qui crée le chantier.yaml du défaut.
    assert set_state(tmp_path, "principal", "pret-a-valider").etat == "pret-a-valider"
    assert (tmp_path / CADRAGE_DIR / "chantier.yaml").is_file()
    assert list_chantiers(tmp_path)[0].etat == "pret-a-valider"


def test_the_default_chantier_state_needs_an_initialised_cadrage(tmp_path: Path) -> None:
    with pytest.raises(GrimoireValidationError, match="non initialisé"):
        set_state(tmp_path, "principal", "valide")


def test_cadrage_dir_and_scoped_status(tmp_path: Path) -> None:
    assert cadrage_dir(tmp_path) == cadrage_dir(tmp_path, "principal") == tmp_path / CADRAGE_DIR
    assert cadrage_dir(tmp_path, "a") == tmp_path / CADRAGE_DIR / "a"
    with pytest.raises(ValueError, match="invalide"):
        cadrage_dir(tmp_path, "../x")
    new_chantier(tmp_path, "A")
    assert status(tmp_path, "a")["initialized"] and not status(tmp_path)["initialized"]


# ── le plan : deux chantiers, un REQ-001 chacun ──────────────────────────────


def test_two_chantiers_with_the_same_requirement_id_give_two_distinct_tasks(tmp_path: Path) -> None:
    new_chantier(tmp_path, "Alpha")
    new_chantier(tmp_path, "Beta")
    _exigences(tmp_path, "alpha", "alpha se connecte", "alpha")
    _exigences(tmp_path, "beta", "beta exporte", "beta")
    service = TaskService(tmp_path)
    for slug in ("alpha", "beta"):
        plan = apply_plan(service, build_plan(tmp_path, service, slug), "cli")
        assert [p.requirement for p in plan.proposals] == ["REQ-001"]
    covers = sorted(c for t in service.list_tasks() for c in covers_of(t))
    assert covers == ["alpha/REQ-001", "beta/REQ-001"]
    # Idempotent et sans fusion : chacun reste couvert par sa propre tâche.
    again = build_plan(tmp_path, service, "alpha")
    assert again.proposals == [] and list(again.covered) == ["REQ-001"] and again.orphans == []
    assert build_plan(tmp_path, service, "beta").proposals == []


def test_plan_carries_the_chantier_mission(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    mission = service.open_mission("Atelier").id
    other = service.open_mission("Autre").id
    new_chantier(tmp_path, "Alpha", mission=other)
    _exigences(tmp_path, "alpha", "alpha", "alpha")
    apply_plan(service, build_plan(tmp_path, service, "alpha", other), "cli")
    [task] = [t for t in service.list_tasks() if covers_of(t)]
    assert task.mission_id == other != mission


def test_default_plan_ignores_prefixed_keys_and_keeps_bare_ones(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="demo")
    new_chantier(tmp_path, "Alpha")
    _exigences(tmp_path, None, "principal", "p")
    _exigences(tmp_path, "alpha", "alpha", "a")
    service = TaskService(tmp_path)
    apply_plan(service, build_plan(tmp_path, service, "alpha"), "cli")
    plan = build_plan(tmp_path, service)  # la tâche d'alpha n'est ni orpheline ni couverture du principal
    assert plan.orphans == [] and plan.covered == {} and [p.requirement for p in plan.proposals] == ["REQ-001"]
    apply_plan(service, plan, "cli")
    assert sorted(c for t in service.list_tasks() for c in covers_of(t)) == ["REQ-001", "alpha/REQ-001"]


def test_questions_are_scoped_per_chantier(tmp_path: Path) -> None:
    new_chantier(tmp_path, "Alpha")
    new_chantier(tmp_path, "Beta")
    for slug in ("alpha", "beta"):
        (cadrage_dir(tmp_path, slug) / "04-exigences.md").write_text(
            "## F\n\n- REQ-001 : sans critère\n", encoding="utf-8")
    service = TaskService(tmp_path)
    for slug in ("alpha", "beta"):
        plan = apply_plan(service, build_plan(tmp_path, service, slug), "cli")
        assert plan.questions[0].status == "open" and plan.questions[0].ambiguity.key.startswith(f"cadrage:{slug}:")
    anchors = {t.id for t in service.list_tasks() if (t.extra or {}).get("cadrage") == "questions"}
    assert len(anchors) == 2  # une ancre par chantier : l'un ne retire pas les questions de l'autre


# ── les gardes du standard ───────────────────────────────────────────────────


def test_a_project_with_only_named_chantiers_gets_no_false_failure(tmp_path: Path) -> None:
    new_chantier(tmp_path, "Alpha")
    ids = {c.id for c in verify_standard_profile(tmp_path, profile_id="starter").checks}
    assert not {i for i in ids if i.startswith("cadrage.")}


def test_the_default_gate_is_unchanged_next_to_named_chantiers(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="demo")
    new_chantier(tmp_path, "Alpha")
    ids = {c.id for c in verify_standard_profile(tmp_path, profile_id="starter").checks}
    assert "cadrage.gate_incomplete" in ids


# ── non-régression : sorties identiques, aucun fichier modifié ───────────────


def test_existing_cadrage_keeps_outputs_and_files_byte_identical(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="demo")
    _exigences(tmp_path, None, "principal", "p")
    before = _fingerprint(tmp_path)
    outputs = {}
    for cmd in ("status", "check", "plan"):
        result = runner.invoke(app, ["cadrage", cmd, str(tmp_path), *(["--dry-run"] if cmd == "plan" else [])])
        outputs[cmd] = (result.exit_code, result.output)
    explicit = {}
    for cmd in ("status", "check", "plan"):
        extra = ["--dry-run"] if cmd == "plan" else []
        result = runner.invoke(app, ["cadrage", cmd, str(tmp_path), "--chantier", "principal", *extra])
        explicit[cmd] = (result.exit_code, result.output)
    assert explicit == outputs
    assert outputs["status"][0] == 0 and outputs["check"][0] == 1 and "REQ-001" in outputs["plan"][1]
    listed = runner.invoke(app, ["cadrage", "list", str(tmp_path), "-o", "json"])
    assert json.loads(listed.output)[0]["slug"] == "principal"
    assert _fingerprint(tmp_path) == before  # ni fichier modifié, ni fichier créé (pas même chantier.yaml)


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_new_list_state_link_and_scoped_commands(tmp_path: Path) -> None:
    root = str(tmp_path)
    out = runner.invoke(app, ["cadrage", "new", "Atelier de spec", root, "--mission", "M-3"])
    assert out.exit_code == 0 and "atelier-de-spec" in out.output
    dup = runner.invoke(app, ["cadrage", "new", "Atelier de spec", root])
    assert dup.exit_code == 1 and "existe déjà" in dup.output
    assert runner.invoke(app, ["cadrage", "state", "atelier-de-spec", "valide", root]).exit_code == 0
    refused = runner.invoke(app, ["cadrage", "state", "atelier-de-spec", "idee", root])
    assert refused.exit_code == 1 and "atteignables" in refused.output
    assert runner.invoke(app, ["cadrage", "link", "atelier-de-spec", "M-9", root]).exit_code == 0
    rows = json.loads(runner.invoke(app, ["cadrage", "list", root, "-o", "json"]).output)
    assert rows[0]["etat"] == "valide" and rows[0]["mission"] == "M-9" and rows[0]["progress"] == "0/5"
    assert "Atelier de spec" in runner.invoke(app, ["cadrage", "list", root]).output
    assert runner.invoke(app, ["cadrage", "status", root, "--chantier", "atelier-de-spec"]).exit_code == 0
    assert runner.invoke(app, ["cadrage", "status", root]).exit_code == 1  # le défaut n'est pas initialisé
    assert runner.invoke(app, ["cadrage", "check", root, "--chantier", "atelier-de-spec"]).exit_code == 1
    assert runner.invoke(app, ["cadrage", "status", root, "--chantier", "../x"]).exit_code == 1
    assert runner.invoke(app, ["cadrage", "init", root, "--chantier", "autre"]).exit_code == 0
    assert (tmp_path / CADRAGE_DIR / "autre" / "01-brief.md").is_file()


def test_cli_plan_on_a_chantier_uses_its_mission_and_prefixed_keys(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    mission = service.open_mission("Atelier").id
    root = str(tmp_path)
    runner.invoke(app, ["cadrage", "new", "Alpha", root, "--mission", mission])
    _exigences(tmp_path, "alpha", "alpha", "alpha")
    result = runner.invoke(app, ["cadrage", "plan", root, "--chantier", "alpha", "--yes", "-o", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["chantier"] == "alpha" and data["tasks"][0]["task_id"]
    [task] = [t for t in TaskService(tmp_path).list_tasks() if covers_of(t)]
    assert covers_of(task) == ["alpha/REQ-001"] and task.mission_id == mission
    assert runner.invoke(app, ["cadrage", "plan", root, "--chantier", "absent"]).exit_code == 1
