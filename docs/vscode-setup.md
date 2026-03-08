<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/wrench.svg" width="32" height="32" alt=""> Configuration VS Code — Grimoire Custom Kit

Guide de configuration optimale de VS Code pour travailler avec les agents Grimoire
sans interruptions ni confirmations intempestives.

<img src="assets/divider.svg" width="100%" alt="">


## <img src="assets/icons/clipboard.svg" width="28" height="28" alt=""> Table des matières

1. [Configuration rapide (copier-coller)](#1-configuration-rapide)
2. [Auto-approbation des outils agents](#2-auto-approbation-des-outils-agents)
3. [Commandes terminal — approve tout vs contrôle fin](#3-commandes-terminal)
4. [Fichiers et éditions — supprimer les confirmations](#4-fichiers-et-éditions)
5. [Modèles et rate limits](#5-modèles-et-rate-limits)
6. [Diff editor — timeout algorithme](#6-diff-editor)
7. [Réseau et VPN](#7-réseau-et-vpn)
8. [Référence des commandes à risque](#8-référence-des-commandes-à-risque)

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> 1. Configuration rapide

Ajouter ces settings dans **User Settings** (`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`) :

```jsonc
{
  // ── Copilot Agent — ne jamais bloquer les agents ──────────────────
  "chat.agent.maxRequests": 500,

  // ── Auto-approbation complète des commandes terminal ──────────────
  // Option A : tout approuver (recommandé pour usage solo)
  "chat.tools.terminal.autoApprove": {
    "/.*/": { "approve": true, "matchCommandLine": true }
  },

  // ── Auto-approbation des URLs (fetch par les agents) ──────────────
  "chat.tools.urls.autoApprove": {
    "https://github.com": true,
    "https://*.githubusercontent.com": true,
    "https://hub.docker.com": true
  },

  // ── Diff editor — pas de timeout ──────────────────────────────────
  "diffEditor.maxComputationTime": 0,
  "diffEditor.maxFileSize": 0,

  // ── Modèle fallback (quand rate limit atteint) ────────────────────
  "chat.models.fallback.enabled": true,
  "chat.models.fallback.model": "copilot:gpt-4.1"
}
```

> **Workspace vs User** : Les settings ci-dessus vont dans les **User Settings**
> (globaux). Les settings spécifiques au projet Grimoire sont déjà dans
> `.vscode/settings.json` du kit.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/team.svg" width="28" height="28" alt=""> 2. Auto-approbation des outils agents

Par défaut, VS Code demande confirmation à **chaque** appel d'outil par un agent
(lecture de fichier, recherche, édition, terminal). Pour les agents Grimoire qui
enchaînent 20-50 appels par workflow, c'est inutilisable.

### Approuver tous les outils automatiquement

Dans la conversation Copilot Chat, quand un agent demande la permission
d'utiliser un outil :

1. Cliquer sur **"Continuer"** avec la flèche déroulante
2. Sélectionner **"Toujours autoriser"** (ou "Allow for this workspace")

VS Code retient ces choix par outil et par workspace.

### Approuver les outils via settings

```jsonc
{
  // Auto-approve les appels MCP (serveurs d'outils externes)
  "chat.mcp.discovery.enabled": {
    "claude-desktop": true,
    "cursor-global": true,
    "cursor-workspace": true,
    "windsurf": true
  }
}
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> 3. Commandes terminal

C'est ici que se concentrent la plupart des confirmations bloquantes. Les agents
Grimoire exécutent régulièrement des commandes : `ls`, `cat`, `grep`, `find`,
`python3`, `git`, `bash`, etc.

### Option A : Tout approuver (recommandé en solo)

```jsonc
{
  "chat.tools.terminal.autoApprove": {
    "/.*/": { "approve": true, "matchCommandLine": true }
  }
}
```

La regex `/.*/` match **toute commande**. C'est le mode le plus fluide — les
agents ne sont jamais bloqués.

> **Sécurité** : Ce mode convient pour un usage solo sur sa propre machine. Les
> agents Copilot n'exécutent que ce que leur prompt leur demande — pas de risque
> d'exécution arbitraire de code malveillant.

### Option B : Approuver sélectivement (mode contrôlé)

Pour garder un contrôle sur les commandes destructives tout en laissant passer
les commandes de lecture :

```jsonc
{
  "chat.tools.terminal.autoApprove": {
    // ── Lecture / navigation (safe) ──
    "cat": true,
    "ls": true,
    "pwd": true,
    "cd": true,
    "find": true,
    "grep": true,
    "head": true,
    "tail": true,
    "wc": true,
    "tree": true,
    "which": true,
    "echo": true,
    "date": true,
    "file": true,
    "stat": true,
    "readlink": true,
    "realpath": true,
    "basename": true,
    "dirname": true,
    "sort": true,
    "cut": true,
    "tr": true,
    "column": true,
    "cmp": true,
    "du": true,
    "df": true,
    "sleep": true,

    // ── Git (lecture seule) ──
    "git branch": true,
    "git diff": true,
    "git grep": true,
    "git log": true,
    "git show": true,
    "git status": true,

    // ── Exécution Python/Node (agents Grimoire) ──
    "python3": true,
    "python": true,
    "node": true,
    "bash": true,
    "sh": true,

    // ── Build / test ──
    "make": true,
    "npm test": true,
    "npm run": true,
    "pip install": true,
    "go build": true,
    "go test": true,
    "cargo build": true,
    "cargo test": true,
    "pytest": true,
    "ruff": true,

    // ── Commandes à risque — confirmation manuelle ──
    "rm": false,
    "rmdir": false,
    "chmod": false,
    "chown": false,
    "kill": false,
    "dd": false,
    "curl": false,
    "wget": false,
    "eval": false,
    "xargs": false,
    "sudo": false
  }
}
```

### Option C : Regex granulaire (avancé)

La syntaxe `matchCommandLine` permet de matcher sur la **ligne de commande
complète**, pas juste le premier mot :

```jsonc
{
  "chat.tools.terminal.autoApprove": {
    // Approuver git add/commit/push mais pas git reset --hard
    "/^git (add|commit|push|pull|fetch|checkout|switch)/": {
      "approve": true,
      "matchCommandLine": true
    },
    // Approuver rm mais seulement sans -rf
    "/^rm [^-]/": {
      "approve": true,
      "matchCommandLine": true
    },
    // Approuver pip install mais pas pip uninstall
    "/^pip3? install/": {
      "approve": true,
      "matchCommandLine": true
    },
    // Bloquer tout le reste
    "/.*/": { "approve": false, "matchCommandLine": true }
  }
}
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/microscope.svg" width="28" height="28" alt=""> 4. Fichiers et éditions

### Désactiver les confirmations de création/édition de fichiers

VS Code Copilot peut demander confirmation avant de créer ou modifier un fichier.
Pour laisser les agents travailler librement :

- Dans Copilot Chat, quand l'agent propose une édition → cliquer **"Apply all"**
 plutôt que fichier par fichier
- Pour les créations de fichiers, cliquer sur **"Always allow"** quand proposé

### Limiter le file watcher (performance)

Les agents Grimoire génèrent beaucoup de fichiers temporaires. Exclure les dossiers
lourds du file watcher réduit la charge :

```jsonc
{
  "files.watcherExclude": {
    "_grimoire/_memory/qdrant_data/**": true,
    "_grimoire-output/.runs/**": true,
    "**/node_modules/**": true,
    "**/.terraform/**": true,
    "**/.venv/**": true,
    "**/__pycache__/**": true
  }
}
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> 5. Modèles et rate limits

### Le problème

Les modèles premium (Claude Sonnet 4, o4-mini, etc.) ont des quotas
de requêtes par minute/heure imposés par GitHub. Chaque appel d'outil d'un agent
= 1 requête. Un workflow Grimoire complet peut consommer 30-80 requêtes.

Message typique :
> Sorry, you have exhausted this model's rate limit. Please wait a moment before
> trying again, or switch to GPT-4.1.

### Configurer le fallback automatique

```jsonc
{
  "chat.models.fallback.enabled": true,
  "chat.models.fallback.model": "copilot:gpt-4.1"
}
```

Quand le modèle principal atteint sa limite, VS Code bascule automatiquement sur
GPT-4.1 (qui a des quotas beaucoup plus généreux).

### Bonnes pratiques pour éviter les rate limits

| Pratique | Impact |
|----------|--------|
| Commencer un nouveau chat régulièrement | Réduit le contexte envoyé à chaque requête |
| Ne pas attacher `@workspace` entier | Moins de tokens consommés par requête |
| Utiliser GPT-4.1 pour les tâches courantes | Quasi illimité, réserver les modèles premium pour le complexe |
| Garder les `copilot-instructions.md` concis | Envoyé à **chaque** requête — chaque Ko compte |
| Réduire le budget contexte des agents | `bash grimoire-init.sh guard --suggest` pour voir les agents trop lourds |

### Switcher de modèle manuellement

Les quotas sont **par modèle**. Quand Claude est limité, GPT-4.1 est toujours
disponible et vice versa. Utiliser le sélecteur de modèle en bas du chat Copilot
pour basculer.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/microscope.svg" width="28" height="28" alt=""> 6. Diff editor

### Algorithme arrêté trop tôt

Message : *"L'algorithme diff a été arrêté tôt (au bout de 5000 ms.)"*

Cause : VS Code coupe le calcul de diff après 5 secondes par défaut.

```jsonc
{
  // 0 = pas de timeout, l'algorithme tourne jusqu'au bout
  "diffEditor.maxComputationTime": 0,
  // 0 = pas de limite de taille de fichier pour le diff
  "diffEditor.maxFileSize": 0
}
```

> Ce setting est déjà inclus dans le `.vscode/settings.json` du kit.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/network.svg" width="28" height="28" alt=""> 7. Réseau et VPN

### ERR_CONNECTION_CLOSED

Message typique :
> Désolé, erreur au niveau du réseau. Veuillez réessayer plus tard.
> Error Code: net::ERR_CONNECTION_CLOSED

**Cause principale** : VPN routant le trafic via un serveur distant (latence
élevée → timeout des connexions longues Copilot).

### Diagnostic

```bash
# Vérifier la latence vers GitHub
ping -c 3 api.github.com

# Latence normale : < 50ms (Europe)
# Latence problématique : > 200ms (VPN vers un autre continent)
```

### Solutions

1. **Déconnecter le VPN** (si non nécessaire) :
   ```bash
   nordvpn disconnect  # ou équivalent
   ```

2. **Connecter sur un serveur proche** :
   ```bash
   nordvpn connect France  # ou le pays le plus proche
   ```

3. **Split tunneling** — exclure VS Code du VPN :
   ```bash
   # NordVPN
   nordvpn allowlist add app /usr/share/code/code

   # Ou via la variable d'environnement (générique)
   # Lancer VS Code hors VPN et le reste via VPN
   ```

4. **Auto-connect sur serveur proche** :
   ```bash
   nordvpn set autoconnect on France
   ```

### Configuration proxy VS Code

Si vous êtes derrière un proxy d'entreprise :

```jsonc
{
  "http.proxy": "http://proxy.entreprise.com:8080",
  "http.proxyStrictSSL": false,
  "http.proxyAuthorization": null
}
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> 8. Référence des commandes à risque

Liste des commandes classées par niveau de risque. Cette référence aide à
configurer le `chat.tools.terminal.autoApprove` en Option B/C.

### Niveau 1 — Safe (toujours approuver)

Commandes en lecture seule, aucune modification du système.

| Commande | Usage agent |
|----------|-------------|
| `cat`, `head`, `tail`, `less` | Lire des fichiers |
| `ls`, `tree`, `find`, `file` | Explorer l'arborescence |
| `grep`, `wc`, `sort`, `cut`, `tr` | Analyser du contenu |
| `pwd`, `cd`, `which`, `echo` | Navigation / info |
| `git status`, `git log`, `git diff` | État du repo |
| `stat`, `du`, `df`, `date` | Métadonnées système |

### Niveau 2 — Modéré (approuver pour les agents Grimoire)

Commandes qui modifient des fichiers dans le workspace, mais de manière
attendue et réversible.

| Commande | Usage agent | Risque |
|----------|-------------|--------|
| `python3`, `node`, `bash` | Exécuter scripts Grimoire | Dépend du script |
| `git add`, `git commit` | Sauvegarder le travail | Réversible (`git reset`) |
| `git push` | Publier | Réversible (`git revert`) |
| `pip install` | Installer dépendances | Non destructif |
| `npm install`, `npm run` | Build/test | Non destructif |
| `make`, `go build`, `cargo build` | Compilation | Non destructif |
| `mkdir`, `touch`, `cp` | Créer fichiers/dossiers | Non destructif |
| `sed`, `awk` | Édition inline | Modifie des fichiers |

### Niveau 3 — Dangereux (confirmation recommandée)

Commandes destructives ou à effet irréversible.

| Commande | Risque | Recommandation |
|----------|--------|----------------|
| `rm`, `rm -rf` | Suppression définitive | `false` ou regex limitant |
| `chmod`, `chown` | Changement de permissions | `false` |
| `kill`, `pkill` | Arrêt de processus | `false` |
| `dd` | Écriture disque bas niveau | **Toujours `false`** |
| `curl`, `wget` | Téléchargement/upload réseau | `false` si risque d'exfiltration |
| `eval`, `exec` | Exécution arbitraire | **Toujours `false`** |
| `sudo` | Élévation de privilèges | **Toujours `false`** |
| `docker rm`, `docker system prune` | Suppression conteneurs/images | `false` |
| `terraform destroy` | Destruction d'infra | **Toujours `false`** |
| `git reset --hard` | Perte de commits | `false` |
| `git push --force` | Écrasement d'historique | `false` |

### Regex utiles pour le mode avancé

```jsonc
{
  // Approuver git sauf les commandes destructives
  "/^git (add|commit|push|pull|fetch|checkout|switch|stash|branch|tag|log|diff|show|status|remote)/": {
    "approve": true, "matchCommandLine": true
  },
  "/^git (reset|clean|rebase|push.*--force)/": {
    "approve": false, "matchCommandLine": true
  },

  // Approuver rm seulement sur des fichiers (pas -rf sur des dossiers)
  "/^rm [^-].*\\.(tmp|log|pyc|bak)$/": {
    "approve": true, "matchCommandLine": true
  },

  // Approuver docker en lecture, bloquer les suppressions
  "/^docker (ps|images|logs|inspect|exec)/": {
    "approve": true, "matchCommandLine": true
  },
  "/^docker (rm|rmi|system|volume rm)/": {
    "approve": false, "matchCommandLine": true
  }
}
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> Checklist de configuration

- [ ] `chat.tools.terminal.autoApprove` configuré (Option A, B ou C)
- [ ] `chat.agent.maxRequests` ≥ 200 (500 recommandé)
- [ ] `diffEditor.maxComputationTime` à 0
- [ ] `chat.models.fallback.enabled` à true
- [ ] VPN configuré sur un serveur proche ou split-tunnel activé
- [ ] `files.watcherExclude` configuré pour les dossiers lourds Grimoire
- [ ] `search.exclude` configuré pour réduire le bruit dans les recherches

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> Fichier complet de référence (User Settings)

<details>
<summary>Cliquer pour déplier — settings.json complet recommandé</summary>

```jsonc
{
  // ══════════════════════════════════════════════════════════════════════
  // User Settings — Optimisé pour Grimoire Custom Kit
  // ══════════════════════════════════════════════════════════════════════

  // ── Copilot ───────────────────────────────────────────────────────────
  "github.copilot.enable": {
    "*": true,
    "plaintext": true,
    "markdown": true,
    "scminput": true
  },
  "github.copilot.nextEditSuggestions.enabled": true,
  "chat.agent.maxRequests": 500,

  // ── Auto-approbation terminal (Option A — tout) ───────────────────────
  "chat.tools.terminal.autoApprove": {
    "/.*/": { "approve": true, "matchCommandLine": true }
  },

  // ── Auto-approbation URLs ──────────────────────────────────────────────
  "chat.tools.urls.autoApprove": {
    "https://github.com": true,
    "https://*.githubusercontent.com": true,
    "https://hub.docker.com": true
  },

  // ── MCP discovery ──────────────────────────────────────────────────────
  "chat.mcp.discovery.enabled": {
    "claude-desktop": true,
    "cursor-global": true,
    "cursor-workspace": true,
    "windsurf": true
  },

  // ── Diff editor ────────────────────────────────────────────────────────
  "diffEditor.maxComputationTime": 0,
  "diffEditor.maxFileSize": 0,

  // ── Modèle fallback ────────────────────────────────────────────────────
  "chat.models.fallback.enabled": true,
  "chat.models.fallback.model": "copilot:gpt-4.1"
}
```

</details>
