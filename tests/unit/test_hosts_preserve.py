"""D-07 / revue S1-01 : une issue non destructive à un fichier hôte légitime de l'utilisateur.

Sans déclaration, un fichier préservé fait échouer `up` et `doctor` et dit les deux issues.
Avec ``hosts.preserve``, le fichier reste à l'utilisateur : `up` passe en le listant, la sonde
le montre ``user-owned`` (jamais ``applied``) et la matrice dit que l'élément n'est pas livré.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.cli.cmd_up import _step_host_sync, _UpState
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError
from grimoire.hosts.matrix import PRESERVED, build_matrix
from grimoire.hosts.probe import probe_hosts
from grimoire.hosts.sync import sync_host_surfaces

pytestmark = pytest.mark.covers("cli:host probe", "cli:host matrix")

runner = CliRunner()
AGENT = ".claude/commands/grimoire-doctor.md"  # même nom qu'un élément du kit, écrit par l'utilisateur
SETTINGS = ".claude/settings.json"
CLAUDE = (HostId.CLAUDE_CODE_CLI,)


def _write_config(root: Path, preserve: list[str] | None = None) -> None:
    extra = f"  preserve: {json.dumps(preserve)}\n" if preserve is not None else ""
    (root / "project-context.yaml").write_text(
        'project:\n  name: "Demo Preserve"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n' + extra,
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_config(tmp_path)
    return tmp_path


def _own_agent(root: Path) -> None:
    agent = root / AGENT
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text("# ma commande a moi\n", encoding="utf-8")


def _up(root: Path) -> _UpState:
    state = _UpState()
    _step_host_sync(state, root, dry_run=False, blocked=False)
    return state


class TestSansDeclaration:
    def test_up_echoue_et_donne_les_deux_issues(self, project: Path) -> None:
        _own_agent(project)
        state = _up(project)
        step = state.steps[-1]
        assert step.status == "failed" and state.failed
        assert AGENT in step.detail
        assert "hosts" in step.detail and "preserve" in step.detail  # issue non destructive
        assert "--force" in step.detail  # issue destructive

    def test_settings_json_hors_json_strict_donne_aussi_les_deux_issues(self, project: Path) -> None:
        sync_host_surfaces(project)
        (project / SETTINGS).write_text('{\n  // mon commentaire\n  "a": 1,\n}\n', encoding="utf-8")
        step = _up(project).steps[-1]
        assert step.status == "failed"
        assert f'"{SETTINGS}"' in step.detail  # le chemin à déclarer, sans le suffixe « (JSON illisible) »
        assert "preserve" in step.detail

    def test_doctor_voit_le_conflit(self, project: Path) -> None:
        sync_host_surfaces(project)
        _own_agent(project)
        result = runner.invoke(app, ["doctor", str(project), "-o", "json"])
        checks = json.loads(result.stdout)
        failed = [c for c in (checks if isinstance(checks, list) else checks.get("checks", checks.get("results", []))) if not c["passed"]]
        assert any(c["name"] == "host_sync_claude" and AGENT in c["detail"] for c in failed)


class TestAvecDeclaration:
    def test_up_passe_et_liste_le_fichier(self, project: Path) -> None:
        _own_agent(project)
        _write_config(project, [AGENT])
        state = _up(project)
        step = state.steps[-1]
        assert step.status == "done" and not state.failed
        assert AGENT in step.detail and "user-owned" in step.detail
        assert (project / AGENT).read_text(encoding="utf-8") == "# ma commande a moi\n"

    def test_up_idempotent_le_fichier_reste_intact_a_chaque_passage(self, project: Path) -> None:
        _own_agent(project)
        _write_config(project, [AGENT])
        for _ in range(3):
            assert _up(project).steps[-1].status == "done"
        assert (project / AGENT).read_text(encoding="utf-8") == "# ma commande a moi\n"

    def test_sync_outcome_distingue_preserve_de_conflit(self, project: Path) -> None:
        _own_agent(project)
        _write_config(project, [AGENT])
        outcome = sync_host_surfaces(project)
        assert outcome.ok and outcome.conflicts == () and outcome.preserved == (AGENT,)
        assert outcome.to_dict()["preserved"] == [AGENT]

    def test_force_n_ecrase_pas_un_fichier_declare(self, project: Path) -> None:
        from grimoire.hosts.collect import build_surface
        from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts

        _own_agent(project)
        _write_config(project, [AGENT])
        surface = build_surface(project)
        for host in supported_hosts():
            emitter = emitter_for(host)
            if emitter is not None:
                apply_plan(emitter.plan(surface, project), project, force=True)
        assert (project / AGENT).read_text(encoding="utf-8") == "# ma commande a moi\n"

    def test_sonde_user_owned_jamais_applied(self, project: Path) -> None:
        sync_host_surfaces(project)
        _own_agent(project)
        _write_config(project, [AGENT])
        report = probe_hosts(project, CLAUDE)
        host = report.hosts[0]
        assert report.ok
        assert {(f.path, f.state) for f in host.files} >= {(AGENT, "user-owned")}
        assert all(f.state != "ok" for f in host.files if f.path == AGENT)

    def test_matrice_dit_que_l_element_n_est_pas_livre(self, project: Path) -> None:
        _own_agent(project)
        _write_config(project, [AGENT])
        matrix = build_matrix(project, CLAUDE)
        cell = next(c for c in matrix.cells if c.subject == f"file:{AGENT}")
        assert cell.status == PRESERVED and cell.ok and "pas livré" in cell.reason

    def test_settings_declare_rend_les_hooks_non_livres_et_la_sonde_reste_verte(self, project: Path) -> None:
        sync_host_surfaces(project)
        (project / SETTINGS).write_text('{\n  // commentaire\n  "a": 1,\n}\n', encoding="utf-8")
        _write_config(project, [SETTINGS])
        matrix = build_matrix(project, CLAUDE)
        hooks = [c for c in matrix.cells if c.kind == "hook"]
        assert any(c.status == PRESERVED for c in hooks)
        assert not any(c.status in {"enforced", "advisory"} for c in hooks)  # plus rien n'y est câblé pour l'hôte
        report = probe_hosts(project, CLAUDE)
        assert report.ok
        assert not any(c.disk == "applied" for c in report.hosts[0].cells if c.cell.kind == "hook")
        assert _up(project).steps[-1].status == "done"

    def test_cli_probe_json_montre_user_owned(self, project: Path) -> None:
        sync_host_surfaces(project)
        _own_agent(project)
        _write_config(project, [AGENT])
        result = runner.invoke(app, ["-o", "json", "host", "probe", "--project-root", str(project)])
        payload = json.loads(result.stdout)
        assert result.exit_code == 0
        assert {"path": AGENT, "state": "user-owned", "cause": "déclaré dans hosts.preserve"} in payload["hosts"][0]["files"]

    def test_cli_matrix_json_montre_la_ligne_fichier(self, project: Path) -> None:
        _own_agent(project)
        _write_config(project, [AGENT])
        result = runner.invoke(app, ["-o", "json", "host", "matrix", "--project-root", str(project), "--host", "claude"])
        cells = json.loads(result.stdout)["cells"]
        assert result.exit_code == 0
        assert any(c["subject"] == f"file:{AGENT}" and c["status"] == "preserved" for c in cells)

    def test_doctor_passe_avec_la_declaration(self, project: Path) -> None:
        sync_host_surfaces(project)
        _own_agent(project)
        _write_config(project, [AGENT])
        result = runner.invoke(app, ["doctor", str(project), "-o", "json"])
        assert "host_sync_claude" not in result.stdout


class TestDisableAllHooks:
    def test_sonde_non_verte_quand_les_hooks_sont_coupes(self, project: Path) -> None:
        sync_host_surfaces(project)
        assert probe_hosts(project, CLAUDE).ok
        path = project / SETTINGS
        data = json.loads(path.read_text(encoding="utf-8"))
        data["disableAllHooks"] = True
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report = probe_hosts(project, CLAUDE)
        assert not report.ok
        disks = {c.disk for c in report.hosts[0].cells if c.cell.kind == "hook" and c.cell.status in {"enforced", "advisory"}}
        assert disks == {"disabled"}

    def test_disable_all_hooks_dans_settings_local(self, project: Path) -> None:
        sync_host_surfaces(project)
        (project / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
        assert not probe_hosts(project, CLAUDE).ok

    def test_false_ne_change_rien(self, project: Path) -> None:
        sync_host_surfaces(project)
        (project / ".claude" / "settings.local.json").write_text('{"disableAllHooks": false}', encoding="utf-8")
        assert probe_hosts(project, CLAUDE).ok


class TestConfig:
    def test_chemin_invalide_est_refuse(self, tmp_path: Path) -> None:
        for bad in ("../x", "/etc/passwd", "a/../../b", ""):
            (tmp_path / "project-context.yaml").write_text(
                f'project:\n  name: "x"\nhosts:\n  preserve: [{json.dumps(bad)}]\n', encoding="utf-8"
            )
            with pytest.raises(GrimoireConfigError):
                GrimoireConfig.from_yaml(tmp_path / "project-context.yaml")

    def test_preserve_sans_enabled(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "x"\nhosts:\n  preserve: ["./.claude/agents/a.md"]\n', encoding="utf-8"
        )
        cfg = GrimoireConfig.from_yaml(tmp_path / "project-context.yaml")
        assert cfg.hosts.enabled is None and cfg.hosts.preserve == (".claude/agents/a.md",)
