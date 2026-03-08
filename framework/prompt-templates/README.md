# Prompt Templates Registry

Templates réutilisables pour les prompts agents Grimoire.
Chaque template utilise des variables `{{var}}` à substituer par l'agent.

## Index

| Template | Usage | Prompts couverts |
|----------|-------|-----------------|
| [operational-mode](operational-mode.md) | Actions IVEV (Identifier→Vérifier→Exécuter→Valider) | 20 (39%) |
| [audit-report](audit-report.md) | Audits avec rapport tabulaire | 7 (14%) |
| [troubleshoot](troubleshoot.md) | Debug et diagnostics | 3 (6%) |
| [compliance-check](compliance-check.md) | Hardening et conformité | 3 (6%) |
| [strategic-plan](strategic-plan.md) | Docs stratégiques et plans | 5 (10%) |
| [periodic-report](periodic-report.md) | Briefings et rapports périodiques | 4 (8%) |
| [dependency-updates](dependency-updates.md) | Audit et mise à jour des dépendances | 4 (8%) |
| [network-troubleshoot](network-troubleshoot.md) | Diagnostic réseau structuré | 3 (6%) |
| [capacity-planning](capacity-planning.md) | Analyse capacité et rightsizing | 4 (8%) |
| [secret-rotation](secret-rotation.md) | Rotation sécurisée des secrets | 4 (8%) |

**Couverture** : 57/66 prompts (86%). Les 9 restants sont spécifiques à leur agent.

## Fragments communs

| Fragment | Usage |
|----------|-------|
| `⚠️ {{action}} → afficher les {{resource_type}} impactées avant exécution` | Actions destructives |
| `VIA INTER-AGENT : [{{src}}→{{dst}}] — {{description}}` | Délégation inter-agent |
| `## {{report_type}} — {{date}}` | Header de rapport daté |
| `curl -sf {{endpoint}}/api/health` | Health check post-action |

## Convention d'utilisation

Les agents n'ont PAS besoin de copier les templates — ils les **référencent** :
```
Suit le template `operational-mode` avec :
- agent_alias = Forge
- domain = Terraform
- ...
```

Les templates servent de **documentation de patterns** et de **guide de cohérence** pour la création de nouveaux prompts.
