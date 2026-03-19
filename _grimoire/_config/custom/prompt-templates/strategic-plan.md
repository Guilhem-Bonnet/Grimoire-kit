# Template: strategic-plan

> Documents stratégiques, plans et registres structurés.
> Utilisé par 5 prompts (10%) chez Phoenix, Atlas.

## Structure

```
{{agent_alias}} crée/met à jour le {{document_name}}.

RAISONNEMENT :
1. INVENTORIER : tous les {{inventory_scope}}.
2. POUR CHAQUE : documenter {{attributes_per_item}}.
3. CALCULER : {{kpi_metrics}}.
4. IDENTIFIER les gaps / risques.
5. PRODUIRE dans {{output_path}}.

FORMAT :
## {{document_name}}
### {{section_per_item}}
{{structured_content}}

### Gaps identifiés
- ...
```

## Prompts utilisant ce template

| Prompt ID | Agent | document_name |
|-----------|-------|--------------|
| dr-plan | Phoenix | Plan Disaster Recovery |
| retention-ops | Phoenix | Politique de rétention |
| service-registry | Atlas | Registre des services |
| network-map | Atlas | Carte réseau |
| adr-tracker | Atlas | Architecture Decision Records |
