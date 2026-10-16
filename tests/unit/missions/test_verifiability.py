"""La classe de vérifiabilité doit monter, jamais descendre, sur le doute (issue #309)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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


# --- W3-05 : jeu étiqueté, zéro faux V0 mesuré


def _corpus() -> list[tuple[str, str, str]]:
    # tests/ n'est pas un package : on charge le jeu par chemin.
    spec = importlib.util.spec_from_file_location(
        "verifiability_corpus", Path(__file__).with_name("verifiability_corpus.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.CORPUS)


def test_corpus_etiquete_taille_et_langues() -> None:
    corpus = _corpus()

    assert len(corpus) >= 100
    assert sum(1 for _, langue, _ in corpus if langue == "fr") >= 40
    assert sum(1 for _, langue, _ in corpus if langue == "en") >= 40
    assert {classe for _, _, classe in corpus} == {"V0", "V1", "V2"}


def test_corpus_etiquete_zero_faux_v0() -> None:
    corpus = _corpus()

    faux_v0 = [
        t for t, _, attendu in corpus if attendu != "V0" and classify(tache(acceptance=(t,))) is Verifiability.V0
    ]
    assert faux_v0 == []


def test_corpus_etiquete_classe_exacte() -> None:
    corpus = _corpus()

    ecarts = [(t, attendu, classify(tache(acceptance=(t,))).value) for t, _, attendu in corpus]
    assert [e for e in ecarts if e[1] != e[2]] == []


def test_critere_vague_fait_passer_la_tache_en_v2() -> None:
    t = tache(acceptance=("pytest vert", "les tests sont lisibles et bien nommés"))
    assert classify(t) is Verifiability.V2


def test_as_dict_expose_reason_citant_le_critere_decisif() -> None:
    vague = as_dict(tache(acceptance=("pytest vert", "le code est propre")))
    assert vague["class"] == "V2"
    assert "le code est propre" in vague["reason"]
    assert "revue" in as_dict(tache(acceptance=("revue par un pair",)))["reason"]
    assert as_dict(tache(acceptance=("le build passe",)))["reason"]
    assert "aucun critère" in as_dict(tache())["reason"]


# --- W3-05, revue S1 : pièges nommés (chacun rouge sur la version précédente du classeur)


def test_existe_loin_du_fichier_n_est_pas_un_verdict_de_fichier() -> None:
    for critere in (
        "a file explains why the design exists",
        "the profile file captures what matters; a clear rationale exists",
        "le fichier README explique pourquoi le module existe",
    ):
        assert classify(tache(acceptance=(critere,))) is Verifiability.V2, critere


def test_existence_de_tests_sans_verdict_est_ambigue_dans_les_deux_langues() -> None:
    classes = {
        c: classify(tache(acceptance=(c,))) for c in ("tests exist", "des tests existent", "il existe des tests")
    }
    assert set(classes.values()) == {Verifiability.V2}, classes


def test_fichier_attendu_reste_mecanique_avec_sujet_direct() -> None:
    for critere in ("file docs/guide.md exists", "the config file exists", "le fichier README existe"):
        assert classify(tache(acceptance=(critere,))) is Verifiability.V0, critere


def test_adjectifs_de_jugement_ne_sont_pas_des_verdicts() -> None:
    for critere in (
        "the test code is clean",
        "tests are clean and readable",
        "tests are valid",
        "les tests correspondent aux besoins métier",
        "the schema matches the product vision",
        "le schéma est valide à mon avis",
        "le seuil de qualité des tests est raisonnable",
        "zéro test flaky ressenti",
        "tests pass muster",
        "les tests passent la barre de qualité attendue par l'équipe",
    ):
        assert classify(tache(acceptance=(critere,))) is Verifiability.V2, critere


def test_verdicts_lies_a_un_outil_restent_mecaniques() -> None:
    for critere in ("lint is clean", "ruff ne signale aucune erreur", "test coverage >= 80", "le schéma valide"):
        assert classify(tache(acceptance=("pytest vert", critere))) is Verifiability.V0, critere


def test_ligne_de_commande_nue_est_un_verdict_par_code_de_sortie() -> None:
    for critere in (
        "pytest tests/unit -q",
        "mypy --strict src/grimoire/",
        "ruff check src/",
        "uv run pytest",
        "cargo test",
        "npm test",
        "pytest -q && ruff check .",
    ):
        assert classify(tache(acceptance=(critere,))) is Verifiability.V0, critere
        assert explain(tache(acceptance=(critere,)))[0][1] == "commande_nue"


def test_outil_nomme_dans_une_phrase_n_est_pas_une_commande() -> None:
    for critere in ("pytest est un bon outil", "pytest is a great framework", "Test ; audit rapport.md ligne R31"):
        assert classify(tache(acceptance=(critere,))) is Verifiability.V2, critere


def test_commande_nue_ne_masque_pas_une_revue() -> None:
    assert classify(tache(acceptance=("pytest tests/unit à la main",))) is Verifiability.V1
