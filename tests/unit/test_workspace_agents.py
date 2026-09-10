"""Le cockpit voit et configure les agents du projet (issue #374).

Trois défauts que ces tests empêchent :

- une lecture qui n'expose que ce que l'IR de host consomme (outils, contexte,
  skills) et jamais la clause d'emploi qu'un opérateur lit avant de dispatcher
  — la moitié de ce que le cockpit doit montrer serait alors invisible ;
- une écriture qui touche le kit au lieu de sa copie ``overrides`` — un
  projet personnalise, il ne modifie jamais ce que le kit livre
  (``docs/artifact-doctrine.md``) ;
- un skill ou un contexte inconnu accepté silencieusement plutôt que refusé
  avec le message que ``collect_agents`` produit déjà.

Le projet de test est réellement initialisé (``grimoire init``, archétype
``meta``) : un projet fabriqué à la main n'aurait ni frontmatter réel, ni
faisceau d'agents comparable, donc ne prouverait rien de ce que
``collect_agents`` valide déjà.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.tools import workspace_api as wa
from grimoire.tools.workspace_routes import workspace_post


def _init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-agents"],
        cwd=str(root),
        check=False,
        capture_output=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def agents_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Un projet réel de l'archétype ``meta`` — huit agents livrés par le kit.

    Portée module : les tests mutateurs ciblent chacun un agent distinct
    (voir le commentaire sur ``AGENT_OPTIMIZER`` etc. plus bas), donc aucun ne
    piétine l'override qu'un autre vient d'écrire — même précédent que
    ``test_prendre_un_override_rend_le_fichier_comparable_puis_editable``
    dans ``test_workspace_api.py`` contre un projet partagé.
    """
    root = tmp_path_factory.mktemp("agents") / "projet-agents"
    _init(root)
    if not (root / "_grimoire" / "kit" / "agents").is_dir():
        pytest.skip("`grimoire init` indisponible ici")
    return root


# ── Lecture ──────────────────────────────────────────────────────────────────


def test_la_vue_expose_la_clause_d_emploi_et_la_couche(agents_project: Path) -> None:
    payload = wa.agents_view(agents_project)

    assert payload["agents"], "l'archétype meta livre des agents"
    assert {s["slug"] for s in payload["skills"]} >= {"grimoire-agent-dispatch"}
    concierge = next(a for a in payload["agents"] if a["name"] == "concierge")
    assert concierge["layer"] == "kit"
    assert concierge["use_when"], "le kit déclare use_when pour chaque agent livré"
    assert concierge["dont_use_when"]
    assert concierge["tool_boundary"]
    assert concierge["skills"] == ["grimoire-agent-dispatch"]
    assert concierge["usage"] == {"choices": 0, "last_chosen_at": None}
    assert payload["entry_point"] == "concierge"


def test_l_usage_reel_vient_du_ledger_de_traces(agents_project: Path) -> None:
    """Compte de choix et dernier choix : agrégés depuis le ledger, pas
    inventés — voir ``grimoire.hosts.runtime._record_decision`` (#374)."""
    from grimoire.bridges.schemas import HostId
    from grimoire.core.agentic_standard import setup_standard_profile
    from grimoire.hosts.runtime import run_hook

    root = agents_project.parent / "projet-usage"
    _init(root)
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    board = root / "_grimoire/standard/task-board.yaml"
    board.write_text(
        board.read_text(encoding="utf-8").replace('status: "proposed"', 'status: "in_progress"'),
        encoding="utf-8",
    )
    run_hook(
        {"hook_event_name": "SubagentStop", "cwd": str(root), "agent_name": "concierge"},
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    run_hook(
        {"hook_event_name": "SubagentStop", "cwd": str(root), "agent_name": "concierge"},
        host_id=HostId.CLAUDE_CODE_CLI,
    )

    payload = wa.agents_view(root)
    concierge = next(a for a in payload["agents"] if a["name"] == "concierge")
    assert concierge["usage"]["choices"] == 2
    assert concierge["usage"]["last_chosen_at"]


# ── Écriture : assigner / retirer un skill ──────────────────────────────────


def test_assigner_un_skill_cree_un_override_que_collect_voit(agents_project: Path) -> None:
    """Le critère d'arrêt de l'issue, à la brique : le fichier créé dans
    ``overrides`` doit être ce que ``collect_agents``/``grimoire host status``
    lisent — pas un artefact parallèle que rien d'autre ne regarde."""
    override = agents_project / "_grimoire/overrides/agents/art-director.md"
    assert not override.is_file()

    result = workspace_post(
        agents_project, "/api/workspace/agents/art-director/skill",
        {"skill": "grimoire-agent-dispatch", "action": "assign"},
    )

    assert override.is_file()
    agent = next(a for a in result["agents"] if a["name"] == "art-director")
    assert agent["layer"] == "overrides"
    assert agent["skills"] == ["grimoire-agent-dispatch"]

    # Ce que voit une lecture indépendante — celle que `grimoire host status`
    # emprunte via `build_surface` — pas seulement la réponse de la route.
    reread = wa.agents_view(agents_project)
    assert "grimoire-agent-dispatch" in next(
        a for a in reread["agents"] if a["name"] == "art-director"
    )["skills"]


def test_retirer_un_skill_fait_disparaitre_la_declaration(agents_project: Path) -> None:
    workspace_post(
        agents_project, "/api/workspace/agents/creative-toolsmith/skill",
        {"skill": "grimoire-agent-dispatch", "action": "assign"},
    )

    result = workspace_post(
        agents_project, "/api/workspace/agents/creative-toolsmith/skill",
        {"skill": "grimoire-agent-dispatch", "action": "remove"},
    )

    agent = next(a for a in result["agents"] if a["name"] == "creative-toolsmith")
    assert agent["skills"] == []
    override = agents_project / "_grimoire/overrides/agents/creative-toolsmith.md"
    assert "skills:" not in override.read_text(encoding="utf-8")


def test_un_skill_inconnu_est_refuse_avec_le_message_de_collect(agents_project: Path) -> None:
    override = agents_project / "_grimoire/overrides/agents/memory-keeper.md"

    with pytest.raises(ValueError, match="skills introuvables"):
        workspace_post(
            agents_project, "/api/workspace/agents/memory-keeper/skill",
            {"skill": "un-skill-qui-n-existe-pas", "action": "assign"},
        )

    # Refus atomique : pas d'override laissé derrière une écriture avortée.
    assert not override.is_file()


def test_assigner_deux_fois_le_meme_skill_est_idempotent(agents_project: Path) -> None:
    for _ in range(2):
        result = workspace_post(
            agents_project, "/api/workspace/agents/project-navigator/skill",
            {"skill": "grimoire-agent-dispatch", "action": "assign"},
        )
    agent = next(a for a in result["agents"] if a["name"] == "project-navigator")
    assert agent["skills"] == ["grimoire-agent-dispatch"]


def test_action_inconnue_est_un_refus_explicite(agents_project: Path) -> None:
    with pytest.raises(ValueError, match="action"):
        workspace_post(
            agents_project, "/api/workspace/agents/security-auditor/skill",
            {"skill": "grimoire-agent-dispatch", "action": "renommer"},
        )


def test_un_agent_inconnu_est_un_404_et_non_une_exception_opaque(agents_project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        workspace_post(
            agents_project, "/api/workspace/agents/n-existe-pas/skill",
            {"skill": "grimoire-agent-dispatch", "action": "assign"},
        )


# ── Écriture : champs de configuration ──────────────────────────────────────


def test_modifier_la_clause_d_emploi_et_les_outils(agents_project: Path) -> None:
    result = workspace_post(
        agents_project, "/api/workspace/agents/custom-agent/fields",
        {
            "use_when": "Situation de test propre à ce projet.",
            "dont_use_when": "Jamais en dehors de ce test.",
            "tools": ["read", "execute"],
        },
    )
    agent = next(a for a in result["agents"] if a["name"] == "custom-agent")
    assert agent["use_when"] == "Situation de test propre à ce projet."
    assert agent["dont_use_when"] == "Jamais en dehors de ce test."
    assert set(agent["tools"]) == {"read", "execute"}
    assert agent["layer"] == "overrides"


def test_un_outil_inconnu_est_refuse_sans_toucher_le_disque(agents_project: Path) -> None:
    override = agents_project / "_grimoire/overrides/agents/agent-optimizer.md"
    assert not override.is_file()

    with pytest.raises(ValueError, match="outil"):
        workspace_post(
            agents_project, "/api/workspace/agents/agent-optimizer/fields",
            {"tools": ["voler"]},
        )

    assert not override.is_file(), "une validation de forme ne doit même pas créer l'override"


def test_un_contexte_inexistant_sur_disque_est_refuse_avec_le_message_de_collect(
    agents_project: Path,
) -> None:
    with pytest.raises(ValueError, match="contexte inexistant"):
        workspace_post(
            agents_project, "/api/workspace/agents/security-auditor/fields",
            {"context": ["chemin/qui/n/existe/pas.md"]},
        )


def test_un_contexte_existant_est_accepte_et_efface_ensuite(agents_project: Path) -> None:
    result = workspace_post(
        agents_project, "/api/workspace/agents/security-auditor/fields",
        {"context": ["project-context.yaml"]},
    )
    agent = next(a for a in result["agents"] if a["name"] == "security-auditor")
    assert agent["context"] == ["project-context.yaml"]

    cleared = workspace_post(
        agents_project, "/api/workspace/agents/security-auditor/fields",
        {"context": []},
    )
    agent = next(a for a in cleared["agents"] if a["name"] == "security-auditor")
    assert agent["context"] == []


def test_un_champ_non_modifiable_est_refuse(agents_project: Path) -> None:
    with pytest.raises(ValueError, match="non modifiable"):
        workspace_post(
            agents_project, "/api/workspace/agents/concierge/fields",
            {"name": "autre-nom"},
        )


def test_un_corps_vide_est_refuse(agents_project: Path) -> None:
    with pytest.raises(ValueError, match="aucun champ"):
        workspace_post(agents_project, "/api/workspace/agents/concierge/fields", {})


def test_editer_un_agent_deja_en_override_ne_le_duplique_pas(agents_project: Path) -> None:
    """Un agent déjà personnalisé garde un seul fichier — la seconde écriture
    modifie l'override existant, elle n'en superpose pas un second."""
    workspace_post(
        agents_project, "/api/workspace/agents/concierge/fields",
        {"use_when": "Première modification."},
    )
    workspace_post(
        agents_project, "/api/workspace/agents/concierge/fields",
        {"use_when": "Seconde modification."},
    )
    payload = wa.agents_view(agents_project)
    matches = [a for a in payload["agents"] if a["name"] == "concierge"]
    assert len(matches) == 1
    assert matches[0]["use_when"] == "Seconde modification."
