"""``grimoire steward map|verdicts|report`` (#876, lot 5) : lecture seule stricte, prouvée sur le disque et par les doubles.

Chaque fichier du projet — dépôt git compris (``.git/objects``, références, index), ledger et remote nue — est
relevé avant et après : seules des écritures sous ``_grimoire-output/steward/`` sont permises. Les doubles
refusent toute sous-commande git ou gh qui écrit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from steward_doubles import Recorder, Scene, git
from typer.testing import CliRunner

from grimoire.cli import cmd_steward
from grimoire.cli.app import app
from grimoire.steward import REPORT_SCHEMA, VALIDATION_SCHEMA, VERDICTS_SCHEMA

pytestmark = pytest.mark.covers("cli:steward map", "cli:steward verdicts", "cli:steward report", level="integration")

runner = CliRunner()


def _snapshot(*roots: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


@pytest.fixture
def recorder(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder(scene.gh)
    monkeypatch.setattr(cmd_steward, "EXEC", rec)
    return rec


def _verdicts(scene: Scene, tmp_path: Path) -> Path:
    path = tmp_path / "verdicts.json"
    path.write_text(
        json.dumps(
            {
                "$schema": VERDICTS_SCHEMA,
                "verdicts": [
                    {"element": "pr:31", "verdict": "conflit_simple"},
                    {"element": f"tache:{scene.tasks['citee']}", "verdict": "livree", "preuves": [{"type": "commit", "sha": scene.shas["cite"]}]},
                    {"element": "issue:50", "verdict": "a_faire", "theme": "rendu"},
                    {"element": f"tache:{scene.tasks['issue_fermee']}", "verdict": "livree", "preuves": [{"type": "note", "texte": "vu"}]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_lecture_seule_stricte(scene: Scene, recorder: Recorder, tmp_path: Path) -> None:
    verdicts = _verdicts(scene, tmp_path)
    remote = tmp_path / "remote.git"
    before = _snapshot(scene.root, remote)
    status_before = git(scene.root, "status", "--porcelain")
    common = ["--project-root", str(scene.root), "--sans-sonde"]

    done = runner.invoke(app, ["steward", "map", *common, "--json"])
    assert done.exit_code == 0, done.output
    done = runner.invoke(app, ["steward", "verdicts", "--file", str(verdicts), *common])
    assert done.exit_code == 1, done.output  # un verdict rejeté (note seule)
    done = runner.invoke(app, ["steward", "report", "--verdicts", str(verdicts), *common])
    assert done.exit_code == 0, done.output

    after = _snapshot(scene.root, remote)
    steward = (scene.root / "_grimoire-output" / "steward").as_posix() + "/"
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    assert changed, "les sorties de l'intendant auraient dû être écrites"
    assert {p for p in changed if not p.startswith(steward)} == set()
    assert {Path(p).name for p in changed} == {".gitignore", "map.json", "validation.json", "report.json", "report.txt"}
    # Les sorties s'ignorent elles-mêmes : git status ne bouge pas.
    assert git(scene.root, "status", "--porcelain") == status_before
    recorder.assert_read_only()


def test_map_json(scene: Scene, recorder: Recorder) -> None:
    done = runner.invoke(app, ["--output", "json", "steward", "map", "--project-root", str(scene.root), "--sans-sonde"])
    assert done.exit_code == 0, done.output
    carte = json.loads(done.output)
    assert carte["$schema"] == "grimoire-steward-map/v1"
    assert carte["resume"]["prs"] == 3


def test_map_texte_et_locales_seulement(scene: Scene, recorder: Recorder) -> None:
    done = runner.invoke(app, ["steward", "map", "--project-root", str(scene.root), "--sans-sonde", "--locales-seulement"])
    assert done.exit_code == 0, done.output
    assert "signal conflit_changelog_seul branche:feat/changelog" in done.output
    carte = json.loads((scene.root / "_grimoire-output" / "steward" / "map.json").read_text(encoding="utf-8"))
    assert not any(b["distante"] for b in carte["branches"]["elements"])


def test_verdicts_json(scene: Scene, recorder: Recorder, tmp_path: Path) -> None:
    done = runner.invoke(
        app, ["steward", "verdicts", "--file", str(_verdicts(scene, tmp_path)), "--project-root", str(scene.root), "--sans-sonde", "--json"]
    )
    result = json.loads(done.output)
    assert result["$schema"] == VALIDATION_SCHEMA
    assert result["resume"]["etablis"] == 3
    assert [e["element"] for e in result["rejetes"]] == [f"tache:{scene.tasks['issue_fermee']}"]


def test_verdicts_fichier_illisible(scene: Scene, recorder: Recorder, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    done = runner.invoke(app, ["steward", "verdicts", "--file", str(bad), "--project-root", str(scene.root)])
    assert done.exit_code == 2


def test_report_regroupe_par_action(scene: Scene, recorder: Recorder, tmp_path: Path) -> None:
    done = runner.invoke(
        app, ["steward", "report", "--verdicts", str(_verdicts(scene, tmp_path)), "--project-root", str(scene.root), "--sans-sonde", "--json"]
    )
    assert done.exit_code == 0, done.output
    report = json.loads(done.output)
    assert report["$schema"] == REPORT_SCHEMA
    actions = report["actions"]
    assert [i["element"] for i in actions["fermer_avec_preuve"]] == [f"tache:{scene.tasks['citee']}"]
    assert [i["element"] for i in actions["realigner"]] == ["pr:31"]
    assert list(actions["a_faire_par_theme"]) == ["rendu"]
    assert [i["element"] for i in report["rejetes"]] == [f"tache:{scene.tasks['issue_fermee']}"]
    # Le signal de la tâche citée est couvert par un verdict établi ; celui de la PR 31 aussi.
    refs = {s["ref"] for s in report["signaux_sans_verdict"]}
    assert f"tache:{scene.tasks['citee']}" not in refs and "pr:31" not in refs
    assert f"tache:{scene.tasks['issue_fermee']}" in refs
    text = (scene.root / "_grimoire-output" / "steward" / "report.txt").read_text(encoding="utf-8")
    assert "Fermer avec preuve (1)" in text and "Limites :" in text


def test_report_sans_verdicts(scene: Scene, recorder: Recorder) -> None:
    done = runner.invoke(app, ["steward", "report", "--project-root", str(scene.root), "--sans-sonde"])
    assert done.exit_code == 0, done.output
    assert "aucun fichier fourni" in done.output
