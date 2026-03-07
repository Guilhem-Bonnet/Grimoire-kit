<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/workflow.svg" width="32" height="32" alt=""> Patterns de Design — Workflows Agentiques

> Patterns extraits de 86 fixes appliqués au workflow `closed-loop-fix` v2.6.
> Ces patterns sont universels et s'appliquent à tout workflow d'agent BMAD.

<img src="assets/divider.svg" width="100%" alt="">


## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 1 — DoD avant fix (Definition of Done)

**Problème résolu :** Agent déclare "done" sans critères de succès définis.

**Pattern :**
```
ANALYST → rédige la DoD avec commandes exactes AVANT d'écrire du code
FIXER   → implémente UNIQUEMENT pour satisfaire la DoD
VALIDATOR → exécute EXACTEMENT les commandes de la DoD
```

**Règle clé :** La DoD est un contrat bidirectionnel. Analyst écrit, Validator exécute. Jamais de test "vérifier que ça marche" — uniquement des commandes avec exit_code attendu.

**Quand utiliser :** Tout workflow de correction, de déploiement, de refactoring.

**Signal d'alarme :** Si le Validator invente des tests non prévus dans la DoD — retour à Analyst.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 2 — Evidence-First (Preuves avant verdict)

**Problème résolu :** Agent affirme que quelque chose fonctionne sans sortie de commande.

**Pattern :**
```yaml
evidence:
  - test: "Health check API"
    command: "curl -sf http://localhost:8080/health"
    stdout: "200 OK"
    exit_code: 0
    passed: true
    timestamp: "2025-01-17T14:32:00Z"
```

**Règle clé :** Zéro déclaration sans preuve attachée. "Non reproductible" sans YAML = invalide.

**Quand utiliser :** Toute phase de validation ou de challenger.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 3 — Adversarial Challenger

**Problème résolu :** Validator passe mais le bug réapparaît en production.

**Pattern :**
```
Après validation réussie :
1. Relire le symptôme ORIGINAL
2. Tenter ACTIVEMENT de reproduire le bug avec exactement la même procédure
3. Tester les cas limites : vide, null, invalide, charge, parallèle
4. Vérifier TOUTE la surface d'impact (pas seulement le composant fixé)
5. Tester la régression : fonctionnalités adjacentes
```

**Règle clé :** Le Challenger doit vouloir casser le fix. Si le cœur n'y est pas → le pattern ne sert à rien.

**Quand utiliser :** S1/S2 obligatoire. S3 optionnel.

**Ne jamais déléguer le Challenger** — l'objectivité est garantie par la séparation du rôle.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 4 — Session Isolation (FER)

**Problème résolu :** État de session contaminé entre deux cycles de fix.

**Pattern :**
```yaml
# Fix Evidence Record — isolé par session
session_id: "fer-2025-01-17-14-32-00"  # unique
current_phase: "INTAKE"
# ...
```

**Règle clé :** Chaque cycle de fix = son propre fichier. Jamais de réutilisation d'un FER d'une session précédente sans protocole de reprise explicite.

**Avantage :** Rollback, audit, comparaison entre sessions possible. Jamais de contamination.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 5 — Circuit-Breaker (Boucle bornée)

**Problème résolu :** Agent boucle infiniment sur un problème complexe.

**Pattern :**
```
max_iterations: S1=3, S2=5, S3=2

if iteration >= max_iterations:
    → ESCALADE HUMAINE (jamais un nouveau fix automatique)

if consecutive_failures >= 2:
    → ANALYST re-challenge la root cause
    → Réinitialiser consecutive_failures ET challenger_failures

if challenger_failures >= 3:
    → ESCALADE HUMAINE (Validator passe mais Challenger casse répétitivement)
```

**Règle clé :** La boucle infinie est le pire ennemi d'un agent de correction. Toujours borner.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/microscope.svg" width="28" height="28" alt=""> PATTERN 6 — Root Cause Invalidation

**Problème résolu :** Agent continue d'appliquer des fixes sur une mauvaise root cause.

**Pattern :**
```
Si consecutive_failures >= 2 :
1. La root cause N-1 était probablement incorrecte
2. Présenter : ancienne RC → pourquoi incorrecte (avec preuves des échecs)
3. Proposer nouvelle hypothèse
4. Si context_type change aussi → vider ENTIÈREMENT DoD + test_suite
   (une DoD conçue pour un context_type réfuté est du bruit pur)
```

**Signal d'alarme critique :** Si on garde la DoD d'une root cause réfutée → les tests testent la mauvaise chose.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 7 — Sévérité Adaptative

**Problème résolu :** Processus trop lourd pour les petits fixes, trop léger pour les critiques.

**Pattern :**
```
S1 — Critique : max 3 iterations, 9 phases (toutes)
S2 — Important : max 5 iterations, 9 phases (toutes)
S3 — Mineur : max 2 iterations, 6 phases (pas de Challenger ni Gatekeeper)
```

**Règle clé :** S3 ne s'enregistre pas dans les patterns (trop faible valeur). S1/S2 uniquement.

**Anti-pattern :** Appliquer le processus S1 à une typo → perte de 20 minutes. Appliquer le processus S3 à un bug de prod → catastrophe.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 8 — Pre-flight Check

**Problème résolu :** Validator utilise une iteration sur un environnement inaccessible.

**Pattern :**
```
AVANT d'exécuter toute test suite :
- ansible : ping -m ping -o
- docker : docker compose ps
- api : curl -sf --max-time 5 [url]/health
- system : ping -c 1 [host]

Si pre-flight échoue → NE PAS incrémenter iteration
→ Suspendre, notifier, attendre résolution
```

**Règle clé :** Un pre-flight échoué ne compte PAS comme une iteration. C'est un blocage externe, pas un échec du fix.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 9 — Auto-amélioration (META-REVIEW)

**Problème résolu :** Le workflow ne s'améliore jamais face aux problèmes récurrents.

**Pattern :**
```
Après chaque cycle certifié (S1/S2) :
1. Analyser phase_timestamps → où le temps a été perdu
2. Analyser Challenger vs Validator → tests Validator insuffisants ?
3. Proposer des changements typés : phase > prompt > field > threshold > pattern
4. Dry-run obligatoire avant toute modification
5. Mini-validation post-modification (cohérence inter-phases)
```

**Règle clé :** Toute proposition de type `phase` est à très haute prudence — impact systémique. Commencer par `threshold` et `field`.

**Persistance :** Proposals écrites dans `meta-review/workflow-improvement-proposal-{session-id}.yaml` — historique consultable.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 10 — Guardrail Destructif

**Problème résolu :** Agent exécute une commande destructive sans confirmation.

**Pattern :**
```
Détecter avant exécution :
- terraform destroy / terraform apply -auto-approve
- docker rm -f / docker system prune
- ansible-playbook sans --check sur all
- DROP TABLE / DELETE FROM sans WHERE
- rm -rf / pkill -9

Si détecté → STOP + confirmation explicite
```

**Règle clé :** La confirmation doit être active ("oui") — pas passive. Une confirmation par type de commande, pas une fois pour toute la session.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 11 — Délégation avec Objectivité Garantie

**Problème résolu :** Agent délègue le fix ET la validation à la même entité.

**Pattern :**
```
ORCHESTRATEUR :
  - Délègue FIXER à l'expert du domaine (optionnel)
  - Garde CHALLENGER et GATEKEEPER pour lui-même (obligatoire)
  - Seul l'Orchestrateur incrémente iteration
  - Seul l'Orchestrateur décide des transitions de phase

EXPERT DÉLÉGUÉ :
  - Joue uniquement le rôle FIXER
  - Son output devient fix_applied
  - L'Orchestrateur reprend Phase 4
```

**Règle clé :** On ne valide jamais son propre travail. Challenger et Gatekeeper = toujours l'Orchestrateur.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 12 — Patterns Périssables (90j)

**Problème résolu :** Pattern basé sur une architecture qui a changé → mauvais fast-path.

**Pattern :**
```
valid_until = date d'enregistrement + 90 jours

À chaque initialisation :
- Filtrer patterns dont valid_until < date courante → ignorer en session
- Si composant du pattern absent de shared-context.md → invalide
- Pattern périmé peut être choisi manuellement (mais pas auto-proposé)
```

**Règle clé :** La mémoire fraîche est plus précieuse que la mémoire abondante. Un pattern périmé peut être pire que pas de pattern du tout.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> PATTERN 13 — FER Taille Bornée

**Problème résolu :** FER devient ingérable après plusieurs itérations verboses.

**Pattern :**
```
FER courant ≤ 100 lignes

Si dépassé en Phase 7 :
→ Externaliser evidence[] + iteration_lessons[] vers fer-history-{session-id}.yaml
→ Remplacer dans FER par :
   evidence_ref: "fer-history-{session-id}.yaml"
   iteration_lessons_ref: "fer-history-{session-id}.yaml"
```

**Règle clé :** Le FER courant = état actuel uniquement. L'historique verbose = fichier dédié. Les agents en session chargent le FER, pas l'historique complet.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> ANTI-PATTERNS À ÉVITER

| Anti-pattern | Conséquence | Correction |
|---|---|---|
| `fix_applied: description: "OK"` | Gatekeeper ne peut pas valider | Description avant/après obligatoire |
| Validator crée des tests non définis dans DoD | DoD ≠ réalité → régression cachée | Fusion additive DoD + routing table |
| Challenger délégué au même agent que Fixer | Biais confirmation | Challenger = toujours l'Orchestrateur |
| Boucle sans max_iterations | Agent bloqué pour toujours | Borne obligatoire : S1=3, S2=5, S3=2 |
| DoD rédigée après le fix | Test écrit pour valider le fix existant | DoD AVANT fix, obligatoire |
| Pattern périmé en fast-path S1 | Mauvais contexte, fix raté | Challenger+Gatekeeper maintenus en S1/S2 |
| FER mutuel entre sessions | État contaminé, faux positifs | 1 FER = 1 session, protocol de reprise explicite |

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> COMBINAISONS DE PATTERNS RECOMMANDÉES

### Stack "Rigueur maximale" (prod critique)
```
DoD avant fix + Evidence-First + Adversarial Challenger + Circuit-Breaker + Pre-flight
→ Zéro régression, zéro faux positif, toujours escalade humaine si bloqué
```

### Stack "Vélocité contrôlée" (dev active)
```
DoD avant fix + Evidence-First + Sévérité Adaptative (S3 si mineur)
→ Processus léger pour les petits fixes, rigoureux pour les importants
```

### Stack "Auto-amélioration" (équipe longue durée)
```
Session Isolation + META-REVIEW + Patterns Périssables
→ Le workflow apprend de lui-même sur 90j de cycles
```
