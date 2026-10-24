"""Le wizard en trois questions (issue #888) : pile, qui travaille, domaine.

Critères d'acceptation de l'issue :

- trois questions posées en interactif, aucune en ``-y`` ;
- archétype déduit identique pour une même réponse en CLI et en cockpit
  (même module :mod:`grimoire.cli.init_questions`) ;
- enregistrement de session : un projet Python nu reçoit ``stack`` +
  ``stack-python`` seuls, sans skill transversal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_init
from grimoire.cli.init_questions import (
    DOMAIN_QUESTION,
    QUESTIONS,
    STACK_QUESTION,
    WHO_QUESTION,
    Answers,
    answers_from_dict,
    answers_to_scan,
    apply_adjustment,
    deduce,
    parse_choice,
    prefill,
    questions_payload,
)
from grimoire.core.archetype_resolver import ArchetypeResolver
from grimoire.core.scanner import ScanResult, StackDetection

pytestmark = pytest.mark.covers("cli:init", level="integration")


def _scan(root: Path, *names: str) -> ScanResult:
    return ScanResult(
        stacks=tuple(StackDetection(name=n, confidence=0.9, evidence=(f"{n}-marker",)) for n in names),
        project_type="generic",
        root=root,
    )


# ── Les questions elles-mêmes ─────────────────────────────────────────────────


class TestQuestions:
    def test_exactly_three_questions_in_the_issue_order(self) -> None:
        assert [q.id for q in QUESTIONS] == ["stack", "who", "domain"]
        assert STACK_QUESTION.multiple is True
        assert WHO_QUESTION.multiple is False and DOMAIN_QUESTION.multiple is False

    def test_every_domain_maps_to_a_known_archetype(self) -> None:
        known = set(cmd_init.KNOWN_ARCHETYPES)
        for option in DOMAIN_QUESTION.options:
            assert deduce(Answers(domain=option.id)).archetype in known

    def test_parse_numbers_ids_and_blank(self) -> None:
        assert parse_choice(STACK_QUESTION, "1,3") == ("python", "go")
        assert parse_choice(STACK_QUESTION, "go python") == ("python", "go")  # ordre de la question
        assert parse_choice(STACK_QUESTION, "0") == ()
        assert parse_choice(WHO_QUESTION, "3") == ("team",)
        default = Answers(stacks=("rust",), who="agents", domain="infra")
        assert parse_choice(STACK_QUESTION, "", default) == ("rust",)
        assert parse_choice(DOMAIN_QUESTION, "", default) == ("infra",)

    def test_parse_refuses_unknown_and_multiple_single_answers(self) -> None:
        with pytest.raises(ValueError, match="valid"):
            parse_choice(WHO_QUESTION, "99")
        with pytest.raises(ValueError, match="one answer"):
            parse_choice(DOMAIN_QUESTION, "1,2")

    def test_adjustment_in_one_line(self) -> None:
        base = Answers(stacks=("python",), who="solo", domain="none")
        adjusted = apply_adjustment(base, "domain=web who=team stack=python,go")
        assert adjusted == Answers(stacks=("python", "go"), who="team", domain="web")
        assert apply_adjustment(base, "   ") == base
        with pytest.raises(ValueError, match="unknown key"):
            apply_adjustment(base, "archetype=web-app")


# ── Pré-remplissage et déduction ──────────────────────────────────────────────


class TestPrefill:
    def test_detection_prefills_stack_and_confident_domain(self, tmp_path: Path) -> None:
        scan = _scan(tmp_path, "react", "python", "docker")
        answers = prefill(scan, ArchetypeResolver().resolve(scan))
        assert answers.stacks == ("python", "typescript", "docker")
        assert answers.domain == "web"  # règle React + Python → web-app

    def test_best_guess_is_never_prefilled(self, tmp_path: Path) -> None:
        scan = _scan(tmp_path, "javascript")
        resolved = ArchetypeResolver().resolve(scan)
        (tmp_path / "package.json").write_text('{"devDependencies": {"vite": "1"}}', encoding="utf-8")
        resolved = ArchetypeResolver().resolve(_scan(tmp_path, "javascript"))
        assert resolved.is_best_guess is True
        assert prefill(scan, resolved).domain == "none"

    def test_empty_scan_prefills_nothing(self, tmp_path: Path) -> None:
        assert prefill(_scan(tmp_path)) == Answers(stacks=(), who="solo", domain="none")


class TestDeduce:
    def test_naked_python_solo_gets_stack_and_stack_python_only(self) -> None:
        d = deduce(Answers(stacks=("python",), who="solo", domain="none"))
        assert d.archetypes == ("stack",)
        assert d.stack_skills == ("stack-python",)
        assert d.expertises == ()  # « python » du registre est déjà stack-python : rien de transversal
        assert d.skill_level == "expert"
        assert d.needs == ("solo-prototyping",)
        assert d.standard_profile == "starter"

    def test_rust_is_recommended_as_an_expertise_not_attached(self) -> None:
        d = deduce(Answers(stacks=("rust",), who="learning", domain="none"))
        assert d.stack_skills == ()
        assert d.expertises == ("rust",)
        assert d.skill_level == "beginner"

    def test_who_drives_the_standard_profile(self) -> None:
        assert deduce(Answers(who="team")).standard_profile == "starter"
        from grimoire.core.agentic_standard import resolve_install_plan

        # Le profil est celui que le catalogue des besoins impose (plancher le plus
        # haut de ses patterns), jamais une table recopiée ici.
        for who, need in (("agents", "multi-agent-orchestration"), ("team-ops", "hooks-skills-governance")):
            d = deduce(Answers(who=who))
            assert d.needs == (need,)
            assert d.standard_profile == resolve_install_plan(needs=(need,)).profile
            assert d.standard_profile != "starter"

    def test_ops_people_with_infra_stack_add_infra_ops(self) -> None:
        d = deduce(Answers(stacks=("python", "terraform"), who="team-ops", domain="web"))
        assert d.archetypes == ("web-app", "infra-ops")
        d2 = deduce(Answers(stacks=("python",), who="team-ops", domain="web"))
        assert d2.archetypes == ("web-app",)

    def test_extra_expertises_from_disk_detection_are_merged(self) -> None:
        d = deduce(Answers(stacks=("terraform",)), extra_expertises=("aws",))
        assert d.expertises == ("aws",)
        assert d.stack_skills == ("stack-terraform",)

    def test_answers_become_the_scan_the_scaffold_believes(self, tmp_path: Path) -> None:
        base = _scan(tmp_path, "python", "docker")
        scan = answers_to_scan(Answers(stacks=("python", "go")), base, tmp_path)
        names = [d.name for d in scan.stacks]
        assert names == ["python", "go"]  # docker décoché disparaît, go déclaré apparaît
        assert scan.stacks[0].evidence == ("python-marker",)
        assert scan.stacks[1].evidence == ("declared in wizard",)


# ── Même déduction en CLI et en cockpit ───────────────────────────────────────


class TestSameDeductionCliAndCockpit:
    @pytest.mark.parametrize(
        "raw",
        [
            {"stack": "python", "who": "solo", "domain": "none"},
            {"stack": ["python", "terraform"], "who": "team-ops", "domain": "web"},
            {"stack": "", "who": "agents", "domain": "infra"},
        ],
    )
    def test_cockpit_payload_equals_cli_deduction(self, tmp_path: Path, raw: dict[str, Any]) -> None:
        cli_side = deduce(answers_from_dict(raw))
        cockpit_side = questions_payload(_scan(tmp_path), raw)["deduction"]
        assert cockpit_side["archetype"] == cli_side.archetype
        assert cockpit_side["archetypes"] == list(cli_side.archetypes)
        assert cockpit_side["standard_profile"] == cli_side.standard_profile

    def test_cockpit_route_prefills_from_the_served_project(self, tmp_path: Path) -> None:
        from grimoire.tools.project_setup import setup_questions_view

        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        payload = setup_questions_view(tmp_path, {})
        assert payload["defaults"]["stack"] == ["python"]
        assert payload["deduction"]["archetype"] == "stack"
        assert [q["id"] for q in payload["questions"]] == ["stack", "who", "domain"]
        # `?prefill=0` : le formulaire « nouveau projet » ne lit pas ce dossier.
        assert setup_questions_view(tmp_path, {"prefill": ["0"]})["defaults"]["stack"] == []
        # Une réponse invalide est nommée, jamais une 500.
        bad = setup_questions_view(tmp_path, {"who": ["99"]})
        assert "error" in bad and bad["deduction"]["archetype"] == "stack"


# ── Le wizard CLI ─────────────────────────────────────────────────────────────


def _hermetic_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_init, "_choose_memory_profile", lambda *a, **kw: ("lexical", "lexical", False, False, False))


class TestWizardAsksThreeQuestions:
    def test_three_questions_asked_in_order_with_detected_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_memory(monkeypatch)
        asked: list[tuple[str, str]] = []

        def ask(prompt: str, **kw: Any) -> str:
            asked.append((prompt, str(kw.get("default", ""))))
            return str(kw.get("default", ""))

        monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(ask))
        monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(lambda *a, **kw: kw.get("default", False)))
        scan = _scan(tmp_path, "python")
        result = cmd_init._run_wizard(tmp_path, scan, ArchetypeResolver().resolve(scan), "lexical")

        choices = [(p, d) for p, d in asked if "Choice" in p]
        assert len(choices) == 3
        assert choices[0][1] == str(STACK_QUESTION.index_of("python"))  # pile pré-remplie
        assert choices[1][1] == str(WHO_QUESTION.index_of("solo"))
        assert choices[2][1] == str(DOMAIN_QUESTION.index_of("none"))
        assert not any("Skill:" in p or "archetype" in p.lower() for p, _ in asked)
        assert result["archetypes"] == ["stack"]
        assert result["needs"] == ["solo-prototyping"]

    def test_answers_change_the_deduction_and_the_adjust_line_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        _hermetic_memory(monkeypatch)
        scripted = iter(["", "", "1", "python,go", "3", "web", "domain=infra who=team-ops", ""])
        monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(lambda *a, **kw: next(scripted)))
        monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(lambda *a, **kw: True))
        result = cmd_init._run_wizard(tmp_path, _scan(tmp_path, "python"), ArchetypeResolver().resolve(_scan(tmp_path, "python")), "lexical")
        assert result["archetypes"] == ["infra-ops"]
        assert result["answers"] == {"stack": ["python", "go"], "who": "team-ops", "domain": "infra"}
        assert result["standard_profile"] == "governed"
        assert [d.name for d in result["scan"].stacks] == ["python", "go"]
        printed = capsys.readouterr().err
        assert "Archetype:   web-app" in printed  # premier résumé
        assert "Archetype:   infra-ops" in printed  # après ajustement

    def test_unreadable_answer_is_reasked_then_default_stands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_memory(monkeypatch)
        calls = {"n": 0}

        def ask(prompt: str, **kw: Any) -> str:
            if "Choice" in prompt:
                calls["n"] += 1
                return "zzz"
            return str(kw.get("default", ""))

        monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(ask))
        monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(lambda *a, **kw: True))
        result = cmd_init._run_wizard(tmp_path, _scan(tmp_path), ArchetypeResolver().resolve(_scan(tmp_path)), "lexical")
        assert calls["n"] == 9  # 3 essais × 3 questions
        assert result["archetype"] == "stack"


class TestExpressAsksNothing:
    def test_yes_mode_never_prompts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        def boom(*a: Any, **kw: Any) -> str:
            msg = "no question allowed in -y"
            raise AssertionError(msg)

        from grimoire.cli.app import app

        with patch.object(cmd_init.Prompt, "ask", staticmethod(boom)), patch.object(cmd_init.Confirm, "ask", staticmethod(boom)):
            result = CliRunner().invoke(app, ["-y", "init", str(tmp_path), "--backend", "lexical"])
        assert result.exit_code == 0, result.output
        text = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
        assert 'archetype: "stack"' in text


# ── Enregistrement de session : Python nu ─────────────────────────────────────


class TestRecordedSessionNakedPython:
    def test_naked_python_receives_stack_and_stack_python_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saisie simulée : Enter partout (les défauts viennent de la détection)."""
        monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
        _hermetic_memory(monkeypatch)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(lambda *a, **kw: str(kw.get("default", ""))))
        monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(lambda *a, **kw: kw.get("default", False)))

        from grimoire.cli.app import app

        result = CliRunner().invoke(app, ["init", str(tmp_path), "--interactive"])
        assert result.exit_code == 0, result.output

        text = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
        assert 'archetype: "stack"' in text
        assert 'skill_level: "expert"' in text
        agent = (tmp_path / "_grimoire" / "kit" / "agents" / "stack-engineer.md").read_text(encoding="utf-8")
        assert 'skills: ["stack-python"]' in agent
        stack_skills = sorted(p.name for p in (tmp_path / "_grimoire" / "kit" / "skills").glob("stack-*.md"))
        assert stack_skills == ["stack-python.md"]
        assert not (tmp_path / "_grimoire" / "overrides" / "skills").exists()  # aucune expertise attachée d'office


class TestUpReadsTheDeducedNeeds:
    def test_interactive_up_uses_wizard_needs_when_none_given(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.cli import cmd_up

        seen: dict[str, Any] = {}

        def fake_init(ctx: Any, target: Path, **kw: Any) -> None:
            (target / "project-context.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
            ctx.obj["init_deduced_needs"] = ["multi-agent-orchestration"]

        def fake_standard(state: Any, target: Path, **kw: Any) -> None:
            seen["needs"] = kw.get("needs")

        monkeypatch.setattr("grimoire.cli.cmd_init.run_init", fake_init)
        for name in ("_step_structure", "_step_refresh", "_step_override_review", "_step_host_sync", "_step_identity", "_step_cadrage"):
            monkeypatch.setattr(cmd_up, name, lambda *a, **kw: None)
        monkeypatch.setattr(cmd_up, "_step_doctor_summary", lambda *a, **kw: [])
        monkeypatch.setattr(cmd_up, "_step_standard", fake_standard)

        from types import SimpleNamespace

        # `run_up_pipeline` ne lit que `ctx.obj` (même raccourci que project_setup).
        ctx: Any = SimpleNamespace(obj={"yes": False, "output": "text"})
        cmd_up.run_up_pipeline(ctx, tmp_path, name="x", user="u", archetypes=[], backend="lexical", no_standard=False, needs=[], dry_run=False, no_cockpit=True, interactive=True, quiet=True)
        assert seen.get("needs") == ["multi-agent-orchestration"]
        assert "init_deduced_needs" not in ctx.obj
