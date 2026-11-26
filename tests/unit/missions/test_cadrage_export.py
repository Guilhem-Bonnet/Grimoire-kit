"""#1342 / #1197 : l'export portable d'un chantier de cadrage (``grimoire cadrage export``), lecture seule.

Un vrai dépôt git par test, un HOME par test (le journal d'autorité est celui du test).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core import atelier_suggestions as store
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.chantiers import new_chantier
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import cadrage_export as ce
from grimoire.missions import requirement_gate as rg
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage export")

runner = CliRunner()
DATE = "2026-11-25"
CANARI = "ghp_" + "a" * 36

EXIGENCES = f"""# Exigences

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
  - valeur de test {CANARI}
- REQ-002 (Should) : export CSV, voir aussi REQ-001
  - le fichier s'ouvre dans un tableur
- REQ-003 (Won't) : journal d'audit
"""
BRAINSTORM = """# Brainstorm

## Pistes

- une piste

## Questions ouvertes

- Faut-il une session de 30 jours ?

## Écarté (et pourquoi)

- Mot de passe écarté : le lien suffit
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    data = {"project": {"name": root.name}, "governance": {"roles": {"moa": "Ada"}, "requirement_gate": "warn"}}
    (root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    cad = root / CADRAGE_DIR
    cad.mkdir(parents=True)
    (cad / "01-brief.md").write_text("---\nphase: brief\nprojet: p\n---\n\n# Brief\n\nle problème\n", encoding="utf-8")
    (cad / "02-brainstorm.md").write_text(BRAINSTORM, encoding="utf-8")
    (cad / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    (root / ".gitignore").write_text("_grimoire*/\nproject-context.yaml\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("x", encoding="utf-8")
    _git(root, "add", "README.md", ".gitignore")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _delivered(root: Path) -> None:
    """REQ-001 validée, servie par une tâche fusionnée qui a touché un fichier et un test."""
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = service.add("connexion", ("ça marche",), requirement="REQ-001").task.id
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "feat")
    (root / "src").mkdir()
    (root / "src" / "login.py").write_text("a", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_login.py").write_text("b", encoding="utf-8")
    _git(root, "add", "src", "tests")
    _git(root, "commit", "-q", "-m", "feat: lien magique")
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "main")
    service.ledger.append_event("task.merged", tid, "task", "x", {"base_sha": base, "head_sha": head, "merged_sha": head})


def _export(root: Path, out: Path, *extra: str) -> Any:
    return runner.invoke(app, ["cadrage", "export", "--path", str(root), "--out", str(out), "--date", DATE, *extra])


def _lot(out: Path) -> dict[str, str]:
    return {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8") for p in sorted(out.rglob("*")) if p.is_file()}


def _front(text: str) -> dict[str, Any]:
    head = text.split("---\n")[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


def test_export_writes_the_okf_lot_with_front_matter_and_relative_links(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    _delivered(root)
    out = tmp_path / "lot"
    res = _export(root, out)
    assert res.exit_code == 0, res.output
    lot = _lot(out)
    assert set(lot) == {
        "index.md", "decisions.md", "manifest.json", "phases/brief.md", "phases/brainstorm.md",
        "phases/exigences.md", "requirements/REQ-001.md", "requirements/REQ-002.md", "requirements/REQ-003.md",
    }
    for name, text in lot.items():
        if name.endswith(".md"):
            meta = _front(text)
            assert {"name", "description", "type"} <= set(meta), name
    fiche = lot["requirements/REQ-001.md"]
    meta = _front(fiche)
    assert meta["type"] == "requirement" and meta["priorite"] == "must" and meta["validation_moa"] == "validée"
    assert meta["valide_par"] == "Ada" and meta["valide_le"]
    assert "`src/login.py`" in fiche and "`tests/test_login.py` (test)" in fiche
    assert "[exigences](../phases/exigences.md)" in fiche
    assert "[REQ-001](REQ-001.md)" in lot["requirements/REQ-002.md"]
    assert _front(lot["requirements/REQ-003.md"])["etat"].startswith("hors plan")
    index = lot["index.md"]
    assert "(requirements/REQ-001.md)" in index and "(phases/brief.md)" in index and DATE in index
    assert "cahier-des-charges.md" not in index and "absente" in index
    assert "le problème" in lot["phases/brief.md"] and "projet: p" not in lot["phases/brief.md"]
    assert "Faut-il une session de 30 jours" in lot["decisions.md"] and "Mot de passe écarté" in lot["decisions.md"]


def test_export_is_deterministic_except_the_date(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    _delivered(root)
    assert _export(root, tmp_path / "a").exit_code == 0
    assert _export(root, tmp_path / "b").exit_code == 0
    first, second = _lot(tmp_path / "a"), _lot(tmp_path / "b")
    assert first == second
    other = tmp_path / "c"
    assert runner.invoke(app, ["cadrage", "export", "--path", str(root), "--out", str(other), "--date", "2026-12-01"]).exit_code == 0
    differing = {n for n, t in _lot(other).items() if t != first[n]}
    assert differing == {"index.md", "manifest.json"}


def test_export_redacts_canary_values_and_leaks_no_machine_path_or_user(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    _delivered(root)
    (root / CADRAGE_DIR / "03-comprehension.md").write_text(
        f"# C\n\nvu dans {root}/src et /home/alice/notes.txt, valeur {CANARI}\n", encoding="utf-8"
    )
    out = tmp_path / "lot"
    assert _export(root, out).exit_code == 0
    blob = "\n".join(_lot(out).values())
    assert CANARI not in blob and "redacted" in blob
    assert str(root) not in blob and str(tmp_path) not in blob and "/home/alice" not in blob and "alice" not in blob
    assert ce.scrub(f"{root}/x", root) == "./x"
    assert ce.scrub(str(Path.home()) + "/y") == "~/y"
    assert ce.scrub("C:\\Users\\bob\\doc") == "~\\doc"
    assert ce._rel_file("/abs/elsewhere/f.py", root) == "f.py" and ce._rel_file("../x/g.py", root) == "g.py"
    assert ce._rel_file("src/ok.py", root) == "src/ok.py"


def test_manifest_lists_every_file_with_sha256_and_a_global_digest(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    out = tmp_path / "lot"
    assert _export(root, out).exit_code == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "grimoire-cadrage-export/v1" and manifest["generated"] == DATE
    names = [e["path"] for e in manifest["files"]]
    assert names == sorted(names) and "manifest.json" not in names
    for entry in manifest["files"]:
        data = (out / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest() and entry["bytes"] == len(data)
    assert len(manifest["digest"]) == 64


def test_verify_passes_then_detects_alteration_missing_and_extra(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    out = tmp_path / "lot"
    assert _export(root, out).exit_code == 0
    ok = runner.invoke(app, ["cadrage", "export", "--verify", str(out)])
    assert ok.exit_code == 0 and "lot intact" in ok.output
    (out / "index.md").write_text("altéré", encoding="utf-8")
    (out / "decisions.md").unlink()
    (out / "intrus.md").write_text("x", encoding="utf-8")
    bad = runner.invoke(app, ["cadrage", "export", "--verify", str(out), "--json"])
    data = json.loads(bad.output)
    assert bad.exit_code == 1 and data["ok"] is False
    text = " ".join(data["problems"])
    assert "index.md : altéré" in text and "decisions.md : manquant" in text and "intrus.md" in text
    human = runner.invoke(app, ["cadrage", "export", "--verify", str(out)])
    assert human.exit_code == 1 and "altéré" in human.output


def test_verify_rejects_a_missing_or_invalid_manifest_a_tampered_digest_and_links(tmp_path: Path) -> None:
    assert ce.verify_export(tmp_path / "absent").ok is False
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "illisible" in ce.verify_export(empty).problems[0]
    (empty / "manifest.json").write_text("pas du json", encoding="utf-8")
    assert "invalide" in ce.verify_export(empty).problems[0]
    (empty / "manifest.json").write_text(json.dumps({"schema": "autre", "files": []}), encoding="utf-8")
    assert "invalide" in ce.verify_export(empty).problems[0]
    (empty / "manifest.json").write_text("[1]", encoding="utf-8")
    assert "invalide" in ce.verify_export(empty).problems[0]
    root = _project(tmp_path / "p")
    out = tmp_path / "lot"
    assert _export(root, out).exit_code == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["digest"] = "0" * 64
    manifest["files"].append({"path": "../evil.md", "sha256": "0" * 64, "bytes": 1})
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = " ".join(ce.verify_export(out).problems)
    assert "empreinte globale" in problems and "hors du lot" in problems
    (out / "lien.md").symlink_to(out / "index.md")
    assert "lien symbolique" in " ".join(ce.verify_export(out).problems)
    link = tmp_path / "lien-lot"
    link.symlink_to(out)
    assert ce.verify_export(link).ok is False


def test_export_refuses_a_non_empty_folder_and_force_only_overwrites_the_lot(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    out = tmp_path / "lot"
    out.mkdir()
    (out / "mien.txt").write_text("à moi", encoding="utf-8")
    refused = _export(root, out)
    assert refused.exit_code == 1 and "pas vide" in refused.output
    forced = _export(root, out, "--force")
    assert forced.exit_code == 0
    assert (out / "mien.txt").read_text(encoding="utf-8") == "à moi" and (out / "index.md").is_file()
    assert _export(root, out, "--force").exit_code == 0
    again = runner.invoke(app, ["cadrage", "export", "--verify", str(out)])
    assert again.exit_code == 1 and "mien.txt" in again.output  # le fichier étranger est dit, jamais supprimé
    empty = tmp_path / "vide"
    empty.mkdir()
    assert _export(root, empty).exit_code == 0


def test_export_confines_the_output_no_symlink_no_file_no_cadrage_folder(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    target = tmp_path / "ailleurs"
    target.mkdir()
    link = tmp_path / "lien"
    link.symlink_to(target)
    assert "lien symbolique" in _export(root, link).output and not any(target.iterdir())
    out = tmp_path / "lot"
    out.mkdir()
    (out / "phases").symlink_to(target)
    res = _export(root, out, "--force")
    assert res.exit_code == 1 and "sortirait" in res.output and not any(target.iterdir())
    victim = tmp_path / "victime.txt"
    victim.write_text("intacte", encoding="utf-8")
    out2 = tmp_path / "lot2"
    out2.mkdir()
    (out2 / "index.md").symlink_to(victim)
    res2 = _export(root, out2, "--force")
    assert res2.exit_code == 1 and victim.read_text(encoding="utf-8") == "intacte"
    afile = tmp_path / "fichier"
    afile.write_text("x", encoding="utf-8")
    assert "pas un dossier" in _export(root, afile).output
    assert "dossier de cadrage" in _export(root, root / CADRAGE_DIR / "sortie").output
    assert not (root / CADRAGE_DIR / "sortie").exists()


def test_export_never_writes_to_the_project(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    _delivered(root)
    journal = Path(os.environ["HOME"])

    def snapshot() -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        for base in (root / "_grimoire", journal):
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    found[str(p)] = p.read_bytes()
        return found

    before = snapshot()
    assert _export(root, tmp_path / "lot").exit_code == 0
    assert snapshot() == before


def test_export_named_chantier_suggestions_and_drift(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    new_chantier(root, "Connexion")
    base = root / CADRAGE_DIR / "connexion"
    assert base.is_dir()
    (base / "04-exigences.md").write_text("- REQ-001 [Must] : lien magique\n  - expire vite\n", encoding="utf-8")
    rg.approve_requirement(root, "connexion/REQ-001", actor="ada")
    (base / "04-exigences.md").write_text("- REQ-001 [Must] : lien magique\n  - expire en 15 min\n", encoding="utf-8")
    items = [
        store.Suggestion("sug-1", "exigences", "b", "f", "avant", "ajouter un critère", "utile", "explorer", "x", "d",
                         etat="acceptee", decide_le="2026-11-20", decide_motif="bien vu"),
        store.Suggestion("sug-2", "brief", "b", "f", "avant", "tout supprimer", "", "sceptique", "x", "d",
                         etat="refusee", decide_le="2026-11-21", decide_motif="non"),
        store.Suggestion("sug-3", "brief", "b", "f", "avant", "en attente", "", "sceptique", "x", "d"),
    ]
    store.save(base, items)
    out = tmp_path / "lot"
    res = runner.invoke(app, ["cadrage", "export", "--path", str(root), "-c", "connexion", "--out", str(out),
                              "--date", DATE, "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["chantier"] == "connexion" and "requirements/REQ-001.md" in payload["files"]
    fiche = (out / "requirements/REQ-001.md").read_text(encoding="utf-8")
    assert _front(fiche)["modifiee_depuis_la_validation"] is True and "Modifiée depuis la validation" in fiche
    decisions = (out / "decisions.md").read_text(encoding="utf-8")
    assert "ajouter un critère" in decisions and "bien vu" in decisions and "tout supprimer" in decisions
    assert "en attente" not in decisions
    assert "Connexion" in (out / "index.md").read_text(encoding="utf-8")


def test_export_suggestion_store_problems_are_noted_not_fatal(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    cad = root / CADRAGE_DIR
    (cad / "suggestions.json").write_text("{pas du json", encoding="utf-8")
    res = _export(root, tmp_path / "a")
    assert res.exit_code == 0 and "abîmé" in res.output
    (cad / "suggestions.json").unlink()
    elsewhere = tmp_path / "s.json"
    elsewhere.write_text("{}", encoding="utf-8")
    (cad / "suggestions.json").symlink_to(elsewhere)
    res = _export(root, tmp_path / "b")
    assert res.exit_code == 0 and "pas un fichier ordinaire" in res.output
    assert "## Suggestions" not in (tmp_path / "b" / "decisions.md").read_text(encoding="utf-8")


def test_export_errors_are_said_with_exit_code_1(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    assert "AAAA-MM-JJ" in runner.invoke(app, ["cadrage", "export", "--path", str(root), "--out", str(tmp_path / "x"),
                                               "--date", "hier"]).output
    assert runner.invoke(app, ["cadrage", "export", "--path", str(root)]).exit_code == 1
    assert runner.invoke(app, ["cadrage", "export", "--path", str(root), "-c", "inconnu",
                               "--out", str(tmp_path / "y")]).exit_code == 1
    with pytest.raises(ce.ExportError):
        ce._confined(tmp_path, "../nope.md")


def test_export_without_requirements_or_phases_still_gives_a_readable_lot(tmp_path: Path) -> None:
    root = tmp_path / "vide"
    root.mkdir()
    (root / CADRAGE_DIR).mkdir(parents=True)
    out = tmp_path / "lot"
    res = _export(root, out)
    assert res.exit_code == 0, res.output
    index = (out / "index.md").read_text(encoding="utf-8")
    assert "aucune exigence identifiée" in index and "absente" in index and "Réserves" in index
    assert ce.verify_export(out).ok
