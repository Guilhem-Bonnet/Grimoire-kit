"""#1309, relecture : clôture opencode asynchrone, `--decision`, contexte du refus, noms et chemins sûrs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.emitters import apply_plan, apply_plans, emitter_for
from grimoire.hosts.emitters.agent_skills import safe_skill_slug, shared_skill_file, shared_skills
from grimoire.hosts.emitters.base import EmitPlan, EmittedFile, JsonMerge, UnsafePlanPathError
from grimoire.hosts.events import HookEvent
from grimoire.hosts.hook_launcher import ENV_OVERRIDE
from grimoire.hosts.runtime import render
from grimoire.hosts.surface import ProjectSurface, SkillSpec

pytestmark = pytest.mark.covers("host:opencode", "host:gemini-cli", level="integration")

PLUGIN = ".opencode/plugin/grimoire-guard.js"


@pytest.fixture(autouse=True)
def _bare_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OVERRIDE, "grimoire-hook")
    for var in ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "GRIMOIRE_TASK_ID", "GRIMOIRE_HOST_PROBE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _surface(root: Path) -> ProjectSurface:
    return build_surface(root)


def _plugin(root: Path, surface: ProjectSurface | None = None) -> str:
    emitter = emitter_for(HostId.OPENCODE)
    assert emitter is not None
    plan = emitter.plan(surface if surface is not None else _surface(root), root)
    return next(f.content for f in plan.files if f.relpath.as_posix() == PLUGIN)


def _skill(slug: str) -> SkillSpec:
    return SkillSpec(slug=slug, name=slug, description="d", body="corps")


# ── M2 : `--decision` hors de la décision par défaut ─────────────────────────


def test_default_decisions_keep_the_bare_arguments(governed: Path) -> None:
    plugin = _plugin(governed)
    assert '["--host", "opencode", "--event", "PreToolUse"]' in plugin
    assert '["--host", "opencode", "--event", "Stop"]' in plugin
    assert "--decision" not in plugin


def test_a_non_default_decision_is_passed_to_the_hook(governed: Path) -> None:
    surface = _surface(governed)
    hooks = tuple(replace(h, decision=f"projet.{h.event.value}") for h in surface.hooks)
    plugin = _plugin(governed, replace(surface, hooks=hooks))
    assert '"--event", "PreToolUse", "--decision", "projet.pre_tool_use"]' in plugin
    assert '"--event", "Stop", "--decision", "projet.stop"]' in plugin


def test_the_closure_timeout_is_capped(governed: Path) -> None:
    surface = _surface(governed)
    hooks = tuple(replace(h, timeout=60) if h.event is HookEvent.STOP else h for h in surface.hooks)
    assert "const STOP_TIMEOUT_MS = 5000" in _plugin(governed, replace(surface, hooks=hooks))


# ── A1 / M1 : clôture asynchrone, échecs journalisés ─────────────────────────

_HARNESS = """import { GrimoireGuard } from "./.opencode/plugin/grimoire-guard.js"
const logged = []
const order = []
const client = { app: { log: async (entry) => { logged.push(entry.body.message) } } }
const hooks = await GrimoireGuard({ directory: process.cwd(), client })
setTimeout(() => order.push("timer"), 10)
await hooks.event(undefined)
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "s1" } } })
order.push("hook")
console.log(JSON.stringify({ logged, order }))
"""


def _run_plugin(root: Path, hook_body: str, *, stop_timeout_ms: int | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node absent")
    (result,) = apply_plans([emitter_for(HostId.OPENCODE).plan(_surface(root), root)], root)  # type: ignore[union-attr]
    assert result.ok
    fake = root / "fake-hook"
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\n{hook_body}\n", encoding="utf-8")
    fake.chmod(0o755)
    plugin = root / PLUGIN
    text = re.sub(
        r"^const HOOK = .*$", f"const HOOK = {json.dumps({'posix': str(fake)})}", plugin.read_text(), flags=re.MULTILINE
    )
    if stop_timeout_ms is not None:
        text = re.sub(
            r"^const STOP_TIMEOUT_MS = .*$", f"const STOP_TIMEOUT_MS = {stop_timeout_ms}", text, flags=re.MULTILINE
        )
    plugin.write_text(text, encoding="utf-8")
    (root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (root / "harness.mjs").write_text(_HARNESS, encoding="utf-8")
    out = subprocess.run(
        [node, "harness.mjs"],
        cwd=root,
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out
    return json.loads(out.stdout.strip().splitlines()[-1])


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="faux grimoire-hook en script shell")


@posix_only
def test_a_slow_closure_hook_does_not_freeze_the_event_loop(governed: Path) -> None:
    seen = _run_plugin(governed, 'sleep 1\nprintf \'{"systemMessage": "lent"}\'')
    # Le minuteur de 10 ms tourne pendant que le hook dort : spawnSync l'aurait retenu.
    assert seen["order"] == ["timer", "hook"]
    assert seen["logged"] == ["[grimoire] clôture : lent"]


@posix_only
def test_a_closure_hook_past_its_deadline_is_reported(governed: Path) -> None:
    seen = _run_plugin(governed, "sleep 5", stop_timeout_ms=200)
    assert seen["logged"] == ["[grimoire] décision de clôture indisponible (délai de 200 ms dépassé)"]


@posix_only
def test_a_failing_closure_hook_is_reported(governed: Path) -> None:
    seen = _run_plugin(governed, "exit 3")
    assert seen["logged"] == ["[grimoire] décision de clôture indisponible (sortie 3)"]


@posix_only
@pytest.mark.parametrize("stdout", ["null", "[1]", "pas du json"])
def test_an_unusable_verdict_is_reported(governed: Path, stdout: str) -> None:
    seen = _run_plugin(governed, f"printf '{stdout}'")
    assert seen["logged"] == ["[grimoire] décision de clôture indisponible (verdict illisible)"]


@posix_only
def test_an_empty_verdict_logs_nothing(governed: Path) -> None:
    assert _run_plugin(governed, "printf '{}'")["logged"] == []


# ── M3 : le contexte d'un refus de clôture n'est pas perdu ───────────────────


@pytest.mark.parametrize("host", [HostId.OPENCODE, HostId.UNKNOWN])
def test_a_closure_refusal_keeps_its_context(tmp_path: Path, host: HostId) -> None:
    hook = HookInput(event=HookEvent.STOP, project_root=tmp_path)
    decision = Decision(outcome=Outcome.BLOCK, reason="gates rouges", context="voir la preuve")
    assert render(decision, hook, host) == {"systemMessage": "gates rouges\n\nvoir la preuve"}


# ── M6 : noms de compétence et chemins hors du projet ────────────────────────


@pytest.mark.parametrize("slug", ["../x", "/abs", "a/b", "a..b", "Maj", "", ".cache"])
def test_an_unsafe_skill_name_is_refused(slug: str) -> None:
    assert not safe_skill_slug(slug)
    with pytest.raises(ValueError, match="nom de compétence refusé"):
        shared_skill_file(_skill(slug))


def test_safe_skill_names_are_written() -> None:
    for slug in ("grimoire-gate", "a.b_c-1"):
        assert safe_skill_slug(slug)
        assert shared_skill_file(_skill(slug)).relpath == Path(".agents/skills") / slug / "SKILL.md"


def test_codex_and_gemini_skip_and_declare_the_same_unsafe_skill(governed: Path) -> None:
    surface = _surface(governed)
    surface = replace(surface, skills=(*surface.skills, _skill("../x")))
    files, gaps = shared_skills(surface.skills)
    assert len(files) == len(surface.skills) - 1 and "'../x'" in gaps[0].reason
    for host in (HostId.CODEX, HostId.GEMINI_CLI):
        emitter = emitter_for(host)
        assert emitter is not None
        plan = emitter.plan(surface, governed)
        assert not any(".." in f.relpath.parts for f in plan.files)
        assert "skill names" in {d.surface for d in plan.degradations}


@pytest.mark.parametrize("relpath", [Path("../x"), Path("/abs/x"), Path("a/../../x")])
def test_apply_plan_refuses_a_path_outside_the_project(tmp_path: Path, relpath: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    safe = EmittedFile(relpath=Path("ok.md"), content="ok\n")
    plan = EmitPlan(host_id=HostId.OPENCODE, files=(safe, EmittedFile(relpath=relpath, content="x\n")))
    with pytest.raises(UnsafePlanPathError, match="hors du projet"):
        apply_plan(plan, root)
    assert not (root / "ok.md").exists()  # rien n'est écrit
    merge = JsonMerge(relpath=relpath, merge=lambda data: data, label="x")
    with pytest.raises(UnsafePlanPathError):
        apply_plan(EmitPlan(host_id=HostId.OPENCODE, files=(), merges=(merge,)), root)


@pytest.mark.parametrize(
    "host",
    [HostId.CLAUDE_CODE_CLI, HostId.GITHUB_COPILOT, HostId.CODEX, HostId.CURSOR, HostId.GEMINI_CLI, HostId.OPENCODE],
)
def test_every_emitter_still_applies_inside_the_project(governed: Path, host: HostId) -> None:
    emitter = emitter_for(host)
    assert emitter is not None
    result = apply_plan(emitter.plan(_surface(governed), governed), governed)
    assert result.ok, result.conflicts
