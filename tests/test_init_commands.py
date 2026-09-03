"""
Tests for grimoire-init.sh new commands: reset, uninstall, quick-update.

These tests create temporary project structures and verify the commands work
correctly without touching real project data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

KIT_DIR = Path(__file__).resolve().parent.parent
INIT_SCRIPT = KIT_DIR / "grimoire-init.sh"
GRIMOIRE_SH = KIT_DIR / "grimoire.sh"


def _find_bash() -> str | None:
    """Un `bash` capable d'exécuter un script, ou None.

    Sur Windows, `bash` tout court résout vers `C:\\Windows\\System32\\bash.exe`
    — le lanceur WSL, qui précède Git Bash dans le PATH des runners. Sans
    distribution installée il sort en erreur avec un message UTF-16, et douze
    tests échouaient en accusant `grimoire-init.sh` d'un défaut qui n'est pas le
    sien.

    On cherche donc Git Bash d'abord, et on ne se rabat sur le PATH que si ce
    qu'on y trouve n'est pas le lanceur WSL.
    """
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if Path(candidate).is_file():
                return candidate
        found = shutil.which("bash")
        if found and "System32" not in found:
            return found
        return None
    return shutil.which("bash")


BASH = _find_bash()

# `grimoire-init.sh`, `grimoire.sh` et `install.sh` sont des points d'entrée
# Unix. Ces tests ne s'exécutent pas sous Windows, et c'est un choix explicite
# plutôt qu'un oubli.
#
# Le job `framework-tests` existe pour les 108 outils Python de
# `framework/tools/` — l'issue #33 avait montré un `import fcntl` fatal
# découvert par un utilisateur et non par la CI. Faire tourner en plus des
# installeurs bash sous Git Bash n'a jamais été son objet, et le dépôt prévoit
# la résorption de ces scripts vers le CLI Python (`planning/resorption-bash.md`).
#
# Mesuré : sous Windows, chacune des 26 invocations atteint le timeout de 30 s,
# soit treize minutes pour un job qui n'apprend rien — et le job mourait avant
# que pytest n'imprime la moindre trace. Le coût est réel, l'information nulle.
#
# Lever ce skip demande de traiter d'abord la portabilité des scripts eux-mêmes
# (chemins Windows, outils Unix supposés présents), qui est le chantier de
# résorption, pas celui de la CI.
requires_bash = pytest.mark.skipif(
    BASH is None or sys.platform == "win32",
    reason=(
        "points d'entrée Unix : sans bash utilisable, ou sous Windows où leur "
        "portabilité n'est pas assurée (voir planning/resorption-bash.md)"
    ),
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(args: list[str], cwd: str | Path, *, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command, return CompletedProcess.

    `stdin` est explicitement fermé. `grimoire-init.sh` contient des invites
    interactives (`read -p "Continuer ? (y/N)"`) : sans stdin fermé, le script
    attend une réponse qui ne viendra jamais. Sous Linux la CI ferme déjà stdin
    et `read` reçoit EOF tout de suite, ce qui masquait le problème ; sous
    Windows le descripteur hérité ne se ferme pas, et le job s'est trouvé bloqué
    plus de quarante minutes là où ubuntu finissait en secondes.

    Le `timeout` ne suffit pas à rattraper ce cas : il tue bien `bash`, mais
    `communicate()` continue d'attendre la fermeture du tube tant qu'un
    petit-fils le tient.
    """
    run_env = {**os.environ, **(env or {})}
    # Décodage explicite : sous Windows, `text=True` seul décode en cp1252, et
    # un script qui parle français casse le décodage avant qu'on lise quoi que
    # ce soit. `replace` garde la sortie lisible, jamais vide.
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=run_env,
        stdin=subprocess.DEVNULL,
    )


def _explain(result: subprocess.CompletedProcess) -> str:
    """Ce qu'un échec doit montrer : le code, et ce que le script a dit.

    Seize tests Windows échouaient en `assert 1 == 0` avec une sortie vide dans
    l'assertion — sans stderr, la cause restait une devinette (#231).
    """
    return f"rc={result.returncode}\n--- stdout ---\n{result.stdout[-1500:]}\n--- stderr ---\n{result.stderr[-1500:]}"


def _create_fake_grimoire(base: Path) -> Path:
    """Create a minimal _grimoire/ structure for testing."""
    grimoire = base / "_grimoire"
    (grimoire / "_config" / "custom" / "agents").mkdir(parents=True)
    (grimoire / "_config" / "custom" / "prompt-templates").mkdir(parents=True)
    (grimoire / "_config" / "custom" / "workflows").mkdir(parents=True)
    (grimoire / "_memory" / "agent-learnings").mkdir(parents=True)
    (grimoire / "_memory" / "session-summaries").mkdir(parents=True)
    (grimoire / "_memory" / "archives").mkdir(parents=True)
    (grimoire / "_memory" / "backends").mkdir(parents=True)

    # Framework files
    (grimoire / "_config" / "custom" / "agent-base.md").write_text("# old agent-base")
    (grimoire / "_config" / "custom" / "cc-verify.sh").write_text("#!/bin/bash\n# old cc")
    (grimoire / "_config" / "custom" / "sil-collect.sh").write_text("#!/bin/bash\n# old sil")

    # Memory files
    (grimoire / "_memory" / "maintenance.py").write_text("# old maintenance")
    (grimoire / "_memory" / "mem0-bridge.py").write_text("# old bridge")
    (grimoire / "_memory" / "session-save.py").write_text("# old save")
    (grimoire / "_memory" / "shared-context.md").write_text("# My context")
    (grimoire / "_memory" / "decisions-log.md").write_text("# Decisions")
    (grimoire / "_memory" / "memories.json").write_text("[]")

    # Agent files
    (grimoire / "_config" / "custom" / "agents" / "atlas.md").write_text("# Atlas")
    (grimoire / "_config" / "custom" / "agents" / "my-custom-agent.md").write_text("# Custom")

    # Agent learnings
    (grimoire / "_memory" / "agent-learnings" / "atlas.md").write_text("# Learnings")

    return grimoire


def _create_fake_output(base: Path) -> Path:
    """Create _grimoire-output/ for testing."""
    output = base / "_grimoire-output"
    (output / ".runs" / "main").mkdir(parents=True)
    (output / "team-vision").mkdir(parents=True)
    (output / ".runs" / "main" / "branch.json").write_text('{"branch": "main"}')
    return output


def _create_project_context(base: Path, version: str = "3.1.0") -> Path:
    """Create project-context.yaml."""
    ctx = base / "project-context.yaml"
    ctx.write_text(textwrap.dedent(f"""\
        project:
          name: "test-project"
        user:
          name: "TestUser"
        grimoire_kit_version: "{version}"
    """))
    return ctx


def _create_copilot_instructions(base: Path) -> Path:
    """Create .github/copilot-instructions.md with Grimoire marker."""
    ci = base / ".github" / "copilot-instructions.md"
    ci.parent.mkdir(parents=True, exist_ok=True)
    ci.write_text("# Copilot Instructions\n> Auto-généré par Grimoire Custom Kit v3.1.0\n", encoding="utf-8")
    return ci


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path):
    """Create a complete fake Grimoire project."""
    _create_fake_grimoire(tmp_path)
    _create_fake_output(tmp_path)
    _create_project_context(tmp_path)
    _create_copilot_instructions(tmp_path)
    return tmp_path


@pytest.fixture
def empty_dir(tmp_path):
    """An empty directory with no Grimoire installation."""
    return tmp_path


# ══════════════════════════════════════════════════════════════════════════════
# cmd_reset tests
# ══════════════════════════════════════════════════════════════════════════════


@requires_bash
class TestCmdReset:
    """Tests for the reset command."""

    def test_reset_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run([BASH, str(INIT_SCRIPT), "reset", "--help"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Remet l'installation" in result.stdout, _explain(result)

    def test_reset_no_grimoire(self, empty_dir):
        """Reset on a directory without _grimoire/ fails."""
        result = _run([BASH, str(INIT_SCRIPT), "reset", "--yes"], cwd=empty_dir)
        assert result.returncode != 0, _explain(result)
        assert "Pas de projet Grimoire" in result.stderr, _explain(result)

    def test_soft_reset_dry_run(self, project_dir):
        """Dry-run soft reset doesn't modify files."""
        # Record original content
        ab = (project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md").read_text()
        assert ab == "# old agent-base"

        result = _run([BASH, str(INIT_SCRIPT), "reset", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Dry-run" in result.stdout, _explain(result)

        # File should be unchanged
        assert (project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md").read_text() == ab

    def test_soft_reset_preserves_memory(self, project_dir):
        """Soft reset preserves _memory/ content."""
        sc = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        sc.write_text("# Important context I wrote")

        result = _run([BASH, str(INIT_SCRIPT), "reset", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        # Memory should be preserved
        assert sc.exists()
        assert sc.read_text() == "# Important context I wrote"

    def test_soft_reset_preserves_custom_agents(self, project_dir):
        """Soft reset preserves custom (non-meta) agents."""
        custom = project_dir / "_grimoire" / "_config" / "custom" / "agents" / "my-custom-agent.md"
        assert custom.exists()

        result = _run([BASH, str(INIT_SCRIPT), "reset", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert custom.exists()
        assert custom.read_text() == "# Custom"

    def test_hard_reset_dry_run(self, project_dir):
        """Hard reset dry-run doesn't delete anything."""
        result = _run([BASH, str(INIT_SCRIPT), "reset", "--hard", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Dry-run" in result.stdout, _explain(result)

        # _grimoire/ should still exist
        assert (project_dir / "_grimoire").exists()

    def test_hard_reset_removes_and_recreates(self, project_dir):
        """Hard reset removes _grimoire/ and recreates the skeleton."""
        custom = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        assert custom.exists()

        result = _run([BASH, str(INIT_SCRIPT), "reset", "--hard", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        # Structure should be recreated
        assert (project_dir / "_grimoire" / "_config" / "custom" / "agents").is_dir()
        assert (project_dir / "_grimoire" / "_memory").is_dir()

        # Old memory content should be gone
        assert not custom.exists() or custom.read_text() != "# My context"

    def test_reset_unknown_option_fails(self, project_dir):
        """Unknown option causes error."""
        result = _run([BASH, str(INIT_SCRIPT), "reset", "--nonexistent"], cwd=project_dir)
        assert result.returncode != 0, _explain(result)


# ══════════════════════════════════════════════════════════════════════════════
# cmd_uninstall tests
# ══════════════════════════════════════════════════════════════════════════════


@requires_bash
class TestCmdUninstall:
    """Tests for the uninstall command."""

    def test_uninstall_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--help"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Supprime complètement" in result.stdout, _explain(result)

    def test_uninstall_no_grimoire(self, empty_dir):
        """Uninstall on an empty dir fails."""
        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes"], cwd=empty_dir)
        assert result.returncode != 0, _explain(result)

    def test_uninstall_removes_grimoire(self, project_dir):
        """Uninstall --yes removes _grimoire/ and _grimoire-output/."""
        assert (project_dir / "_grimoire").exists()
        assert (project_dir / "_grimoire-output").exists()

        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert not (project_dir / "_grimoire").exists()
        assert not (project_dir / "_grimoire-output").exists()

    def test_uninstall_removes_copilot_instructions(self, project_dir):
        """Uninstall removes .github/copilot-instructions.md if generated by Grimoire."""
        ci = project_dir / ".github" / "copilot-instructions.md"
        assert ci.exists()

        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert not ci.exists()

    def test_uninstall_removes_project_context(self, project_dir):
        """Uninstall removes project-context.yaml by default."""
        ctx = project_dir / "project-context.yaml"
        assert ctx.exists()

        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert not ctx.exists()

    def test_uninstall_keep_config(self, project_dir):
        """--keep-config preserves project-context.yaml."""
        ctx = project_dir / "project-context.yaml"
        assert ctx.exists()

        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes", "--keep-config"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert ctx.exists()
        assert not (project_dir / "_grimoire").exists()

    def test_uninstall_success_message(self, project_dir):
        """Success message is displayed."""
        result = _run([BASH, str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "désinstallé avec succès" in result.stdout, _explain(result)


# ══════════════════════════════════════════════════════════════════════════════
# cmd_quickupdate tests
# ══════════════════════════════════════════════════════════════════════════════


@requires_bash
class TestCmdQuickUpdate:
    """Tests for the quick-update command."""

    def test_quickupdate_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run([BASH, str(INIT_SCRIPT), "quick-update", "--help"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Mise à jour rapide" in result.stdout, _explain(result)

    def test_quickupdate_no_grimoire(self, empty_dir):
        """Quick-update on an empty dir fails."""
        result = _run([BASH, str(INIT_SCRIPT), "quick-update"], cwd=empty_dir)
        assert result.returncode != 0, _explain(result)

    def test_quickupdate_dry_run(self, project_dir):
        """Dry-run doesn't modify files."""
        ab = project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md"
        original = ab.read_text()

        result = _run([BASH, str(INIT_SCRIPT), "quick-update", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "Dry-run" in result.stdout, _explain(result)

        assert ab.read_text() == original

    def test_quickupdate_preserves_custom_agents(self, project_dir):
        """Quick-update doesn't touch custom agents."""
        custom = project_dir / "_grimoire" / "_config" / "custom" / "agents" / "my-custom-agent.md"
        original = custom.read_text()

        result = _run([BASH, str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert custom.read_text() == original

    def test_quickupdate_preserves_memory(self, project_dir):
        """Quick-update doesn't touch memory."""
        sc = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        sc.write_text("# My precious context")

        result = _run([BASH, str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)

        assert sc.read_text() == "# My precious context"

    def test_quickupdate_reports_counts(self, project_dir):
        """Output mentions update counts."""
        result = _run([BASH, str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0, _explain(result)
        assert "mis à jour" in result.stdout, _explain(result)

    def test_quickupdate_unknown_option_fails(self, project_dir):
        """Unknown option causes error."""
        result = _run([BASH, str(INIT_SCRIPT), "quick-update", "--nonexistent"], cwd=project_dir)
        assert result.returncode != 0, _explain(result)


# ══════════════════════════════════════════════════════════════════════════════
# grimoire.sh routing tests
# ══════════════════════════════════════════════════════════════════════════════


@requires_bash
class TestGrimoireShRouting:
    """Tests that grimoire.sh correctly routes to new commands."""

    def test_help_shows_reset(self):
        """grimoire help lists the reset command."""
        result = _run([BASH, str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "reset" in result.stdout

    def test_help_shows_uninstall(self):
        """grimoire help lists the uninstall command."""
        result = _run([BASH, str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "uninstall" in result.stdout

    def test_help_shows_quick_update(self):
        """grimoire help lists the quick-update command."""
        result = _run([BASH, str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "quick-update" in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# install.sh bootstrap tests
# ══════════════════════════════════════════════════════════════════════════════

INSTALL_SH = KIT_DIR / "install.sh"


@requires_bash
class TestInstallSh:
    """Tests for the bootstrap install.sh."""

    def test_install_sh_exists(self):
        """install.sh exists."""
        assert INSTALL_SH.exists()

    def test_install_sh_help(self):
        """--help prints usage."""
        result = _run([BASH, str(INSTALL_SH), "--help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "Bootstrap Installer" in result.stdout

    def test_install_sh_has_shebang(self):
        """install.sh has proper shebang."""
        first_line = INSTALL_SH.read_text().split("\n")[0]
        assert first_line == "#!/usr/bin/env bash"
