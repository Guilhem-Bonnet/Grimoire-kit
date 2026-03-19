# Template: audit-report

> Audit systématique avec rapport tabulaire et scoring.
> Utilisé par 7 prompts (14%) chez Vault, Hawk, Phoenix, Sentinel.

## Structure

```
{{agent_alias}} lance un audit {{audit_domain}}.

RAISONNEMENT :
1. SCANNER : inventorier {{scan_targets}}.
2. POUR CHAQUE : évaluer selon {{evaluation_criteria}}.
3. CLASSIFIER par {{severity_levels}}.
4. {{#if auto_fix}}CORRIGER les éléments {{auto_fix_threshold}} directement.{{/if}}
5. PRODUIRE le rapport.

FORMAT DE SORTIE :
## Audit {{audit_domain}} — {{date}}
| {{table_columns}} |
|{{table_separators}}|
...

{{#if scoring}}
### Score global : X/10
{{/if}}

Résumer : "X conformes, Y avertissements, Z critiques".
```

## Variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `audit_domain` | Domaine audité | Sécurité, Observabilité, Backup |
| `scan_targets` | Cibles à scanner | secrets, métriques, agents |
| `evaluation_criteria` | Critères d'évaluation | chiffrement, rétention, complétude |
| `severity_levels` | Niveaux de sévérité | critique/warning/ok |
| `table_columns` | Colonnes du rapport | Item \| Statut \| Détail |
| `auto_fix` | Correction auto ? (bool) | true/false |
| `auto_fix_threshold` | Seuil de correction auto | critiques uniquement |
| `scoring` | Inclure un score ? (bool) | true/false |

## Prompts utilisant ce template

| Prompt ID | Agent | audit_domain |
|-----------|-------|-------------|
| security-audit | Vault | Secrets, ports, permissions |
| observability-audit | Hawk | Métriques, logs, alertes |
| audit-backup | Phoenix | Couverture backup, RPO |
| audit-single | Sentinel | Qualité d'un agent |
| audit-all | Sentinel | Comparaison tous agents |
| scope-analysis | Sentinel | Chevauchements de scope |
| protocol-check | Sentinel | Symétrie protocoles |
