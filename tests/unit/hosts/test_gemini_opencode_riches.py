"""#1281 L6 : Gemini (compétences natives, Antigravity) et opencode (clôture, ``model`` par agent).

Sources relues le 2026-11-22 (voir les modules émetteurs) :

- Gemini CLI : https://geminicli.com/docs/cli/skills/, https://geminicli.com/docs/cli/creating-skills/,
  https://geminicli.com/docs/core/subagents/ ;
- Antigravity : https://antigravity.google/docs/customization-discovery, https://antigravity.google/docs/skills,
  https://antigravity.google/docs/hooks ;
- opencode (V1) : https://opencode.ai/docs/en/plugins/, https://opencode.ai/docs/en/agents/.

Aucun de ces hôtes n'a été lancé : les tests vérifient ce que le kit **écrit** et que rien
n'est annoncé bloquant sans capture (la fin de tour d'opencode reste consultative).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import GovernanceFunction, ParityLevel, function_states, profile_for
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.emitters import apply_plans, emitter_for
from grimoire.hosts.emitters.base import EmitPlan
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import ENV_OVERRIDE
from grimoire.hosts.matrix import ADVISORY, ENFORCED, build_matrix
from grimoire.hosts.runtime import render

pytestmark = pytest.mark.covers("host:gemini", "host:opencode", level="integration")

AGENT_DIR = Path("_grimoire/_config/custom/agents")
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
#: Champs documentés d'un sous-agent Gemini (geminicli.com/docs/core/subagents).
GEMINI_AGENT_FIELDS = {"name", "description", "kind", "tools", "mcpServers", "model", "temperature", "max_turns"}
GEMINI_AGENT_FIELDS |= {"timeout_mins"}
#: Champs d'agent opencode écrits par le kit, tous documentés (opencode.ai/docs/en/agents).
OPENCODE_AGENT_FIELDS = {"description", "mode", "model", "tools"}


@pytest.fixture(autouse=True)
def _bare_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OVERRIDE, "grimoire-hook")
    for var in ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "GRIMOIRE_TASK_ID", "GRIMOIRE_HOST_PROBE"):
        monkeypatch.delenv(var, raising=False)


def _agent(root: Path, name: str, *, reasoning: str, cost: str = "medium") -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (root / AGENT_DIR / f"{name}.md").write_text(
        f'---\nname: "{name}"\ndescription: "{name} test"\ntools: [read, edit]\nmodel_affinity:\n'
        f"  reasoning: {reasoning}\n  context_window: medium\n  cost: {cost}\n---\n\nTu es {name}.\n",
        encoding="utf-8",
    )


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    _agent(tmp_path, "architecte", reasoning="high", cost="high")  # strong
    _agent(tmp_path, "scribe", reasoning="low")  # cheap
    _agent(tmp_path, "moyen", reasoning="medium")  # inherit
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _policy(root: Path, text: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(text, encoding="utf-8")


def _plan(root: Path, host: HostId) -> EmitPlan:
    emitter = emitter_for(host)
    assert emitter is not None
    return emitter.plan(build_surface(root), root)


def _files(plan: EmitPlan) -> dict[str, str]:
    return {f.relpath.as_posix(): f.content for f in plan.files}


def _frontmatter(text: str) -> dict[str, str]:
    head = text.split("---", 2)[1]
    lines = [line for line in head.strip().splitlines() if not line.startswith((" ", "-"))]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in lines)}


def _gap(plan: EmitPlan, surface: str) -> str:
    return next(d.reason for d in plan.degradations if d.surface == surface)


# ── Gemini : compétences natives ─────────────────────────────────────────────


def test_gemini_skills_follow_the_documented_skill_format(governed: Path) -> None:
    files = _files(_plan(governed, HostId.GEMINI_CLI))
    skills = {path: text for path, text in files.items() if path.startswith(".agents/skills/")}
    assert skills
    for path, text in skills.items():
        assert path.endswith("/SKILL.md")
        fields = _frontmatter(text)
        # `name` égal au dossier, `description` requise (creating-skills ; Antigravity : skills).
        assert set(fields) == {"name", "description"}
        assert fields["name"].strip("'\"") == Path(path).parent.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", Path(path).parent.name)
        assert fields["description"].strip("'\"")
    assert not any(path.startswith("_grimoire/hosts/skills/") for path in files)
    entry = files["GEMINI.md"]
    assert "`.agents/skills/`" in entry and "activate_skill" in entry


def test_gemini_and_codex_share_skills_byte_for_byte_and_stay_idempotent(governed: Path) -> None:
    gemini, codex = _plan(governed, HostId.GEMINI_CLI), _plan(governed, HostId.CODEX)
    shared_g = {p: t for p, t in _files(gemini).items() if p.startswith(".agents/skills/")}
    shared_c = {p: t for p, t in _files(codex).items() if p.startswith(".agents/skills/")}
    assert shared_g == shared_c
    first = apply_plans([codex, gemini], governed)
    assert all(r.ok for r in first)
    assert not first[1].removed and not first[0].removed
    second = apply_plans([_plan(governed, HostId.GEMINI_CLI), _plan(governed, HostId.CODEX)], governed)
    assert all(r.ok and not r.written and not r.removed for r in second)


def test_gemini_keeps_a_hand_written_skill_and_the_old_copies_of_other_hosts(governed: Path) -> None:
    mine = governed / ".agents/skills/maison/SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: maison\ndescription: à moi\n---\nCorps.\n", encoding="utf-8")
    # Copie de repli d'un autre hôte (Cursor, prose) : Gemini ne la possède plus et ne la retire pas.
    other = governed / "_grimoire/hosts/skills/grimoire-gate.md"
    other.parent.mkdir(parents=True)
    other.write_text("<!-- grimoire:managed -->\n# copie\n", encoding="utf-8")
    (result,) = apply_plans([_plan(governed, HostId.GEMINI_CLI)], governed)
    assert result.ok
    assert mine.read_text(encoding="utf-8").endswith("Corps.\n")
    assert other.is_file() and "_grimoire/hosts/skills/grimoire-gate.md" not in result.removed


# ── Gemini : `model` d'un sous-agent ─────────────────────────────────────────


def test_gemini_agents_carry_no_model_without_a_declared_tier(governed: Path) -> None:
    plan = _plan(governed, HostId.GEMINI_CLI)
    files = _files(plan)
    for name in ("architecte", "scribe", "moyen"):
        fields = _frontmatter(files[f".gemini/agents/{name}.md"])
        assert set(fields) <= GEMINI_AGENT_FIELDS, fields
        assert "model" not in fields
    reason = _gap(plan, "model affinity")
    assert "architecte" in reason and "scribe" in reason and "moyen" not in reason


def test_gemini_model_comes_from_the_table_for_the_gemini_host_only(governed: Path) -> None:
    _policy(
        governed, "dispatch:\n  models:\n    gemini:\n      cheap: gemini-3-preview\n    cursor:\n      strong: x\n"
    )
    plan = _plan(governed, HostId.GEMINI_CLI)
    files = _files(plan)
    assert _frontmatter(files[".gemini/agents/scribe.md"])["model"] == "'gemini-3-preview'"
    assert "model" not in _frontmatter(files[".gemini/agents/architecte.md"])
    assert "model" not in _frontmatter(files[".gemini/agents/moyen.md"])  # inherit, défaut documenté
    reason = _gap(plan, "model affinity")
    assert "architecte" in reason and "scribe" not in reason


def test_gemini_degradation_disappears_once_every_tier_is_named(governed: Path) -> None:
    _policy(governed, "dispatch:\n  models:\n    gemini:\n      cheap: gemini-a\n      strong: gemini-b\n")
    plan = _plan(governed, HostId.GEMINI_CLI)
    assert "model affinity" not in {d.surface for d in plan.degradations}


# ── Antigravity : minimum sourcé, le reste déclaré ───────────────────────────


def test_antigravity_gets_skills_and_its_unwritten_surfaces_are_declared(governed: Path) -> None:
    plan = _plan(governed, HostId.GEMINI_CLI)
    paths = set(_files(plan))
    assert not any(p.startswith(".agents/") and not p.startswith(".agents/skills/") for p in paths)
    surfaces = {d.surface for d in plan.degradations}
    assert {"antigravity hooks", "antigravity agents", "antigravity mcp"} <= surfaces
    assert ".agents/hooks.json" in _gap(plan, "antigravity hooks")


def test_the_gemini_runtime_wire_is_unchanged(tmp_path: Path) -> None:
    hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, tool_name="run_shell_command")
    rendered = render(Decision(outcome=Outcome.DENY, reason="non"), hook, HostId.GEMINI_CLI)
    assert rendered["decision"] == "deny" and rendered["reason"] == "non"


def test_gemini_profile_and_host_list_say_skills_are_native() -> None:
    assert profile_for(HostId.GEMINI_CLI).skills_native
    result = CliRunner().invoke(app, ["-o", "json", "host", "list"])
    assert result.exit_code == 0, result.output
    hosts = {h["host_id"]: h for h in json.loads(result.output)["hosts"]}
    assert hosts["host-gemini-cli"]["surfaces"]["skills"] is True
    assert hosts["host-gemini-cli"]["surfaces"]["blocking_hooks"] is False
    # opencode : `stop` déclenché (session.idle) mais jamais compté parmi les refus.
    assert "stop" in hosts["host-opencode"]["hook_events"]
    assert hosts["host-opencode"]["surfaces"]["blocking_hooks"] is True


# ── opencode : `model` par agent ─────────────────────────────────────────────


def test_opencode_agents_carry_no_model_without_a_declared_tier(governed: Path) -> None:
    plan = _plan(governed, HostId.OPENCODE)
    files = _files(plan)
    for name in ("architecte", "scribe", "moyen"):
        fields = _frontmatter(files[f".opencode/agent/{name}.md"])
        assert set(fields) <= OPENCODE_AGENT_FIELDS, fields
        assert "model" not in fields
    reason = _gap(plan, "model affinity")
    assert "architecte" in reason and "scribe" in reason and "moyen" not in reason


def test_opencode_model_is_provider_slash_model_from_the_table(governed: Path) -> None:
    _policy(
        governed,
        "dispatch:\n  models:\n    opencode:\n      cheap: anthropic/claude-haiku\n      strong: sans-fournisseur\n",
    )
    plan = _plan(governed, HostId.OPENCODE)
    files = _files(plan)
    assert _frontmatter(files[".opencode/agent/scribe.md"])["model"] == "'anthropic/claude-haiku'"
    # Format documenté `provider/model-id` : un nom sans fournisseur n'est pas écrit, et c'est dit.
    assert "model" not in _frontmatter(files[".opencode/agent/architecte.md"])
    reason = _gap(plan, "model affinity")
    assert "strong=sans-fournisseur" in reason and "architecte" in reason


def test_opencode_model_degradation_disappears_once_every_tier_is_named(governed: Path) -> None:
    _policy(governed, "dispatch:\n  models:\n    opencode:\n      cheap: a/b\n      strong: c/d\n")
    assert "model affinity" not in {d.surface for d in _plan(governed, HostId.OPENCODE).degradations}


# ── opencode : décision de clôture sur `session.idle` ────────────────────────


def test_opencode_plugin_answers_session_idle_without_claiming_a_block(governed: Path) -> None:
    plan = _plan(governed, HostId.OPENCODE)
    plugin = _files(plan)[".opencode/plugin/grimoire-guard.js"]
    assert 'event?.type === "session.idle"' in plugin
    assert '["--host", "opencode", "--event", "Stop"]' in plugin
    assert "const STOP_ENABLED = true" in plugin and "client.app.log" in plugin
    assert "hook stop verification" in {d.surface for d in plan.degradations}
    assert "hook stop" not in {d.surface for d in plan.degradations}
    instructions = _files(plan)[".opencode/grimoire.md"]
    assert "**Stop** (consultatif, `session.idle`)" in instructions


def test_opencode_stop_is_advisory_in_the_matrix_and_pretooluse_stays_enforced(governed: Path) -> None:
    matrix = build_matrix(governed, (HostId.OPENCODE,))
    assert matrix.ok, matrix.failing
    cells = {c.subject: c for c in matrix.for_host(HostId.OPENCODE)}
    assert cells["hook:stop"].status == ADVISORY
    assert cells["hook:pre_tool_use"].status == ENFORCED
    state = function_states(HostId.OPENCODE)[GovernanceFunction.WRITE_TRACE]
    assert state.level is ParityLevel.DEGRADED and "session.idle" in state.reason


def test_a_closure_refusal_reaches_opencode_as_a_message_not_a_block(tmp_path: Path) -> None:
    hook = HookInput(event=HookEvent.STOP, project_root=tmp_path)
    rendered = render(Decision(outcome=Outcome.BLOCK, reason="gates rouges"), hook, HostId.OPENCODE)
    assert rendered == {"systemMessage": "gates rouges"}
    # Claude Code, qui sait refuser la fin de tour, garde son refus.
    assert render(Decision(outcome=Outcome.BLOCK, reason="r"), hook, HostId.CLAUDE_CODE_CLI)["decision"] == "block"


_IDLE_HARNESS = """import { GrimoireGuard } from "./.opencode/plugin/grimoire-guard.js"
const logged = []
const client = { app: { log: async (entry) => { logged.push(entry.body) } } }
const hooks = await GrimoireGuard({ directory: process.cwd(), client })
await hooks.event({ event: { type: "session.updated", properties: { sessionID: "s1" } } })
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } })
console.log(JSON.stringify(logged))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="faux grimoire-hook en script shell")
def test_the_plugin_logs_the_closure_verdict_on_session_idle(governed: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node absent")
    (result,) = apply_plans([_plan(governed, HostId.OPENCODE)], governed)
    assert result.ok
    fake = governed / "fake-hook"
    received = governed / "received.json"
    fake.write_text(
        f'#!/bin/sh\ncat > "{received}"\necho "$@" >> "{received}.args"\n'
        'printf \'{"systemMessage": "gates rouges"}\'\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    plugin = governed / ".opencode/plugin/grimoire-guard.js"
    text = re.sub(
        r"^const HOOK = .*$", f"const HOOK = {json.dumps({'posix': str(fake)})}", plugin.read_text(), flags=re.MULTILINE
    )
    plugin.write_text(text, encoding="utf-8")
    (governed / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (governed / "harness.mjs").write_text(_IDLE_HARNESS, encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", "")}
    out = subprocess.run(
        [node, "harness.mjs"], cwd=governed, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert out.returncode == 0, out
    (entry,) = json.loads(out.stdout.strip().splitlines()[-1])
    assert entry == {"service": "grimoire", "level": "warn", "message": "[grimoire] clôture : gates rouges"}
    payload = json.loads(received.read_text(encoding="utf-8"))
    assert payload["hook_event_name"] == "Stop" and payload["session_id"] == "s1"
    # Un seul appel : `session.updated` n'est pas une fin de tour.
    assert (governed / "received.json.args").read_text().splitlines() == ["--host opencode --event Stop"]


# ── `grimoire up` réel ───────────────────────────────────────────────────────


def test_grimoire_up_writes_gemini_and_opencode_and_keeps_user_files(governed: Path) -> None:
    (governed / "project-context.yaml").write_text(
        'project:\n  name: "Demo"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        'hosts:\n  enabled: ["gemini", "opencode"]\n',
        encoding="utf-8",
    )
    _policy(governed, "dispatch:\n  models:\n    opencode:\n      cheap: a/b\n    gemini:\n      cheap: gemini-x\n")
    (governed / "opencode.json").write_text(json.dumps({"model": "x/y", "instructions": ["mine.md"]}), encoding="utf-8")
    settings = governed / ".gemini/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    result = CliRunner().invoke(app, ["up", str(governed)])
    assert result.exit_code == 0, result.output
    assert json.loads(settings.read_text(encoding="utf-8"))["theme"] == "dark"
    config = json.loads((governed / "opencode.json").read_text(encoding="utf-8"))
    assert config["model"] == "x/y" and config["instructions"][0] == "mine.md"
    assert list((governed / ".agents/skills").glob("*/SKILL.md"))
    assert "model: 'a/b'" in (governed / ".opencode/agent/scribe.md").read_text(encoding="utf-8")
    assert "model: 'gemini-x'" in (governed / ".gemini/agents/scribe.md").read_text(encoding="utf-8")
    assert "session.idle" in (governed / ".opencode/plugin/grimoire-guard.js").read_text(encoding="utf-8")

    again = CliRunner().invoke(app, ["up", str(governed)])
    assert again.exit_code == 0, again.output
    gemini, opencode = _plan(governed, HostId.GEMINI_CLI), _plan(governed, HostId.OPENCODE)
    assert all(not r.written and not r.removed for r in apply_plans([gemini, opencode], governed, dry_run=True))


def test_the_session_idle_observation_is_advisory_and_never_a_blocking_capture() -> None:
    """Observation du 2026-11-22 sur opencode 1.17.11 : la fin de tour arrive, rien n'est refusé."""
    fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "hosts"
    name = "opencode/02-session-idle-stop-advisory.json"
    observed = json.loads((fixtures / name).read_text(encoding="utf-8"))["observed"]
    assert observed["session_idle_fired"] and observed["hook_payload"]["hook_event_name"] == "Stop"
    assert observed["turn_blocked"] is False
    profile = profile_for(HostId.OPENCODE)
    assert name not in profile.blocking_captures
    assert not profile.blocks(HookEvent.STOP)
