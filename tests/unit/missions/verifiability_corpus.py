"""Jeu étiqueté de critères d'acceptation pour mesurer la classe de vérifiabilité (W3-05).

Chaque entrée : (texte, langue, classe attendue). L'étiquette dit ce qu'un
relecteur humain conclurait du critère pris seul :

- ``V0`` : le verdict tombe sans personne (test, lint, schéma, gate, code de
  sortie, build/CI, fichier attendu) ;
- ``V1`` : le verdict passe par une revue, un jugement ou une validation ;
- ``V2`` : le critère est vague, ou ne dit pas qui rend le verdict.

Le point mesuré est l'absence de faux V0 : un critère étiqueté V1 ou V2 que
le classeur déclarerait V0. Les entrées à piège (mot mécanique dans une
phrase qui n'est pas un verdict) sont marquées dans leur commentaire.
"""

from __future__ import annotations

CORPUS: tuple[tuple[str, str, str], ...] = (
    # --- V0 français
    ("pytest vert", "fr", "V0"),
    ("la suite de tests passe", "fr", "V0"),
    ("les tests unitaires passent", "fr", "V0"),
    ("les tests d'intégration sont verts", "fr", "V0"),
    ("ruff ne signale aucune erreur", "fr", "V0"),
    ("mypy passe en mode strict", "fr", "V0"),
    ("le linter ne remonte aucun avertissement", "fr", "V0"),
    ("le typage est vérifié sans erreur", "fr", "V0"),
    ("le schéma JSON valide le fichier produit", "fr", "V0"),
    ("validation du schéma réussie", "fr", "V0"),
    ("grimoire standard gate retourne 0", "fr", "V0"),
    ("le gate check de la tâche est vert", "fr", "V0"),
    ("la commande retourne 0", "fr", "V0"),
    ("le code de sortie est nul", "fr", "V0"),
    ("le build passe", "fr", "V0"),
    ("la CI verte sur la PR", "fr", "V0"),
    ("le pipeline vert sur main", "fr", "V0"),
    ("le fichier docs/guide.md existe", "fr", "V0"),
    ("existence du fichier de configuration", "fr", "V0"),
    ("la couverture de tests reste au-dessus du seuil", "fr", "V0"),
    ("le build réussi est archivé", "fr", "V0"),
    ("pytest tests/unit/missions passe", "fr", "V0"),
    ("les tests verts sur la branche", "fr", "V0"),
    ("le contenu exact du fichier correspond au modèle", "fr", "V0"),
    # --- V0 anglais
    ("pytest passes", "en", "V0"),
    ("the test suite passes", "en", "V0"),
    ("all unit tests pass", "en", "V0"),
    ("integration tests are green", "en", "V0"),
    ("ruff reports no errors", "en", "V0"),
    ("mypy type checking passes", "en", "V0"),
    ("lint is clean", "en", "V0"),
    ("the output validates against the JSON schema", "en", "V0"),
    ("schema validation passes", "en", "V0"),
    ("standard gate passes", "en", "V0"),
    ("the command returns exit code 0", "en", "V0"),
    ("exit status is zero", "en", "V0"),
    ("build passes", "en", "V0"),
    ("CI green on the PR", "en", "V0"),
    ("pipeline green on main", "en", "V0"),
    ("file docs/guide.md exists", "en", "V0"),
    ("the config file exists at the documented path", "en", "V0"),
    ("test coverage stays above the threshold", "en", "V0"),
    ("green tests on the branch", "en", "V0"),
    ("build green on the release branch", "en", "V0"),
    # --- V1 français
    ("revue par un pair", "fr", "V1"),
    ("relecture du guide par l'équipe", "fr", "V1"),
    ("la revue de code est approuvée", "fr", "V1"),
    ("le jugement du mainteneur est requis", "fr", "V1"),
    ("les tests manuels sont concluants", "fr", "V1"),
    ("vérifié à la main sur l'environnement de recette", "fr", "V1"),
    ("démonstration devant le product owner", "fr", "V1"),
    ("validé par le PO", "fr", "V1"),
    ("validation par l'architecte", "fr", "V1"),
    ("approuvé par la sécurité", "fr", "V1"),
    ("signé par le responsable qualité", "fr", "V1"),
    ("revue du rapport de tests", "fr", "V1"),  # piège : mot mécanique + revue
    ("relecture des résultats de pytest", "fr", "V1"),  # piège
    ("revue des avertissements de ruff", "fr", "V1"),  # piège
    ("le PO valide le build en démo", "fr", "V1"),  # piège
    ("validation fonctionnelle par le PO", "fr", "V1"),
    ("démo des tests de bout en bout", "fr", "V1"),  # piège
    ("revue du schéma par l'équipe données", "fr", "V1"),  # piège
    # --- V1 anglais
    ("peer review", "en", "V1"),
    ("code review approved", "en", "V1"),
    ("the docs get a review from the tech writer", "en", "V1"),
    ("maintainer's judgement required", "en", "V1"),
    ("manual testing passes", "en", "V1"),
    ("manually verified on staging", "en", "V1"),
    ("verified by hand on staging", "en", "V1"),
    ("demo to the product owner", "en", "V1"),
    ("approved by the security team", "en", "V1"),
    ("reviewed by two maintainers", "en", "V1"),
    ("sign-off from the architect", "en", "V1"),
    ("review of the test report", "en", "V1"),  # piège
    ("review of the CI logs", "en", "V1"),  # piège
    ("peer review of the schema", "en", "V1"),  # piège
    ("the product owner accepts the build in a demo", "en", "V1"),  # piège
    ("approval by the lead before the gate is opened", "en", "V1"),  # piège
    # --- V2 français : vague, ou mot mécanique qui n'est pas un verdict
    ("le code est propre", "fr", "V2"),
    ("l'interface est agréable", "fr", "V2"),
    ("la documentation est claire", "fr", "V2"),
    ("les performances sont satisfaisantes", "fr", "V2"),
    ("le comportement est conforme aux attentes", "fr", "V2"),
    ("la solution est robuste", "fr", "V2"),
    ("tout fonctionne correctement", "fr", "V2"),
    ("le résultat est de bonne qualité", "fr", "V2"),
    ("les utilisateurs sont contents", "fr", "V2"),
    ("le code est bien testé", "fr", "V2"),  # piège : « testé » n'est pas un verdict
    ("les tests sont lisibles et bien nommés", "fr", "V2"),  # piège : qualité des tests
    ("le schéma est élégant", "fr", "V2"),  # piège : « schéma » sans verdict
    ("le build est rapide à comprendre", "fr", "V2"),  # piège
    ("les gates sont bien pensés", "fr", "V2"),  # piège
    ("le linter est agréable à configurer", "fr", "V2"),  # piège
    ("les tests couvrent les cas importants", "fr", "V2"),  # piège : couverture jugée, pas mesurée
    ("la suite de tests est maintenable", "fr", "V2"),  # piège
    ("le typage est cohérent avec le reste du projet", "fr", "V2"),  # piège
    ("le fichier de configuration est bien organisé", "fr", "V2"),
    ("la commande est simple à utiliser", "fr", "V2"),  # piège
    # --- V2 anglais
    ("the code is clean", "en", "V2"),
    ("the UI feels nice", "en", "V2"),
    ("documentation is clear", "en", "V2"),
    ("performance is acceptable", "en", "V2"),
    ("it works as expected", "en", "V2"),
    ("the solution is robust", "en", "V2"),
    ("everything works properly", "en", "V2"),
    ("users are happy", "en", "V2"),
    ("the code is well tested", "en", "V2"),  # piège
    ("tests are readable and well named", "en", "V2"),  # piège
    ("the schema is elegant", "en", "V2"),  # piège
    ("the build is easy to understand", "en", "V2"),  # piège
    ("the gates are well thought out", "en", "V2"),  # piège
    ("the linter is pleasant to configure", "en", "V2"),  # piège
    ("tests cover the important cases", "en", "V2"),  # piège
    ("the test suite is maintainable", "en", "V2"),  # piège
    ("the command is easy to use", "en", "V2"),  # piège
    ("the config file is well organised", "en", "V2"),
    # --- Revue S1 (W3-05) : lignes de commande nues, verdict = code de sortie
    ("pytest tests/unit -q", "en", "V0"),
    ("mypy --strict src/grimoire/", "en", "V0"),
    ("ruff check src/", "en", "V0"),
    ("uv run pytest", "en", "V0"),
    ("cargo test", "en", "V0"),
    ("npm test", "en", "V0"),
    ("pytest -q && ruff check .", "en", "V0"),
    ("Run pytest tests/unit/missions", "en", "V0"),
    ("le fichier README existe", "fr", "V0"),
    ("the file README.md exists", "en", "V0"),
    ("le fichier attendu docs/x.md existe", "fr", "V0"),
    ("le fichier docs/x.md est présent", "fr", "V0"),
    ("le fichier docs/x.md est créé", "fr", "V0"),
    ("un fichier décrit pourquoi X existe", "fr", "V2"),  # piège
    ("le fichier « dist/out.json » existe (acceptance exécutée)", "fr", "V0"),
    # --- Revue S1 (W3-05) : « exists » / « existe » loin du fichier n'est pas un verdict
    ("a file explains why the design exists", "en", "V2"),  # piège
    ("the profile file captures what matters; a clear rationale exists", "en", "V2"),  # piège
    ("le fichier README explique pourquoi le module existe", "fr", "V2"),  # piège
    ("tests exist", "en", "V2"),  # piège : existence de tests, aucun verdict
    ("des tests existent", "fr", "V2"),  # piège
    ("il existe des tests", "fr", "V2"),  # piège
    # --- Revue S1 (W3-05) : adjectifs de jugement qui ne sont pas des verdicts
    ("tests are clean and readable", "en", "V2"),  # piège
    ("the test code is clean", "en", "V2"),  # piège
    ("tests are valid", "en", "V2"),  # piège : même statut que « clean »
    ("les tests correspondent aux besoins métier", "fr", "V2"),  # piège
    ("the schema matches the product vision", "en", "V2"),  # piège
    ("the test suite matches our expectations", "en", "V2"),  # piège
    ("le schéma est valide à mon avis", "fr", "V2"),  # piège
    ("le seuil de qualité des tests est raisonnable", "fr", "V2"),  # piège
    ("zéro test flaky ressenti", "fr", "V2"),  # piège
    ("aucun test ne doit être fragile", "fr", "V2"),  # piège
    ("les tests sont nuls", "fr", "V2"),  # piège
    ("tests pass muster", "en", "V2"),  # piège : locution, pas un verdict
    ("les tests passent la barre de qualité attendue par l'équipe", "fr", "V2"),  # piège
    ("pytest est un bon outil", "fr", "V2"),  # piège : outil nommé, pas une commande
    ("pytest is a great framework", "en", "V2"),  # piège
    ("Test ; audit rapport.md ligne R31", "fr", "V2"),  # pointeur de preuve, ni commande ni verdict
)
