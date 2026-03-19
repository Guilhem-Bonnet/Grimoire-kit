# Template: compliance-check

> Vérification de conformité et hardening.
> Utilisé par 3 prompts (6%) chez Vault.

## Structure

```
{{agent_alias}} vérifie la conformité {{standard_name}}.

RAISONNEMENT :
1. SCANNER : état actuel de {{scan_scope}}.
2. COMPARER avec {{standard_name}} — identifier les écarts.
3. CORRIGER : appliquer les remédiations.
4. VALIDER : re-scanner pour confirmer conformité.

RAPPORT :
## Conformité {{standard_name}} — {{date}}
| Contrôle | Attendu | Actuel | Statut |
|----------|---------|--------|--------|
```

## Prompts utilisant ce template

| Prompt ID | Agent | standard_name |
|-----------|-------|--------------|
| tls-hardening | Vault | TLS best practices |
| system-hardening | Vault | CIS / hardening OS |
| firewall-ops | Vault | Politique pare-feu |
