"""D-07 : la matrice règle × hôte est calculée, la sonde lit le disque, ``host_sync.ok`` est exigé.

Vrai projet temporaire, vrais émetteurs. Les seuls doubles servent à fabriquer le
mensonge qu'on veut voir refusé (un émetteur qui oublie de déclarer sa limite).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.layered_rules import add_rule
from grimoire.hosts import matrix as matrix_mod
from grimoire.hosts.emitters import EmittedFile, apply_plans, emitter_for, supported_hosts
from grimoire.hosts.emitters.base import EmitPlan
from grimoire.hosts.matrix import (
    ABSENT,
    ADVISORY,
    DEGRADED,
    ENFORCED,
    INERT,
    MISSCOPED,
    NATIVE,
    PROSE,
    UNDECLARED,
    build_matrix,
)
from grimoire.hosts.probe import probe_hosts

ALL = tuple(supported_hosts())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Matrice"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _sync(root: Path, hosts: tuple[HostId, ...] = ALL) -> None:
    matrix = build_matrix(root, hosts)
    results = apply_plans([matrix.plans[h] for h in matrix.hosts], root)
    assert all(r.ok for r in results), [r.conflicts for r in results]


def _cell(root: Path, host: HostId, subject: str, *, matrix: matrix_mod.Matrix | None = None) -> matrix_mod.Cell:
    m = matrix or build_matrix(root, (host,))
    return next(c for c in m.for_host(host) if c.subject == subject)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── la matrice vient de ce que les émetteurs écrivent ─────────────────────────


class TestMatrix:
    def test_claude_execute_et_bloque_ce_qui_est_bloquant(self, project: Path) -> None:
        pre = _cell(project, HostId.CLAUDE_CODE_CLI, "hook:pre_tool_use")
        assert pre.status == ENFORCED and pre.enforcement == "blocking"
        assert pre.carriers == (".claude/settings.json",)
        assert _cell(project, HostId.CLAUDE_CODE_CLI, "hook:session_start").status == ADVISORY

    def test_un_hote_de_prose_enonce_la_regle_sans_l_executer(self, project: Path) -> None:
        for host in (HostId.CODEX, HostId.CURSOR, HostId.GEMINI_CLI):
            cell = _cell(project, host, "hook:pre_tool_use")
            assert cell.status == PROSE, host
            assert cell.carriers  # l'entrypoint porte la règle, c'est vérifiable sur disque

    def test_opencode_garde_pre_tool_use_et_declare_le_reste_absent(self, project: Path) -> None:
        assert _cell(project, HostId.OPENCODE, "hook:pre_tool_use").status == ENFORCED
        assert _cell(project, HostId.OPENCODE, "hook:session_start").status == ABSENT

    def test_la_matrice_par_defaut_est_conforme_pour_chaque_hote(self, project: Path) -> None:
        matrix = build_matrix(project)
        assert matrix.hosts == ALL
        assert matrix.ok, [c.to_dict() for c in matrix.failing]

    def test_une_limite_oubliee_par_l_emetteur_est_undeclared(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real = emitter_for(HostId.CODEX)
        assert real is not None

        class Silent:
            def plan(self, surface: object, root: Path) -> EmitPlan:
                plan = real.plan(surface, root)  # type: ignore[arg-type]
                return replace(plan, degradations=tuple(d for d in plan.degradations if d.surface != "hooks"))

        monkeypatch.setattr(matrix_mod, "emitter_for", lambda h: Silent() if h is HostId.CODEX else emitter_for(h))
        matrix = build_matrix(project, (HostId.CODEX,))
        assert not matrix.ok
        assert {c.status for c in matrix.failing} == {UNDECLARED}

    def test_un_hook_ecrit_pour_un_evenement_que_l_hote_ignore_est_inert(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = emitter_for(HostId.CURSOR)
        assert real is not None

        class Wired:
            def plan(self, surface: object, root: Path) -> EmitPlan:
                plan = real.plan(surface, root)  # type: ignore[arg-type]
                fake = EmittedFile(Path("hooks.json"), '{"command": "grimoire-hook --host cursor --event PreToolUse"}')
                return replace(plan, files=(*plan.files, fake))

        monkeypatch.setattr(matrix_mod, "emitter_for", lambda h: Wired() if h is HostId.CURSOR else emitter_for(h))
        assert _cell(project, HostId.CURSOR, "hook:pre_tool_use", matrix=build_matrix(project, (HostId.CURSOR,))).status == INERT


class TestRuleCells:
    def test_regle_scopee_native_sur_cursor_et_copilot_degradee_ailleurs(self, project: Path) -> None:
        add_rule(project, "project", "NEVER edit generated-files @src/**")
        subject = "rule:project:NEVER edit generated-files @src/**"
        matrix = build_matrix(project)
        assert _cell(project, HostId.CURSOR, subject, matrix=matrix).status == NATIVE
        assert _cell(project, HostId.GITHUB_COPILOT, subject, matrix=matrix).status == NATIVE
        for host in (HostId.CLAUDE_CODE_CLI, HostId.CODEX, HostId.GEMINI_CLI, HostId.OPENCODE):
            assert _cell(project, host, subject, matrix=matrix).status == DEGRADED, host
        assert matrix.ok

    def test_une_regle_d_une_couche_non_livree_est_declaree_sur_tous_les_hotes(self, project: Path) -> None:
        add_rule(project, "organization", "ALWAYS run tests")
        matrix = build_matrix(project)
        cells = [c for c in matrix.cells if c.subject == "rule:organization:ALWAYS run tests"]
        assert len(cells) == len(ALL)
        assert {c.status for c in cells} == {DEGRADED}
        assert matrix.ok

    def test_une_portee_perdue_par_le_fichier_est_misscoped(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        add_rule(project, "project", "NEVER edit generated-files @src/**")
        real = emitter_for(HostId.CURSOR)
        assert real is not None

        class Unscoped:
            def plan(self, surface: object, root: Path) -> EmitPlan:
                plan = real.plan(surface, root)  # type: ignore[arg-type]
                files = tuple(
                    replace(f, content=f.content.replace("globs: src/**\n", "")) if f.relpath.name.startswith("grimoire-rules") else f
                    for f in plan.files
                )
                return replace(plan, files=files)

        monkeypatch.setattr(matrix_mod, "emitter_for", lambda h: Unscoped() if h is HostId.CURSOR else emitter_for(h))
        cell = _cell(project, HostId.CURSOR, "rule:project:NEVER edit generated-files @src/**",
                     matrix=build_matrix(project, (HostId.CURSOR,)))
        assert cell.status == MISSCOPED


# ── la sonde lit le disque ────────────────────────────────────────────────────


class TestProbe:
    def test_un_projet_synchronise_est_conforme_sur_tous_les_hotes(self, project: Path) -> None:
        add_rule(project, "project", "NEVER edit generated-files @src/**")
        _sync(project)
        report = probe_hosts(project, ALL)
        assert report.ok, report.to_dict()
        assert {h.host for h in report.hosts} == {h.value for h in ALL}

    def test_un_projet_jamais_synchronise_est_manquant_pas_ok(self, project: Path) -> None:
        report = probe_hosts(project, (HostId.CLAUDE_CODE_CLI,))
        assert not report.ok
        host = report.hosts[0]
        assert {f.state for f in host.files} == {"missing"}
        assert {c.disk for c in host.cells} == {"missing"}

    def test_un_hook_retire_de_settings_json_est_periome_avec_le_hook_nomme(self, project: Path) -> None:
        _sync(project, (HostId.CLAUDE_CODE_CLI,))
        settings = project / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        del data["hooks"]["PreToolUse"]
        settings.write_text(json.dumps(data), encoding="utf-8")
        report = probe_hosts(project, (HostId.CLAUDE_CODE_CLI,))
        assert not report.ok
        by_subject = {c.cell.subject: c for c in report.hosts[0].cells}
        assert by_subject["hook:pre_tool_use"].disk == "stale"
        assert by_subject["hook:session_start"].disk == "stale"  # même fichier, périmé : on ne le dit pas à jour
        assert ".claude/settings.json" in report.drift

    def test_une_regle_disparue_du_fichier_copilot_est_manquante(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run tests")
        _sync(project, (HostId.GITHUB_COPILOT,))
        (project / ".github" / "instructions" / "grimoire-rules.instructions.md").unlink()
        report = probe_hosts(project, (HostId.GITHUB_COPILOT,))
        cell = next(c for c in report.hosts[0].cells if c.cell.subject.startswith("rule:"))
        assert cell.disk == "missing" and not report.ok

    def test_un_fichier_edite_a_la_main_est_un_conflit_pas_une_derive(self, project: Path) -> None:
        _sync(project, (HostId.CLAUDE_CODE_CLI,))
        readme = project / ".claude" / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nnote perso\n", encoding="utf-8")
        report = probe_hosts(project, (HostId.CLAUDE_CODE_CLI,))
        assert not report.ok
        assert report.hosts[0].conflicts == (".claude/README.md",)
        assert report.drift == ()  # `host sync` ne le réécrirait pas : ce n'est pas du retard à rattraper

    def test_aucun_hote_sonde_n_est_pas_un_succes(self, project: Path) -> None:
        report = probe_hosts(project, ())
        assert not report.verified and not report.ok

    def test_la_sonde_n_ecrit_rien(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run tests")
        _sync(project, (HostId.CLAUDE_CODE_CLI, HostId.CURSOR))
        (project / ".claude" / "settings.json").unlink()
        before = _snapshot(project)
        probe_hosts(project, ALL)
        assert _snapshot(project) == before
