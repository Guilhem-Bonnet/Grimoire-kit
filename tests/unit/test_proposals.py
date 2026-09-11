"""Le déclencheur de propositions d'artefact (issue #395).

Critère d'arrêt de l'issue, décliné en tests unitaires : deux non-choix sur
la même spécialité produisent une proposition, un seul n'en produit pas ;
`accept` écrit un agent réel dans `overrides`, valide pour la garde de
distinction et visible dans la carte des agents installés ; `reject` retire
la proposition et un troisième non-choix ne la refait pas apparaître (il
faut que le compte double depuis le refus) ; un journal absent ne produit ni
proposition ni erreur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.hosts.decisions import record_agent_miss
from grimoire.proposals import (
    accept_proposal,
    count_pending,
    list_proposals,
    reject_proposal,
    sync_proposals,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal Grimoire project — same shape as ``tests/unit/mcp/test_server.py``."""
    (tmp_path / "project-context.yaml").write_text(
        "project:\n"
        "  name: test-proposals\n"
        "  type: webapp\n"
        "user:\n"
        "  name: Guilhem\n"
        "  language: Français\n"
        "  skill_level: expert\n"
        "agents:\n"
        "  archetype: minimal\n",
        encoding="utf-8",
    )
    return tmp_path


def _miss(project: Path, *, specialty: str, category: str = "infra", fallback: str = "") -> None:
    record_agent_miss(project, category=category, specialty=specialty, fallback_agent=fallback)


# ── Le seuil — jamais au premier non-choix ──────────────────────────────────


def test_a_single_miss_produces_no_proposal(project: Path) -> None:
    _miss(project, specialty="terraform")
    assert list_proposals(project) == []


def test_two_misses_on_the_same_specialty_produce_one_proposal(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposals = list_proposals(project)
    assert len(proposals) == 1
    assert proposals[0].specialty == "terraform"
    assert proposals[0].status == "pending"
    assert proposals[0].count == 2


def test_an_unnameable_specialty_never_becomes_a_proposal(project: Path) -> None:
    _miss(project, specialty="", category="design")
    _miss(project, specialty="", category="design")
    assert list_proposals(project) == []


def test_the_threshold_is_configurable_but_never_one(project: Path) -> None:
    (project / "project-context.yaml").write_text(
        (project / "project-context.yaml").read_text(encoding="utf-8") + "proposals:\n  threshold: 1\n",
        encoding="utf-8",
    )
    _miss(project, specialty="terraform")
    # threshold: 1 est clampé à 2 — jamais de proposition sur un seul non-choix.
    assert list_proposals(project) == []


def test_a_higher_configured_threshold_delays_the_proposal(project: Path) -> None:
    (project / "project-context.yaml").write_text(
        (project / "project-context.yaml").read_text(encoding="utf-8") + "proposals:\n  threshold: 3\n",
        encoding="utf-8",
    )
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    assert list_proposals(project) == []
    _miss(project, specialty="terraform")
    assert len(list_proposals(project)) == 1


# ── Type d'artefact — doctrine skill/agent ──────────────────────────────────


def test_no_fallback_agent_ever_observed_proposes_an_agent(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]
    assert proposal.artifact_type == "agent"
    assert proposal.slug == "terraform-specialist"


def test_a_fallback_agent_observed_proposes_an_attachable_skill(project: Path) -> None:
    _miss(project, specialty="chaos-engineering", fallback="generic-dev")
    _miss(project, specialty="chaos-engineering", fallback="generic-dev")
    proposal = list_proposals(project)[0]
    assert proposal.artifact_type == "skill"
    assert proposal.target_agent == "generic-dev"


def _write_installed_agent(project: Path, name: str) -> None:
    dest = project / "_grimoire" / "overrides" / "agents" / f"{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f'---\nname: "{name}"\ndescription: "Agent générique de test"\n---\n\nCorps.\n',
        encoding="utf-8",
    )


def test_accepting_a_skill_proposal_writes_it_and_attaches_it_to_the_fallback(project: Path) -> None:
    _write_installed_agent(project, "generic-dev")
    _miss(project, specialty="chaos-engineering", fallback="generic-dev")
    _miss(project, specialty="chaos-engineering", fallback="generic-dev")
    proposal = list_proposals(project)[0]

    result = accept_proposal(project, proposal.slug)
    assert result["ok"] is True

    skill_path = project / "_grimoire" / "overrides" / "skills" / "chaos-engineering.md"
    assert skill_path.is_file()

    agent_path = project / "_grimoire" / "overrides" / "agents" / "generic-dev.md"
    assert "skills:" in agent_path.read_text(encoding="utf-8")
    assert "chaos-engineering" in agent_path.read_text(encoding="utf-8")


def test_accepting_a_skill_proposal_without_the_fallback_agent_installed_is_refused(project: Path) -> None:
    _miss(project, specialty="chaos-engineering", fallback="ghost-agent")
    _miss(project, specialty="chaos-engineering", fallback="ghost-agent")
    proposal = list_proposals(project)[0]

    result = accept_proposal(project, proposal.slug)
    assert result["ok"] is False
    skill_path = project / "_grimoire" / "overrides" / "skills" / "chaos-engineering.md"
    assert not skill_path.is_file(), "un échec d'attachement ne doit pas laisser un fichier orphelin"


# ── Accepter ─────────────────────────────────────────────────────────────────


def test_accepting_an_agent_proposal_writes_a_real_override_file(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]

    result = accept_proposal(project, proposal.slug)
    assert result["ok"] is True

    dest = project / "_grimoire" / "overrides" / "agents" / "terraform-specialist.md"
    assert dest.is_file()
    content = dest.read_text(encoding="utf-8")
    assert 'name: "terraform-specialist"' in content
    assert "terraform" in content  # use_when/dont_use_when mentionnent la spécialité

    from grimoire.core.integrity import installed_agent_tags

    assert "terraform-specialist" in installed_agent_tags(project)

    # La proposition ne réapparaît plus comme « en attente ».
    refreshed = list_proposals(project)
    assert next(p for p in refreshed if p.slug == proposal.slug).status == "accepted"
    assert count_pending(project) == 0


def test_accepting_twice_is_refused_the_second_time(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]
    accept_proposal(project, proposal.slug)
    second = accept_proposal(project, proposal.slug)
    assert second["ok"] is False


def test_accepting_an_unknown_slug_is_an_honest_refusal(project: Path) -> None:
    result = accept_proposal(project, "does-not-exist")
    assert result["ok"] is False
    assert "introuvable" in result["error"]


# ── Refuser ──────────────────────────────────────────────────────────────────


def test_rejecting_removes_it_from_the_pending_count(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]

    result = reject_proposal(project, proposal.slug)
    assert result["ok"] is True
    assert count_pending(project) == 0


def test_a_third_miss_right_after_a_reject_does_not_resurrect_it(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]
    reject_proposal(project, proposal.slug)

    _miss(project, specialty="terraform")  # troisième non-choix
    proposals = sync_proposals(project)
    assert len(proposals) == 1
    assert proposals[0].status == "rejected"
    assert count_pending(project) == 0


def test_doubling_the_count_since_the_reject_brings_it_back(project: Path) -> None:
    _miss(project, specialty="terraform")
    _miss(project, specialty="terraform")
    proposal = list_proposals(project)[0]
    reject_proposal(project, proposal.slug)  # rejeté au compte 2 — il faut 4 pour réapparaître

    _miss(project, specialty="terraform")  # 3
    _miss(project, specialty="terraform")  # 4
    proposals = sync_proposals(project)
    assert len(proposals) == 1
    assert proposals[0].status == "pending"
    assert count_pending(project) == 1


# ── Absence de journal ───────────────────────────────────────────────────────


def test_an_absent_ledger_produces_neither_proposal_nor_error(project: Path) -> None:
    assert list_proposals(project) == []
    assert count_pending(project) == 0


def test_an_unwritable_ledger_never_breaks_the_read(project: Path) -> None:
    traces_dir = project / TRACES_DIR
    traces_dir.parent.mkdir(parents=True, exist_ok=True)
    traces_dir.write_text("pas un dossier", encoding="utf-8")
    assert sync_proposals(project) == []  # ne lève pas
