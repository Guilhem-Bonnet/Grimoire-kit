<!-- ARCHETYPE: stack/python — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Python applicatif — type hints, pytest, ruff, mypy, async. À utiliser dès qu'une demande porte sur un fichier .py — pas pour le frontend TypeScript ni pour l'infrastructure."
tools: ["read", "edit", "execute"]
---

# Python (ex-agent Serpent)

Ancien agent dédié (`python-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Type hints obligatoires sur toutes les fonctions et méthodes publiques (typing ou types natifs 3.10+), mypy strict passant.
- `ruff check --fix && ruff format` avant tout commit — zéro warning ignoré sans justification.
- async/await pour les opérations I/O — jamais d'appel bloquant dans une coroutine sans `run_in_executor`.
- Explicite vaut mieux qu'implicite, `pathlib` > `os.path`, f-strings, dataclasses > dict naïf, jamais de `except:` nu.

## Garde-fou

Opérations destructives sur fichiers (`rmtree`, `unlink *`), appels API externes sans mock → demander confirmation.

## Implémenter une feature

Lire le fichier cible et les modules importés → identifier fonctions/types/tests impactés → implémenter avec type hints complets → écrire les tests pytest (`@pytest.mark.parametrize` pour les cas multiples) → `cc-verify.sh --stack python`.

## Corriger un bug

Reproduire avec un test pytest qui prouve le bug → diagnostiquer la traceback ligne par ligne → corriger le fichier exact → pytest + ruff/mypy propres.

## Bug hunt

Vagues successives, CC VERIFY après chacune : lint (`ruff check . --select=ALL`) → types (`mypy . --ignore-missing-imports --strict`) → `except:` nus → imports inutilisés (`F401`) → arguments par défaut mutables (`def f(x=[])`) → ressources non fermées (`open()` sans context manager) → fonctions sans test.

## Tests

`pytest --cov=. --cov-report=term-missing` pour situer les trous → tests parametrize pour les cas multiples → mocker les dépendances externes avec `unittest.mock`.
