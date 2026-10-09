"""W1-11 : MemoryLint et MissionIntakeService ne sont plus du code mort.

Un module exporté par ``grimoire.tools`` qu'aucune commande CLI ni aucun outil
MCP n'appelle est une promesse sans livraison. Ces tests posent la garde
d'architecture, puis les deux branchements : ``memory lint`` (code retour non
nul sur une contradiction) et le ``risk_profile`` posé à la création d'une tâche.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_task import task_app

runner = CliRunner()
SRC = Path(__file__).resolve().parents[2] / "src" / "grimoire"

# Exports de ``tools/`` encore sans appelant CLI/MCP, constatés le jour de W1-11.
# Ce sont l'API SDK publique (docs/sdk-guide.md) : le cliquet mesure l'atteignabilité
# CLI/MCP, il n'autorise PAS leur suppression. L'ensemble ne peut que rétrécir.
_ORPHELINS_CONNUS = {"AgentForge", "ContextGuard", "ContextRouter", "PreflightCheck"}


def _exports_tools() -> dict[str, str]:
    """Classe exportée -> nom du module, par import réel de ``grimoire.tools``."""
    import importlib

    tools = importlib.import_module("grimoire.tools")
    exports = {nom: getattr(tools, nom).__module__.rsplit(".", 1)[-1] for nom in tools.__all__}
    assert exports, "grimoire.tools.__all__ est vide : la garde ne mesurerait rien"
    return exports


def _source_appelle(source: str, classe: str, module: str) -> bool:
    """Vrai si le code (AST, hors commentaires et docstrings) importe ou nomme l'export."""
    tree = ast.parse(source)
    for noeud in ast.walk(tree):
        if isinstance(noeud, ast.ImportFrom) and noeud.module:
            if noeud.module == f"grimoire.tools.{module}" and any(a.name == classe for a in noeud.names):
                return True
            if noeud.module == "grimoire.tools" and any(a.name in (classe, module) for a in noeud.names):
                return True
        elif isinstance(noeud, ast.Import):
            if any(a.name.startswith(f"grimoire.tools.{module}") for a in noeud.names):
                return True
        elif (isinstance(noeud, ast.Name) and noeud.id == classe) or (
            isinstance(noeud, ast.Attribute) and noeud.attr == classe
        ):
            return True
    return False


def _a_un_appelant(classe: str, module: str) -> bool:
    for dossier in ("cli", "mcp"):
        for fichier in (SRC / dossier).rglob("*.py"):
            if _source_appelle(fichier.read_text(encoding="utf-8"), classe, module):
                return True
    return False


def test_tout_export_de_tools_a_un_appelant_cli_ou_mcp() -> None:
    sans_appelant = {c for c, m in _exports_tools().items() if not _a_un_appelant(c, m)}
    assert sans_appelant - _ORPHELINS_CONNUS == set()


def test_le_cliquet_des_orphelins_ne_garde_que_des_orphelins() -> None:
    exports = _exports_tools()
    disparus = _ORPHELINS_CONNUS - set(exports)
    assert disparus == set(), f"plus exportés, à retirer de _ORPHELINS_CONNUS : {sorted(disparus)}"
    branches = {c for c in _ORPHELINS_CONNUS if _a_un_appelant(c, exports[c])}
    assert branches == set(), f"à retirer de _ORPHELINS_CONNUS : {sorted(branches)}"


def test_les_exports_se_lisent_par_ast_pas_par_texte() -> None:
    # Commentaire et docstring ne sont pas des appelants ; un appel réel l'est.
    assert not _source_appelle('"""MemoryLint ici."""\n# TODO: brancher AgentForge\n', "MemoryLint", "memory_lint")
    assert not _source_appelle("# TODO: brancher AgentForge\nx = 1\n", "AgentForge", "agent_forge")
    assert _source_appelle("from grimoire.tools.memory_lint import MemoryLint\n", "MemoryLint", "memory_lint")
    assert _source_appelle("from grimoire.tools import memory_lint\n", "MemoryLint", "memory_lint")
    assert _source_appelle("import grimoire.tools as t\nt.MemoryLint(r)\n", "MemoryLint", "memory_lint")


def test_les_noms_du_guide_sdk_restent_importables() -> None:
    import importlib

    guide = (SRC.parents[1] / "docs" / "sdk-guide.md").read_text(encoding="utf-8")
    tools = importlib.import_module("grimoire.tools")
    cites = {nom for nom in tools.__all__ if re.search(rf"\b{nom}\b", guide)}
    assert cites, "docs/sdk-guide.md ne cite plus aucun export de grimoire.tools"
    for nom in cites:
        assert hasattr(tools, nom), f"{nom} cité dans docs/sdk-guide.md mais non importable"


def _memoire(racine: Path, contradiction: bool) -> None:
    (racine / "project-context.yaml").write_text("project:\n  name: t\n", encoding="utf-8")
    mem = racine / "_grimoire" / "_memory"
    (mem / "agent-learnings").mkdir(parents=True)
    (mem / "agent-learnings" / "dev.md").write_text(
        "# Dev\n- [2024-01-01] TDD approach adopted and validated for backend\n", encoding="utf-8"
    )
    texte = "TDD approach rejected and abandoned for backend" if contradiction else "Tout autre sujet sans rapport"
    (mem / "decisions-log.md").write_text(f"# Dec\n- [2024-01-02] {texte}\n", encoding="utf-8")


def test_memory_lint_sort_en_erreur_sur_une_contradiction(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 1, res.output
    rapport = json.loads(res.output)
    assert any(i["category"] == "contradiction" for i in rapport["issues"])


def test_memory_lint_sort_a_zero_sans_contradiction(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=False)
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["summary"]["errors"] == 0


def _profil_de(projet: Path, titre: str) -> str:
    res = runner.invoke(task_app, ["add", titre, "-a", "fait", "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    from grimoire.missions.service import TaskService

    service = TaskService(projet, Path("_grimoire-runtime-output/ledger"))
    return service.list_tasks()[-1].risk_profile.value


def test_task_add_pose_le_risk_profile_depuis_l_intake(tmp_path: Path) -> None:
    assert _profil_de(tmp_path, "Purge the archives then delete old exports") == "strict"


def test_task_add_garde_standard_sans_signal_de_risque(tmp_path: Path) -> None:
    assert _profil_de(tmp_path, "Ajouter /health") == "standard"


def test_task_add_ne_rabaisse_jamais_en_light(tmp_path: Path) -> None:
    for titre in (
        "Review the migration of production user accounts",
        "Inspect prod database",
        "List the files",
        "Check the CI",
    ):
        assert _profil_de(tmp_path, titre) != "light", titre


def test_task_add_ne_prend_pas_token_pour_un_secret(tmp_path: Path) -> None:
    assert _profil_de(tmp_path, "Fix the token budget overflow") == "standard"
    assert _profil_de(tmp_path, "Rotate the api token") in {"strict", "security_critical"}


def test_memory_lint_remonte_jusqu_a_la_racine_du_projet(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    sub = tmp_path / "sub"
    sub.mkdir()
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(sub)])
    assert res.exit_code == 1, res.output


def test_memory_lint_racine_inexistante_sort_non_nul(tmp_path: Path) -> None:
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path / "absent")])
    assert res.exit_code != 0, res.output


def test_memory_lint_memoire_vide_sort_en_2_sauf_allow_empty(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: t\n", encoding="utf-8")
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 2, res.output
    ok = runner.invoke(app, ["memory", "lint", "--json", "--allow-empty", "--project-root", str(tmp_path)])
    assert ok.exit_code == 0, ok.output


def test_memory_lint_contradiction_resolue_et_consignee_sort_a_zero(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    log = tmp_path / "_grimoire" / "_memory" / "contradiction-log.md"
    log.write_text(
        "# Contradictions\n- [2024-01-03] Resolved: TDD approach adopted for backend; "
        "the rejected entry is superseded\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["summary"]["errors"] == 0
