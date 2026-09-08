"""La classe de vérifiabilité doit monter, jamais descendre, sur le doute (issue #309)."""

from __future__ import annotations

from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.verifiability import Verifiability, as_dict, classify, explain


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


def test_v0_tous_criteres_mecaniques_francais() -> None:
    t = tache(acceptance=("la suite de tests passe", "ruff et mypy passent"))
    assert classify(t) is Verifiability.V0


def test_v0_tous_criteres_mecaniques_anglais() -> None:
    t = tache(acceptance=("the test suite passes",), expected_evidence=("ci build passes",))
    assert classify(t) is Verifiability.V0


def test_v0_couvre_expected_evidence_et_acceptance() -> None:
    # Un critère sérieux peut vivre dans expected_evidence seul.
    t = tache(acceptance=("pytest vert",), expected_evidence=("grimoire standard gate check",))
    assert classify(t) is Verifiability.V0


def test_v1_revue_francaise() -> None:
    t = tache(acceptance=("revue de code par un pair",))
    assert classify(t) is Verifiability.V1


def test_v1_revue_anglaise() -> None:
    t = tache(acceptance=("approved by a senior reviewer",))
    assert classify(t) is Verifiability.V1


def test_v1_juge_mixe_avec_mecanique_reste_v1() -> None:
    # Un juge à côté de critères mécaniques ne dilue pas l'exigence humaine.
    t = tache(acceptance=("les tests passent", "validé par le tech lead"))
    assert classify(t) is Verifiability.V1


def test_v2_sans_critere_du_tout() -> None:
    t = tache(acceptance=(), expected_evidence=())
    assert classify(t) is Verifiability.V2


def test_v2_critere_vague_ne_peut_jamais_donner_v0() -> None:
    """Le cas qui compte : la vague ne se déguise jamais en preuve mécanique."""
    t = tache(acceptance=("le code est propre",))
    assert classify(t) is not Verifiability.V0
    assert classify(t) is Verifiability.V2


def test_v2_un_seul_critere_ambigu_fait_basculer_un_lot_par_ailleurs_mecanique() -> None:
    t = tache(acceptance=("la suite de tests passe", "le résultat est satisfaisant"))
    assert classify(t) is Verifiability.V2


def test_precedence_ambigu_l_emporte_meme_avec_un_juge_present() -> None:
    # Un seul critère non mécanique et non-juge suffit à faire V2, même à
    # côté d'un juge : la montée de classe n'a pas d'exception.
    t = tache(acceptance=("validé par le PO", "c'est bon"))
    assert classify(t) is Verifiability.V2


def test_criteres_mixtes_revue_et_mecanique_sans_ambiguite_reste_v1() -> None:
    t = tache(
        acceptance=("la suite de tests passe", "le schéma valide"),
        expected_evidence=("relecture du rapport de tests",),
    )
    assert classify(t) is Verifiability.V1


def test_explain_signale_le_critere_ambigu_par_un_motif_none() -> None:
    t = tache(acceptance=("la suite de tests passe", "le code est propre"))
    details = explain(t)
    assert details == [
        ("la suite de tests passe", "test"),
        ("le code est propre", None),
    ]


def test_explain_ordre_acceptance_puis_expected_evidence() -> None:
    t = tache(acceptance=("ruff passe",), expected_evidence=("revue par un pair",))
    details = explain(t)
    assert [c for c, _ in details] == ["ruff passe", "revue par un pair"]
    assert details[0][1] == "lint_typage"
    assert details[1][1] == "revue"


def test_as_dict_porte_classe_explication_et_criteres() -> None:
    t = tache(acceptance=("le build passe",))
    payload = as_dict(t)
    assert payload["class"] == "V0"
    assert payload["explanation"] == Verifiability.V0.explanation
    assert payload["criteria"] == [{"criterion": "le build passe", "pattern": "build_ci"}]


def test_toutes_les_classes_ont_une_explication_non_vide() -> None:
    for classe in Verifiability:
        assert classe.explanation


def test_faux_v0_validation_nue_par_une_personne_monte() -> None:
    # « validation » sans schéma ne dit pas qui juge : ambigu, donc V2, jamais V0.
    t = tache(acceptance=("pytest vert", "validation fonctionnelle par le PO en démo"))
    assert classify(t) is not Verifiability.V0


def test_faux_v0_tests_manuels_sont_un_jugement_humain() -> None:
    t = tache(acceptance=("les tests manuels sont concluants",))
    assert classify(t) is Verifiability.V1
    t_en = tache(acceptance=("manual testing passes",))
    assert classify(t_en) is Verifiability.V1


def test_validation_contre_un_schema_reste_mecanique() -> None:
    t = tache(acceptance=("validation du schéma JSON",), expected_evidence=("schema validation passes",))
    assert classify(t) is Verifiability.V0
