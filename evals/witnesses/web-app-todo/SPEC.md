# Témoin `web-app-todo` — spécification de la baseline

> Statut : spécification de cadrage. Le code n'est pas encore construit.
> Cette spec définit **exactement** ce que la baseline doit contenir pour que
> les 8 tâches de `evals/tasks/web-app-todo.yaml` soient exécutables et
> mesurables selon `docs/evals-protocol.md`.

## But

La baseline est une petite application de todo **volontairement imparfaite** :
elle sert de point de départ identique aux deux bras (`governed` / `baseline`).
Sa qualité importe moins que sa **reproductibilité** et la présence des défauts
que les tâches doivent corriger.

Principe directeur : **minimal mais réel**. Assez de code pour que « tests
verts » et « régression introduite » soient des signaux fiables ; pas une
vitrine. Viser ~1 500-2 500 lignes au total, tests inclus.

## Stack (figée)

| Couche | Techno | Contrainte |
|---|---|---|
| Frontend | React (Vite, TypeScript) | Vitest pour les tests |
| API | Go (net/http ou chi, au choix du builder) | `go test`, `go vet` |
| Base | PostgreSQL | migrations SQL numérotées, réversibles |
| Orchestration | `docker-compose` (postgres + api) | démarrage en une commande |

`go.mod` doit être épinglé sur **Go 1.22** (pas 1.23) — voir tâche
`migrate-go-version`.

## Surface fonctionnelle minimale

### Schéma

- `tasks` : `id`, `title`, `done` (bool), `created_at` (timestamptz, **stocké
  en UTC**).
- `tags` : `id`, `name`.
- `task_tags` : `task_id`, `tag_id` (n-n).

Seed : ~10 tâches, ~4 tags, quelques associations — assez pour que le N+1 et le
tri soient observables.

### Endpoints API

| Méthode | Route | Rôle | État de départ imposé |
|---|---|---|---|
| GET | `/tasks` | Liste + tags de chaque tâche | **N+1 intentionnel** : une requête tags par tâche |
| POST | `/tasks` | Créer une tâche | validation **inline** dans le handler |
| PUT | `/tasks/:id` | Modifier titre/done | validation **inline** |
| POST | `/tasks/:id/complete` | Marquer terminée (unitaire) | pas de version batch |

Pas de rate-limit. Pas de package de validation. Pas de champ échéance.

### SPA

- Liste des tâches avec leurs tags et l'horodatage.
- Formulaire d'ajout, case « done » par tâche.
- **Horodatage affiché en UTC brut** (bug `fix-timezone-display`).
- **Appels `fetch` directs dispersés** dans les composants, pas de client
  centralisé (dette `refactor-api-client`).

### Tests de base (fournis avec la baseline)

Suffisamment pour que la suite serve de filet :
- API : création/liste/complete (chemin nominal + une erreur de validation).
- Front : rendu de la liste, ajout d'une tâche.

Ces tests doivent **passer** sur la baseline. Ils constituent le signal
« régression introduite » quand une tâche les casse.

## Mapping tâche → surface (invariant de conception)

Chaque tâche doit avoir son point d'ancrage présent dès la baseline :

| Tâche | Type | Ce que la baseline DOIT fournir |
|---|---|---|
| `feat-due-dates` | feature | schéma `tasks` migrable, endpoints create/update, liste SPA triable |
| `feat-bulk-complete` | feature | endpoint `complete` unitaire + sélection UI à étendre |
| `fix-timezone-display` | bugfix | **bug présent** : affichage UTC brut ; stockage UTC correct (à ne pas changer) |
| `fix-n-plus-one` | bugfix | **bug présent** : `GET /tasks` fait une requête tags par tâche ; test de comptage de requêtes possible |
| `refactor-handlers` | refactor | **dette présente** : validation inline dans les handlers, tests de contrat existants |
| `refactor-api-client` | refactor | **dette présente** : `fetch` directs dans plusieurs composants |
| `migrate-go-version` | migration | `go.mod` sur 1.22 + au moins une API dépréciée en 1.23 utilisée |
| `sec-rate-limit` | security | endpoints d'écriture identifiables, aucun rate-limit initial |

> Règle : ne jamais « pré-corriger » un défaut listé ci-dessus. Un défaut absent
> rend la tâche correspondante intestable et invalide le bras `baseline`.

## Emplacement et versionnement

- Code du témoin : `evals/witnesses/web-app-todo/app/` (versionné avec les
  suites de tâches — reproductibilité exigée par le protocole §Reproductibilité).
- La baseline est un **état figé** : un tag ou un commit-pin la référence dans
  chaque rapport de campagne.
- Les runs partent tous d'une **copie propre** de cet état (voir
  `RUN-PROTOCOL.md`).

## Critère « prêt pour evals »

La baseline est prête quand :

1. `docker-compose up` démarre postgres + API sans intervention.
2. `cd app/api && go test ./... && go vet ./...` : vert.
3. `cd app/web && npm test` : vert.
4. Les 8 points d'ancrage du tableau ci-dessus sont vérifiables (les 2 bugs
   reproductibles, les 2 dettes visibles, Go 1.22, pas de rate-limit,
   pas d'échéance, `complete` unitaire).
5. Un `README.md` documente le démarrage et **liste les défauts intentionnels**
   (pour qu'un mainteneur ne les « corrige » pas par mégarde).
