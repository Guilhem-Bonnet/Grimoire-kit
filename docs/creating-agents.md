# :material-account-hard-hat-outline: Créer un agent — Guide complet

---

## :material-speedometer: Voie rapide — UDF Dynamic Factory

> **Recommandé en premier.** Avant de créer un agent manuellement, vérifiez si le SOG peut le générer dynamiquement via l'UDF.

Décrivez simplement ce dont vous avez besoin au Grimoire Master :

```
"J'ai besoin d'un agent expert en migrations de base de données"
```

Le SOG évalue automatiquement :

1. **Gap detection** — aucun agent existant ne couvre ce besoin
2. **Triage de durabilité** — calcule le score éphémère/permanent
3. **Dispatch vers agent-builder** — création automatique en mode Rapid ou Full
4. **Déploiement immédiat** — l'agent est disponible dès la sauvegarde

### Modes de création UDF

| Mode | Score durabilité | Sortie | Durée |
|---|---|---|---|
| **Rapid (éphémère)** | < 3 | `_dyn-{slug}.agent.md` (expire 7j) | Immédiat |
| **Full (permanent)** | ≥ 3 | `{slug}.agent.md` + prompt dédié | Court terme |

### Promotion automatique

Un agent éphémère réutilisé **3 fois ou plus** est automatiquement promu en agent permanent. Le SOG vous notifie au prochain tour.

!!! tip "Quand préférer la création manuelle ?"
    Utilisez la voie manuelle quand vous avez besoin d'un contrôle fin sur les prompts métier, les exemples spécifiques au projet, ou l'intégration dans un workflow existant.

---

## :material-cog: Agent Forge — Scaffold assisté

`agent-forge` génère un scaffold depuis un besoin textuel ou des gaps détectés automatiquement.

```bash
# Depuis une description textuelle
grimoire forge --from "agent pour les migrations de base de données"

# Depuis les gaps détectés (shared-context.md)
grimoire forge --from-gap

# Lister les proposals en attente de review
grimoire forge --list

# Installer après review
grimoire forge --install db-migrator
```

Pipeline de génération :

```
forge --from "..."
  → _grimoire-output/forge-proposals/agent-[tag].proposed.md
  → Réviser les [TODO] : identité, prompts métier
  → forge --install [tag]
  → Validation qualité automatique
```

!!! warning "Les prompts métier sont à vous"
    Le scaffold couvre la structure, les outils, les protocoles inter-agents. Les sections `[TODO]` (prompts spécifiques au domaine) nécessitent votre connaissance du projet.

---

## :material-file-document-outline: Anatomie d'un agent

Un agent Grimoire est un fichier Markdown structuré :

```
mon-agent.md
├── Frontmatter YAML         (name, description, model routing)
├── Persona / Identity       (nom, expertise, règles)
├── Activation               (comment démarrer la session)
├── Menu                     (actions numérotées)
└── Prompts                  (instructions détaillées par action)
```

---

## :material-hammer-wrench: Créer un agent de zéro

### 1. Copier le template

```bash
cp .github/agents/_templates/permanent-agent.tpl.md \
   .github/agents/mon-agent.agent.md
```

### 2. Remplir les variables

| Variable | Description | Exemple |
|---|---|---|
| `{{agent_name}}` | Nom affiché | `Gardien` |
| `{{agent_tag}}` | Tag court (minuscule, kebab) | `gardien` |
| `{{agent_role}}` | Rôle en une phrase | `Sécurité applicative` |
| `{{domain}}` | Domaine d'expertise | `sécurité, authentification, RBAC` |
| `{{persona}}` | Persona courte | `Expert sécurité pragmatique` |

### 3. Configurer le routing modèle

Déclarez le profil de routing dans le frontmatter :

```yaml
---
name: gardien
description: Gardien — Sécurité applicative
routing_profile: deep_reasoning
---
```

Profils disponibles :

| Profil | Adapté pour |
|---|---|
| `deep_reasoning` | Audit, architecture, décisions critiques |
| `general_code` | Implémentation, tests, debug |
| `writing_structured` | Docs, PRD, YAML, prompts |
| `fast_iter` | Checks rapides, brainstorming |

### 4. Écrire l'identité

La section `<identity>` est la plus importante — elle définit le comportement de l'agent :

```markdown
<identity>
Tu es Gardien, expert en sécurité applicative pour le projet {{project_name}}.
Tu maîtrises OAuth2/OIDC, RBAC, rate-limiting, WAF et les headers de sécurité.
Consulte shared-context.md pour l'architecture complète avant toute intervention.

Règles :
- Jamais de secret hardcodé dans un rapport
- Chaque vulnérabilité → CVE ou OWASP ref associée
- CC obligatoire sur tout fix sécurité
</identity>
```

### 5. Définir les prompts

Chaque action du menu pointe vers un `<prompt>` :

```markdown
<prompt id="audit-auth" title="Audit Authentification">
### Audit du système d'authentification

**Étapes :**
1. Scanner les endpoints d'authentification
2. Vérifier la configuration JWT/OAuth2
3. Tester les flux de login/logout
4. Vérifier les rate-limits

**Output :**
- Rapport dans decisions-log.md
- Actions correctives si trouvées

<example>
Vérifier que /api/auth/login :
- Accepte uniquement POST
- Rate-limité à 5 tentatives/min
- Retourne 401 avec body générique (pas de leak d'info)
</example>
</prompt>
```

### 6. Clause "Use when"

Ajoutez une clause `USE WHEN` en en-tête — elle guide le dispatch SOG :

```markdown
<!--
USE WHEN:
- Audit de sécurité applicative ou infrastructure
- Review de code avant merge (endpoints sensibles)
- Incident de sécurité en cours

DON'T USE WHEN:
- Questions de design ou d'architecture (voir architect)
- Exploration exploratoire sans risque identifié
-->
```

### 7. Enregistrer l'agent

Ajoutez dans `_grimoire-runtime/_config/agent-manifest.csv` :

```csv
"gardien","Gardien","Sécurité Applicative","security","_dyn","gardien.agent.md"
```

Ajoutez dans `project-context.yaml` pour le dispatch contextuel :

```yaml
agents:
  custom_agents:
    - name: gardien
      domain: "Sécurité applicative"
      keywords: "oauth jwt rbac auth login permission security headers csp cors"
```

---

## :material-lightbulb-on-outline: Bonnes pratiques

=== "Scope strict"

    Chaque agent doit avoir un périmètre clair. Si deux agents se chevauchent → fusionner ou clarifier les frontières. Un agent qui fait tout ne fait rien bien.

=== "Exemples concrets"

    Les `<example>` dans les prompts sont essentiels. Un agent sans exemples produit des résultats génériques. Incluez des commandes, chemins et valeurs spécifiques à **votre** projet.

=== "Mémoire dédiée"

    Créez un fichier learnings dédié :
    ```bash
    echo "# Learnings — Gardien" > \
      _grimoire-runtime/_memory/agent-learnings/security-app.md
    ```

=== "Test du dispatch"

    Vérifiez que le SOG route correctement vers votre agent :
    ```bash
    # Le SOG doit détecter "gardien" pour cette requête
    grimoire dispatch "vérifier la sécurité des endpoints API"
    ```

=== "Budget contexte"

    Après installation, vérifiez le budget fenêtre :
    ```bash
    grimoire guard --agent gardien --detail --suggest
    # Seuil recommandé : < 40% de la fenêtre du modèle cible
    ```

---

## :material-link: Voir aussi

- [Concepts — SOG et dispatch](concepts.md#sog-smart-orchestrator-gateway)
- [Concepts — UDF Dynamic Factory](concepts.md#udf-unified-dynamic-factory)
- [Référence YAML](grimoire-yaml-reference.md)
- [Taxonomie des workflows](workflow-taxonomy.md)
