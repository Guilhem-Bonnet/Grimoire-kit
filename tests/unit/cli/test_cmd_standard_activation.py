"""CLI wiring of the Claude Code activation hook (grimoire standard)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.claude_activation import activation_directive_template

runner = CliRunner()

#: Verbatim wording shipped between #585 (lot B) and #597 (lot G1) — 824
#: characters, ``{task_id}`` placeholder literal. Frozen here independently
#: of ``claude_activation._HISTORICAL_DIRECTIVE_TEMPLATES``.
_STALE_824_CHAR_TEMPLATE = (
    "[Grimoire Standard — activation]\n"
    "Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font\n"
    "partie de la tâche demandée :\n"
    "1. AVANT toute modification de code : remplis\n"
    "   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,\n"
    "   périmètre outillé (tool boundary) concret, critères de sortie.\n"
    "2. PENDANT le travail : consigne chaque preuve (commande exécutée, test\n"
    "   vert, diff clé) comme ligne concrète de l'inventaire dans\n"
    "   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le\n"
    "   résumé placeholder.\n"
    "3. AVANT de conclure : exécute\n"
    "   `grimoire standard gate run-tests --task-id {task_id}` puis\n"
    "   `grimoire standard gate check --task-id {task_id} --strict` puis\n"
    "   `grimoire standard verify .` et corrige tout échec.\n"
    "Une clôture sans gates verts est une tâche non terminée.\n"
)


class TestActivationContext:
    def test_prints_builtin_directive(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "activation-context", str(tmp_path)])
        assert result.exit_code == 0
        assert "[Grimoire Standard]" in result.output
        assert "gate check --task-id bootstrap --strict" in result.output

    def test_prefers_project_file(self, tmp_path: Path) -> None:
        context = tmp_path / ".claude" / "activation-context.md"
        context.parent.mkdir(parents=True)
        context.write_text("directive maison\n", encoding="utf-8")
        result = runner.invoke(app, ["standard", "activation-context", str(tmp_path)])
        assert result.exit_code == 0
        assert result.output == "directive maison\n"


class TestStandardInitClaudeHook:
    def test_init_installs_hook_by_default(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path)])
        assert result.exit_code == 0
        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        commands = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            for hook in entry["hooks"]
        ]
        # The directive is unchanged; only its carrier is. The session-start
        # decision reads the same `.claude/activation-context.md`, and routing
        # it through the shared hook entry point is what lets one rule reach
        # every host. Two entries would inject the directive twice, so the
        # legacy command is replaced rather than kept alongside.
        assert commands == ["grimoire-hook --host claude --event SessionStart"]
        assert (tmp_path / ".claude" / "activation-context.md").is_file()

    def test_init_installs_the_blocking_gate_hook(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path), "--profile", "governed"])
        assert result.exit_code == 0
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "Stop" in settings["hooks"], "un profil gouverné doit pouvoir refuser une clôture"

    def test_no_claude_hook_opt_out(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path), "--no-claude-hook"])
        assert result.exit_code == 0
        assert not (tmp_path / ".claude").exists()

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["standard", "init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert not (tmp_path / ".claude").exists()

    def test_json_output_reports_activation(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["--output", "json", "standard", "init", str(tmp_path)]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["claude_activation"]["status"] == "installed"
        assert ".claude/settings.json" in payload["claude_activation"]["written"]
        assert payload["claude_activation"]["context_needs_review"] is False


class TestStandardInitActivationContextOwnership:
    """Issue #582 lot G4: ``.claude/activation-context.md`` is a kit-owned
    file, refreshed like the other standard artifacts instead of being frozen
    forever at whatever wording was current when the project enrolled."""

    def test_a_stale_known_rendering_is_left_by_a_plain_rerun(self, tmp_path: Path) -> None:
        """Without ``--force``, a bare ``init`` rerun never rewrites an
        existing file — same contract as every other standard artifact;
        refreshing a stale-but-known rendering is `grimoire up`'s job."""
        first = runner.invoke(app, ["standard", "init", str(tmp_path)])
        assert first.exit_code == 0, first.output

        context_path = tmp_path / ".claude" / "activation-context.md"
        context_path.write_text(_STALE_824_CHAR_TEMPLATE, encoding="utf-8")

        second = runner.invoke(app, ["standard", "init", str(tmp_path)])
        assert second.exit_code == 0, second.output
        assert context_path.read_text(encoding="utf-8") == _STALE_824_CHAR_TEMPLATE

    def test_force_overwrites_even_a_hand_edited_context(self, tmp_path: Path) -> None:
        first = runner.invoke(app, ["standard", "init", str(tmp_path)])
        assert first.exit_code == 0, first.output

        context_path = tmp_path / ".claude" / "activation-context.md"
        context_path.write_text("Notre directive maison.\n", encoding="utf-8")

        second = runner.invoke(app, ["standard", "init", str(tmp_path), "--force"])
        assert second.exit_code == 0, second.output
        assert context_path.read_text(encoding="utf-8") == activation_directive_template()
