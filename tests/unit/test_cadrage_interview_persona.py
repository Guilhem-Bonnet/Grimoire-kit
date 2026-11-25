"""L'orchestrateur mène l'entretien de cadrage : skill livré, émission, renvoi depuis la persona (#1342 M5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.archetypes import bundled_path
from grimoire.hosts.capabilities import HostId
from grimoire.hosts.collect import build_surface, collect_skills
from grimoire.hosts.emitters import apply_plan, emitter_for

SLUG = "grimoire-cadrage"
SKILL_SOURCE = Path(__file__).resolve().parents[2] / "framework" / "hosts" / "skills" / f"{SLUG}.md"


def _skill_text() -> str:
    return SKILL_SOURCE.read_text(encoding="utf-8")


def test_cadrage_interview_persona_skill_carries_the_key_rules() -> None:
    text = _skill_text()
    for marker in (
        "grimoire cadrage interview --json",
        "grimoire cadrage interview answer",
        "grimoire cadrage check",
        "grimoire cadrage plan",
        "Poser une question à la fois",
        "Un incident",
        "trois questions au plus",
        "Brainstormer avant de proposer",
        "pré-mortem",
        "pourquoi pas plus simple",
        "Un « je ne sais pas » devient une hypothèse",
        "il ne les valide jamais",
        "Seule la MOA valide",
        "Ne jamais écrire une validation",
        "Quand ne pas le mener",
        "critère d'acceptation",
        "Selon la nature de la demande",
        "Investigation",
        "Recherche ou comparatif",
        "Anomalie",
        "une réponse prouvée",
        "--kind",
    ):
        assert marker in text, marker


def test_cadrage_interview_persona_skill_is_collected(tmp_path: Path) -> None:
    assert SLUG in {s.slug for s in collect_skills(tmp_path)}


@pytest.mark.parametrize(
    ("host", "relpath"),
    [
        (HostId.CLAUDE_CODE_CLI, f".claude/skills/{SLUG}/SKILL.md"),
        (HostId.GITHUB_COPILOT, f".github/skills/{SLUG}/SKILL.md"),
    ],
)
def test_cadrage_interview_persona_skill_is_emitted(tmp_path: Path, host: HostId, relpath: str) -> None:
    emitter = emitter_for(host)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(tmp_path), tmp_path), tmp_path)
    emitted = (tmp_path / relpath).read_text(encoding="utf-8")
    assert "Seule la MOA valide" in emitted


def test_cadrage_interview_persona_entry_persona_points_to_the_skill() -> None:
    text = (bundled_path() / "meta" / "agents" / "concierge.md").read_text(encoding="utf-8")
    assert SLUG in text
    assert "grimoire cadrage interview" in text
    assert "never validate" in text
