"""Le concierge délègue par défaut, il ne légitime plus l'auto-exécution (GAO-d-le-concier-001).

Mesure sur 98 sessions Claude Code : le concierge n'intervient que dans 3, les
délégations qui ont lieu vont à ``general-purpose`` en sonnet dans 92 % des cas,
jamais en haiku. Cause : l'étape 8 TRIAGE de la fiche autorisait l'auto-exécution
sans condition dès qu'aucun spécialiste précis ne convenait — ce qui, en
pratique, couvre toute demande un peu ouverte. Ce test verrouille la nouvelle
règle : l'auto-exécution ne vaut que pour une demande directe et bornée (le
critère déjà posé par la compétence ``grimoire-agent-dispatch``, cité ici, pas
dupliqué), et toute délégation choisit son modèle par classe de vérifiabilité
(V0 → haiku, V1 → sonnet, V2 → le modèle de la session).

PR #656 a introduit une deuxième régression (GAO-h-concierge-001) : la même
étape ROUTE, lue telle quelle par le concierge lancé en sous-agent restreint
(``tools: "read, search"``, pas d'outil de délégation), lui demandait de
« traiter » les demandes bornées lui-même et de garder la vérification — en
contradiction avec sa propre ``tool_boundary`` (« n'exécute lui-même aucune
action métier »). Les tests ci-dessous verrouillent la distinction : boucle
principale (outil de délégation présent) agit ou délègue ; sous-agent, ou
hôte sans délégation, ne fait jamais rien hors read/search et se contente de
recommander.
"""

from __future__ import annotations

from grimoire.archetypes import bundled_path


def _concierge_source() -> str:
    path = bundled_path() / "meta" / "agents" / "concierge.md"
    return path.read_text(encoding="utf-8")


def test_self_execution_is_gated_on_a_direct_bounded_request() -> None:
    text = _concierge_source()
    # L'ancienne légitimation sans condition a disparu : ce n'est plus
    # « aucun spécialiste ne convient » qui ouvre l'auto-exécution.
    assert "no specialist fits, you do the work yourself" not in text
    assert "direct, bounded request" in text
    assert "grimoire-agent-dispatch" in text
    # La table de décision est citée, pas recopiée dans la fiche.
    assert "Demande directe et bornée" not in text


def test_delegation_picks_a_model_by_verifiability_class() -> None:
    text = _concierge_source()
    assert "V0" in text and "haiku" in text
    assert "V1" in text and "sonnet" in text
    assert "V2" in text
    # Recherche large / mesure : sous-agent économique, conclusions seulement.
    assert "conclusions" in text
    # La vérification reste chez l'orchestrateur, jamais déléguée.
    assert "Verification" in text and "orchestrator" in text


def test_route_step_distinguishes_main_loop_from_boundary_sub_agent() -> None:
    """GAO-h-concierge-001 : pas de contradiction avec ``tool_boundary``.

    Le concierge peut tourner sans outil de délégation — sous-agent isolé
    (``Agent`` tool absent) ou hôte n'offrant pas d'outil de délégation. Dans
    ce cas, sa propre frontmatter (``tools: "read, search"``) et sa
    ``tool_boundary`` (« n'exécute lui-même aucune action métier ») interdisent
    d'agir : la fiche doit dire de trier puis de recommander, jamais de
    « traiter » la demande.
    """
    text = _concierge_source()
    assert "delegation tool" in text
    assert "read, search" in text
    assert "recommend the persona and model" in text
    assert "switch" in text
    # Le repli existant (« dis à l'utilisateur vers quel agent basculer »,
    # retiré par #656) doit être réintroduit sous une forme équivalente.
    assert "tell the user which agent to switch to" in text


def test_boundary_case_never_acts_outside_read_search() -> None:
    text = _concierge_source()
    assert "never act outside read/search" in text
    # Le garde-fou « je n'ai pas les droits » couvre les deux cas, sans
    # jamais légitimer une exécution directe côté sous-agent restreint.
    assert "je n'ai pas les droits" in text
