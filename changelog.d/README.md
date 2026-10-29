# Fragments de CHANGELOG

Une PR `feat:`, `fix:` ou `perf:` dépose ici **un fichier** `changelog.d/<slug>.md`
au lieu d'écrire en tête de `CHANGELOG.md` : deux PR ne touchent plus la même
ligne et ne se mettent plus en conflit.

Le fichier contient une ou plusieurs puces, au format des entrées de `[Unreleased]` :

```markdown
- fix(missions): le janitor retire un worktree qui ne porte que les sorties du kit (#1014)
```

À la release, `scripts/changelog-assemble.py X.Y.Z` (appelé par `make release`)
range les fragments sous `## [X.Y.Z]` et supprime les fichiers.
Écrire directement sous `[Unreleased]` reste toléré pour les PR déjà ouvertes.
