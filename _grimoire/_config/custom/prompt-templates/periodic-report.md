# Template: periodic-report

> Briefings périodiques et synthèses multi-sources.
> Utilisé par 4 prompts (8%) chez Atlas, Sentinel.

## Structure

```
{{agent_alias}} prépare un {{report_type}}.

RAISONNEMENT :
1. LIRE : {{data_sources}}.
2. SYNTHÉTISER en < {{max_tokens}} tokens.
3. STRUCTURER selon le format ci-dessous.

FORMAT :
## {{report_type}} — {{date}}

### Points clés
- ...

### Métriques
| KPI | Valeur |
|-----|--------|

### Actions recommandées
1. ...
```

## Prompts utilisant ce template

| Prompt ID | Agent | report_type |
|-----------|-------|------------|
| project-status | Atlas | État du projet |
| session-brief | Atlas | Briefing de session |
| quality-report | Sentinel | Rapport qualité agents |
| consolidate-learnings | Atlas | Consolidation des apprentissages |
