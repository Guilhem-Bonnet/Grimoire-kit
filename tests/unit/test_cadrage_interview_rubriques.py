"""Entretien guidé : chaque réponse va dans la rubrique que vise sa question, « sans objet » et « je ne sais pas » aussi.

La route du cockpit et la commande ``grimoire cadrage interview`` écrivent le même fichier (même ``answer``) ; ``retract``
retire proprement ce qui a été écrit, y compris pour un état d'avant le suivi des lignes écrites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage_progress import progress
from grimoire.core.chantiers import new_chantier
from grimoire.tools import atelier_blocs
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_assistant as asst
from grimoire.tools import workspace_atelier_guide as guide

FILES = ("01-brief.md", "02-brainstorm.md", "03-comprehension.md", "05-cahier-des-charges.md", "entretien.json")
Q_USER = "Qui utilisera cela, et dans quelle situation concrète ?"
Q_PROBLEM = "Que fait cette personne aujourd'hui pour s'en passer, et qu'est-ce qui l'agace ?"
Q_SUCCESS = "À quoi voit-on, concrètement, que c'est réussi ?"
Q_EXCLUDED = "Qu'est-ce qui est explicitement exclu de cette livraison ?"


def _project(base: Path, name: str) -> Path:
    root = base / name
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    wa.set_atelier(root, True)
    new_chantier(root, "Un chantier", slug="un")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _project(tmp_path, "p")


def _read(root: Path, name: str) -> str:
    return (root / "_grimoire" / "cadrage" / "un" / name).read_text(encoding="utf-8")


def _section(root: Path, name: str, title: str) -> str:
    return _read(root, name).split(f"## {title}")[1].split("\n## ")[0]


def _all(root: Path) -> dict[str, str]:
    return {name: _read(root, name) for name in FILES}


def _row(root: Path, phase: str) -> dict[str, Any]:
    return next(p for p in progress(root, "un")["phases"] if p["id"] == phase)


def test_each_answer_goes_into_the_rubric_its_question_targets(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "utilisateurs", "Les comptables")
    interview.answer(project, "un", "utilisateurs", "Ils ressaisissent les factures à la main")
    user = _section(project, "01-brief.md", "L'utilisateur")
    problem = _section(project, "01-brief.md", "Le problème")
    assert f"- *{Q_USER}* Les comptables" in user and "ressaisissent" not in user
    assert f"- *{Q_PROBLEM}* Ils ressaisissent les factures à la main" in problem
    assert _row(project, "brief")["sections"]["Le problème"] == "rempli"  # l'avancement le voit


def test_the_answer_line_keeps_the_blocks_and_the_progress_sound(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "Un export CSV s'ouvre dans le tableur")
    blocks = atelier_blocs.split("brief", _read(project, "01-brief.md"))
    line = f"- *{Q_SUCCESS}* Un export CSV s'ouvre dans le tableur"
    assert [b.kind for b in blocks if b.text == line] == ["liste"]  # un bloc, pas de structure cassée
    assert _row(project, "brief")["sections"]["Un succès ressemble à"] == "rempli"


def test_an_answer_beyond_the_seeds_or_with_its_own_question(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "Un", question="Quel *verdict* ?\nEt après ?")
    interview.answer(project, "un", "criteres", "Deux")
    interview.answer(project, "un", "criteres", "Trois")  # au-delà des amorces : la rubrique de l'angle, sans question
    section = _section(project, "01-brief.md", "Un succès ressemble à")
    assert "- *Quel verdict ? Et après ?* Un" in section and "- *Quel verdict mesurable (test, commande, revue) le prouverait ?* Deux" in section
    assert "\n- Trois" in section


def test_alternatives_split_between_pistes_and_ecarte(project: Path) -> None:
    interview.interview_plan(project, "un", depth="approfondi")
    interview.answer(project, "un", "alternatives", "Un tableur partagé")
    interview.answer(project, "un", "alternatives", "Un script jetable : trop fragile")
    assert "Un tableur partagé" in _section(project, "02-brainstorm.md", "Pistes")
    assert "Un script jetable" in _section(project, "02-brainstorm.md", "Écarté (et pourquoi)")


def test_je_ne_sais_pas_writes_the_question_as_an_open_question(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "", unknown=True)
    interview.answer(project, "un", "hors-perimetre", "à voir avec le métier", unknown=True)
    opened = _section(project, "02-brainstorm.md", "Questions ouvertes")
    assert f"- À préciser : {Q_SUCCESS}" in opened
    assert f"- À préciser : {Q_EXCLUDED} — à voir avec le métier" in opened
    assert "À valider (je ne sais pas)" not in _read(project, "03-comprehension.md")
    assert [b.kind for b in atelier_blocs.split("brainstorm", _read(project, "02-brainstorm.md")) if "À préciser" in b.text] == [
        "question", "question",
    ]
    assert progress(project, "un")["a_confirmer"] == 2  # compté « je ne sais pas », comme avant
    interview.answer(project, "un", "criteres", "", unknown=True)  # redit : pas de doublon
    assert _read(project, "02-brainstorm.md").count(f"À préciser : {Q_SUCCESS}") == 1


def test_sans_objet_marks_the_rubric_in_place_and_leaves_the_brainstorm_alone(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    before = _read(project, "02-brainstorm.md")
    interview.answer(project, "un", "hors-perimetre", "sans objet", skip=True)
    interview.answer(project, "un", "contraintes", "Hors de portée du métier", skip=True)
    assert _section(project, "05-cahier-des-charges.md", "Hors périmètre").strip() == "> À compléter — sans objet d'après l'entretien (à confirmer)"
    assert _section(project, "03-comprehension.md", "Contraintes").strip() == (
        "> À compléter — sans objet d'après l'entretien : Hors de portée du métier (à confirmer)"
    )
    # Le découpage en blocs le voit comme un emplacement non rempli, sans le casser.
    blocks = atelier_blocs.split("cahier-des-charges", _read(project, "05-cahier-des-charges.md"))
    assert any(b.data.get("placeholder") and "sans objet" in b.text for b in blocks)
    assert _read(project, "02-brainstorm.md") == before
    scope = _row(project, "cahier-des-charges")
    assert scope["sections"]["Hors périmètre"] == "sans-objet" and scope["sans_objet"] == 1
    assert scope["vides"] == scope["total"] - 1  # traité : plus au gabarit


def test_sans_objet_never_satisfies_the_gate(project: Path) -> None:
    """Le gate du cahier des charges rend les mêmes erreurs avant et après un « sans objet » (question laissée à l'humain)."""
    from grimoire.core.cadrage import check

    interview.interview_plan(project, "un", depth="standard")
    before = [e for e in check(project, "un")[0] if "Cahier des charges" in e or "Exigences" in e]
    assert any("Hors périmètre" in e and "Risques" in e for e in before)
    for angle in ("hors-perimetre", "risques"):
        interview.answer(project, "un", angle, "sans objet", skip=True)
    assert "sans objet d'après l'entretien" in _section(project, "05-cahier-des-charges.md", "Risques")  # visible dans le document
    after = [e for e in check(project, "un")[0] if "Cahier des charges" in e or "Exigences" in e]
    assert after == before  # mais le gate ne l'a pas lu comme rempli
    assert _row(project, "cahier-des-charges")["sections"]["Risques"] == "sans-objet"  # et l'avancement le dit


def test_a_human_written_sans_objet_still_fills_the_rubric_for_the_gate(project: Path) -> None:
    """Comme sur main : un « > Sans objet » écrit à la main est du contenu pour `cadrage check` (rubrique remplie)."""
    from grimoire.core.cadrage import PHASES, check, phase_report

    path = project / "_grimoire/cadrage/un/05-cahier-des-charges.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("> À compléter — risques.", "> Sans objet : pas de risque identifié"),
        encoding="utf-8",
    )
    report = phase_report(project, next(p for p in PHASES if p.id == "cahier-des-charges"), "un")
    assert report["sections"]["Risques"] == "rempli"
    assert not any("Risques" in e and "Cahier des charges" in e for e in check(project, "un")[0])


def test_retract_of_a_sans_objet_restores_the_exact_template(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    before = _read(project, "05-cahier-des-charges.md")
    interview.answer(project, "un", "risques", "sans objet", skip=True)
    assert interview.retract(project, "un", "risques")["removed"] is True
    assert _read(project, "05-cahier-des-charges.md") == before


def test_sans_objet_on_a_rubric_that_already_has_content_writes_nothing(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "hors-perimetre", "Le reste")
    kept = _read(project, "05-cahier-des-charges.md")
    interview.answer(project, "un", "hors-perimetre", "sans objet", skip=True)
    assert _read(project, "05-cahier-des-charges.md") == kept
    assert _row(project, "cahier-des-charges")["sections"]["Hors périmètre"] == "rempli"
    assert interview.retract(project, "un", "hors-perimetre")["removed"] is True  # rien à retirer, et c'est dit sans erreur


def test_retract_leaves_the_files_as_they_were(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    before = _all(project)
    for angle, text, kwargs in (
        ("utilisateurs", "Les comptables", {}),
        ("criteres", "", {"unknown": True}),
        ("hors-perimetre", "sans objet", {"skip": True}),
    ):
        interview.answer(project, "un", angle, text, **kwargs)
        assert _all(project) != before
        done = interview.retract(project, "un", angle)
        assert done["removed"] is True
        assert {k: v for k, v in _all(project).items() if k != "entretien.json"} == {
            k: v for k, v in before.items() if k != "entretien.json"
        }
    interview.answer(project, "un", "criteres", "ctx", unknown=True)
    assert interview.retract(project, "un", "criteres") == {"kind": "unknown", "text": "ctx", "removed": True}


def test_retract_of_a_state_written_before_the_lines_were_tracked(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "utilisateurs", "Les comptables")
    state = project / "_grimoire/cadrage/un/entretien.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    del data["angles"]["utilisateurs"]["lines"]  # un état d'avant : la ligne n'avait pas de question devant
    state.write_text(json.dumps(data), encoding="utf-8")
    path = project / "_grimoire/cadrage/un/01-brief.md"
    path.write_text(path.read_text(encoding="utf-8").replace(f"- *{Q_USER}* Les comptables", "- Les comptables"), encoding="utf-8")
    assert interview.retract(project, "un", "utilisateurs") == {"kind": "answer", "text": "Les comptables", "removed": True}
    old_skip = {"state": "ecarte", "reason": "inutile", "answers": []}
    data["angles"]["utilisateurs"] = old_skip
    state.write_text(json.dumps(data), encoding="utf-8")
    brainstorm = project / "_grimoire/cadrage/un/02-brainstorm.md"
    brainstorm.write_text(
        brainstorm.read_text(encoding="utf-8").replace(
            "> À compléter — écarté (et pourquoi).", "- Angle « Utilisateurs et usage réel » écarté : inutile"
        ),
        encoding="utf-8",
    )
    assert interview.retract(project, "un", "utilisateurs")["removed"] is True
    assert "écarté : inutile" not in _read(project, "02-brainstorm.md")


def test_retract_of_an_unknown_written_before_the_lines_were_tracked(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    state = project / "_grimoire/cadrage/un/entretien.json"
    note = f"{interview.UNKNOWN_PREFIX}Critères de réussite — à voir"
    state.write_text(json.dumps({"depth": "standard", "angles": {"criteres": {"state": "couvert", "answers": [note]}}}), encoding="utf-8")
    path = project / "_grimoire/cadrage/un/03-comprehension.md"
    path.write_text(path.read_text(encoding="utf-8").replace("> À compléter — hypothèses.", f"- {note}"), encoding="utf-8")
    assert interview.retract(project, "un", "criteres") == {"kind": "unknown", "text": "à voir", "removed": True}
    assert "À valider" not in _read(project, "03-comprehension.md")


def test_the_trace_is_realigned_when_it_outgrows_the_answers(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "Un")
    state = project / "_grimoire/cadrage/un/entretien.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["angles"]["criteres"]["lines"].append(data["angles"]["criteres"]["lines"][0])  # une trace de trop
    state.write_text(json.dumps(data), encoding="utf-8")
    interview.answer(project, "un", "criteres", "Deux")
    assert len(json.loads(state.read_text(encoding="utf-8"))["angles"]["criteres"]["lines"]) == 2
    assert interview.retract(project, "un", "criteres")["removed"] is True
    assert "Deux" not in _read(project, "01-brief.md") and "Un" in _read(project, "01-brief.md")


def test_retract_removes_the_last_identical_line_not_one_copied_above(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "Un export")
    line = f"- *{Q_SUCCESS}* Un export"
    path = project / "_grimoire/cadrage/un/01-brief.md"
    path.write_text(path.read_text(encoding="utf-8").replace(line, f"{line}\n- Notes de l'humain\n{line}"), encoding="utf-8")
    interview.retract(project, "un", "criteres")
    kept = _section(project, "01-brief.md", "Un succès ressemble à")
    assert kept.count(line) == 1 and kept.index(line) < kept.index("Notes de l'humain")  # la copie du haut reste...
    assert "Notes de l'humain" in kept


def test_an_accepted_document_is_never_written_or_unwritten_by_the_interview(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "criteres", "Un export")
    path = project / "_grimoire/cadrage/un/01-brief.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: draft", "status: accepted", 1), encoding="utf-8")
    frozen = path.read_bytes()
    with pytest.raises(ValueError, match="accepté"):
        interview.retract(project, "un", "criteres")
    with pytest.raises(ValueError, match="accepté"):
        interview.answer(project, "un", "criteres", "Deux")
    assert path.read_bytes() == frozen


def test_the_cockpit_route_and_the_command_write_the_same_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asst, "agent_status", lambda root: {"available": False, "mode": "kanban", "reason": "", "name": ""})
    page, cli = _project(tmp_path, "page"), _project(tmp_path, "cli")

    def step() -> dict[str, Any]:
        current = guide.view(page, "un")["current"]
        return {"angle": current["angle"], "index": current["index"]}

    asst.act(page, "un", {"geste": "guide-demarrer"})
    asst.act(page, "un", {"geste": "guide-repondre", "text": "Les comptables", **step()})
    asst.act(page, "un", {"geste": "guide-ne-sais-pas", **step()})  # 2e question de « utilisateurs » : l'angle se ferme
    asst.act(page, "un", {"geste": "guide-sans-objet", **step()})
    asst.act(page, "un", {"geste": "guide-repondre", "text": "Le reste", **step()})
    interview.interview_plan(cli, "un")
    interview.answer(cli, "un", "utilisateurs", "Les comptables")
    interview.answer(cli, "un", "utilisateurs", "", unknown=True)
    interview.answer(cli, "un", "criteres", "sans objet", skip=True)
    interview.answer(cli, "un", "hors-perimetre", "Le reste")
    assert _all(page) == _all(cli)
    asst.act(page, "un", {"geste": "guide-retour", "index": 0})
    interview.retract(cli, "un", "hors-perimetre")
    assert _all(page) == _all(cli)


@pytest.mark.parametrize("bad", [
    {"lines": "x"}, {"lines": [{"phase": "brief"}]}, {"lines": [{"phase": "zzz", "section": "a", "line": "b"}]},
    {"skip": {"phase": "brief"}}, {"skip": "oui"},
    # une trace éditée à la main ne peut viser ni une exigence, ni une rubrique que l'angle ne vise pas, ni une ligne étrangère
    {"lines": [{"phase": "exigences", "section": "Fonctionnelles", "line": "- REQ-001 : le système exporte"}]},
    {"lines": [{"phase": "brief", "section": "Pourquoi maintenant", "line": "- x"}]},
    {"lines": [{"phase": "brief", "section": "Un succès ressemble à", "line": "REQ-001 : sans puce"}]},
    {"lines": [{"phase": "brief", "section": "Un succès ressemble à", "line": "- a\n- REQ-001 : deux lignes"}]},
    {"lines": [{"phase": "brief", "section": "Un succès ressemble à", "line": "- a", "extra": "x"}]},
    {"lines": [{"phase": "brief", "section": "Un succès ressemble à", "line": "- À préciser : x"}]},
    {"skip": {"phase": "exigences", "section": "Fonctionnelles", "line": "> À compléter — sans objet d'après l'entretien (à confirmer)"}},
    {"skip": {"phase": "brief", "section": "Un succès ressemble à", "line": "- REQ-001 : x"}},
])
def test_a_malformed_trace_in_the_state_is_refused(project: Path, bad: dict[str, Any]) -> None:
    state = project / "_grimoire/cadrage/un/entretien.json"
    state.write_text(json.dumps({"depth": "standard", "angles": {"criteres": {"answers": [], **bad}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalide"):
        interview.view(project, "un")
