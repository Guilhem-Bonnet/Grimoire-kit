"""Le gate de preuve rend `evidence-gates.yaml` opposable (issue #137)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidenceProfile,
    EvidenceService,
)
from grimoire.missions.gates import GATES_FILE, GateVerdict, check_transition
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType

STANDARD = Path("_grimoire/standard")
EVIDENCE_LEDGER = Path("_grimoire-runtime-output/evidence")

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack", "decision_trace"]
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate", "evidence_gate"]
profile_strictness:
  starter: advisory
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text(
        "profile: governed\n", encoding="utf-8"
    )
    return tmp_path


def tache(**kw: object) -> MissionTask:
    base: dict[str, object] = {
        "id": "GAO-demo-001", "mission_id": "M-1", "title": "Démo",
        "status": TaskState.RUNNING, "type": TaskType.IMPLEMENTATION,
        "risk_profile": RiskProfile.STANDARD, "acceptance": ACCEPTATION,
        "created_at": "2026-08-27T00:00:00Z", "owner": "amelia",
    }
    base.update(kw)
    return MissionTask(**base)  # type: ignore[arg-type]


ACCEPTATION = ("le test passe",)


def _item(kind: EvidenceKind, resume: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"evd-{kind.value}", kind=kind, uri=f"artefact://{kind.value}",
        digest="sha256:" + kind.value, summary=resume,
    )


def pack(root: Path, *, couvre: bool = True, verifie: bool = True) -> None:
    """Poser un vrai pack de preuve, et facultativement le faire verifier.

    La couverture est calculee par le service : un critere est couvert si un
    item le mentionne. On ne la falsifie donc pas, on la produit.
    """
    svc = EvidenceService(root / EVIDENCE_LEDGER)
    resume = "le test passe" if couvre else "rien a voir"
    p = svc.create_pack(
        task_id="GAO-demo-001",
        profile=EvidenceProfile.STANDARD,
        items=[_item(EvidenceKind.TEST, resume), _item(EvidenceKind.LOG, resume)],
        acceptance=ACCEPTATION,
    )
    if verifie:
        svc.verify(p, acceptance=ACCEPTATION)


# ── le critère d'acceptation de #137 ────────────────────────────────────────

def test_on_ne_ferme_pas_une_tache_sans_verdict(projet: Path) -> None:
    """Sans verdict de vérification, la fermeture est refusée et le dit."""
    verdict = check_transition(projet, tache(), "review", "accepted")
    assert verdict.blocked
    noms = {r.evidence for r in verdict.refusals}
    assert "review_gate" in noms
    raison = next(r for r in verdict.refusals if r.evidence == "review_gate")
    assert "aucun verdict" in raison.reason
    assert raison.remedy


def test_le_refus_nomme_l_artefact_manquant(projet: Path) -> None:
    """« preuve manquante » n'aide personne ; le chemin attendu, si."""
    verdict = check_transition(projet, tache(), "in_progress", "review")
    trace = next(r for r in verdict.refusals if r.evidence == "decision_trace")
    assert "_grimoire-output/decisions/GAO-demo-001/decision-trace.yaml" in trace.remedy


def test_passage_en_revue_sans_pack_de_preuve_refuse(projet: Path) -> None:
    verdict = check_transition(projet, tache(), "in_progress", "review")
    assert verdict.blocked
    assert "evidence_pack" in {r.evidence for r in verdict.refusals}


# ── ce qui doit passer ──────────────────────────────────────────────────────

def test_un_verdict_accepte_ouvre_la_fermeture(projet: Path) -> None:
    pack(projet)
    verdict = check_transition(projet, tache(), "review", "accepted")
    assert verdict.refusals == ()
    assert not verdict.blocked


def test_criteres_et_proprietaire_suffisent_a_passer_ready(projet: Path) -> None:
    verdict = check_transition(projet, tache(status=TaskState.PROPOSED), "proposed", "ready")
    assert verdict.refusals == ()


def test_une_transition_non_declaree_n_est_pas_gardee(projet: Path) -> None:
    """Les gates disent ce qui est exigé, pas ce qui est permis."""
    verdict = check_transition(projet, tache(), "blocked", "ready")
    assert not verdict.declared
    assert not verdict.blocked


# ── fail-closed ─────────────────────────────────────────────────────────────

def test_une_preuve_sans_resolveur_refuse_au_lieu_de_passer(projet: Path) -> None:
    """Ne pas savoir vérifier n'est pas une autorisation : sans ça, le gate
    serait d'autant plus vert qu'il comprend moins."""
    (projet / GATES_FILE).write_text(
        GATES.replace('["review_gate", "evidence_gate"]', '["signature_du_pape"]'),
        encoding="utf-8",
    )
    verdict = check_transition(projet, tache(), "review", "accepted")
    assert verdict.blocked
    refus = verdict.refusals[0]
    assert refus.evidence == "signature_du_pape"
    assert "aucun résolveur" in refus.reason


def test_un_profil_inconnu_est_traite_comme_strict(projet: Path) -> None:
    (projet / STANDARD / "standard-profile.yaml").write_text(
        "profile: maison\n", encoding="utf-8"
    )
    assert check_transition(projet, tache(), "review", "accepted").strictness == "hard_fail"


def test_sans_fichier_de_gates_aucune_transition_n_est_declaree(tmp_path: Path) -> None:
    verdict = check_transition(tmp_path, tache(), "review", "accepted")
    assert not verdict.declared
    assert verdict.strictness == "hard_fail"


# ── la strictness déclarée est respectée, pas réinventée ────────────────────

def test_un_profil_advisory_signale_sans_bloquer(projet: Path) -> None:
    """La strictness est déclarée par le projet ; l'ignorer serait inventer une
    politique à sa place. Le refus reste visible, il ne barre pas la route."""
    (projet / STANDARD / "standard-profile.yaml").write_text(
        "profile: starter\n", encoding="utf-8"
    )
    verdict = check_transition(projet, tache(), "review", "accepted")
    assert verdict.refusals
    assert not verdict.blocked
    assert verdict.strictness == "advisory"


# ── couverture d'acceptation ────────────────────────────────────────────────

def test_un_critere_non_couvert_bloque_la_fermeture(projet: Path) -> None:
    pack(projet, couvre=False)
    verdict = check_transition(projet, tache(), "review", "accepted")
    noms = {r.evidence for r in verdict.refusals}
    assert "evidence_gate" in noms


def test_sans_proprietaire_ni_claim_personne_n_en_repond(projet: Path) -> None:
    verdict = check_transition(projet, tache(status=TaskState.PROPOSED, owner=""), "proposed", "ready")
    assert "owner_or_agent_role" in {r.evidence for r in verdict.refusals}


def test_un_claim_tient_lieu_de_proprietaire(projet: Path) -> None:
    from grimoire.missions.schemas import TaskClaim

    t = tache(status=TaskState.PROPOSED, owner="", claim=TaskClaim(actor_id="amelia", host_id="h1"))
    assert check_transition(projet, t, "proposed", "ready").refusals == ()


def test_le_verdict_est_serialisable_pour_le_json_du_cli() -> None:
    v = GateVerdict("t", "hard_fail", ())
    assert v.transition_id == "t" and v.declared
