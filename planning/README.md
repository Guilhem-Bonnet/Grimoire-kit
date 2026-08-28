# Planning — plans cibles, backlogs, inventaires

Ce dossier contient les documents de travail du projet : plans cibles,
trajectoires, backlogs, inventaires d'usage, corpus de comparaison. Ils
décrivent ce qu'on a l'intention de faire et où on en est, pas ce que le
produit fait aujourd'hui.

**Ils ne sont pas publiés.** Le manuel construit par mkdocs ne lit que `docs/`.
Ces documents vivaient auparavant dans `docs/`, donc dans la documentation
utilisateur : quelqu'un venu apprendre à se servir du kit tombait sur un plan
cible de 990 lignes et sur un backlog trimestriel. Un lecteur ne peut pas
distinguer une capacité livrée d'une intention quand les deux sont rangées au
même endroit.

La règle de tri :

| Le document décrit… | Il va dans… |
| --- | --- |
| ce que le produit fait, et comment s'en servir | `docs/` |
| la norme que le produit applique | `docs/standard/` |
| ce qu'on a l'intention de faire, ou l'état d'un chantier | `planning/` |

Une décision arrêtée n'est pas un plan : elle devient une ADR dans
`docs/adr-*.md`, où elle est publiée.

## Contenu

| Document | Nature |
| --- | --- |
| `agentic-standard-final-target.md` | cible maximale du standard agentique |
| `agentic-standard-target-plan.md` | trajectoire projet vers cette cible |
| `agentic-standard-target-architecture.md` | architecture de convergence visée |
| `agentic-standard-benchmark-corpus-2026Q2.md` | comparaison au corpus de frameworks, écarts relevés |
| `travaux-inacheves-2026Q2.md` | backlog trimestriel |
| `cloud-agent-governance-target-plan.md` | plan cible de gouvernance des agents cloud (arrêté en ADR-004) |
| `memory-os-roadmap.md` | roadmap Memory OS |
| `resorption-bash.md` | inventaire et plan de résorption de `grimoire-init.sh` |
| `framework-tools-inventory.md` | inventaire d'usage de `framework/tools/` |
| `product-quality-target-plan.md` | plan cible de qualité produit |
| `flow-engine-target-plan.md` | plan cible du moteur de flows |
