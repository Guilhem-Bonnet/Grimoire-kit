# Template: troubleshoot

> Debug et diagnostic de composants défaillants.
> Utilisé par 3 prompts (6%) chez Flow, Helm, Forge.

## Structure

```
{{agent_alias}} debug {{component_type}}.

RAISONNEMENT :
1. IDENTIFIER : quel {{component_type}} a un problème ?
2. DIAGNOSTIQUER (séquence) :
   {{#each diagnostic_commands}}
   - {{this}}
   {{/each}}
3. CAUSE ROOT : identifier le problème fondamental.
4. CORRIGER : appliquer le fix.
5. VALIDER : confirmer la résolution.

{{#if common_causes}}
CAUSES FRÉQUENTES :
{{#each common_causes}}
- {{this}}
{{/each}}
{{/if}}
```

## Prompts utilisant ce template

| Prompt ID | Agent | component_type |
|-----------|-------|---------------|
| debug-pipeline | Flow | Workflow CI/CD |
| troubleshoot-ops | Helm | Pod/Service K8s |
| docker-ops (crash) | Forge | Container Docker |
