"""W1-11 : MemoryLint et MissionIntakeService ne sont plus du code mort.

Un module exporté par ``grimoire.tools`` qu'aucune commande CLI ni aucun outil
MCP n'appelle est une promesse sans livraison. Ces tests posent la garde
d'architecture, puis les deux branchements : ``memory lint`` (code retour non
nul sur une contradiction) et le ``risk_profile`` posé à la création d'une tâche.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_task import task_app

runner = CliRunner()
SRC = Path(__file__).resolve().parents[2] / "src" / "grimoire"

# Exports de ``tools/`` encore sans appelant CLI/MCP, constatés le jour de W1-11.
# Cliquet : l'ensemble ne peut que rétrécir (voir le second test).
_ORPHELINS_CONNUS = {"AgentForge", "ContextGuard", "ContextRouter", "PreflightCheck"}


def _exports_tools() -> dict[str, str]:
    """Classe exportée -> nom du module, lu dans ``tools/__init__.py``."""
    texte = (SRC / "tools" / "__init__.py").read_text(encoding="utf-8")
    return {m.group(2): m.group(1) for m in re.finditer(r"from grimoire\.tools\.(\w+) import (\w+)", texte)}


def _a_un_appelant(classe: str, module: str) -> bool:
    motif = re.compile(rf"\b{classe}\b|grimoire\.tools\.{module}\b|tools import {module}\b")
    for dossier in ("cli", "mcp"):
        for fichier in (SRC / dossier).rglob("*.py"):
            if motif.search(fichier.read_text(encoding="utf-8")):
                return True
    return False


def test_tout_export_de_tools_a_un_appelant_cli_ou_mcp() -> None:
    sans_appelant = {c for c, m in _exports_tools().items() if not _a_un_appelant(c, m)}
    assert sans_appelant - _ORPHELINS_CONNUS == set()


def test_le_cliquet_des_orphelins_ne_garde_que_des_orphelins() -> None:
    exports = _exports_tools()
    branches = {c for c in _ORPHELINS_CONNUS if c in exports and _a_un_appelant(c, exports[c])}
    assert branches == set(), f"à retirer de _ORPHELINS_CONNUS : {sorted(branches)}"


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
