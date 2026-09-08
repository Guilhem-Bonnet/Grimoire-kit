"""Historique des dispatchs par couple (type de tâche, classe) — issue #312, lot 4.

Pas de classifieur, pas d'entraînement : des compteurs sur des événements
``task.dispatched`` fabriqués directement au ledger — la cascade elle-même
(``run_dispatch``) est déjà couverte par ``test_dispatch.py``, et n'a pas
besoin de tourner ici pour vérifier la règle de seuil.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.missions.dispatch_history import (
    ESCALATION_THRESHOLD,
    MIN_OBSERVATIONS,
    compute_dispatch_history,
    recommend_start_tier,
)
from grimoire.missions.ledger import MissionLedger

LEDGER = Path("_grimoire-runtime-output/ledger")


def _ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / LEDGER)


def _fabrique_dispatch(
    ledger: MissionLedger,
    task_id: str,
    *,
    task_type: str,
    verifiability: str,
    tiers: tuple[str, ...],
) -> None:
    """Un dispatch fabriqué : une tentative par palier de *tiers*, dans l'ordre.

    Reproduit exactement les clés que ``dispatch._dispatch_event_payload``
    écrit (``task_type``/``verifiability``/``start_tier`` inclus) — c'est ce
    format que ``dispatch_history`` doit savoir lire. ``tiers[0]`` est le
    palier de départ ; toute tentative à un palier différent du premier
    compte comme une escalade.
    """
    start_tier = tiers[0]
    for i, tier in enumerate(tiers, start=1):
        verdict = "green" if i == len(tiers) else "red"
        ledger.append_event(
            "task.dispatched",
            task_id,
            "task",
            "test",
            {
                "task_id": task_id,
                "attempt": i,
                "tier": tier,
                "provider": f"{tier}-provider",
                "model": f"{tier}-model",
                "verdict": verdict,
                "checks": [],
                "cost_usd": None,
                "task_type": task_type,
                "verifiability": verifiability,
                "start_tier": start_tier,
                "start_tier_reason": "test fabriqué",
            },
        )


def test_moins_de_n_observations_reste_au_plancher(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    for n in range(MIN_OBSERVATIONS - 1):
        _fabrique_dispatch(ledger, f"GAO-x-{n:03d}", task_type="implementation", verifiability="V0", tiers=("cheap",))

    tier, reason = recommend_start_tier(ledger, task_type="implementation", verifiability="V0", floor="cheap")

    assert tier == "cheap"
    assert "moins de" in reason


def test_escalade_60_pourcent_depuis_cheap_recommande_mid(tmp_path: Path) -> None:
    """Type X, 60 % d'escalade depuis cheap (3 sur 5) : le prochain dispatch part de mid."""
    ledger = _ledger(tmp_path)
    escalades = [True, True, True, False, False]
    for n, escalade in enumerate(escalades):
        tiers = ("cheap", "mid") if escalade else ("cheap",)
        _fabrique_dispatch(ledger, f"GAO-x-{n:03d}", task_type="implementation", verifiability="V0", tiers=tiers)

    tier, reason = recommend_start_tier(ledger, task_type="implementation", verifiability="V0", floor="cheap")

    assert tier == "mid"
    assert "cheap" in reason
    assert "60%" in reason


def test_zero_escalade_reste_cheap(tmp_path: Path) -> None:
    """Type Y, 0 % d'escalade : rien ne bouge."""
    ledger = _ledger(tmp_path)
    for n in range(MIN_OBSERVATIONS):
        _fabrique_dispatch(ledger, f"GAO-y-{n:03d}", task_type="documentation", verifiability="V0", tiers=("cheap",))

    tier, reason = recommend_start_tier(ledger, task_type="documentation", verifiability="V0", floor="cheap")

    assert tier == "cheap"
    assert "sous le seuil" in reason


def test_escalade_depuis_mid_recommande_strong(tmp_path: Path) -> None:
    """Une tâche V1 (plancher mid) qui escalade trop vers strong."""
    ledger = _ledger(tmp_path)
    escalades = [True, True, True, True, False]
    for n, escalade in enumerate(escalades):
        tiers = ("mid", "strong") if escalade else ("mid",)
        _fabrique_dispatch(ledger, f"GAO-z-{n:03d}", task_type="security", verifiability="V1", tiers=tiers)

    tier, reason = recommend_start_tier(ledger, task_type="security", verifiability="V1", floor="mid")

    assert tier == "strong"
    assert "mid" in reason


def test_redescente_apres_n_verts_stables_en_mid_et_cheap_non_retente(tmp_path: Path) -> None:
    """Un couple V0 poussé à mid, stable depuis, sans avoir retenté cheap : re-sonde depuis cheap."""
    ledger = _ledger(tmp_path)
    # D'abord assez d'escalades depuis cheap pour justifier historiquement la
    # montée à mid (peu importe ici : le calcul ne regarde que les dernières
    # observations à `mid`, pas comment le couple y est arrivé).
    for n in range(MIN_OBSERVATIONS):
        _fabrique_dispatch(ledger, f"GAO-w-cheap-{n:03d}", task_type="cleanup", verifiability="V0", tiers=("mid",))

    tier, reason = recommend_start_tier(ledger, task_type="cleanup", verifiability="V0", floor="cheap")

    assert tier == "cheap"
    assert "re-sonde" in reason


def test_v1_ne_redescend_jamais_sous_mid(tmp_path: Path) -> None:
    """Même scénario que la redescente, mais le plancher V1 interdit d'aller sous mid."""
    ledger = _ledger(tmp_path)
    for n in range(MIN_OBSERVATIONS):
        _fabrique_dispatch(ledger, f"GAO-v1-{n:03d}", task_type="security", verifiability="V1", tiers=("mid",))

    tier, reason = recommend_start_tier(ledger, task_type="security", verifiability="V1", floor="mid")

    assert tier == "mid"
    assert "re-sonde" not in reason


def test_un_cheap_recent_empeche_une_deuxieme_redescente(tmp_path: Path) -> None:
    """Cheap déjà re-tenté dans les N dernières observations : pas de deuxième motif de re-sondage.

    Le résultat (``cheap``) est le même que la redescente elle-même produirait
    — le plancher de la classe *est* ``cheap`` — mais la raison ne doit pas
    prétendre re-sonder ce qui vient déjà d'être re-sondé.
    """
    ledger = _ledger(tmp_path)
    for n in range(MIN_OBSERVATIONS):
        _fabrique_dispatch(ledger, f"GAO-r-mid-{n:03d}", task_type="cleanup", verifiability="V0", tiers=("mid",))
    _fabrique_dispatch(ledger, "GAO-r-cheap-000", task_type="cleanup", verifiability="V0", tiers=("cheap",))

    tier, reason = recommend_start_tier(ledger, task_type="cleanup", verifiability="V0", floor="cheap")

    assert tier == "cheap"
    assert "re-sonde" not in reason


def test_evenement_sans_task_type_est_ignore(tmp_path: Path) -> None:
    """Un événement écrit par une version antérieure au lot 4 (sans task_type/verifiability) ne casse rien."""
    ledger = _ledger(tmp_path)
    ledger.append_event(
        "task.dispatched",
        "GAO-vieux-001",
        "task",
        "test",
        {"task_id": "GAO-vieux-001", "attempt": 1, "tier": "cheap", "verdict": "green"},
    )

    tier, reason = recommend_start_tier(ledger, task_type="implementation", verifiability="V0", floor="cheap")

    assert tier == "cheap"
    assert "moins de" in reason
    assert compute_dispatch_history(ledger) == ()


def test_compute_dispatch_history_regroupe_par_couple_et_serialise(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    for n in range(MIN_OBSERVATIONS):
        _fabrique_dispatch(
            ledger, f"GAO-a-{n:03d}", task_type="implementation", verifiability="V0", tiers=("cheap", "mid")
        )
    for n in range(3):
        _fabrique_dispatch(ledger, f"GAO-b-{n:03d}", task_type="test", verifiability="V0", tiers=("cheap",))

    histories = compute_dispatch_history(ledger)

    assert {(h.task_type, h.verifiability) for h in histories} == {("implementation", "V0"), ("test", "V0")}
    impl = next(h for h in histories if h.task_type == "implementation")
    assert impl.observations == MIN_OBSERVATIONS
    assert impl.by_start_tier["cheap"].observations == MIN_OBSERVATIONS
    assert impl.by_start_tier["cheap"].escalations == MIN_OBSERVATIONS
    assert impl.recommended_start_tier == "mid"
    data = impl.to_dict()
    assert data["by_start_tier"]["cheap"]["escalation_rate"] == 1.0

    test_type = next(h for h in histories if h.task_type == "test")
    assert test_type.observations == 3
    assert test_type.recommended_start_tier == "cheap"  # moins de MIN_OBSERVATIONS


def test_seuil_est_strictement_superieur_pas_egal(tmp_path: Path) -> None:
    """40 % pile ne déclenche rien — le seuil est ``> 40 %``, pas ``>= 40 %``."""
    ledger = _ledger(tmp_path)
    escalades = [True, True, False, False, False]  # exactement 40 %
    assert sum(escalades) / len(escalades) == ESCALATION_THRESHOLD
    for n, escalade in enumerate(escalades):
        tiers = ("cheap", "mid") if escalade else ("cheap",)
        _fabrique_dispatch(ledger, f"GAO-seuil-{n:03d}", task_type="implementation", verifiability="V0", tiers=tiers)

    tier, _reason = recommend_start_tier(ledger, task_type="implementation", verifiability="V0", floor="cheap")

    assert tier == "cheap"
