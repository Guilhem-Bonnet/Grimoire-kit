---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Multi-agents en cloud, clonage pour la charge, protocole d'agent à agent (2026-09)

> Reprise, au titre de R72 (tâche `GAO-r72-converti-001`), de la note de veille de l'issue
> [#376](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/376), fermée le 2026-10-20, dont le texte ne vivait que dans l'issue.
> Question de Guilhem du 2026-09-10 ; texte repris le 2026-10-25 depuis l'issue, **sans ajout de fait nouveau** : aucune source externe n'a été rouverte pour cette reprise.

## Question

En cloud, sait-on s'adapter à du multi-agents pour la charge, par exemple en clonant plusieurs fois le même agent ? Est-ce la chose à faire ? Faut-il penser un protocole d'agent à agent ?

## Sur le clonage d'agents pour la charge

L'unité de parallélisation de ce système n'est pas l'agent, c'est **la tâche**. Le moteur de flow et le dispatch remettent une tâche à un exécuteur ; deux tâches indépendantes peuvent partir en parallèle vers deux exécuteurs, qui peuvent être le même agent. Cloner un agent n'ajoute rien à cela : c'est déjà ce que fait le dispatch quand on lui donne deux tâches.

Le clonage n'a de sens que pour un agent **sans état** sur des tâches indépendantes (un fuzzer, un relecteur). Il n'en a aucun pour un orchestrateur ou un agent à contexte partagé, où deux clones divergeraient sans le savoir.

## Pourquoi ce n'est pas la chose à faire maintenant

Le goulot n'est pas la charge, c'est la qualité : vingt et un agents sur trente-trois étaient indiscernables au moment de la note, et les gardes qui manquaient venaient d'être posées. Paralléliser un système dont les agents sont indifférenciés multiplie l'indifférencié, plus vite. La charge devient un sujet quand les agents sont distincts, mesurés, et que l'instrumentation montre une file de tâches qui attend. (Chiffres de la note du 2026-09-10 ; non recomptés ici.)

## Sur un protocole d'agent à agent

Un tel protocole sert à faire dialoguer des agents de fournisseurs différents, exposés comme services. Or le kit n'appelle aucun modèle : il émet des configurations d'hôtes, et ses agents vivent **dans** un hôte. Les exposer comme services serait une autre architecture. Pour l'outillage entre agents, le kit a déjà un serveur MCP. Le protocole d'agent à agent redevient une question le jour où un agent Grimoire doit parler à un agent qui n'est pas dans le même hôte, pas avant.

## Ce qui prépare le terrain sans rien coûter

Garder la tâche comme unité de dispatch, ce qui est déjà le cas. Le jour où la charge compte, on parallélise des dispatches, pas des agents, et rien n'est à réécrire.

## Ce que ça change pour le kit

**Rien.** La note est une veille, pas un chantier ; sa conclusion (la tâche reste l'unité de dispatch, pas de clonage, pas de protocole d'agent à agent) n'appelle aucune tâche. Conditions de réouverture, telles que la note les posait : une file de tâches qui attend, mesurée, avec des agents distincts ; ou un agent Grimoire qui doit parler à un agent hors de son hôte. Point à relire un jour, `non vérifié` dans cette reprise : l'état du registre de dispatch (`grimoire registry dispatches`, cité par #740) comme instrument de la file d'attente.

## Sources

- Issue [#376](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/376) — dépôt du kit, texte lu le 2026-10-25 `[doc interne]`
