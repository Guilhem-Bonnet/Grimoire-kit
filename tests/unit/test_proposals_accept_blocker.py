"""`accept_blocker` : le refus qu'« Accepter » rencontrera à coup sûr.

Défaut corrigé (cockpit, Flotte, section Propositions) : une proposition
``memory-link`` sans porteur offrait « Accepter », que le serveur refusait
toujours, et ce refus partait dans la Console que la coque ne montre plus —
« Accepter ne fait rien ». Le serveur dit désormais, sur la proposition
seule, quand l'acceptation échouera toujours ; ``accept_proposal`` renvoie
exactement ce texte, et ``proposals_view`` le joint au rendu de la fiche.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.proposals import Proposal, accept_blocker, accept_proposal, create_manual_proposal


def _proposal(**fields: object) -> Proposal:
    base: dict[str, object] = {"slug": "p", "specialty": "s", "artifact_type": "agent", "status": "pending", "count": 1}
    base.update(fields)
    return Proposal(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"artifact_type": "memory-link", "artifact_ref": "_grimoire/_memory/x.md"}, "aucun porteur plausible"),
        ({"artifact_type": "memory-link", "target_agent": "dev"}, "artifact_ref vide"),
        ({"artifact_type": "skill"}, "aucun agent de repli"),
        ({"artifact_type": "repair", "carrier_reason": "substitution évidente : a -> b"}, "artifact_ref vide"),
        ({"artifact_type": "repair", "artifact_ref": "f.md:1 → a", "carrier_reason": "revue"}, "revue humaine requise"),
        (
            {"artifact_type": "override-migration", "target_agent": "dev", "carrier_reason": "revue nécessaire"},
            "grimoire agent override convert dev --dry-run",
        ),
        ({"artifact_type": "needs-hosts", "slug": "needs-declare-commands", "artifact_ref": "test"}, "à la main"),
        ({"artifact_type": "needs-hosts", "slug": "autre-chose"}, "inconnu"),
    ],
)
def test_accept_blocker_nomme_le_refus_certain(fields: dict[str, object], expected: str) -> None:
    assert expected in accept_blocker(_proposal(**fields))


@pytest.mark.parametrize(
    "fields",
    [
        {"artifact_type": "agent"},
        {"artifact_type": "memory-link", "target_agent": "dev", "artifact_ref": "_grimoire/_memory/x.md"},
        {"artifact_type": "skill", "target_agent": "dev"},
        {"artifact_type": "repair", "artifact_ref": "f.md:1 → a", "carrier_reason": "substitution évidente : a -> b"},
        {
            "artifact_type": "override-migration",
            "target_agent": "dev",
            "carrier_reason": "conversion sûre : corps identique",
        },
        {"artifact_type": "needs-hosts", "slug": "hosts-declare-enabled", "artifact_ref": "claude"},
    ],
)
def test_accept_blocker_ne_predit_rien_quand_l_acceptation_peut_aboutir(fields: dict[str, object]) -> None:
    assert accept_blocker(_proposal(**fields)) == ""


def test_memory_link_sans_porteur_dit_quoi_raccorder_sans_parler_d_override() -> None:
    message = accept_blocker(
        _proposal(artifact_type="memory-link", artifact_ref="_grimoire/_memory/agent-learnings/a.md")
    )
    assert "_grimoire/_memory/agent-learnings/a.md" in message
    assert "context:" in message
    assert "override" not in message.lower()


def test_accept_proposal_renvoie_le_texte_d_accept_blocker(tmp_path: Path) -> None:
    proposal = create_manual_proposal(
        tmp_path,
        slug="memory-link-orpheline",
        specialty="fiche mémoire non raccordée : orpheline.md",
        artifact_type="memory-link",
        carrier_reason="aucun porteur plausible : à placer à la main",
        artifact_ref="_grimoire/_memory/orpheline.md",
    )
    result = accept_proposal(tmp_path, proposal.slug)
    assert result == {"ok": False, "error": accept_blocker(proposal)}


def test_proposals_view_joint_accept_blocker(tmp_path: Path) -> None:
    from grimoire.tools import view_cache
    from grimoire.tools.workspace_api import proposals_view

    create_manual_proposal(
        tmp_path,
        slug="memory-link-orpheline",
        specialty="fiche mémoire non raccordée : orpheline.md",
        artifact_type="memory-link",
        artifact_ref="_grimoire/_memory/orpheline.md",
    )
    create_manual_proposal(
        tmp_path,
        slug="memory-link-portee",
        specialty="fiche mémoire non raccordée : portee.md",
        artifact_type="memory-link",
        target_agent="dev",
        artifact_ref="_grimoire/_memory/portee.md",
    )
    view_cache.invalidate(f"proposals_view:{tmp_path.resolve()}")
    by_slug = {p["slug"]: p for p in proposals_view(tmp_path)["proposals"]}
    assert "aucun porteur plausible" in by_slug["memory-link-orpheline"]["accept_blocker"]
    assert by_slug["memory-link-portee"]["accept_blocker"] == ""
