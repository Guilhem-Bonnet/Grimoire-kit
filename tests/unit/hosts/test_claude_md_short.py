"""GAO-claude-code-001 — un ``CLAUDE.md`` court pour Claude Code, sans import Copilot.

Constat mesuré sur ``main`` : le ``CLAUDE.md`` posé par le kit importait
``.github/copilot-instructions.md`` en entier — ~5,8 k caractères relus à
chaque tour de chaque session. Ces instructions visent Copilot Chat
(démarrage ``@concierge``, tableau des workflows, registre des agents,
arborescence) et ne servent pas à un worker sans interface.

Le fichier court garde langue, contrat de complétion, règle « non vérifié »
et pointeurs de mémoire ; le détail reste dans les fichiers cités. Il est
émis dans un bloc géré : ce que le projet écrit hors du bloc n'est pas
réécrit.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, ScaffoldPlan
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import apply_plan, emitter_for
from grimoire.hosts.emitters.claude_code import claude_md_content, project_language

SHORT_LIMIT = 1500

LEGACY_POINTER = (
    "# Démo — Claude Code\n\n"
    "Ce projet utilise **Grimoire Kit**. Les instructions agent canoniques "
    "sont dans `.github/copilot-instructions.md` (importées ci-dessous).\n\n"
    "@.github/copilot-instructions.md\n"
)

USER_RULES = "# Mes règles\n\nSois concis.\n"


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Démo"\n'
        'user:\n  language: "Français"\n'
        'agents:\n  archetype: "minimal"\n'
        'hosts:\n  enabled: ["claude"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _plan(project: Path):
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    return emitter.plan(build_surface(project), project)


def _claude_md(project: Path):
    return next(f for f in _plan(project).files if f.relpath.as_posix() == "CLAUDE.md")


def test_le_claude_md_genere_n_importe_pas_copilot_et_reste_court(tmp_path: Path) -> None:
    """Critère d'acceptation : aucun import du fichier Copilot, moins de 1 500 caractères."""
    emitted = _claude_md(_project(tmp_path))

    assert "copilot-instructions.md" not in emitted.content
    assert "@.github/copilot-instructions.md" not in emitted.content
    assert len(emitted.content) < SHORT_LIMIT


def test_le_claude_md_genere_porte_les_quatre_sections_attendues(tmp_path: Path) -> None:
    """Langue, contrat de complétion, règle « non vérifié », pointeurs de mémoire."""
    content = _claude_md(_project(tmp_path)).content

    assert "Réponds en Français" in content
    assert "Contrat de complétion" in content
    assert "cc-verify.sh" in content
    assert "« non vérifié »" in content
    assert "_grimoire/_memory/shared-context.md" in content
    assert "_grimoire/_memory/decisions-log.md" in content
    assert "_grimoire/_memory/failure-museum.md" in content
    assert "_grimoire/_memory/session-state.md" in content


def test_le_fichier_ecrit_sur_disque_reste_sous_la_limite(tmp_path: Path) -> None:
    """Le bloc géré lui-même (bornes comprises) reste sous 1 500 caractères."""
    project = _project(tmp_path)
    result = apply_plan(_plan(project), project)

    assert "CLAUDE.md" in result.written
    written = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert len(written) < SHORT_LIMIT
    assert "copilot-instructions.md" not in written
    assert "grimoire:begin" in written and "grimoire:end" in written


def test_un_claude_md_utilisateur_n_est_pas_ecrase(tmp_path: Path) -> None:
    """Criterion 2 : un fichier écrit par l'utilisateur reste intact, et le sync le dit."""
    project = _project(tmp_path)
    target = project / "CLAUDE.md"
    target.write_text(USER_RULES, encoding="utf-8")

    result = apply_plan(_plan(project), project)

    assert "CLAUDE.md" in result.skipped
    assert result.conflicts["CLAUDE.md"] == "foreign"
    assert target.read_text(encoding="utf-8") == USER_RULES


def test_le_sync_ne_reecrit_pas_le_bloc_qu_il_vient_de_poser(tmp_path: Path) -> None:
    project = _project(tmp_path)
    apply_plan(_plan(project), project)

    again = apply_plan(_plan(project), project)

    assert not again.written
    assert "CLAUDE.md" in again.unchanged


def test_un_ancien_pointeur_du_kit_est_migre_vers_le_fichier_court(tmp_path: Path) -> None:
    """Le pointeur que les versions précédentes scaffolidaient est reconnu et remplacé.

    Sans cette reconnaissance, le sync prendrait ce fichier pour un fichier
    étranger, le préserverait, et « host sync ok » échouerait sur tout projet
    existant.
    """
    project = _project(tmp_path)
    target = project / "CLAUDE.md"
    target.write_text(LEGACY_POINTER, encoding="utf-8")

    result = apply_plan(_plan(project), project)

    assert "CLAUDE.md" in result.written
    migrated = target.read_text(encoding="utf-8")
    assert "copilot-instructions.md" not in migrated
    assert migrated.lstrip().startswith("<!-- grimoire:begin")


def test_le_kit_reconnait_le_claude_md_qu_il_a_ecrit(tmp_path: Path) -> None:
    """Un `CLAUDE.md` émis par le kit est « sien » (prunable), pas un fichier étranger."""
    from grimoire.hosts.emitters.base import owned_managed_paths

    project = _project(tmp_path)
    apply_plan(_plan(project), project)

    owned = [p.relative_to(project).as_posix() for p in owned_managed_paths(_plan(project), project)]
    assert "CLAUDE.md" in owned


def test_le_kit_reconnait_l_ancien_pointeur_comme_sien(tmp_path: Path) -> None:
    """L'ancien rendu est reconnu pour la migration (et donc pour l'élagage d'un hôte désactivé)."""
    from grimoire.hosts.emitters.base import owned_managed_paths

    project = _project(tmp_path)
    (project / "CLAUDE.md").write_text(LEGACY_POINTER, encoding="utf-8")

    owned = [p.relative_to(project).as_posix() for p in owned_managed_paths(_plan(project), project)]
    assert "CLAUDE.md" in owned


def test_le_contenu_ne_depend_pas_du_nom_du_projet_pour_la_taille(tmp_path: Path) -> None:
    """Deux noms de projet de tailles très différentes restent sous le plafond."""
    assert len(claude_md_content("p", "Français")) < SHORT_LIMIT
    assert len(claude_md_content("projet-" * 20, "Français")) < SHORT_LIMIT


def test_la_langue_par_defaut_est_le_francais_sans_config(tmp_path: Path) -> None:
    """Une config absente ne fait pas échouer l'émission : on retombe sur le défaut du kit."""
    assert project_language(tmp_path) == "Français"


def test_le_fichier_d_instructions_copilot_reste_planifie(tmp_path: Path) -> None:
    """Criterion 3 : la surface Copilot garde son producteur d'instructions, inchangé."""
    resolved = ResolvedArchetype(
        archetype="minimal",
        archetypes=["minimal"],
        stack_agents=[],
        feature_agents=[],
        reason="test",
    )
    scaffolder = ProjectScaffolder(
        target=tmp_path,
        project_name="Démo",
        user_name="test-user",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=resolved,
        backend="local",
    )
    plan = ScaffoldPlan()
    scaffolder._plan_copilot_instructions(plan)

    copilot = next(t for t in plan.templates if t.label == ".github/copilot-instructions.md")
    assert "@concierge" in copilot.content
