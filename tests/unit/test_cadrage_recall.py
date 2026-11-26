"""#1342 / #1197 : le rappel du cadrage — mémoire, exigences des chantiers, pistes écartées — en lecture seule."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core import cadrage_recall as cr
from grimoire.core.cadrage import scaffold
from grimoire.memory.backends.base import MemoryEntry
from grimoire.missions import cadrage_trace as ct
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage recall")
runner = CliRunner()

EXIGENCES = """# Exigences

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 (Should) : export CSV des commandes
"""
BRAINSTORM = """# Brainstorm

## Pistes

- une piste quelconque

## Questions ouvertes

- Faut-il un export CSV des commandes pour la comptabilité ?

## Écarté (et pourquoi)

- Authentification par mot de passe écartée : le lien magique suffit
"""
SECRET = "ghp_" + "a" * 36


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "p"
    scaffold(project, project_name="p", chantier="auth")
    (project / "_grimoire" / "cadrage" / "auth" / "chantier.yaml").write_text(
        "slug: auth\ntitre: Connexion\netat: en-cours\n", encoding="utf-8"
    )
    (project / "_grimoire" / "cadrage" / "auth" / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    (project / "_grimoire" / "cadrage" / "auth" / "02-brainstorm.md").write_text(BRAINSTORM, encoding="utf-8")
    return project


def _entry(text: str, **kw: Any) -> MemoryEntry:
    return MemoryEntry(id="m1", text=text, created_at="2026-10-03T10:00:00Z", source="docs/adr-004.md", **kw)


def _no_trace(*_: Any) -> Any:
    return SimpleNamespace(requirements=[])


# ── le lexical ──────────────────────────────────────────────────────────────


def test_tokens_fold_accents_stopwords_and_plurals() -> None:
    assert cr._tokens("Les Commandes, l'exportation écarté avec CSV") == {"commande", "exportation", "ecarte", "csv"}
    assert cr._score(frozenset(), "x") == 0.0
    assert cr._score(frozenset({"lien", "magique"}), "un lien seul") == 0.0  # sous deux mots communs
    assert cr._score(frozenset({"lien", "magique"}), "lien magique") > 0.0
    assert cr._score(frozenset({"lien"}), "lien magique") > 0.0  # une requête d'un mot suffit


def test_clean_redacts_secrets_strips_controls_and_bounds() -> None:
    text = cr._clean(f"clé {SECRET}\x1b[31m rouge\n\ttexte " + "x" * 400)
    assert SECRET not in text and "[redacted:github-token]" in text
    assert "\x1b" not in text and "\n" not in text
    assert len(text) <= cr.MAX_TEXT_CHARS and text.endswith("…")


def test_brainstorm_items_read_only_discarded_and_open_questions() -> None:
    items = cr._brainstorm_items(BRAINSTORM + "\n## Questions ouvertes\n\n> À compléter — questions ouvertes.\n")
    assert [k for k, _ in items] == ["question", "ecarte"]
    assert cr._brainstorm_items("## Pistes\n- a\n") == []


# ── le rappel ───────────────────────────────────────────────────────────────


def test_recall_ranks_three_sources_and_joins_code_link(root: Path) -> None:
    row = SimpleNamespace(
        key="auth/REQ-001", stage_label="livrée", validated_at="2026-10-05T09:00:00Z", files=["src/login.py"] * 12,
        tasks=[SimpleNamespace(id="T-1", status="done")],
    )
    seen: list[Any] = []

    def tracer(_root: Path, chantier: str | None, only: frozenset[str]) -> Any:
        seen.append((chantier, only))
        return SimpleNamespace(requirements=[row])

    out = cr.recall(
        root, "lien magique pour se connecter", limit=5,
        memory=lambda q, n: [_entry("Décision : on garde le lien magique sans mot de passe")], tracer=tracer,
    )
    kinds = {r.kind for r in out.recalls}
    assert kinds == {"memoire", "exigence", "ecarte"}
    req = next(r for r in out.recalls if r.kind == "exigence")
    assert (req.ref, req.status, req.when) == ("auth/REQ-001", "livrée", "2026-10-05")
    assert req.code == {"tasks": [{"id": "T-1", "status": "done"}], "files": ["src/login.py"] * 8, "files_total": 12}
    assert seen == [("auth", frozenset({"auth/REQ-001"}))]  # la trace ne porte que sur l'exigence retenue
    mem = next(r for r in out.recalls if r.kind == "memoire")
    assert (mem.source, mem.when) == ("docs/adr-004.md", "2026-10-03")
    assert json.loads(json.dumps(out.to_dict()))["schema"] == cr.SCHEMA


def test_recall_requirement_without_task_has_no_code_and_trace_failure_is_said(root: Path) -> None:
    row = SimpleNamespace(key="auth/REQ-002", stage_label="proposée", validated_at="", files=[], tasks=[])
    out = cr.recall(root, "export CSV des commandes", memory=lambda q, n: [], tracer=lambda *_: SimpleNamespace(requirements=[row]))
    req = next(r for r in out.recalls if r.kind == "exigence")
    assert req.status == "proposée" and req.code is None

    def boom(*_: Any) -> Any:
        raise RuntimeError("ledger illisible")

    out = cr.recall(root, "export CSV des commandes", memory=lambda q, n: [], tracer=boom)
    assert any("trace indisponible" in i for i in out.issues) and any(r.kind == "exigence" for r in out.recalls)


def test_recall_exclude_chantier_limit_and_empty_input(root: Path) -> None:
    mem = lambda q, n: []  # noqa: E731
    assert cr.recall(root, "lien magique", exclude_chantier="auth", memory=mem, tracer=_no_trace).recalls == []
    assert len(cr.recall(root, "lien magique export CSV commandes", limit=1, memory=mem, tracer=_no_trace).recalls) == 1
    assert cr.recall(root, "   ", memory=mem).recalls == []
    assert cr.recall(root, "lien magique", limit=0, memory=mem).recalls == []
    assert cr.recall(root, "lien magique", limit=99, memory=mem, tracer=_no_trace).to_dict()["count"] <= cr.MAX_LIMIT


def test_recall_memory_failure_is_said_and_lexical_still_answers(root: Path) -> None:
    def boom(q: str, n: int) -> list[MemoryEntry]:
        raise RuntimeError("qdrant injoignable")

    out = cr.recall(root, "lien magique", memory=boom, tracer=_no_trace)
    assert any("mémoire en panne" in i for i in out.issues) and out.recalls


def test_recall_without_project_config_degrades_silently_to_cadrage(root: Path) -> None:
    out = cr.recall(root, "lien magique", tracer=_no_trace)  # pas de grimoire-project.yaml : mémoire indisponible
    assert any("mémoire indisponible" in i for i in out.issues) and out.recalls


def test_recall_secrets_in_memory_and_cadrage_are_redacted(root: Path) -> None:
    (root / "_grimoire" / "cadrage" / "auth" / "02-brainstorm.md").write_text(
        f"## Écarté (et pourquoi)\n\n- jeton {SECRET} lien magique écarté\n", encoding="utf-8"
    )
    out = cr.recall(root, "lien magique", memory=lambda q, n: [_entry(f"lien magique {SECRET}")], tracer=_no_trace)
    assert out.recalls and SECRET not in json.dumps(out.to_dict())


def test_unreadable_chantiers_are_said(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.core.chantiers as chantiers

    def boom(_: Path) -> Any:
        raise OSError("disque")

    monkeypatch.setattr(chantiers, "list_chantiers", boom)
    assert any("chantiers illisibles" in i for i in cr.recall(root, "lien magique", memory=lambda q, n: []).issues)
    monkeypatch.setattr(
        chantiers, "list_chantiers", lambda _: [SimpleNamespace(slug="s", etat="x", error="")] * (cr.MAX_CHANTIERS + 1)
    )
    out = cr.recall(root, "lien magique", memory=lambda q, n: [])
    assert any("chantiers" in i and "plus récents" in i for i in out.issues)


def test_recall_with_default_chantier_and_real_trace(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="p")
    cadrage = tmp_path / "_grimoire" / "cadrage"
    (cadrage / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    out = cr.recall(tmp_path, "lien magique de connexion", memory=lambda q, n: [])
    (req,) = [r for r in out.recalls if r.kind == "exigence"]
    assert req.ref == "REQ-001" and req.status == "proposée" and req.chantier == "principal" and req.code is None


def test_out_of_scope_requirement_is_labelled(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="p")
    (tmp_path / "_grimoire" / "cadrage" / "04-exigences.md").write_text(
        "- REQ-009 [Won't] : synchronisation hors ligne des commandes\n", encoding="utf-8"
    )
    out = cr.recall(tmp_path, "synchronisation hors ligne", memory=lambda q, n: [])
    assert out.recalls[0].status == "hors périmètre"


# ── la mémoire réelle (gestionnaire injecté) ────────────────────────────────


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: Any) -> None:
    import grimoire.core.config as config
    import grimoire.memory.manager as manager_mod

    monkeypatch.setattr(config.GrimoireConfig, "find_and_load", classmethod(lambda cls, root: object()))
    monkeypatch.setattr(manager_mod.MemoryManager, "from_config", classmethod(lambda cls, cfg, **kw: manager))


def test_search_memory_uses_hybrid_then_falls_back_to_lexical_companion(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Companion:
        def search(self, query: str, *, limit: int) -> list[MemoryEntry]:
            return [_entry("lexical seul : lien magique")]

    def hybrid_ok(query: str, *, limit: int) -> list[MemoryEntry]:
        return [_entry("hybride : lien magique")]

    def hybrid_down(query: str, *, limit: int) -> list[MemoryEntry]:
        raise RuntimeError("tous les backends en panne")

    mgr = SimpleNamespace(surface="", hybrid_search=hybrid_ok, lexical_companion=Companion())
    _patch_manager(monkeypatch, mgr)
    issues: list[str] = []
    assert cr._search_memory(root, "lien magique", 3, issues)[0].text.startswith("hybride") and not issues
    assert mgr.surface == "cli"

    mgr.hybrid_search = hybrid_down
    assert cr._search_memory(root, "lien magique", 3, issues)[0].text.startswith("lexical seul")
    assert any("lexical seul" in i for i in issues)

    mgr.lexical_companion = None
    assert cr._search_memory(root, "lien magique", 3, issues) == []

    class BrokenCompanion:
        def search(self, query: str, *, limit: int) -> list[MemoryEntry]:
            raise RuntimeError("fts corrompu")

    mgr.lexical_companion = BrokenCompanion()
    assert cr._search_memory(root, "lien magique", 3, issues) == []
    assert any("recherche lexicale en panne" in i for i in issues)


# ── la trace bornée ─────────────────────────────────────────────────────────


def test_build_trace_only_limits_the_traced_requirements(root: Path) -> None:
    full = ct.build_trace(root, TaskService(root), "auth")
    assert {r.key for r in full.requirements} == {"auth/REQ-001", "auth/REQ-002"}
    one = ct.build_trace(root, TaskService(root), "auth", only=frozenset({"auth/REQ-002"}))
    assert [r.key for r in one.requirements] == ["auth/REQ-002"]


# ── la CLI ──────────────────────────────────────────────────────────────────


def test_cli_recall_json_and_text(root: Path) -> None:
    res = runner.invoke(app, ["cadrage", "recall", "lien magique pour se connecter", "--path", str(root), "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["schema"] == cr.SCHEMA and data["count"] >= 2
    assert {r["kind"] for r in data["recalls"]} >= {"exigence", "ecarte"}
    res = runner.invoke(app, ["cadrage", "recall", "lien magique pour se connecter", "--path", str(root)])
    assert res.exit_code == 0 and "exigence" in res.output and "piste écartée" in res.output
    assert "dégradé" in res.output  # pas de configuration : mémoire indisponible, dit
    res = runner.invoke(app, ["cadrage", "recall", "lien magique", "--path", str(root), "-c", "auth", "-n", "2"])
    assert "aucun rappel pertinent" in res.output


def test_cli_recall_text_shows_the_code_link(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(
        key="auth/REQ-001", stage_label="livrée", validated_at="", files=["src/login.py"],
        tasks=[SimpleNamespace(id="T-1", status="done")],
    )
    monkeypatch.setattr(cr, "_default_tracer", lambda *_: SimpleNamespace(requirements=[row]))
    res = runner.invoke(app, ["cadrage", "recall", "lien magique connecter", "--path", str(root)])
    assert "code : tâches T-1 ; fichiers src/login.py" in res.output
    row.files = []
    res = runner.invoke(app, ["cadrage", "recall", "lien magique connecter", "--path", str(root)])
    assert "aucun fichier connu" in res.output


def _interview(root: Path, *args: str) -> Any:
    return runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), *args])


@pytest.fixture
def demo(root: Path) -> Path:
    res = runner.invoke(app, ["cadrage", "new", "Connexion par lien magique", "--slug", "demo", str(root)])
    assert res.exit_code == 0, res.output
    return root


def test_interview_carries_at_most_three_recalls_and_nothing_when_empty(tmp_path: Path, demo: Path) -> None:
    out = json.loads(_interview(demo, "-o", "json").output)
    assert 1 <= len(out["recalls"]) <= cr.MAX_INTERVIEW_RECALLS
    assert {"kind", "text", "source"} <= set(out["recalls"][0])
    assert "recall_issues" not in out or out["recalls"]
    text = _interview(demo).output
    assert "Déjà au dossier" in text and "ne valide rien" in text
    empty = tmp_path / "vide"
    runner.invoke(app, ["cadrage", "new", "Zzz", "--slug", "demo", str(empty)])
    quiet = json.loads(_interview(empty, "-o", "json").output)
    assert quiet["recalls"] == [] and "recall_issues" not in quiet
    assert "Déjà au dossier" not in _interview(empty).output


def test_interview_json_says_a_degraded_memory_when_recalls_exist(demo: Path) -> None:
    out = json.loads(_interview(demo, "-o", "json").output)
    assert out["recalls"] and any("mémoire indisponible" in i for i in out["recall_issues"])


def test_interview_of_a_plain_question_carries_no_recall(demo: Path) -> None:
    out = json.loads(_interview(demo, "-o", "json", "--kind", "question").output)
    assert out["recalls"] == []


def test_interview_recalls_never_raises(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.core.cadrage_interview as interview

    def boom(*_: Any) -> str:
        raise OSError("brief illisible")

    monkeypatch.setattr(interview, "_request_text", boom)
    assert cr.interview_recalls(root, "auth", {"angles": []}).recalls == []


def test_recall_writes_nothing_to_the_cadrage(root: Path) -> None:
    before = {p: p.read_bytes() for p in (root / "_grimoire").rglob("*") if p.is_file()}
    cr.recall(root, "lien magique", memory=lambda q, n: [_entry("x")], tracer=_no_trace)
    assert {p: p.read_bytes() for p in (root / "_grimoire").rglob("*") if p.is_file()} == before


def test_empty_memory_text_oversized_brainstorm_and_unreadable_requirements_are_skipped(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brainstorm = root / "_grimoire" / "cadrage" / "auth" / "02-brainstorm.md"
    brainstorm.write_text("## Écarté (et pourquoi)\n\n- lien magique écarté " + "x" * cr.MAX_FILE_BYTES, encoding="utf-8")
    out = cr.recall(root, "lien magique", memory=lambda q, n: [_entry("   ")], tracer=_no_trace)
    assert {r.kind for r in out.recalls} == {"exigence"}  # texte vide et fichier trop gros : rien, sans bruit

    import grimoire.core.cadrage_requirements as requirements

    def boom(*_: Any) -> Any:
        raise OSError("illisible")

    monkeypatch.setattr(requirements, "read_requirements", boom)
    out = cr.recall(root, "lien magique", memory=lambda q, n: [], tracer=_no_trace)
    assert any("chantier auth illisible" in i for i in out.issues) and out.recalls == []
