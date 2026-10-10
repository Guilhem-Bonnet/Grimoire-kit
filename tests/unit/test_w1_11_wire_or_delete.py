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

import pytest
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


@pytest.mark.parametrize(
    "titre",
    [
        "Rotate the GitHub token",
        "Leaked OAuth token in logs",
        "Fix CSRF token validation",
        "Rotate the token",
        "Revoke refresh tokens",
        "Rotate the api token",
    ],
)
def test_task_add_garde_les_jetons_d_acces_en_critique(tmp_path: Path, titre: str) -> None:
    assert _profil_de(tmp_path, titre) in {"strict", "security_critical"}, titre


@pytest.mark.parametrize(
    "titre",
    [
        "Fix the token budget overflow",
        "Count tokens in the prompt",
        "Improve the tokenizer speed",
        "Reduce LLM token usage",
        "Raise the token limit",
    ],
)
def test_task_add_ne_prend_pas_un_token_de_llm_pour_un_secret(tmp_path: Path, titre: str) -> None:
    assert _profil_de(tmp_path, titre) == "standard", titre


@pytest.mark.parametrize(
    "titre",
    [
        "Save refresh tokens in plaintext",
        "Saving GitHub tokens to keyring",
        "Count leaked tokens in logs",
        "Reduce session tokens lifetime",
        "Enforce minimum reset token length",
        "Fix access token overflow",
        "Store input token from OAuth callback",
    ],
)
def test_task_add_un_jeton_d_acces_qualifie_reste_critique(tmp_path: Path, titre: str) -> None:
    # Ni un verbe (count/save/reduce) ni un mot (length/overflow/input) ne rend un secret inoffensif.
    assert _profil_de(tmp_path, titre) == "security_critical", titre


def test_memory_lint_remonte_jusqu_a_la_racine_du_projet(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    sub = tmp_path / "sub"
    sub.mkdir()
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(sub)])
    assert res.exit_code == 1, res.output


def test_memory_lint_racine_inexistante_sort_non_nul(tmp_path: Path) -> None:
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path / "absent")])
    assert res.exit_code == 1, res.output
    assert "Not a Grimoire project" in res.output


def test_memory_lint_racine_inexistante_ne_remonte_pas_vers_un_projet_ancetre(tmp_path: Path) -> None:
    # Une racine absente sous un projet existant ne doit pas être résolue vers l'ancêtre.
    _memoire(tmp_path, contradiction=True)
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path / "absent" / "sub")])
    assert res.exit_code == 1, res.output
    assert "Not a Grimoire project" in res.output


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


def _lint_avec_journal(tmp_path: Path, ligne: str | None) -> int:
    _memoire(tmp_path, contradiction=True)
    if ligne is not None:
        log = tmp_path / "_grimoire" / "_memory" / "contradiction-log.md"
        log.write_text(f"# Contradictions\n{ligne}\n", encoding="utf-8")
    return runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)]).exit_code


@pytest.mark.parametrize(
    "ligne",
    [
        "- [2024-01-03] UNRESOLVED: TDD approach adopted vs rejected for backend, still open",
        "- Not resolved yet: TDD approach adopted vs rejected for backend",
        "- [2024-01-03] Not resolved: TDD approach adopted and rejected for backend",
        "- Unresolved: TDD backend approach adopted vs rejected",
        "- [2024-01-03] Open: TDD approach adopted vs rejected for backend, resolved later maybe",
        # résolution antérieure aux deux entrées (2024-01-01 et 2024-01-02)
        "- [2023-12-31] Resolved: TDD approach adopted for backend; the rejected entry is superseded",
        # résolution postérieure à l'une des deux entrées seulement
        "- [2024-01-01] Resolved: TDD approach adopted for backend; the rejected entry is superseded",
        # case non cochée ou point d'exclamation : pas une résolution
        "- [ ] Resolved: TDD approach adopted vs rejected for backend (to confirm)",
        "- [!] Resolved: TDD approach adopted vs rejected for backend",
        # non datée : ne peut pas couvrir des entrées datées
        "- Resolved: TDD approach adopted for backend; the rejected entry is superseded",
    ],
)
def test_memory_lint_une_fausse_resolution_ne_masque_pas_la_contradiction(tmp_path: Path, ligne: str) -> None:
    assert _lint_avec_journal(tmp_path, ligne) == 1


def test_memory_lint_sans_journal_sort_en_1(tmp_path: Path) -> None:
    assert _lint_avec_journal(tmp_path, None) == 1


@pytest.mark.parametrize(
    "ligne",
    [
        "- [2024-01-03] Resolved: TDD approach adopted for backend; the rejected entry is superseded",
        "- [2024-01-02] RESOLVED : TDD approach adopted for backend; the rejected entry is superseded",
        "- [2024-01-03 10:00] Resolved: TDD approach adopted for backend; the rejected entry is superseded",
    ],
)
def test_memory_lint_une_vraie_resolution_ancree_et_datee_masque_la_contradiction(tmp_path: Path, ligne: str) -> None:
    assert _lint_avec_journal(tmp_path, ligne) == 0


def test_memory_lint_une_resolution_non_datee_ne_masque_pas_la_reapparition(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    mem = tmp_path / "_grimoire" / "_memory"
    (mem / "decisions-log.md").write_text(
        "# Dec\n- [2024-01-02] TDD approach rejected and abandoned for backend\n"
        "- [2025-06-01] TDD approach rejected again for backend\n",
        encoding="utf-8",
    )
    (mem / "contradiction-log.md").write_text(
        "# Contradictions\n- Resolved: TDD approach adopted for backend; the rejected entry is superseded\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 1, res.output


def test_memory_lint_une_resolution_datee_ne_masque_pas_une_reapparition_posterieure(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    mem = tmp_path / "_grimoire" / "_memory"
    (mem / "decisions-log.md").write_text(
        "# Dec\n- [2025-06-01] TDD approach rejected again for backend\n", encoding="utf-8"
    )
    (mem / "contradiction-log.md").write_text(
        "# Contradictions\n- [2024-01-03] Resolved: TDD approach adopted for backend; the rejected entry is superseded\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 1, res.output


_GABARIT = Path(__file__).resolve().parents[2] / "framework" / "memory" / "contradiction-log.tpl.md"


def test_le_gabarit_du_journal_documente_le_format_reconnu_par_memory_lint(tmp_path: Path) -> None:
    gabarit = _GABARIT.read_text(encoding="utf-8")
    exemples = [ligne for ligne in gabarit.splitlines() if ligne.startswith("- [YYYY-MM-DD] Resolved:")]
    assert exemples, "le gabarit ne documente pas le format `- [date] Resolved:` lu par memory lint"
    # La ligne documentée, une fois la date posée, résout bel et bien la contradiction.
    ligne = exemples[0].replace("YYYY-MM-DD", "2024-01-03").split("<")[0].rstrip()
    assert _lint_avec_journal(tmp_path, f"{ligne} TDD approach adopted for backend; rejected entry superseded") == 0


def test_le_gabarit_copie_tel_quel_ne_masque_aucune_contradiction(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    log = tmp_path / "_grimoire" / "_memory" / "contradiction-log.md"
    log.write_text(_GABARIT.read_text(encoding="utf-8"), encoding="utf-8")
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    assert res.exit_code == 1, res.output


def test_la_suggestion_de_correction_donne_le_format_attendu(tmp_path: Path) -> None:
    _memoire(tmp_path, contradiction=True)
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    suggestion = json.loads(res.output)["issues"][0]["fix_suggestion"]
    assert "[YYYY-MM-DD] Resolved:" in suggestion


# ── Revue 4 : la résolution désigne la paire, sa date vient du préfixe ───────


def _lint_sujet(tmp_path: Path, learning: str, decision: str, journal: str) -> int:
    """Lint d'une mémoire dont le contenu est donné tel quel (entrées + journal)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project-context.yaml").write_text("project:\n  name: t\n", encoding="utf-8")
    mem = tmp_path / "_grimoire" / "_memory"
    (mem / "agent-learnings").mkdir(parents=True)
    (mem / "agent-learnings" / "dev.md").write_text(f"# Dev\n{learning}\n", encoding="utf-8")
    (mem / "decisions-log.md").write_text(f"# Dec\n{decision}\n", encoding="utf-8")
    (mem / "contradiction-log.md").write_text(f"# Contradictions\n{journal}\n", encoding="utf-8")
    return runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)]).exit_code


_FRONT_POS = "- [2024-01-01] TDD approach adopted and validated for frontend"
_FRONT_NEG = "- [2024-01-02] TDD approach rejected and abandoned for frontend"


def test_une_resolution_sur_un_autre_sujet_ne_masque_pas_la_contradiction(tmp_path: Path) -> None:
    journal = "- [2024-01-03] Resolved: TDD approach adopted for backend; the rejected entry is superseded"
    assert _lint_sujet(tmp_path, _FRONT_POS, _FRONT_NEG, journal) == 1


def test_une_resolution_sur_le_bon_sujet_masque_la_contradiction(tmp_path: Path) -> None:
    journal = "- [2024-01-03] Resolved: TDD approach adopted for frontend; the rejected entry is superseded"
    assert _lint_sujet(tmp_path, _FRONT_POS, _FRONT_NEG, journal) == 0


def test_la_suggestion_nomme_les_termes_a_reprendre(tmp_path: Path) -> None:
    _lint_sujet(tmp_path, _FRONT_POS, _FRONT_NEG, "")
    res = runner.invoke(app, ["memory", "lint", "--json", "--project-root", str(tmp_path)])
    suggestion = json.loads(res.output)["issues"][0]["fix_suggestion"]
    assert "frontend" in suggestion


def test_une_entree_non_datee_n_est_pas_couverte_par_une_resolution_datee(tmp_path: Path) -> None:
    pos = "- TDD approach adopted and validated for frontend"
    assert _lint_sujet(tmp_path / "a", pos, _FRONT_NEG, "- [2024-01-03] Resolved: TDD approach adopted for frontend") == 1
    assert _lint_sujet(tmp_path / "b", pos, _FRONT_NEG, "- [2099-01-01] Resolved: TDD approach adopted for frontend") == 1


def test_entrees_non_datees_et_resolution_non_datee_restent_couvertes(tmp_path: Path) -> None:
    pos = "- TDD approach adopted and validated for frontend"
    neg = "- TDD approach rejected and abandoned for frontend"
    assert _lint_sujet(tmp_path, pos, neg, "- Resolved: TDD approach adopted for frontend") == 0


def test_une_date_en_fin_de_ligne_n_est_pas_la_date_de_la_resolution(tmp_path: Path) -> None:
    journal = "- Resolved: TDD approach adopted for frontend (ticket [2099-01-01])"
    assert _lint_sujet(tmp_path, _FRONT_POS, _FRONT_NEG, journal) == 1


def test_une_resolution_datee_dans_le_futur_ne_masque_rien(tmp_path: Path) -> None:
    journal = "- [2099-01-01] Resolved: TDD approach adopted for frontend"
    assert _lint_sujet(tmp_path, _FRONT_POS, _FRONT_NEG, journal) == 1


# ── Revue 4 : le niveau de risque de l'enveloppe suit RiskProfile ────────────


def test_chaque_profil_de_risque_a_un_niveau_dans_l_enveloppe() -> None:
    from grimoire.core.standard_task_scaffold import _RISK_LEVEL_BY_PROFILE
    from grimoire.missions.schemas import RiskProfile

    assert {p.value for p in RiskProfile} == {str(k) for k in _RISK_LEVEL_BY_PROFILE}
    assert _RISK_LEVEL_BY_PROFILE[RiskProfile.SECURITY_CRITICAL] == "critical"


def test_l_enveloppe_d_une_tache_security_critical_porte_le_risque_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.core.standard_state import LEDGER_RELPATH
    from grimoire.core.standard_task_scaffold import _task_facts
    from grimoire.missions.ledger import MissionLedger

    root = tmp_path / "p"
    root.mkdir()
    assert runner.invoke(app, ["init", "-y", str(root)]).exit_code == 0
    monkeypatch.chdir(root)
    res = runner.invoke(app, ["task", "add", "Rotate the GitHub token", "-a", "fait"])
    assert res.exit_code == 0, res.output
    tache = MissionLedger(root / LEDGER_RELPATH).list_tasks()[0]
    assert tache.risk_profile.value == "security_critical"
    assert _task_facts(root, tache.id).risk_level == "critical"
