# Troubleshooting — BMAD Custom Kit

Solutions aux problèmes les plus fréquents.

---

## 1. La mémoire sémantique ne fonctionne pas

**Symptôme** : `⚠️ Mémoire sémantique indisponible` ou recherche peu pertinente

**Diagnostic** :
```bash
python3 _bmad/_memory/mem0-bridge.py status
```

**Causes et fixes** :

| Cause | Message | Fix |
|-------|---------|-----|
| `qdrant-client` non installé | `Qdrant lib: ❌` | `pip install qdrant-client` |
| `sentence-transformers` manquant | `Embeddings: ❌` | `pip install sentence-transformers` |
| Erreur init Qdrant | `init échoué` | Supprimer `_bmad/_memory/qdrant_data/` et relancer |
| Toutes dépendances manquantes | Mode fallback JSON | `pip install -r _bmad/_memory/requirements.txt` |

**Note importante** : le fallback JSON est **fonctionnel**. Les agents travaillent normalement — seule la qualité de la recherche sémantique est réduite (mots-clés vs embeddings). Tu peux travailler sans Qdrant.

```bash
# Réinstaller toutes les dépendances
pip install -r _bmad/_memory/requirements.txt

# Vérifier le résultat
python3 _bmad/_memory/mem0-bridge.py status
```

---

## 2. cc-verify.sh ne trouve pas le bon stack

**Symptôme** : `⚠️ Aucun stack reconnu` sur un projet Go/TypeScript/etc.

**Diagnostic** :
```bash
bash _bmad/_config/custom/cc-verify.sh  # sans --stack
```

**Causes** :

| Symptôme | Cause probable | Fix |
|----------|----------------|-----|
| Go non détecté | `go.mod` absent ou hors de portée | Ajouter `go.mod` à la racine |
| TypeScript non détecté | `package.json` sans `tsc` dans devDependencies | `npm install -D typescript` |
| Terraform non détecté | Fichiers `.tf` > 7 niveaux de profondeur | `--stack terraform` en option |

**Forcer un stack** :
```bash
bash _bmad/_config/custom/cc-verify.sh --stack go
bash _bmad/_config/custom/cc-verify.sh --stack typescript
bash _bmad/_config/custom/cc-verify.sh --stack go,docker
```

---

## 3. Le pre-commit hook bloque le commit

**Symptôme** : `🚫 Commit bloqué — CC FAIL détecté`

**C'est normal** — c'est le Completion Contract qui fonctionne correctement.

**Workflow** :
```bash
# 1. Voir les erreurs
git commit  # → affiche le CC FAIL

# 2. Corriger les erreurs
# (go build, npx tsc, pytest, etc. selon le stack)

# 3. Re-tenter
git commit

# Bypass d'urgence (DÉCONSEILLÉ — à éviter en équipe)
git commit --no-verify
```

**Si le hook est trop agressif** (faux positifs) :
```bash
# Vérifier ce que le hook détecte
bash .git/hooks/pre-commit

# Désactiver temporairement (ne pas laisser en place)
chmod -x .git/hooks/pre-commit
# ... corriger ...
chmod +x .git/hooks/pre-commit
```

---

## 4. bmad-init.sh écrase mon installation existante

**Symptôme** : Prompt `Continuer et écraser ? (y/N)` à chaque lancement

**Fix** :
```bash
# Option 1 — Confirmer manuellement
bash bmad-init.sh --name "..." --user "..." # répondre 'y' au prompt

# Option 2 — Mode force (pas de prompt)
bash bmad-init.sh --name "..." --user "..." --force

# Option 3 — Cibler un dossier différent
bash bmad-init.sh --name "..." --user "..." --target /chemin/vers/projet
```

---

## 5. sil-collect.sh ne génère rien

**Symptôme** : `Aucune source de données disponible` / rapport vide

**Explication** : C'est **attendu** sur un projet neuf. Le SIL a besoin d'historique accumulé.

```
Sources attendues (toutes vides sur un projet neuf) :
- _bmad/_memory/decisions-log.md
- _bmad/_memory/contradiction-log.md
- _bmad/_memory/agent-learnings/*.md
- _bmad/_memory/activity.jsonl
```

**Quand utiliser le SIL** : après 2-3 semaines d'utilisation normale, quand les agents ont accumulé des learnings et que tu as noté des décisions.

**Forcer la génération** (pour tester) :
```bash
bash _bmad/_config/custom/sil-collect.sh --force-empty
```

---

## 6. Les agents ne se souviennent pas du contexte entre sessions

**Symptôme** : L'agent ne connaît pas le projet au démarrage

**Cause** : `shared-context.md` non rempli ou `agent-learnings/` vides

**Fix** :
```bash
# 1. Compléter shared-context.md
nano _bmad/_memory/shared-context.md
# Remplir : stack, architecture, API, conventions, équipe

# 2. Vérifier les learnings
ls _bmad/_memory/agent-learnings/
# Des fichiers .md doivent exister pour chaque agent

# 3. Tester la mémoire
python3 _bmad/_memory/mem0-bridge.py search "nom du projet"
```

---

## 7. auto_select_archetype détecte le mauvais archétype

**Symptôme** : `--auto` sélectionne `minimal` au lieu de `web-app` ou `infra-ops`

**Diagnostic** :
```bash
# Simuler la détection depuis la racine du projet
source <(sed -n '/^detect_stack/,/^}/p' /chemin/vers/bmad-init.sh)
source <(sed -n '/^auto_select_archetype/,/^}/p' /chemin/vers/bmad-init.sh)
stacks=$(detect_stack "$(pwd)")
echo "Stacks : $stacks"
echo "Archétype : $(auto_select_archetype "$stacks")"
```

**Logique de détection** :
- `infra-ops` si terraform, k8s, ou ansible détecté
- `web-app` si frontend (react/vue/next/vite) **ET** (go, node, ou python) détectés
- `minimal` sinon

**Fix** : spécifier l'archétype manuellement :
```bash
bash bmad-init.sh --name "..." --user "..." --archetype web-app
```

---

## 8. Erreur `Permission denied` sur les scripts

```bash
chmod +x _bmad/_config/custom/cc-verify.sh
chmod +x _bmad/_config/custom/sil-collect.sh
chmod +x .git/hooks/pre-commit
```

---

## 9. `python3 maintenance.py health-check` échoue

```bash
# Vérifier Python
python3 --version  # 3.10+ requis

# Vérifier le path
cd _bmad/_memory/ && python3 maintenance.py health-check

# Vérifier les dépendances
pip3 install -r requirements.txt
```

---

## 10. `guard` ne trouve aucun agent

```bash
# Vérifier depuis le bon répertoire (doit être la racine du kit ou du projet)
bash bmad-init.sh guard --list-models    # doit lister les modèles connus

# Lancer avec le project-root explicite
python3 framework/tools/context-guard.py --project-root /chemin/vers/projet
```

`guard` cherche des agents dans :  
- `_bmad/_config/custom/agents/`  
- `_bmad/bmm/agents/`  
- `archetypes/**/agents/`  

Si aucun agent trouvé, vérifiez que `<activation` ou `NEVER break character` est présent dans les fichiers `.md`.

---

## 11. `evolve` génère 0 mutations

C'est **normal** pour un projet neuf ou le repo kit lui-même (pas de BMAD_TRACE).

`dna-evolve.py` a besoin de données réelles pour proposer des mutations :

```bash
# Vérifier que BMAD_TRACE existe avec du contenu
wc -l BMAD_TRACE.md 2>/dev/null || echo "Pas de BMAD_TRACE dans ce répertoire"

# Renseigner explicitement le fichier TRACE (si dans un sous-dossier)
bash bmad-init.sh evolve --trace _bmad/_config/custom/BMAD_TRACE.md

# Forcer un rapport même sans données
bash bmad-init.sh evolve --report
```

Après quelques semaines d'usage réel (5+ interactions par agent), les mutations apparaîtront.

---

## 12. `forge` génère un agent avec de mauvais tags / nommage incorrect

```bash
# Vérifier la description (éviter les caractères spéciaux)
bash bmad-init.sh forge --from "migrations base de donnees PostgreSQL"

# Lister les proposals déjà générés pour éviter les doublons
bash bmad-init.sh forge --list

# Installer manuellement un proposal spécifique
bash bmad-init.sh forge --install db-migrator
```

Les tags sont dérivés des 12 domaines prédéfinis (database, security, frontend, api, testing, data, devops, monitoring, networking, storage, documentation, performance). Si le domaine n'est pas reconnu, `forge` utilise `custom`.

---

## 13. `bench` ne trouve pas de données / rapport vide

```bash
# Vérifier que des sessions existent
ls _bmad-output/bench-sessions/ 2>/dev/null || echo "Aucune session bench"

# Lancer bench depuis la racine du projet (là où _bmad-output/ existe)
cd /chemin/vers/projet && bash /chemin/vers/kit/bmad-init.sh bench --summary

# Générer un premier rapport même sans données historiques
bash bmad-init.sh bench --report
```

`bench` analyse les fichiers dans `_bmad-output/bench-sessions/`. Si ce dossier est vide, le rapport affichera "Données insuffisantes" — c'est normal pour une installation fraîche.

---

## 14. Rate limit Copilot — « exhausted this model's rate limit »

Ce message vient du provider (GitHub / OpenAI / Anthropic) quand le quota de requêtes ou tokens par période est dépassé.

### Réduire la fréquence du rate limit

1. **Garder les conversations courtes** — commencer un nouveau chat régulièrement plutôt que d'accumuler 50+ échanges dans un même fil (le contexte croît à chaque message)

2. **Vérifier le budget contexte des agents** — des agents trop lourds consomment plus de tokens par requête :
   ```bash
   bash bmad-init.sh guard --suggest
   ```
   Si un agent dépasse 30-40%, envisagez de réduire son `agent-base.md` ou ses learnings.

3. **Limiter les fichiers inclus** — ne référencer dans le chat que les fichiers immédiatement nécessaires (pas ``@workspace`` sur tout le répertoire)

4. **Éviter les instructions inutilement longues** — les prompts système (copilot-instructions.md, agent-base.md) sont envoyés à **chaque** requête

### Quand le rate limit est atteint

1. **Switcher de modèle** — les quotas sont **par modèle**. Changer de Claude à GPT-4o (ou inversement) dans le sélecteur de modèle Copilot Chat reset le compteur
2. **Attendre 1-2 minutes** — la plupart des rate limits sont par minute
3. **Utiliser les outils CLI en attendant** — `guard`, `bench`, `evolve`, `forge` sont 100% locaux (Python stdlib) et ne consomment aucun quota :
   ```bash
   # Un rate limit ? Bon moment pour un diagnostic local
   bash bmad-init.sh guard --json
   bash bmad-init.sh evolve --report
   bash bmad-init.sh doctor
   ```

---

## 15. Fuite de processus Python — VS Code test auto-discovery

**Symptôme** : Des centaines de processus `python3 -m unittest discover` apparaissent dans le gestionnaire de tâches, consommant des Go de RAM. La machine devient inutilisable (swap massif).

**Diagnostic** :
```bash
# Compter les processus unittest orphelins
ps aux | grep -c 'unittest discover'

# Vérifier la mémoire consommée
ps aux | grep 'unittest discover' | awk '{sum += $6} END {printf "%.0f Mo (%d processus)\n", sum/1024, NR}'
```

**Cause** : L'extension Python de VS Code (Pylance + test explorer) lance automatiquement `python3 -m unittest discover -s <dossier_tests> -p test_*.py` pour alimenter le panneau "Testing". Sur un projet avec 50+ fichiers de test, chaque événement du file watcher (sauvegarde, modification Copilot, etc.) peut déclencher un nouveau spawn. Les processus s'accumulent car les anciens ne sont pas toujours terminés avant que les nouveaux soient lancés.

**Impact observé** : 670+ processus orphelins, ~21 Go de RAM consommés sur 31 Go, 6,7 Go de swap.

**Fix immédiat** — tuer les processus orphelins :
```bash
pkill -f 'unittest discover'
```

**Fix permanent** — désactiver la test discovery automatique dans `.vscode/settings.json` :
```json
{
  "python.testing.unittestEnabled": false,
  "python.testing.pytestEnabled": false
}
```

**Alternative** — si tu veux garder le panneau Testing fonctionnel, configure pytest explicitement avec un scope limité :
```json
{
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["tests/", "--no-header", "-q"]
}
```

**Prévention** : lancer les tests manuellement depuis le terminal :
```bash
python3 -m pytest tests/ -v
# ou
python3 -m unittest discover -s tests -p 'test_*.py'
```

> **Note** : ce problème est spécifique aux workspaces multi-root ou aux projets avec un grand nombre de fichiers de test. Les projets avec < 10 fichiers de test ne sont généralement pas affectés.

---

## Obtenir de l'aide

Si le problème persiste :

1. `python3 _bmad/_memory/mem0-bridge.py status` — état complet de la mémoire
2. `bash _bmad/_config/custom/cc-verify.sh` — état du CC
3. `bash bmad-init.sh doctor` — diagnostic global du kit
4. `bash bmad-init.sh guard --json` — budget de contexte agents (JSON pour le partager)
5. Ouvrir une issue sur GitHub avec la sortie de ces commandes
