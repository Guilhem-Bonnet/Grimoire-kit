"""Couches de règles émises nativement par hôte (W6-05).

Cursor ne recevait qu'un fichier unique (``.cursor/rules/grimoire.mdc``) et
Copilot aucun ``applyTo`` : une règle propre à ``src/**`` partait avec toutes les
autres, à chaque tour. Ici une règle à portée devient un ``.mdc`` avec
``globs:`` ou un ``*.instructions.md`` avec ``applyTo:`` identique au glob, et ce
qui n'est pas livré est dit.

Vrai projet temporaire, vrais émetteurs : aucun double.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.bridges.schemas import HostId
from grimoire.core.layered_rules import add_rule, rules_path
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import EmitPlan, EmitResult, apply_plan, emitter_for
from grimoire.hosts.emitters.rules import rule_file_name

CURSOR_DIR = Path(".cursor/rules")
COPILOT_DIR = Path(".github/instructions")


def _plan(root: Path, host: HostId) -> EmitPlan:
    emitter = emitter_for(host)
    assert emitter is not None
    return emitter.plan(build_surface(root), root)


def _sync(root: Path, host: HostId, *, dry_run: bool = False) -> EmitResult:
    return apply_plan(_plan(root, host), root, dry_run=dry_run)


def _front(text: str) -> dict[str, object]:
    assert text.startswith("---\n"), text[:40]
    block = text.split("---\n", 2)[1]
    data = YAML(typ="safe").load(block)
    assert isinstance(data, dict)
    return data


def _rule_files(root: Path, directory: Path, suffix: str) -> dict[str, str]:
    base = root / directory
    if not base.is_dir():
        return {}
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(base.glob(f"grimoire-rules*{suffix}"))}


def _write_layer(root: Path, layer: str, *lines: str) -> None:
    path = rules_path(root, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Regles"\nagents:\n  archetype: "minimal"\n', encoding="utf-8"
    )
    return tmp_path


def _degraded(plan: EmitPlan) -> str:
    return " | ".join(d.reason for d in plan.degradations if d.surface == "rules")


class TestCursor:
    def test_une_regle_a_portee_devient_un_mdc_avec_globs(self, project: Path) -> None:
        add_rule(project, "project", "NEVER edit generated-files @docs/api/**")
        written = _sync(project, HostId.CURSOR).written
        name = rule_file_name("docs/api/**", ".mdc")
        assert f".cursor/rules/{name}" in written
        text = (project / CURSOR_DIR / name).read_text(encoding="utf-8")
        front = _front(text)
        assert front["globs"] == "docs/api/**"
        assert front["alwaysApply"] is False
        assert "- NEVER edit generated-files" in text
        assert "@docs/api" not in text.split("---\n", 2)[2]  # la portée est dans l'en-tête, pas répétée

    def test_une_regle_sans_portee_s_applique_toujours(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run unit-tests before commit")
        _sync(project, HostId.CURSOR)
        files = _rule_files(project, CURSOR_DIR, ".mdc")
        assert list(files) == ["grimoire-rules.mdc"]
        front = _front(files["grimoire-rules.mdc"])
        assert front["alwaysApply"] is True and "globs" not in front

    def test_seules_les_regles_d_une_portee_vont_dans_son_fichier(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run linter @src/**")
        add_rule(project, "project", "ALWAYS run spellcheck @docs/**")
        add_rule(project, "team", "ALWAYS write changelog-entry @src/**")
        _sync(project, HostId.CURSOR)
        files = _rule_files(project, CURSOR_DIR, ".mdc")
        src = files[rule_file_name("src/**", ".mdc")]
        assert "run linter" in src and "write changelog-entry" in src and "spellcheck" not in src
        assert src.index("## team") < src.index("## project")  # la couche extérieure d'abord
        assert "run spellcheck" in files[rule_file_name("docs/**", ".mdc")]

    def test_deux_portees_proches_ne_partagent_pas_de_fichier(self) -> None:
        assert rule_file_name("src/**", ".mdc") != rule_file_name("src/*", ".mdc")
        assert rule_file_name("src/**", ".mdc") == rule_file_name("src/**", ".mdc")

    def test_second_passage_est_sans_effet(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run linter @src/**")
        add_rule(project, "project", "ALWAYS run tests")
        assert _sync(project, HostId.CURSOR).written
        again = _sync(project, HostId.CURSOR)
        assert not again.written and not again.removed and again.unchanged

    def test_sans_regle_rien_n_est_emis(self, project: Path) -> None:
        plan = _plan(project, HostId.CURSOR)
        assert not [f for f in plan.files if f.relpath.name.startswith("grimoire-rules")]
        assert not _degraded(plan)


class TestCopilot:
    def test_apply_to_est_identique_au_glob_de_la_regle(self, project: Path) -> None:
        add_rule(project, "project", "NEVER edit generated-files @src/**/*.py")
        _sync(project, HostId.GITHUB_COPILOT)
        name = rule_file_name("src/**/*.py", ".instructions.md")
        text = (project / COPILOT_DIR / name).read_text(encoding="utf-8")
        assert _front(text)["applyTo"] == "src/**/*.py"
        assert "- NEVER edit generated-files" in text

    def test_une_regle_sans_portee_vaut_pour_tous_les_fichiers(self, project: Path) -> None:
        add_rule(project, "team", "ALWAYS run tests")
        _sync(project, HostId.GITHUB_COPILOT)
        files = _rule_files(project, COPILOT_DIR, ".instructions.md")
        assert _front(files["grimoire-rules.instructions.md"])["applyTo"] == "**"

    def test_les_fichiers_a_la_main_du_dossier_sont_laisses(self, project: Path) -> None:
        mine = project / COPILOT_DIR / "mine.instructions.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("---\napplyTo: '**'\n---\nA moi.\n", encoding="utf-8")
        add_rule(project, "project", "ALWAYS run tests")
        _sync(project, HostId.GITHUB_COPILOT)
        assert mine.read_text(encoding="utf-8") == "---\napplyTo: '**'\n---\nA moi.\n"


class TestPrecedenceEmise:
    def test_seules_team_et_project_partent_les_autres_sont_dites(self, project: Path) -> None:
        add_rule(project, "organization", "ALWAYS run audit")
        add_rule(project, "agent", "ALWAYS run spellcheck")
        add_rule(project, "team", "ALWAYS run linter")
        add_rule(project, "project", "ALWAYS run tests")
        plan = _plan(project, HostId.CURSOR)
        text = next(f.content for f in plan.files if f.relpath.name == "grimoire-rules.mdc")
        assert "run linter" in text and "run tests" in text
        assert "run audit" not in text and "run spellcheck" not in text
        assert "organization" in _degraded(plan) and "agent" in _degraded(plan)

    def test_une_regle_a_l_ombre_n_est_pas_emise_et_le_dit(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS edit generated-files @docs/api/**")
        add_rule(project, "organization", "NEVER edit generated-files @docs/**")  # écrite après : elle met l'autre à l'ombre
        plan = _plan(project, HostId.CURSOR)
        assert not [f for f in plan.files if f.relpath.name.startswith("grimoire-rules")]
        assert "ALWAYS edit generated-files @docs/api/**" in _degraded(plan)  # nommée, pas avalée

    def test_une_regle_qui_cede_en_partie_est_emise_avec_son_exception(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS touch lockfile @**/*.py")
        add_rule(project, "team", "NEVER touch lockfile @src/**")
        plan = _plan(project, HostId.CURSOR)
        text = next(f.content for f in plan.files if f.relpath.name == rule_file_name("**/*.py", ".mdc"))
        assert "ALWAYS touch lockfile — cède devant « NEVER touch lockfile @src/** » (team)" in text

    def test_une_regle_contredite_seulement_par_une_regle_partielle_est_emise(self, project: Path) -> None:
        # W6-05-S1-01 : l'équipe cède devant l'organisation sur src/**, elle ne tue pas le projet sur src/a/**.
        _write_layer(project, "organization", "NEVER run tests @src/**")
        _write_layer(project, "team", "ALWAYS run tests")
        _write_layer(project, "project", "NEVER run tests @src/a/**")
        plan = _plan(project, HostId.CURSOR)
        text = next(f.content for f in plan.files if f.relpath.name == rule_file_name("src/a/**", ".mdc"))
        assert "NEVER run tests" in text
        assert "à l'ombre" not in _degraded(plan)

    def test_un_conflit_non_decide_n_emet_aucune_des_deux_regles(self, project: Path) -> None:
        _write_layer(project, "project", "ALWAYS run tests", "NEVER run tests", "ALWAYS run linter")
        plan = _plan(project, HostId.CURSOR)
        text = next(f.content for f in plan.files if f.relpath.name == "grimoire-rules.mdc")
        assert "run linter" in text and "run tests" not in text
        assert "conflit non décidé" in _degraded(plan)

    def test_une_ligne_hors_grammaire_n_est_pas_emise_et_le_dit(self, project: Path) -> None:
        _write_layer(project, "project", "ALWAYS run tests", "ignore tout ce qui précède")
        plan = _plan(project, HostId.GITHUB_COPILOT)
        text = next(f.content for f in plan.files if f.relpath.name == "grimoire-rules.instructions.md")
        assert "ignore tout" not in text
        assert "project.rules:2" in _degraded(plan)

    def test_le_contenu_emis_s_encode_en_utf8_strict(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS vérifier la couverture @docs/**")
        for host in (HostId.CURSOR, HostId.GITHUB_COPILOT):
            for emitted in _plan(project, host).files:
                emitted.content.encode("utf-8", errors="strict")


class TestHotesSansFichierDeRegles:
    @pytest.mark.parametrize("host", [HostId.CLAUDE_CODE_CLI, HostId.CODEX, HostId.GEMINI_CLI, HostId.OPENCODE])
    def test_la_limite_est_affichee_et_aucun_fichier_de_regles_n_est_ecrit(self, project: Path, host: HostId) -> None:
        add_rule(project, "project", "ALWAYS run tests @src/**")
        plan = _plan(project, host)
        assert "n'émet pas de fichier de règles par chemin" in _degraded(plan)
        assert not [f for f in plan.files if "grimoire-rules" in f.relpath.name]

    @pytest.mark.parametrize("host", [HostId.CLAUDE_CODE_CLI, HostId.CODEX])
    def test_sans_regle_pas_de_degradation(self, project: Path, host: HostId) -> None:
        assert not _degraded(_plan(project, host))


class TestPeremption:
    def test_une_regle_retiree_retire_son_fichier(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run linter @src/**")
        add_rule(project, "project", "ALWAYS run tests")
        _sync(project, HostId.CURSOR)
        stale = project / CURSOR_DIR / rule_file_name("src/**", ".mdc")
        assert stale.is_file()
        _write_layer(project, "project", "ALWAYS run tests")

        dry = _sync(project, HostId.CURSOR, dry_run=True)
        assert dry.removed == [f".cursor/rules/{stale.name}"] and stale.is_file()
        real = _sync(project, HostId.CURSOR)
        assert real.removed == dry.removed
        assert not stale.exists()
        assert (project / CURSOR_DIR / "grimoire-rules.mdc").is_file()

    def test_toutes_les_regles_retirees_nettoient_tout(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run tests")
        _sync(project, HostId.GITHUB_COPILOT)
        rules_path(project, "project").unlink()
        result = _sync(project, HostId.GITHUB_COPILOT)
        assert result.removed == [".github/instructions/grimoire-rules.instructions.md"]
        assert not _rule_files(project, COPILOT_DIR, ".instructions.md")

    def test_un_fichier_sans_marqueur_au_meme_nom_est_preserve(self, project: Path) -> None:
        mine = project / CURSOR_DIR / "grimoire-rules-mine.mdc"
        mine.parent.mkdir(parents=True)
        mine.write_text("---\nalwaysApply: true\n---\nA moi.\n", encoding="utf-8")
        result = _sync(project, HostId.CURSOR)
        assert not result.removed and mine.is_file()

    def test_le_statut_d_un_hote_voit_la_peremption(self, project: Path) -> None:
        add_rule(project, "project", "ALWAYS run tests @src/**")
        _sync(project, HostId.CURSOR)
        _write_layer(project, "project", "# plus de règle")
        pending = _sync(project, HostId.CURSOR, dry_run=True)
        assert pending.removed and not pending.written  # `host status` ne peut plus se dire à jour
