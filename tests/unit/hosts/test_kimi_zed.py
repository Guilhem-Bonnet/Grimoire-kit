"""#1281 L8 : Kimi Code et Zed (#725), deux hôtes sans garde, déclarés comme tels.

Sources relues le 2026-11-22 (voir les modules émetteurs) :

- Kimi Code : https://moonshotai.github.io/kimi-code/en/customization/agents.html,
  https://moonshotai.github.io/kimi-code/en/customization/skills.html,
  https://moonshotai.github.io/kimi-code/en/customization/mcp.html,
  https://moonshotai.github.io/kimi-code/en/customization/hooks.md ;
- Zed : https://zed.dev/docs/ai/instructions, https://zed.dev/docs/ai/skills,
  https://zed.dev/docs/ai/mcp, https://zed.dev/docs/reference/all-settings, https://zed.dev/docs/tasks.

Aucun des deux n'a été lancé : les tests vérifient ce que le kit **écrit**, que la
configuration de l'utilisateur est préservée, et que rien n'est annoncé bloquant.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import (
    GovernanceFunction,
    ParityLevel,
    function_states,
    gaps_for,
    profile_for,
    resolve_host,
)
from grimoire.hosts.collect import build_surface
from grimoire.hosts.detection import detect_enabled_hosts
from grimoire.hosts.emitters import EmitPlan, apply_plans, emitter_for
from grimoire.hosts.emitters.base import CONFLICT_UNREADABLE
from grimoire.hosts.emitters.zed import GOVERNED_TASK, GOVERNED_TASK_LABEL, ZED_INSTRUCTION_FILES
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import ENV_OVERRIDE
from grimoire.hosts.matrix import build_matrix
from grimoire.hosts.surface import SkillSpec

pytestmark = pytest.mark.covers("host:kimi-code", "host:zed", level="real")

AGENT_DIR = Path("_grimoire/_config/custom/agents")
NO_GUARD = (HostId.KIMI_CODE, HostId.ZED)
#: Champs d'agent Kimi Code écrits par le kit, tous documentés (customization/agents).
KIMI_AGENT_FIELDS = {"name", "description"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OVERRIDE, "grimoire-hook")
    for var in ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "GRIMOIRE_TASK_ID", "GRIMOIRE_HOST_PROBE"):
        monkeypatch.delenv(var, raising=False)


def _agent(root: Path, name: str) -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (root / AGENT_DIR / f"{name}.md").write_text(
        f'---\nname: "{name}"\ndescription: "{name} test"\ntools: [read, edit]\n---\n\nTu es {name}.\n',
        encoding="utf-8",
    )


def _hosts(root: Path, *aliases: str) -> None:
    (root / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        f"hosts:\n  enabled: {json.dumps(list(aliases))}\n",
        encoding="utf-8",
    )


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    _agent(tmp_path, "architecte")
    _agent(tmp_path, "scribe")
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": ["serve"]}, "autre": {"command": "x"}}}
        ),
        encoding="utf-8",
    )
    return tmp_path


def _plan(root: Path, host: HostId) -> EmitPlan:
    emitter = emitter_for(host)
    assert emitter is not None
    return emitter.plan(build_surface(root), root)


def _sync(root: Path, host: HostId, *, force: bool = False) -> None:
    (result,) = apply_plans([_plan(root, host)], root, force=force)
    assert result.ok, result.conflicts


def _frontmatter(text: str) -> dict[str, object]:
    assert text.startswith("---\n")
    head = text.split("---\n", 2)[1]
    loaded = yaml.safe_load(head)
    assert isinstance(loaded, dict)
    return loaded


# ── Profils : aucune garde, rien d'annoncé bloquant ─────────────────────────


@pytest.mark.parametrize("host", NO_GUARD)
def test_no_guard_host_claims_no_refusal(host: HostId) -> None:
    profile = profile_for(host)
    assert not profile.enforces_blocking
    assert profile.documented_blocking_events == frozenset()
    assert profile.supported_events() == ()
    assert not any(profile.writes_refusal(event) for event in HookEvent)
    gap = next(g for g in gaps_for(profile) if g.surface == "blocking_hooks")
    assert "CI" in gap.fallback
    states = function_states(host)
    assert states[GovernanceFunction.TOOL_GUARD].level is ParityLevel.ABSENT
    assert states[GovernanceFunction.WRITE_TRACE].level is ParityLevel.ABSENT
    assert all(state.level is not ParityLevel.RICH for state in states.values())


def test_aliases_and_detection() -> None:
    assert resolve_host("kimi") is resolve_host("kimi-code") is HostId.KIMI_CODE
    assert resolve_host("zed") is HostId.ZED
    assert profile_for(HostId.KIMI_CODE).acp_command == ("kimi", "acp")
    assert profile_for(HostId.ZED).acp_command == ()


def test_detection_reads_project_dirs(tmp_path: Path) -> None:
    (tmp_path / ".kimi-code").mkdir()
    (tmp_path / ".zed").mkdir()
    assert detect_enabled_hosts(tmp_path) == ("kimi", "zed")


def test_host_list_json_declares_both_without_guard(governed: Path) -> None:
    result = CliRunner().invoke(app, ["-o", "json", "host", "list"])
    assert result.exit_code == 0, result.output
    hosts = {entry["host_id"]: entry for entry in json.loads(result.output)["hosts"]}
    for host in NO_GUARD:
        entry = hosts[host.value]
        assert entry["surfaces"]["blocking_hooks"] is False
        assert entry["unverified_blocking_events"] == []
        assert entry["hook_events"] == []
    assert hosts[HostId.ZED.value]["instructions_entrypoint"] == ".rules"
    table = CliRunner().invoke(app, ["host", "list"])
    assert "Kimi Code" in table.output and "Zed" in table.output


# ── Kimi Code ───────────────────────────────────────────────────────────────


def test_kimi_writes_agents_skills_mcp_and_agents_md(governed: Path) -> None:
    _hosts(governed, "kimi")
    plan = _plan(governed, HostId.KIMI_CODE)
    _sync(governed, HostId.KIMI_CODE)

    agent = (governed / ".kimi-code/agents/architecte.md").read_text(encoding="utf-8")
    assert set(_frontmatter(agent)) == KIMI_AGENT_FIELDS
    assert "grimoire:managed" in agent and "architecte" in agent
    assert list((governed / ".agents/skills").glob("*/SKILL.md"))
    mcp = json.loads((governed / ".kimi-code/mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": ["serve"]}}}
    agents_md = (governed / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.count("<!-- grimoire:begin") == 1
    assert "Kimi Code" in agents_md and "kimi --agent architecte" in agents_md
    assert "ne sont pas opposables" in agents_md
    surfaces = {d.surface for d in plan.degradations}
    assert {"hooks", "commands", "model affinity", "tool boundary", "subagents", "mcp trust", "mcp config"} <= surfaces
    assert "instructions" not in surfaces


def test_kimi_never_writes_model_even_when_the_table_declares_one(governed: Path) -> None:
    _hosts(governed, "kimi")
    policy = governed / "_grimoire/standard/orchestration-policy.yaml"
    policy.write_text("dispatch:\n  models:\n    kimi:\n      cheap: kimi-k2\n", encoding="utf-8")
    plan = _plan(governed, HostId.KIMI_CODE)
    for emitted in plan.files:
        if emitted.relpath.parts[:2] == (".kimi-code", "agents"):
            assert "model" not in _frontmatter(emitted.content)


@pytest.mark.parametrize(("declared", "reason"), [(None, "pas déclaré"), (["kimi", "codex"], "Codex est activé")])
def test_kimi_leaves_agents_md_to_codex_or_to_the_declaration(
    governed: Path, declared: list[str] | None, reason: str
) -> None:
    if declared is not None:
        _hosts(governed, *declared)
    plan = _plan(governed, HostId.KIMI_CODE)
    assert "AGENTS.md" not in plan.labels()
    gap = next(d for d in plan.degradations if d.surface == "instructions")
    assert reason in gap.reason


def test_kimi_and_codex_synced_together_do_not_fight_over_agents_md(governed: Path) -> None:
    _hosts(governed, "kimi", "codex")
    plans = [_plan(governed, HostId.CODEX), _plan(governed, HostId.KIMI_CODE)]
    assert all(r.ok for r in apply_plans(plans, governed))
    again = [_plan(governed, HostId.CODEX), _plan(governed, HostId.KIMI_CODE)]
    assert all(not r.written for r in apply_plans(again, governed, dry_run=True))
    assert "— Codex" in (governed / "AGENTS.md").read_text(encoding="utf-8")


def test_kimi_rejects_non_kebab_agent_names(governed: Path) -> None:
    _agent(governed, "Mauvais_Nom")
    plan = _plan(governed, HostId.KIMI_CODE)
    assert not any("Mauvais_Nom" in label for label in plan.labels())
    gap = next(d for d in plan.degradations if d.surface == "agent names")
    assert "Mauvais_Nom" in gap.reason


def test_kimi_mcp_merge_preserves_the_user(governed: Path) -> None:
    _hosts(governed, "kimi")
    mcp = governed / ".kimi-code/mcp.json"
    mcp.parent.mkdir(parents=True)
    user = {"mcpServers": {"grimoire": {"command": "mon-grimoire"}, "ctx7": {"url": "https://x"}}, "autre": 1}
    mcp.write_text(json.dumps(user), encoding="utf-8")
    _sync(governed, HostId.KIMI_CODE)
    assert json.loads(mcp.read_text(encoding="utf-8")) == user
    again = apply_plans([_plan(governed, HostId.KIMI_CODE)], governed)[0]
    assert not again.written


@pytest.mark.parametrize(
    "content",
    [
        '{"mcpServers": []}',
        '{\n  // commentaire\n  "mcpServers": {}\n}',
        '{"mcpServers": {"a": {"command": "x"}}, "mcpServers": {"b": {"command": "y"}}}',
    ],
)
def test_kimi_mcp_refuses_rather_than_overwrite(governed: Path, content: str) -> None:
    mcp = governed / ".kimi-code/mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text(content, encoding="utf-8")
    (result,) = apply_plans([_plan(governed, HostId.KIMI_CODE)], governed)
    assert CONFLICT_UNREADABLE in result.conflicts.values()
    assert mcp.read_text(encoding="utf-8") == content


def test_kimi_remote_server_and_no_kit_server(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"grimoire": {"type": "http", "url": "https://mcp.example/"}}}), encoding="utf-8"
    )
    plan = _plan(tmp_path, HostId.KIMI_CODE)
    (merge,) = plan.merges
    assert merge.merge({}) == {"mcpServers": {"grimoire": {"url": "https://mcp.example/"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"autre": {"command": "x"}}}), encoding="utf-8")
    assert _plan(tmp_path, HostId.KIMI_CODE).merges == ()


# ── Zed ─────────────────────────────────────────────────────────────────────


def test_zed_writes_rules_skills_settings_and_governed_task(governed: Path) -> None:
    _hosts(governed, "claude", "zed")
    (governed / "CLAUDE.md").write_text("# À moi\n", encoding="utf-8")
    plan = _plan(governed, HostId.ZED)
    _sync(governed, HostId.ZED)

    rules = (governed / ".rules").read_text(encoding="utf-8")
    assert rules.count("<!-- grimoire:begin") == 1
    assert all(f"`{name}`" in rules for name in ZED_INSTRUCTION_FILES[1:])
    assert "architecte" in rules and GOVERNED_TASK_LABEL in rules
    assert list((governed / ".agents/skills").glob("*/SKILL.md"))
    settings = json.loads((governed / ".zed/settings.json").read_text(encoding="utf-8"))
    assert settings == {"context_servers": {"grimoire": {"command": "grimoire-mcp", "args": ["serve"]}}}
    tasks = json.loads((governed / ".zed/tasks.json").read_text(encoding="utf-8"))
    assert tasks == [GOVERNED_TASK]
    assert tasks[0]["cwd"] == "$ZED_WORKTREE_ROOT" and tasks[0]["use_new_terminal"] is True
    surfaces = {d.surface for d in plan.degradations}
    expected = {"hooks", "subagents", "commands", "permissions", "profiles", "worktree trust", "instructions"}
    assert expected | {"model affinity", "mcp config"} <= surfaces
    assert "governed task" not in surfaces
    hooks = next(d for d in plan.degradations if d.surface == "hooks")
    assert "#725" in hooks.reason and GOVERNED_TASK_LABEL in hooks.fallback


def test_zed_keeps_user_rules_settings_and_tasks(governed: Path) -> None:
    _hosts(governed, "claude", "zed")
    (governed / ".rules").write_text("Règle maison.\n", encoding="utf-8")
    zed = governed / ".zed"
    zed.mkdir()
    (zed / "settings.json").write_text(
        json.dumps({"theme": "One Dark", "context_servers": {"grimoire": {"command": "le-mien"}}}), encoding="utf-8"
    )
    user_task = {"label": "tests", "command": "pytest"}
    (zed / "tasks.json").write_text(json.dumps([GOVERNED_TASK, user_task]), encoding="utf-8")

    (result,) = apply_plans([_plan(governed, HostId.ZED)], governed, force=True)
    assert result.ok, result.conflicts

    assert (governed / ".rules").read_text(encoding="utf-8").startswith("Règle maison.\n")
    settings = json.loads((zed / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"theme": "One Dark", "context_servers": {"grimoire": {"command": "le-mien"}}}
    assert json.loads((zed / "tasks.json").read_text(encoding="utf-8")) == [user_task, GOVERNED_TASK]
    again = apply_plans([_plan(governed, HostId.ZED)], governed)[0]
    assert not again.written and again.ok


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        (".zed/settings.json", '{\n  // JSONC accepté par Zed\n  "theme": "x"\n}\n'),
        (".zed/settings.json", '{"context_servers": ["grimoire"]}'),
        (".zed/tasks.json", '[\n  // commentaire\n  {"label": "a", "command": "b"}\n]\n'),
        (".zed/tasks.json", '{"label": "pas une liste"}'),
        # Clé répétée : json.loads garderait la dernière en silence.
        (".zed/settings.json", '{"theme": "a", "theme": "b"}'),
        (".zed/tasks.json", '[{"label": "a", "command": "b", "command": "c"}]'),
        # Une tâche de l'utilisateur au libellé du kit, même commande, args et env en plus.
        (
            ".zed/tasks.json",
            json.dumps([{**GOVERNED_TASK, "args": ["--model", "opus"], "env": {"X": "1"}}]),
        ),
        (".zed/tasks.json", json.dumps([{**GOVERNED_TASK, "use_new_terminal": False}])),
        (".zed/tasks.json", json.dumps([{"label": GOVERNED_TASK_LABEL, "command": "claude --autre"}])),
    ],
)
def test_zed_refuses_rather_than_overwrite(governed: Path, relpath: str, content: str) -> None:
    _hosts(governed, "claude", "zed")
    path = governed / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    (result,) = apply_plans([_plan(governed, HostId.ZED)], governed)
    assert CONFLICT_UNREADABLE in result.conflicts.values()
    assert path.read_text(encoding="utf-8") == content


def test_zed_empty_tasks_file_is_filled(governed: Path) -> None:
    _hosts(governed, "claude", "zed")
    tasks = governed / ".zed/tasks.json"
    tasks.parent.mkdir()
    tasks.write_text("\n", encoding="utf-8")
    _sync(governed, HostId.ZED)
    assert json.loads(tasks.read_text(encoding="utf-8")) == [GOVERNED_TASK]


def test_zed_without_claude_declares_the_missing_task(governed: Path) -> None:
    _hosts(governed, "zed")
    plan = _plan(governed, HostId.ZED)
    assert ".zed/tasks.json" not in plan.labels()
    assert any(d.surface == "governed task" for d in plan.degradations)
    hooks = next(d for d in plan.degradations if d.surface == "hooks")
    assert "`.rules`" in hooks.fallback


def test_zed_task_follows_a_present_claude_dir_without_declaration(governed: Path) -> None:
    assert ".zed/tasks.json" not in _plan(governed, HostId.ZED).labels()
    (governed / ".claude").mkdir()
    assert ".zed/tasks.json" in _plan(governed, HostId.ZED).labels()


def test_zed_declares_skills_it_would_ignore(governed: Path) -> None:
    surface = build_surface(governed)
    odd = SkillSpec(slug="mon_outil", name="mon_outil", description="quand tester", body="Corps.\n")
    emitter = emitter_for(HostId.ZED)
    assert emitter is not None
    plan = emitter.plan(replace(surface, skills=(*surface.skills, odd)), governed)
    assert ".agents/skills/mon_outil/SKILL.md" in plan.labels()  # écrite pour les autres hôtes
    gap = next(d for d in plan.degradations if d.surface == "skill names")
    assert "mon_outil" in gap.reason


# ── Matrice et `grimoire up` réel ──────────────────────────────────────────


def test_matrix_shows_prose_cells_for_both(governed: Path) -> None:
    _hosts(governed, "kimi", "zed")
    matrix = build_matrix(governed, NO_GUARD)
    for host in NO_GUARD:
        assert all(cell.status != "enforced" for cell in matrix.for_host(host))


def test_grimoire_up_real_cli_writes_kimi_and_zed_and_keeps_user_files(governed: Path) -> None:
    _hosts(governed, "claude", "kimi", "zed")
    (governed / ".zed").mkdir()
    (governed / ".zed/settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    (governed / ".zed/tasks.json").write_text(json.dumps([{"label": "t", "command": "make"}]), encoding="utf-8")
    (governed / ".kimi-code").mkdir()
    (governed / ".kimi-code/mcp.json").write_text(json.dumps({"mcpServers": {"x": {"command": "y"}}}), encoding="utf-8")
    (governed / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": ["serve"]}}}), encoding="utf-8"
    )
    home = governed / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "GRIMOIRE_TASK", "GRIMOIRE_HOST"))}
    env.update({"HOME": str(home), "USERPROFILE": str(home), ENV_OVERRIDE: "grimoire-hook"})
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])

    def up() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "grimoire", "up", str(governed)],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    first = up()
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads((governed / ".zed/settings.json").read_text(encoding="utf-8"))["theme"] == "dark"
    tasks = json.loads((governed / ".zed/tasks.json").read_text(encoding="utf-8"))
    assert tasks == [{"label": "t", "command": "make"}, GOVERNED_TASK]
    mcp = json.loads((governed / ".kimi-code/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(mcp) == {"x", "grimoire"}
    assert (governed / ".kimi-code/agents/scribe.md").is_file()
    assert (governed / ".rules").is_file() and (governed / "AGENTS.md").is_file()

    second = up()
    assert second.returncode == 0, second.stdout + second.stderr
    plans = [_plan(governed, HostId.KIMI_CODE), _plan(governed, HostId.ZED)]
    assert all(not r.written and not r.removed for r in apply_plans(plans, governed, dry_run=True))


def test_zed_removes_only_its_own_task_when_claude_is_no_longer_declared(governed: Path) -> None:
    _hosts(governed, "zed")
    tasks = governed / ".zed/tasks.json"
    tasks.parent.mkdir()
    user_task = {"label": "tests", "command": "pytest"}
    tasks.write_text(json.dumps([user_task, GOVERNED_TASK]), encoding="utf-8")
    _sync(governed, HostId.ZED)
    assert json.loads(tasks.read_text(encoding="utf-8")) == [user_task]
    again = apply_plans([_plan(governed, HostId.ZED)], governed)[0]
    assert ".zed/tasks.json" in again.unchanged
    # Une variante au libellé du kit n'est pas la tâche du kit : jamais retirée.
    variant = {**GOVERNED_TASK, "args": ["--x"]}
    tasks.write_text(json.dumps([variant]), encoding="utf-8")
    _sync(governed, HostId.ZED)
    assert json.loads(tasks.read_text(encoding="utf-8")) == [variant]


def test_multiline_descriptions_stay_on_one_line(governed: Path) -> None:
    _hosts(governed, "kimi", "zed")
    surface = build_surface(governed)
    agent = replace(surface.agents[0], description="première ligne\n| colonne | piège |\nfin")
    odd = replace(surface, agents=(agent, *surface.agents[1:]))
    for host, relpath in ((HostId.ZED, ".rules"), (HostId.KIMI_CODE, "AGENTS.md")):
        emitter = emitter_for(host)
        assert emitter is not None
        entry = next(f for f in emitter.plan(odd, governed).files if f.relpath.as_posix() == relpath)
        line = next(line for line in entry.content.splitlines() if "première ligne" in line)
        assert "fin" in line
        assert "\\| colonne \\| piège \\|" in line


@pytest.mark.parametrize(("host", "dirname"), [(HostId.ZED, ".zed"), (HostId.KIMI_CODE, ".kimi-code")])
def test_a_host_dir_linked_outside_the_project_is_never_written(
    governed: Path, tmp_path_factory: pytest.TempPathFactory, host: HostId, dirname: str
) -> None:
    _hosts(governed, "claude", "kimi", "zed")
    outside = tmp_path_factory.mktemp("ailleurs")
    (governed / dirname).symlink_to(outside, target_is_directory=True)
    plan = _plan(governed, host)
    assert not any(label.startswith(f"{dirname}/") for label in plan.labels())
    assert any(d.surface == "symlink" for d in plan.degradations)
    apply_plans([plan], governed)
    assert list(outside.iterdir()) == []


def test_a_host_dir_linked_inside_the_project_is_kept(governed: Path) -> None:
    _hosts(governed, "claude", "zed")
    (governed / "config-zed").mkdir()
    (governed / ".zed").symlink_to(governed / "config-zed", target_is_directory=True)
    plan = _plan(governed, HostId.ZED)
    assert ".zed/settings.json" in plan.labels()
    assert not any(d.surface == "symlink" for d in plan.degradations)
