"""opencode comme hôte natif : émission déterministe, sync, contrat versionné."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import contract_for, is_fenced, profile_for, resolve_host
from grimoire.hosts.collect import build_surface
from grimoire.hosts.detection import alias_for_host, detect_enabled_hosts
from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts
from grimoire.hosts.runtime import run_hook
from grimoire.hosts.sync import sync_host_surfaces

AGENT_DIR = Path("_grimoire/_config/custom/agents")


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    (tmp_path / AGENT_DIR).mkdir(parents=True)
    for name in ("concierge", "scribe"):
        (tmp_path / AGENT_DIR / f"{name}.md").write_text(
            f'---\nname: "{name}"\ndescription: "{name} — test"\nmodel_affinity:\n  reasoning: medium\n'
            f"  context_window: medium\n  cost: medium\n---\nTu es {name}.\n",
            encoding="utf-8",
        )
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _emit(root: Path) -> list[str]:
    emitter = emitter_for(HostId.OPENCODE)
    assert emitter is not None
    return apply_plan(emitter.plan(build_surface(root), root), root).written


def _tree(root: Path) -> dict[str, str]:
    files = [root / "opencode.json", *(root / ".opencode").rglob("*")]
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in files if p.is_file()}


class TestRegistry:
    def test_host_is_known_everywhere(self) -> None:
        assert HostId.OPENCODE in supported_hosts()
        assert resolve_host("opencode") is HostId.OPENCODE
        assert alias_for_host(HostId.OPENCODE) == "opencode"
        profile = profile_for(HostId.OPENCODE)
        assert profile.subagents_native and profile.skills_native and profile.commands_native
        assert profile.blocking_hooks and profile.mcp_native
        assert profile.supported_events() == (profile.supported_events()[0],)
        assert profile.supports_event(profile.supported_events()[0])

    @pytest.mark.parametrize("marker", [".opencode", "opencode.json"])
    def test_detection(self, tmp_path: Path, marker: str) -> None:
        path = tmp_path / marker
        path.mkdir() if marker == ".opencode" else path.write_text("{}", encoding="utf-8")
        assert detect_enabled_hosts(tmp_path) == ("opencode",)


class TestEmission:
    def test_surfaces_are_written_where_opencode_loads_them(self, governed: Path) -> None:
        written = _emit(governed)
        assert ".opencode/agent/concierge.md" in written
        assert ".opencode/plugin/grimoire-guard.js" in written
        assert ".opencode/grimoire.md" in written
        assert "opencode.json" in written
        assert any(w.startswith(".opencode/skill/") and w.endswith("/SKILL.md") for w in written)
        assert any(w.startswith(".opencode/command/") for w in written)
        concierge = (governed / ".opencode/agent/concierge.md").read_text(encoding="utf-8")
        assert "mode: 'primary'" in concierge and "bash: false" in concierge and "read: true" in concierge
        assert "mode: 'subagent'" in (governed / ".opencode/agent/scribe.md").read_text(encoding="utf-8")

    def test_two_runs_are_identical(self, governed: Path) -> None:
        _emit(governed)
        first = _tree(governed)
        second_run = _emit(governed)
        assert second_run == []
        assert _tree(governed) == first
        emitter = emitter_for(HostId.OPENCODE)
        assert emitter is not None
        plan_a = emitter.plan(build_surface(governed), governed)
        plan_b = emitter.plan(build_surface(governed), governed)
        assert [(f.relpath, f.content) for f in plan_a.files] == [(f.relpath, f.content) for f in plan_b.files]

    def test_config_merge_keeps_the_project_own_settings(self, governed: Path) -> None:
        (governed / "opencode.json").write_text(
            json.dumps({"model": "x/y", "instructions": ["mine.md"], "mcp": {"grimoire": {"type": "remote", "url": "u"}}}),
            encoding="utf-8",
        )
        _emit(governed)
        data = json.loads((governed / "opencode.json").read_text(encoding="utf-8"))
        assert data["model"] == "x/y"
        assert data["instructions"] == ["mine.md", ".opencode/grimoire.md"]
        assert data["mcp"]["grimoire"] == {"type": "remote", "url": "u"}

    def test_mcp_entry_is_a_local_command_array(self, governed: Path) -> None:
        _emit(governed)
        data = json.loads((governed / "opencode.json").read_text(encoding="utf-8"))
        assert data["mcp"]["grimoire"] == {"type": "local", "command": ["grimoire-mcp"], "enabled": True}

    def test_plugin_refuses_by_throwing_in_tool_execute_before(self, governed: Path) -> None:
        _emit(governed)
        plugin = (governed / ".opencode/plugin/grimoire-guard.js").read_text(encoding="utf-8")
        assert '"tool.execute.before"' in plugin and "throw new Error" in plugin
        assert "grimoire-hook" in plugin and '"opencode"' in plugin
        assert 'const GUARDED = ["bash", "edit", "write", "patch", "multiedit", "read"]' in plugin
        assert "grimoire:managed" in plugin

    def test_plugin_follows_the_blocking_pretooluse_hook(self, governed: Path) -> None:
        from dataclasses import replace

        from grimoire.hosts.surface import Enforcement

        surface = build_surface(governed)
        emitter = emitter_for(HostId.OPENCODE)
        assert emitter is not None
        paths = {f.relpath.as_posix() for f in emitter.plan(surface, governed).files}
        assert ".opencode/plugin/grimoire-guard.js" in paths
        advisory = replace(surface, hooks=tuple(replace(h, enforcement=Enforcement.ADVISORY) for h in surface.hooks))
        paths = {f.relpath.as_posix() for f in emitter.plan(advisory, governed).files}
        assert ".opencode/plugin/grimoire-guard.js" not in paths

    def test_a_hand_written_file_at_a_managed_path_is_preserved(self, governed: Path) -> None:
        mine = governed / ".opencode/agent/concierge.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("à moi\n", encoding="utf-8")
        _emit(governed)
        assert mine.read_text(encoding="utf-8") == "à moi\n"

    def test_degradations_are_declared(self, governed: Path) -> None:
        emitter = emitter_for(HostId.OPENCODE)
        assert emitter is not None
        surfaces = {d.surface for d in emitter.plan(build_surface(governed), governed).degradations}
        assert {"model affinity", "permissions", "ask"} <= surfaces


class TestHostSync:
    def test_sync_writes_opencode_when_declared(self, governed: Path) -> None:
        (governed / "project-context.yaml").write_text(
            "project:\n  name: t\nhosts:\n  enabled: [opencode]\n", encoding="utf-8"
        )
        outcome = sync_host_surfaces(governed)
        assert outcome.ok, outcome.error
        assert ".opencode/plugin/grimoire-guard.js" in outcome.written
        assert not (governed / ".claude").exists()

    def test_sync_ignores_opencode_when_not_enabled(self, governed: Path) -> None:
        outcome = sync_host_surfaces(governed)
        assert outcome.ok
        assert not (governed / ".opencode").exists()


class TestGuardDecisionThroughTheWire:
    """Ce que le plugin reçoit de `grimoire-hook --host opencode` : un deny exploitable."""

    def test_destructive_command_is_denied_with_a_reason(self, governed: Path) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "bash",
            "tool_input": {"command": "rm -rf build"},
            "cwd": str(governed),
        }
        result, _decision, _hook = run_hook(payload, host_id=HostId.OPENCODE)
        specific = result["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
        assert specific["permissionDecisionReason"]

    def test_read_only_command_is_allowed(self, governed: Path) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "bash",
            "tool_input": {"command": "git status"},
            "cwd": str(governed),
        }
        result, _d, _h = run_hook(payload, host_id=HostId.OPENCODE)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestContract:
    def test_fenced_only_on_the_captured_version(self) -> None:
        assert is_fenced(HostId.OPENCODE, "1.17.11") is True
        for version in (None, "", "1.17.10", "1.18.0", "1.17.110"):
            assert is_fenced(HostId.OPENCODE, version) is False

    def test_only_pretooluse_deny_is_verified(self) -> None:
        contract = contract_for(HostId.OPENCODE, "1.17.11")
        verified = {k for k, e in contract.evidence.items() if e.verified}
        assert verified == {"pretooluse_deny"}


class TestGuardFailsClosedWhenTheBinaryIsMissing:
    """`grimoire-hook` introuvable : refus en profil bloquant, avertissement sinon."""

    HARNESS = (
        'import { GrimoireGuard } from "./.opencode/plugin/grimoire-guard.js"\n'
        "const hooks = await GrimoireGuard({ directory: process.cwd() })\n"
        "try {\n"
        '  await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "ls" } })\n'
        '  console.log("ALLOWED")\n'
        "} catch (error) {\n"
        '  console.log("REFUSED " + error.message)\n'
        "}\n"
    )

    def _run(self, root: Path) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node absent")
        (root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
        (root / "harness.mjs").write_text(self.HARNESS, encoding="utf-8")
        empty = root / "empty-bin"
        empty.mkdir(exist_ok=True)
        env = {"PATH": str(empty)}
        # Node abandonne (abort 134) sous Windows sans SystemRoot : on le conserve, PATH reste vide.
        for key in ("SYSTEMROOT", "SystemRoot", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        return subprocess.run([node, "harness.mjs"], cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False)

    def test_governed_project_refuses_the_tool(self, governed: Path) -> None:
        _emit(governed)
        out = self._run(governed)
        assert out.stdout.startswith("REFUSED"), out
        assert "grimoire-hook introuvable, garde indisponible" in out.stdout

    def test_floor_above_a_lighter_declared_profile_refuses_the_tool(self, governed: Path) -> None:
        """W2-07-S1-03 : `profile: starter` sous un plancher `governed` reste un profil bloquant."""
        manifest = governed / "_grimoire/standard/standard-profile.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("profile: governed", "profile: starter", 1), encoding="utf-8")
        _emit(governed)
        assert self._run(governed).stdout.startswith("ALLOWED")  # sans plancher : avertissement
        (governed / "_grimoire/standard/profile-floor.yaml").write_text("floor: governed\n", encoding="utf-8")
        out = self._run(governed)
        assert out.stdout.startswith("REFUSED"), out

    def test_unreadable_floor_file_refuses_the_tool(self, governed: Path) -> None:
        manifest = governed / "_grimoire/standard/standard-profile.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("profile: governed", "profile: starter", 1), encoding="utf-8")
        (governed / "_grimoire/standard/profile-floor.yaml").write_text("floor: platinum\n", encoding="utf-8")
        _emit(governed)
        assert self._run(governed).stdout.startswith("REFUSED")

    def test_plugin_reads_the_floor_file(self, governed: Path) -> None:
        _emit(governed)
        assert "profile-floor.yaml" in (governed / ".opencode/plugin/grimoire-guard.js").read_text(encoding="utf-8")

    def test_ungoverned_project_passes_with_a_warning(self, tmp_path: Path) -> None:
        plugin = tmp_path / ".opencode/plugin"
        plugin.mkdir(parents=True)
        governed_root = tmp_path  # no _grimoire/standard/standard-profile.yaml here
        emitter = emitter_for(HostId.OPENCODE)
        assert emitter is not None
        # Reuse a real emission, then drop the governed marker the fixture would have created.
        (tmp_path / AGENT_DIR).mkdir(parents=True)
        (tmp_path / AGENT_DIR / "scribe.md").write_text('---\nname: "scribe"\ndescription: "s"\n---\nC.\n', encoding="utf-8")
        _emit(governed_root)
        assert not (tmp_path / "_grimoire/standard/standard-profile.yaml").exists()
        out = self._run(tmp_path)
        assert out.stdout.startswith("ALLOWED"), out
        assert "garde PreToolUse inactive" in out.stderr
