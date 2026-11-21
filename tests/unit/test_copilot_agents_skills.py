"""#1280 P2-1 / P2-3 : `model` des agents Copilot, `agents:` documenté, champs d'invocation des skills et prompts.

Vrai projet temporaire, vrai émetteur ; l'émission Claude Code sert de témoin de non-régression.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.collect import build_surface, collect_skills
from grimoire.hosts.emitters import apply_plan, emitter_for
from grimoire.hosts.emitters.base import EmitPlan
from grimoire.hosts.matrix import build_matrix
from grimoire.hosts.surface import CommandSpec

AGENT_DIR = Path("_grimoire/_config/custom/agents")
SKILL_DIR = Path("_grimoire/kit/skills")
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


def _agent(root: Path, name: str, *, reasoning: str, cost: str) -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (root / AGENT_DIR / f"{name}.md").write_text(
        f'---\nname: "{name}"\ndescription: "{name} test"\nmodel_affinity:\n  reasoning: {reasoning}\n'
        f"  context_window: medium\n  cost: {cost}\n---\n\nTu es {name}.\n",
        encoding="utf-8",
    )


def _policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude", "copilot"]\n',
        encoding="utf-8",
    )
    _agent(tmp_path, "concierge", reasoning="high", cost="low")  # sonnet
    _agent(tmp_path, "architecte", reasoning="high", cost="high")  # opus
    _agent(tmp_path, "scribe", reasoning="low", cost="medium")  # haiku
    _agent(tmp_path, "moyen", reasoning="medium", cost="medium")  # inherit
    return tmp_path


def _emit(root: Path, host: HostId) -> tuple[dict[str, str], EmitPlan]:
    emitter = emitter_for(host)
    assert emitter is not None
    plan = emitter.plan(build_surface(root), root)
    apply_plan(plan, root)
    return {str(f.relpath): f.content for f in plan.files}, plan


def _copilot(root: Path) -> tuple[dict[str, str], EmitPlan]:
    return _emit(root, HostId.GITHUB_COPILOT)


def _model_line(text: str) -> str | None:
    return next((line for line in text.splitlines() if line.startswith("model:")), None)


def test_no_model_emitted_without_copilot_models_and_gap_stays_declared(project: Path) -> None:
    files, plan = _copilot(project)
    for name in ("concierge", "architecte", "scribe", "moyen"):
        assert _model_line(files[f".github/agents/{name}.agent.md"]) is None
    gap = next(d for d in plan.degradations if d.surface == "model affinity")
    for name in ("concierge", "architecte", "scribe"):
        assert name in gap.reason
    assert "moyen" not in gap.reason
    assert "model-policy" not in "".join(files.values())


def test_model_emitted_only_for_a_declared_tier(project: Path) -> None:
    _policy(project, "dispatch:\n  copilot_models:\n    haiku: claude-haiku-4.5\n    sonnet: claude-sonnet-4.5\n")
    files, plan = _copilot(project)
    assert _model_line(files[".github/agents/scribe.agent.md"]) == "model: 'claude-haiku-4.5'"
    assert _model_line(files[".github/agents/concierge.agent.md"]) == "model: 'claude-sonnet-4.5'"
    # opus non déclaré : rien d'inventé ; inherit : rien.
    assert _model_line(files[".github/agents/architecte.agent.md"]) is None
    assert _model_line(files[".github/agents/moyen.agent.md"]) is None
    gap = next(d for d in plan.degradations if d.surface == "model affinity")
    assert "architecte" in gap.reason and "scribe" not in gap.reason and "concierge" not in gap.reason
    assert "model-policy" not in "".join(files.values())


def test_no_model_degradation_when_every_requested_tier_is_mapped(project: Path) -> None:
    _policy(project, "dispatch:\n  copilot_models:\n    haiku: h\n    sonnet: s\n    opus: o\n")
    files, plan = _copilot(project)
    assert "model affinity" not in {d.surface for d in plan.degradations}
    assert _model_line(files[".github/agents/architecte.agent.md"]) == "model: 'o'"
    assert _model_line(files[".github/agents/moyen.agent.md"]) is None


def test_tool_names_are_the_documented_copilot_aliases(project: Path) -> None:
    files, _ = _copilot(project)
    documented = {"execute", "read", "edit", "search", "agent", "web", "todo"}
    for rel, text in files.items():
        if not rel.startswith(".github/agents/"):
            continue
        line = next(line for line in text.splitlines() if line.startswith("tools:"))
        names = {n.strip(" '") for n in line.removeprefix("tools:").strip(" []").split(",")}
        assert names <= documented, (rel, names - documented)


def test_agents_allow_list_stays_on_the_entry_agent_and_is_declared(project: Path) -> None:
    files, plan = _copilot(project)
    entry = files[".github/agents/concierge.agent.md"]
    assert any(line.startswith("agents:") for line in entry.splitlines())
    assert not any(line.startswith("agents:") for line in files[".github/agents/scribe.agent.md"].splitlines())
    gap = next(d for d in plan.degradations if d.surface == "agents allow-list")
    assert "Local" in gap.reason and "task" in gap.reason


def test_claude_code_agent_files_are_unchanged_by_copilot_models(project: Path) -> None:
    claude, _ = _emit(project, HostId.CLAUDE_CODE_CLI)
    before = {k: v for k, v in claude.items() if k.startswith(".claude/agents/")}
    assert before
    _policy(project, "dispatch:\n  copilot_models:\n    haiku: claude-haiku-4.5\n")
    after, _ = _emit(project, HostId.CLAUDE_CODE_CLI)
    assert before == {k: v for k, v in after.items() if k.startswith(".claude/agents/")}
    assert "model: 'haiku'" in before[".claude/agents/scribe.md"]


def _skill(root: Path, slug: str, extra: str) -> None:
    (root / SKILL_DIR).mkdir(parents=True, exist_ok=True)
    (root / SKILL_DIR / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: Skill {slug}\n{extra}---\nCorps {slug}.\n", encoding="utf-8"
    )


def test_skill_invocation_fields_are_read_and_emitted_for_copilot(project: Path) -> None:
    _skill(project, "manuel", "user-invocable: true\ndisable-model-invocation: true\nargument-hint: '[fichier]'\n")
    _skill(project, "interne", "user-invocable: false\n")
    _skill(project, "nu", "")
    by_slug = {s.slug: s for s in collect_skills(project)}
    assert by_slug["manuel"].disable_model_invocation is True
    assert by_slug["nu"].user_invocable is None
    files, _ = _copilot(project)
    manuel = files[".github/skills/manuel/SKILL.md"]
    assert "argument-hint: '[fichier]'" in manuel
    assert "user-invocable: true" in manuel
    assert "disable-model-invocation: true" in manuel
    interne = files[".github/skills/interne/SKILL.md"]
    assert "user-invocable: false" in interne and "disable-model-invocation" not in interne
    nu = files[".github/skills/nu/SKILL.md"]
    assert not any(k in nu for k in ("user-invocable", "disable-model-invocation", "argument-hint"))


def test_claude_code_skill_files_ignore_the_copilot_invocation_fields(project: Path) -> None:
    _skill(project, "manuel", "disable-model-invocation: true\nargument-hint: '[x]'\n")
    claude, _ = _emit(project, HostId.CLAUDE_CODE_CLI)
    text = claude[".claude/skills/manuel/SKILL.md"]
    assert "disable-model-invocation" not in text and "argument-hint" not in text


def test_prompt_file_carries_argument_hint() -> None:
    from grimoire.hosts.emitters.copilot import _prompt_file

    cmd = CommandSpec(slug="x", description="d", body="corps\n", argument_hint="[cible]")
    assert "argument-hint: '[cible]'" in _prompt_file(cmd).content
    assert "argument-hint" not in _prompt_file(CommandSpec(slug="y", description="d", body="b\n")).content


def test_host_matrix_stays_coherent_with_models_declared(project: Path) -> None:
    _policy(project, "dispatch:\n  copilot_models:\n    haiku: claude-haiku-4.5\n")
    matrix = build_matrix(project, (HostId.CLAUDE_CODE_CLI, HostId.GITHUB_COPILOT))
    assert matrix.ok
