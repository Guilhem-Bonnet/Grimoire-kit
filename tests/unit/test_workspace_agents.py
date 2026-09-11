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
    """Un projet réel de l'archétype ``meta`` — 3 agents à faisceau distinct livrés par le kit (issue #375).

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


# ── Fraîcheur (issue #396) ───────────────────────────────────────────────────
#
# Distincte de ``usage`` ci-dessus : ``usage`` compte tout sous-agent tracé
# (``SubagentStop``, tag libre), ``freshness`` ne lit que le tag
# ``agent.dispatch`` — le même filtre que ``grimoire doctor`` et
# ``registry dispatches``. Vérifié empiriquement dans
# ``grimoire.hosts.runtime._record_decision`` : les deux tags ne sont pas
# écrits par le même événement, donc un agent peut être « utilisé »
# (sous-agent tracé) sans jamais avoir été « choisi » (``agent.dispatch``).


def test_sans_historique_la_fraicheur_n_est_pas_jugee(agents_project: Path) -> None:
    """Journal absent ou trop jeune : aucun agent n'est marqué périmé.

    ``too_recent`` vaut ``True`` ici aussi : ``concierge.md`` vient d'être
    écrit par ``grimoire init`` (fixture de module), donc plus jeune que le
    seuil par défaut — mais ``judged`` étant déjà faux (aucun journal), ce
    plancher par agent ne change rien au résultat, seulement au diagnostic.
    """
    payload = wa.agents_view(agents_project)
    assert payload["freshness_judged"] is False
    assert payload["freshness_threshold_days"] == 90
    concierge = next(a for a in payload["agents"] if a["name"] == "concierge")
    assert concierge["freshness"] == {
        "last_seen": None,
        "days_since": None,
        "stale": False,
        "too_recent": True,
        "judged": False,
    }


def test_un_agent_trop_recent_n_est_pas_marque_perime(agents_project: Path) -> None:
    """Plancher par agent : un agent plus jeune que le seuil n'est jamais périmé.

    Critère d'arrêt du suivi de l'issue #396 : journal de 100 jours, seuil
    90, agent sans ``agent.dispatch`` dont le fichier date d'hier → non
    périmé, marqué ``too_recent`` ; le même agent avec un fichier de 100
    jours → périmé (couvert par
    ``test_un_agent_perime_porte_le_badge_stale`` ci-dessus).
    """
    import os
    from datetime import UTC, datetime, timedelta

    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import AGENT_DISPATCH_TAG, TraceLedger, TraceOutcome

    root = agents_project.parent / "projet-fraicheur-recent"
    _init(root)

    # Le journal a 100 jours d'historique (seuil 90 : jugé), mais le fichier
    # de `security-auditor` ne date que d'hier — jamais choisi, mais pas
    # encore eu le temps de l'être.
    yesterday = (datetime.now(UTC) - timedelta(days=1)).timestamp()
    security_auditor_path = root / "_grimoire" / "kit" / "agents" / "security-auditor.md"
    os.utime(security_auditor_path, (yesterday, yesterday))

    ledger = TraceLedger(root / TRACES_DIR)
    ledger.record(
        run_id="RUN-recent",
        workflow_instance_id="",
        mission_id="",
        task_id="",
        recipe_id="grimoire.entry-persona",
        outcome=TraceOutcome.SUCCESS,
        started_at=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
        agent_id="concierge",
        tags=[AGENT_DISPATCH_TAG],
    )

    payload = wa.agents_view(root)
    assert payload["freshness_judged"] is True
    security_auditor = next(a for a in payload["agents"] if a["name"] == "security-auditor")
    assert security_auditor["freshness"]["too_recent"] is True
    assert security_auditor["freshness"]["stale"] is False


def test_un_agent_perime_porte_le_badge_stale(agents_project: Path) -> None:
    """Un ``agent.dispatch`` vieux de 100 jours dépasse le seuil par défaut (90).

    Le fichier de l'agent est lui aussi vieilli à 100 jours : sans ça, le
    plancher par agent (le fichier vient d'être créé par ``grimoire init``,
    quelques secondes plus tôt) le marquerait ``too_recent`` plutôt que
    ``stale`` — exactement le comportement que
    ``test_un_agent_trop_recent_n_est_pas_marque_perime`` vérifie séparément.
    """
    import os
    from datetime import UTC, datetime, timedelta

    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import AGENT_DISPATCH_TAG, TraceLedger, TraceOutcome

    root = agents_project.parent / "projet-fraicheur"
    _init(root)

    old_time = (datetime.now(UTC) - timedelta(days=100)).timestamp()
    for agent_file in ("concierge.md", "security-auditor.md"):
        path = root / "_grimoire" / "kit" / "agents" / agent_file
        if path.is_file():
            os.utime(path, (old_time, old_time))

    ledger = TraceLedger(root / TRACES_DIR)
    ledger.record(
        run_id="RUN-fraicheur",
        workflow_instance_id="",
        mission_id="",
        task_id="",
        recipe_id="grimoire.entry-persona",
        outcome=TraceOutcome.SUCCESS,
        started_at=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
        agent_id="concierge",
        tags=[AGENT_DISPATCH_TAG],
    )

    payload = wa.agents_view(root)
    assert payload["freshness_judged"] is True
    concierge = next(a for a in payload["agents"] if a["name"] == "concierge")
    assert concierge["freshness"]["too_recent"] is False
    assert concierge["freshness"]["stale"] is True
    assert concierge["freshness"]["days_since"] == 100

    security_auditor = next((a for a in payload["agents"] if a["name"] == "security-auditor"), None)
    if security_auditor is not None:
        assert security_auditor["freshness"]["last_seen"] is None
        assert security_auditor["freshness"]["stale"] is True


# ── Écriture : assigner / retirer un skill ──────────────────────────────────


def test_assigner_un_skill_cree_un_override_que_collect_voit(agents_project: Path) -> None:
    """Le critère d'arrêt de l'issue, à la brique : le fichier créé dans
    ``overrides`` doit être ce que ``collect_agents``/``grimoire host status``
    lisent — pas un artefact parallèle que rien d'autre ne regarde."""
    override = agents_project / "_grimoire/overrides/agents/security-auditor.md"
    assert not override.is_file()

    result = workspace_post(
        agents_project, "/api/workspace/agents/security-auditor/skill",
        {"skill": "grimoire-agent-dispatch", "action": "assign"},
    )

    assert override.is_file()
    agent = next(a for a in result["agents"] if a["name"] == "security-auditor")
    assert agent["layer"] == "overrides"
    assert agent["skills"] == ["grimoire-agent-dispatch"]

    # Ce que voit une lecture indépendante — celle que `grimoire host status`
    # emprunte via `build_surface` — pas seulement la réponse de la route.
    reread = wa.agents_view(agents_project)
    assert "grimoire-agent-dispatch" in next(
        a for a in reread["agents"] if a["name"] == "security-auditor"
    )["skills"]


def test_retirer_un_skill_fait_disparaitre_la_declaration(agents_project: Path) -> None:
    # security-auditor : mutations en séquence avec le test précédent, seul
    # agent du trio meta sans skill baked-in — les deux tests laissent l'agent
    # dans un état final compatible (assign puis remove ramène à []).
    workspace_post(
        agents_project, "/api/workspace/agents/security-auditor/skill",
        {"skill": "grimoire-agent-dispatch", "action": "assign"},
    )

    result = workspace_post(
        agents_project, "/api/workspace/agents/security-auditor/skill",
        {"skill": "grimoire-agent-dispatch", "action": "remove"},
    )

    agent = next(a for a in result["agents"] if a["name"] == "security-auditor")
    assert agent["skills"] == []
    override = agents_project / "_grimoire/overrides/agents/security-auditor.md"
    assert "skills:" not in override.read_text(encoding="utf-8")


def test_un_skill_inconnu_est_refuse_avec_le_message_de_collect(agents_project: Path) -> None:
    override = agents_project / "_grimoire/overrides/agents/concierge.md"

    with pytest.raises(ValueError, match="skills introuvables"):
        workspace_post(
            agents_project, "/api/workspace/agents/concierge/skill",
            {"skill": "un-skill-qui-n-existe-pas", "action": "assign"},
        )

    # Refus atomique : pas d'override laissé derrière une écriture avortée.
    assert not override.is_file()


def test_assigner_deux_fois_le_meme_skill_est_idempotent(agents_project: Path) -> None:
    # security-auditor termine le test précédent avec skills == [] : rejouer
    # deux fois le même assign dessus reste vérifiable exactement.
    for _ in range(2):
        result = workspace_post(
            agents_project, "/api/workspace/agents/security-auditor/skill",
            {"skill": "grimoire-agent-dispatch", "action": "assign"},
        )
    agent = next(a for a in result["agents"] if a["name"] == "security-auditor")
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
        agents_project, "/api/workspace/agents/concierge/fields",
        {
            "use_when": "Situation de test propre à ce projet.",
            "dont_use_when": "Jamais en dehors de ce test.",
            "tools": ["read", "execute"],
        },
    )
    agent = next(a for a in result["agents"] if a["name"] == "concierge")
    assert agent["use_when"] == "Situation de test propre à ce projet."
    assert agent["dont_use_when"] == "Jamais en dehors de ce test."
    assert set(agent["tools"]) == {"read", "execute"}
    assert agent["layer"] == "overrides"


def test_un_gabarit_non_rendu_n_est_pas_un_agent_du_cockpit(agents_project: Path) -> None:
    """``custom-agent.md`` (issue #381) : un gabarit non rempli n'est pas un
    agent installé, ni pour ``layout.installed_agents`` ni pour
    ``collect_agents`` — le cockpit ne doit donc jamais l'offrir à l'édition."""
    names = {a["name"] for a in wa.agents_view(agents_project)["agents"]}
    assert "custom-agent" not in names

    with pytest.raises(FileNotFoundError):
        workspace_post(
            agents_project, "/api/workspace/agents/custom-agent/fields",
            {"use_when": "Ne devrait jamais s'écrire."},
        )


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
