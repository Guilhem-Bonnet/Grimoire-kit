"""Le conseil de dispatch (vérifiabilité + modèle recommandé) — #654.

Une seule fonction, dans ``missions/`` (pas dans un hôte) : la classe vient de
``verifiability.classify``, jamais recalculée — même source que ``grimoire
task dispatch`` (``missions.dispatch.start_tier_for``).

Depuis #662 (défaut « V2 sans valeur invalide ») : ``recommended_model``
n'est jamais ``"session"`` — ce n'est pas un nom de modèle que l'outil
``Agent`` de Claude Code accepte pour ``model=``. V2 rend ``recommended_model:
None`` (« omets le paramètre ») plus ``model_tier: "session"``, toujours
renseigné. ``RECOMMENDED_MODEL_BY_CLASS`` garde ses valeurs internes
(``"session"`` compris) : ``_model_label`` (``hosts.emitters.claude_code``)
continue de les lire telles quelles pour composer sa phrase humaine.
"""

from __future__ import annotations

from grimoire.missions.dispatch import start_tier_for
from grimoire.missions.dispatch_advice import (
    RECOMMENDED_MODEL_BY_CLASS,
    dispatch_advice,
    model_hint,
    model_tier_for,
    recommended_model_for,
)
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
    assert advice["model_tier"] == "cheap"


def test_v1_recommande_sonnet() -> None:
    t = tache(acceptance=("revue de code par un pair",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V1"
    assert advice["recommended_model"] == "sonnet"
    assert advice["model_tier"] == "mid"


def test_v2_sans_critere_ne_recommande_aucun_modele_sans_exception() -> None:
    """V2 n'a aucun modèle à passer à `model=` : `recommended_model` est `None`,
    jamais `"session"` (pas un nom de modèle valide pour l'outil `Agent`)."""
    t = tache(acceptance=(), expected_evidence=())
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V2"
    assert advice["recommended_model"] is None
    assert advice["model_tier"] == "session"


def test_v2_critere_ambigu_ne_recommande_aucun_modele() -> None:
    t = tache(acceptance=("le code est propre",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["class"] == "V2"
    assert advice["recommended_model"] is None
    assert advice["model_tier"] == "session"


def test_v2_est_json_serialisable_sans_valeur_ambigue() -> None:
    """Le contrat externe (MCP/CLI) sérialise `recommended_model` en `null`,
    jamais en la chaîne `"session"` que rien n'accepte pour `model=`."""
    import json

    t = tache(acceptance=())
    payload = json.loads(json.dumps(dispatch_advice(t)))
    assert payload["recommended_model"] is None
    assert payload["model_tier"] == "session"


def test_model_hint_ne_rend_jamais_none_tel_quel() -> None:
    t = tache(acceptance=())
    hint = model_hint(dispatch_advice(t))
    assert "None" not in hint
    assert "omets le paramètre model" in hint
    assert model_hint(dispatch_advice(tache(acceptance=("la suite de tests passe",)))) == "haiku"


def test_verifiability_contient_explanation_et_criteres() -> None:
    t = tache(acceptance=("la suite de tests passe",))
    advice = dispatch_advice(t)
    assert advice["verifiability"]["explanation"] == Verifiability.V0.explanation
    assert advice["verifiability"]["criteria"] == [{"criterion": "la suite de tests passe", "pattern": "test"}]


def test_recommended_model_for_couvre_les_trois_classes() -> None:
    """Contrat externe : jamais ``"session"`` — ``None`` pour V2."""
    assert recommended_model_for(Verifiability.V0) == "haiku"
    assert recommended_model_for(Verifiability.V1) == "sonnet"
    assert recommended_model_for(Verifiability.V2) is None
    assert set(RECOMMENDED_MODEL_BY_CLASS) == set(Verifiability)
    assert [model_tier_for(v) for v in (Verifiability.V0, Verifiability.V1, Verifiability.V2)] == [
        "cheap",
        "mid",
        "session",
    ]


def test_recommended_model_by_class_garde_sa_valeur_interne_pour_model_label() -> None:
    """Contrat interne : ``_model_label`` (``hosts.emitters.claude_code``) lit
    encore ``RECOMMENDED_MODEL_BY_CLASS[V2] == "session"`` tel quel — seul le
    contrat externe (:func:`recommended_model_for`) traduit en ``None``."""
    assert RECOMMENDED_MODEL_BY_CLASS[Verifiability.V2] == "session"


def test_coherence_avec_task_dispatch_meme_tache() -> None:
    """La classe qui pilote ``recommended_model``/``model_tier`` est celle que ``task dispatch`` verrait.

    ``start_tier_for`` refuse une V2 (``None``) et pose ``cheap``/``mid`` pour
    V0/V1 — ``model_tier_for`` lit ``start_tier_for`` lui-même : si l'une refuse (V2), l'autre retombe sur le palier le plus
    prudent (``session``), jamais un accident de calcul indépendant.
    """
    for acceptance, attendu_tier_dispatch, attendu_modele, attendu_tier in (
        (("la suite de tests passe",), "cheap", "haiku", "cheap"),
        (("revue de code par un pair",), "mid", "sonnet", "mid"),
        ((), None, None, "session"),
    ):
        t = tache(acceptance=acceptance)
        klass = classify(t)
        assert start_tier_for(klass) == attendu_tier_dispatch
        advice = dispatch_advice(t)
        assert advice["recommended_model"] == attendu_modele
        assert advice["model_tier"] == attendu_tier
