"""Le conseil de dispatch (vérifiabilité + modèle recommandé) — #654.

Une seule fonction, dans ``missions/`` (pas dans un hôte) : la classe vient de
``verifiability.classify``, jamais recalculée — même source que ``grimoire
task dispatch`` (``missions.dispatch.start_tier_for``).
"""

from __future__ import annotations

from grimoire.missions.dispatch import start_tier_for
from grimoire.missions.dispatch_advice import RECOMMENDED_MODEL_BY_CLASS, dispatch_advice, recommended_model_for
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.verifiability import Verifiability, classify


def tache(**kw: object) -> MissionTask:
    base: dict[str, object] = {
        "id": "GAO-demo-001",
        "mission_id": "M-1",
        "title": "Démo",
        "status": TaskState.RUNNING,
        "type": TaskType.IMPLEMENTATION,
        "risk_profile": RiskProfile.STANDARD,
        "acceptance": (),
        "created_at": "2026-08-27T00:00:00Z",
    }
    base.update(kw)
    return MissionTask(**base)  # type: ignore[arg-type]


def test_v0_recommande_haiku() -> None:
    t = tache(acceptance=("la suite de tests passe",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V0"
    assert advice["recommended_model"] == "haiku"


def test_v1_recommande_sonnet() -> None:
    t = tache(acceptance=("revue de code par un pair",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V1"
    assert advice["recommended_model"] == "sonnet"


def test_v2_sans_critere_recommande_le_modele_de_session_sans_exception() -> None:
    t = tache(acceptance=(), expected_evidence=())
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V2"
    assert advice["recommended_model"] == "session"


def test_v2_critere_ambigu_recommande_le_modele_de_session() -> None:
    t = tache(acceptance=("le code est propre",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V2"
    assert advice["recommended_model"] == "session"


def test_verifiability_contient_explanation_et_criteres() -> None:
    t = tache(acceptance=("la suite de tests passe",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["explanation"] == Verifiability.V0.explanation
    assert advice["verifiability"]["criteria"] == [{"criterion": "la suite de tests passe", "pattern": "test"}]


def test_recommended_model_for_couvre_les_trois_classes() -> None:
    assert recommended_model_for(Verifiability.V0) == "haiku"
    assert recommended_model_for(Verifiability.V1) == "sonnet"
    assert recommended_model_for(Verifiability.V2) == "session"
    assert set(RECOMMENDED_MODEL_BY_CLASS) == set(Verifiability)


def test_coherence_avec_task_dispatch_meme_tache() -> None:
    """La classe qui pilote ``recommended_model`` est celle que ``task dispatch`` verrait.

    ``start_tier_for`` refuse une V2 (``None``) et pose ``cheap``/``mid`` pour
    V0/V1 — pas la même table que ``RECOMMENDED_MODEL_BY_CLASS``, mais dérivée
    de la même classe : si l'une refuse (V2), l'autre retombe sur le choix le
    plus prudent (``session``), jamais un accident de calcul indépendant.
    """
    for acceptance, attendu_tier, attendu_modele in (
        (("la suite de tests passe",), "cheap", "haiku"),
        (("revue de code par un pair",), "mid", "sonnet"),
        ((), None, "session"),
    ):
        t = tache(acceptance=acceptance)
        klass = classify(t)
        assert start_tier_for(klass) == attendu_tier
        assert dispatch_advice(t)["recommended_model"] == attendu_modele
