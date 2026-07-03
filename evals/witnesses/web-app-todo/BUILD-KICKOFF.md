# Kickoff — construction de la baseline `web-app-todo`

> À coller au début d'une **session fraîche** dédiée. Le build de l'app est un
> vrai travail de code (~1 500-2 500 lignes) qui mérite un contexte propre.

## Prompt de démarrage

```text
Construis la baseline du témoin d'évaluation web-app-todo dans
evals/witnesses/web-app-todo/app/, en suivant STRICTEMENT
evals/witnesses/web-app-todo/SPEC.md.

Contraintes non négociables :
- Stack figée : React (Vite + TypeScript, Vitest) / Go 1.22 (net/http ou chi,
  go test + go vet) / PostgreSQL (migrations SQL numérotées réversibles) /
  docker-compose.
- Poser les DÉFAUTS INTENTIONNELS de la SPEC (ne PAS les corriger) :
  1. GET /tasks fait une requête tags par tâche (N+1).
  2. La SPA affiche created_at en UTC brut (pas de conversion fuseau).
  3. Validation inline dans les handlers Go (pas de package dédié).
  4. Appels fetch directs dispersés dans les composants (pas de client centralisé).
  5. go.mod épinglé Go 1.22 + au moins une API dépréciée en 1.23 réellement utilisée.
  6. Aucun rate-limit ; endpoints d'écriture identifiables.
  7. Pas de champ échéance ; endpoint complete unitaire (pas de batch).
- Tests de base fournis et VERTS sur la baseline (filet de régression).
- README.md documentant le démarrage ET listant les 7 défauts intentionnels.

Critère de fin = les 5 points de la section « Critère prêt pour evals » de la
SPEC sont satisfaits. Valide avec docker-compose up, go test ./..., go vet ./...,
npm test avant de conclure.
```

## Découpage suggéré (pour la session build)

1. Squelette + `docker-compose` + migrations + seed → `docker-compose up` vert.
2. API Go : les 4 endpoints avec les défauts 1/3/6/7 posés + tests API verts.
3. SPA React : liste/ajout/complete avec défauts 2/4 posés + tests front verts.
4. Épinglage Go 1.22 + usage d'une API dépréciée en 1.23 (défaut 5).
5. README avec liste explicite des défauts + passe finale des 5 critères.

## Après le build

Enchaîner sur le **pilote** décrit dans `RUN-PROTOCOL.md` (2 tâches × 2 bras ×
3 répétitions) pour valider la mécanique de mesure avant d'engager la campagne
complète (budget LLM à cadrer avec l'utilisateur).
