<!-- ARCHETYPE: stack/go — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Go backend — architecture hexagonale, tests table-driven, erreurs wrappées. À utiliser dès qu'une demande porte sur un fichier .go — pas pour le frontend TypeScript ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# Go (ex-agent Gopher)

Ancien agent dédié (`go-expert`, faisceau outils identique au généraliste de la pile — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par le généraliste au lieu d'occuper un agent à part.

## Principes

- Architecture hexagonale : ports (interfaces) dans `internal/ports/`, implémentations dans `internal/adapters/` — le domain ne dépend jamais d'un adapter.
- Toute fonction/méthode publique a son test table-driven dans le `_test.go` correspondant.
- Erreurs explicites — jamais `_, err := ...` sans justification ; erreurs wrappées (`fmt.Errorf %w`) ; zéro `panic()` en production.
- `context.Context` propagé sur chaque appel réseau/DB.

## Garde-fou

Migrations DB non réversibles (`DROP TABLE`, `DROP COLUMN`) → afficher l'impact et demander confirmation.

## Implémenter une feature

Lire `shared-context.md` pour les conventions établies → planifier domain → port/interface → adapter/handler → implémenter avec le test table-driven en même temps que le code → `cc-verify.sh --stack go` (build + test + vet).

## Corriger un bug

Écrire ou identifier le test qui prouve le bug → lire la stack trace/logs pour la ligne exacte → corriger → vérifier que le test du bug et les tests existants passent.

## Bug hunt

`go vet ./...` + `staticcheck ./...` → erreurs ignorées (`grep -r "_, err"`) → data races (`go test ./... -race`) → risques de nil pointer → fuites de context dans les goroutines → requêtes/rows sans `.Close()`. Corriger par vague, CC VERIFY après chacune.

## Performance

Mesurer d'abord (`go test ./... -bench=. -benchmem`), profiler si besoin (`pprof`), optimiser en conservant la lisibilité, remesurer.
