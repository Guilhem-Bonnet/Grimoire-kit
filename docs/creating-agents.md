<p align="right"><a href="../README.md">README</a></p>

# <img src="../assets/icons/team.svg" width="32" height="32" alt=""> Créer un agent — Guide

> Ce guide répond au **comment**. Avant de commencer, vérifiez le **quand** dans
> [Doctrine — quand créer un artefact](artifact-doctrine.md) : un agent n'est justifié que si la
> décision est impossible à écrire d'avance et qu'il a une frontière d'outils qui lui est propre.
> Sinon, un skill ou un prompt suffit et coûte moins cher.

## <img src="../assets/icons/team.svg" width="28" height="28" alt=""> Voie rapide — Agent Forge (BM-52)

`agent-forge.py` génère un scaffold rempli intelligemment depuis un besoin textuel ou des gaps détectés automatiquement.

```bash
# Depuis une description textuelle
bash grimoire-init.sh forge --from "je veux un agent pour les migrations de base de données"

# Depuis les requêtes inter-agents non résolues (shared-context.md)
bash grimoire-init.sh forge --from-gap

# Depuis les failures Grimoire_TRACE sans agent propriétaire
bash grimoire-init.sh forge --from-trace

# Lister les proposals en attente de review
bash grimoire-init.sh forge --list

# Installer après review du [TODO]
bash grimoire-init.sh forge --install db-migrator
```

**Pipeline :**
```
forge --from "..."
  → _grimoire-output/forge-proposals/agent-[tag].proposed.md
  → [ Réviser les [TODO] : identité, prompts métier ]
  → forge --install [tag]
  → Sentinel [AA] audit qualité
```

> **Note :** Le scaffold couvre la structure, les outils, l'icône et les protocoles inter-agents. 
> Les prompts métier (sections `[TODO]`) nécessitent votre connaissance du domaine.

> **Conseil budget :** Après avoir installé un nouvel agent, vérifiez qu'il ne sature pas la fenêtre de contexte :
> ```bash
> bash grimoire-init.sh guard --agent [id-de-votre-agent] --detail --suggest
> ```
> Seuil recommandé : < 40% de la fenêtre du modèle cible.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/team.svg" width="28" height="28" alt=""> Anatomie d'un agent Grimoire Custom

Un agent est un fichier Markdown structuré avec des balises XML qui définissent sa personnalité, ses capacités et ses actions.

```
mon-agent.md
├── Persona (identité, principes, règles)
├── Activation (comment démarrer)
├── Menu (actions numérotées)
└── Prompts (instructions détaillées par action)
```

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/team.svg" width="28" height="28" alt=""> Créer un agent de zéro

### 1. Copier le template

```bash
cp _grimoire/kit/agents/custom-agent.tpl.md \
   _grimoire/kit/agents/mon-nouvel-agent.md
```

### 2. Remplir les variables

| Variable | Description | Exemple |
|----------|-------------|---------|
| `{{agent_name}}` | Nom affiché | "Gardien" |
| `{{agent_icon}}` | Emoji | "" |
| `{{agent_tag}}` | Tag court (minuscule) | "gardien" |
| `{{agent_role}}` | Rôle en une phrase | "Sécurité applicative" |
| `{{domain}}` | Domaine d'expertise | "sécurité, authentification, RBAC" |
| `{{learnings_file}}` | Nom du fichier learnings | "security-app" |
| `{{domain_word}}` | Mot-clé pour decisions-log | "sécurité" |
| `{{use_when}}` | Champ obligatoire — situation qui justifie cet agent | "Audit d'un flux OAuth2/RBAC" |
| `{{dont_use_when}}` | Champ obligatoire — cas hors périmètre | "Sécurité infra, voir infra-ops" |
| `{{tools}}` | Champ obligatoire — capacités (peut-il éditer, exécuter ?) | "read, edit" |
| `{{tool_boundary}}` | Champ obligatoire — périmètre fin, chemins/commandes propres | "Endpoints /api/auth/*, pas d'accès infra" |

`tools` et `tool_boundary` ne se devinent pas par copie du voisin : un agent qui hérite des mêmes
capacités que le généraliste sans périmètre distinct n'a pas de frontière propre au sens de la
doctrine — c'est un signal pour le retirer, pas un champ à remplir par défaut.

### 2b. Configurer model_affinity (optionnel)

Déclarez les besoins LLM de votre agent dans le frontmatter YAML :

```yaml
---
name: "mon-agent"
description: "Mon Agent — Alias"
model_affinity:
  reasoning: high       # low | medium | high | extreme
  context_window: medium  # small (≤32K) | medium (≤128K) | large (≤200K) | massive (>1M)
  speed: fast           # fast | medium | slow-ok
  cost: medium          # low | medium | any
---
```

| Axe | Quand utiliser `extreme`/`massive` | Quand utiliser `low`/`small` |
|---|---|---|
| **reasoning** | Debug deep, audit sécurité, architecture | CRUD, mémoire, monitoring |
| **context_window** | Scan codebase entier, refactoring large | Tâches ciblées, corrections ponctuelles |
| **speed** | Boucles rapides fix→test, CI | Décisions stratégiques, audits |
| **cost** | Tâches critiques, sécurité | Tâches répétitives, consolidation |

Vérifiez la recommandation : `bash grimoire-init.sh guard --recommend-models`

**Ce que `grimoire host sync` en fait par hôte :** Claude Code croise `reasoning`
et `cost` pour choisir un modèle (`reasoning: high` → `opus` quoi qu'il arrive ;
sinon `cost: low` → `haiku` ; sinon `reasoning: low` → `haiku` ; sinon `inherit`,
le modèle de la session). Le concierge illustre le premier cas : raisonnement
élevé pour trier juste, mais invoqué à chaque tour — `cost: low` ne le
rétrograde pas. Copilot, lui, ne reçoit pas cette affinité : son contrat
`.github/agents/*.agent.md` n'accepte qu'un nom de modèle explicite ou une
liste de repli, sans équivalent documenté de `inherit` ; `grimoire host sync
--host copilot` ne devine donc aucun nom de modèle et signale l'écart comme
dégradation plutôt que de le taire. Le fichier de l'agent d'entrée émis pour
Claude Code porte en plus une « Politique de dispatch » (issue #329) : le
modèle d'un sous-agent suit la classe de vérifiabilité de sa tâche
(V0 → `haiku`, V1 → `sonnet`, V2 → le modèle de la session) et chaque
sous-agent doit clore sa réponse par un bloc ```grimoire-uncertainties``` ;
Copilot documente la même règle sans nom de modèle dans son README de
surface, pour la raison ci-dessus.

**Ce que `context:` change à l'émission (issue #379) :** sans cette clé, le
fichier d'agent émis dit toujours à l'hôte de lire `_grimoire/_memory/shared-
context.md` s'il existe — le comportement d'avant #379, inchangé bit à bit.
Un agent qui déclare `context:` remplace cette étape par une instruction qui
ne nomme que ses propres chemins déclarés, vérifiés à l'existence au moment
de la collecte : le contexte partagé du projet disparaît de son activation
s'il ne l'a pas demandé. C'est un rétrécissement volontaire, jamais une
amputation par défaut — un agent muet sur `context:` ne perd rien.

### 3. Écrire l'identité

La section `<identity>` est la plus importante. Elle doit :
- Décrire l'expertise spécifique au projet
- Mentionner les outils/technologies maîtrisés
- Référencer `shared-context.md` pour le contexte d'infra

```markdown
<identity>
Tu es Gardien, expert en sécurité applicative pour le projet {{project_name}}.
Tu maîtrises OAuth2/OIDC, RBAC, rate-limiting, WAF, et les headers de sécurité.
Consulte shared-context.md pour l'architecture complète.
</identity>
```

### 4. Définir les prompts

Chaque action du menu pointe vers un `<prompt>`. Structure recommandée :

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
Vérifier que le endpoint /api/auth/login :
- Accepte uniquement POST
- Rate-limité à 5 tentatives/min
- Retourne 401 avec body générique (pas de leak d'info)
</example>
</prompt>
```

### 5. Enregistrer l'agent

Ajouter dans `_grimoire/kit/agent-manifest.csv` :

```csv
"mon-nouvel-agent","Gardien","Sécurité Applicative","shield-pulse","security-app","custom","_grimoire/kit/agents/mon-nouvel-agent.md"
```

Ajouter dans `_grimoire/_memory/shared-context.md` (table équipe) :

```markdown
| mon-nouvel-agent | Gardien | shield-pulse | Sécurité applicative |
```

Créer le fichier learnings :

```bash
echo "# Learnings — Gardien" > _grimoire/_memory/agent-learnings/security-app.md
```

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Clause "Use when"

Depuis la doctrine d'emploi (voir [artifact-doctrine.md](artifact-doctrine.md)), la clause n'est
plus une suggestion : `use_when`, `dont_use_when`, `tools` et `tool_boundary` sont des champs
**obligatoires** du frontmatter de chaque agent livré par le kit —
`tests/unit/test_artifact_employment_clause.py` échoue sinon.

```yaml
use_when: "Situation précise où invoquer cet agent."
dont_use_when: "Cas hors périmètre — nommez l'agent compétent si possible."
tools: "read, edit, execute — la capacité grossière de l'agent."
tool_boundary: "Périmètre fin — chemins, commandes, propres à cet agent."
```

Le commentaire `USE WHEN` / `DON'T USE WHEN` en en-tête reste utile en complément : il donne à
l'orchestrateur une version plus riche, multi-lignes, pour le routage.

```markdown
<!--
USE WHEN:
- [Situation ou besoin 1]
- [Situation ou besoin 2]
- [Situation ou besoin 3]
DON'T USE WHEN:
- [Cas hors-périmètre]
-->
```

**Exemples :**

```markdown
<!--
USE WHEN:
- Besoin de diagnostiquer un problème technique récurrent
- Besoin de preuves d'exécution avant de claimer "done"
- Fix qui a échoué plusieurs fois sans explication claire
DON'T USE WHEN:
- Exploration exploratoire (pas de bug précis à corriger)
- Questions de design ou d'architecture (voir Atlas ou Sentinel)
-->
```

Cette clause est lue par l'orchestrateur au moment du routage.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/team.svg" width="28" height="28" alt=""> Personnaliser un agent du kit (override)

Un fichier sous `_grimoire/overrides/agents/<nom>.md` prime sur son homologue
`_grimoire/kit/agents/<nom>.md` — c'est la seule façon sanctionnée de
personnaliser un agent livré par le kit sans le forker en entier. Deux formes
existent (issue #427) : la copie intégrale et l'override **partiel**.

### Copie intégrale

Un fichier qui reprend tout le contenu du fichier kit, avec une ou deux
lignes changées. Fonctionne, mais **masque totalement** les mises à niveau
futures de cet agent : quand le kit refait cet agent (nouveaux `skills:`,
`use_when` reformulé, corps réécrit), la copie ne bouge pas — rien ne
l'indique tant que personne ne compare les deux fichiers à la main.

### Override partiel (`extends: kit`)

```yaml
---
extends: kit
model_affinity:
  reasoning: high
---
```

Un override partiel ne redéfinit que les champs de frontmatter qu'il liste
explicitement, parmi : `model_affinity`, `context`, `skills`, `tools`,
`use_when`, `dont_use_when`, `max_turns`, `description`, `tool_boundary`.
Tout le reste — le corps de l'agent compris — est lu depuis le fichier kit de
même nom à chaque `grimoire host sync` / `grimoire doctor` / `grimoire up`.
Assigner un skill ou modifier une clause d'emploi depuis le cockpit produit
désormais un override partiel de ce type dès qu'un agent kit du même nom
existe ; une copie intégrale ne reste automatique que pour un agent sans
contrepartie kit (répertoire hérité, ou identité forkée volontairement).

Une valeur explicitement vidée (`skills: []`, `use_when: ""`, un skill
retiré) reste vide même si le kit, lui, déclare une valeur pour ce champ —
une clé absente du tout, en revanche, suit le kit. `extends: kit` sans agent
kit de même nom refuse au chargement, nommant l'agent : rien ne retombe
silencieusement sur une persona vide.

### Dérive : quand le kit a changé sous une copie intégrale

Chaque override écrit par le kit (cockpit, `grimoire agent override
convert`) enregistre `kit_source_hash:` — une empreinte du fichier kit au
moment de l'écriture. `grimoire doctor` et le cockpit comparent cette
empreinte à celle du fichier kit actuel :

- **absente** (override antérieur à cette issue) → INFO « empreinte
  inconnue, revoir à la main » ;
- **différente** → WARN nommant l'agent, avec un résumé (sections de
  frontmatter ajoutées/retirées côté kit, delta de lignes du corps pour une
  copie intégrale ; champs figés pour un override partiel). Jamais FAIL : une
  dérive d'override est une dette à revoir, pas une panne.

`grimoire up` liste, après avoir rafraîchi le palier kit, les overrides à
revoir — sans jamais les toucher. Trois choix, à faire à la main :

```bash
# Voir ce que donnerait la conversion sans rien écrire
grimoire agent override convert <nom> --dry-run

# Convertir réellement une copie intégrale en override partiel équivalent
grimoire agent override convert <nom>
```

`convert` refuse — en nommant les lignes qui diffèrent — quand le corps de
la copie a été modifié par rapport au kit : convertir fusionnerait alors du
texte, ce que cette commande ne fait jamais. Le troisième choix, retirer
l'override, se fait en supprimant le fichier `_grimoire/overrides/agents/<nom>.md`.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/lightbulb.svg" width="28" height="28" alt=""> Bonnes pratiques

### Scope strict
Chaque agent doit avoir un périmètre clair. Si deux agents se chevauchent, c'est un signe qu'il faut fusionner ou clarifier les frontières.

### Exemples concrets
Les `<example>` dans les prompts sont essentiels. Un agent sans exemples produit des résultats génériques. Incluez des commandes, chemins et valeurs spécifiques à votre projet.

### Keywords pour le dispatch
Le routage lit la clause `USE WHEN` / `DON'T USE WHEN` de l'agent lui-même (voir plus haut), pas `project-context.yaml` : `agents.custom_agents` n'est qu'une liste de noms suivie par `grimoire add`/`grimoire remove`, elle n'influence ni le dispatch ni le chargement. Décrivez donc les cas d'usage directement dans le fichier de l'agent :

```markdown
<!--
USE WHEN:
- Question de sécurité applicative (oauth, jwt, rbac, permissions, headers)
DON'T USE WHEN:
- Sécurité infrastructure (voir infra-ops)
-->
```

### Test de l'agent

```bash
# Vérifier la cohérence
python _grimoire/kit/memory/maintenance.py context-drift

# Tester le dispatch
grimoire memory search "vérifier la sécurité des endpoints API"
```
