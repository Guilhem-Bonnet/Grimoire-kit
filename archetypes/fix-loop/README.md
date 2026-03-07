<p align="right"><a href="../../README.md">README</a></p>

# <img src="../../docs/assets/icons/grimoire.svg" width="32" height="32" alt=""> Archétype fix-loop

## <img src="../../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Qu'est-ce que c'est ?

L'archétype **fix-loop** fournit un workflow de correction fermée certifiée, éprouvé sur 86 cycles d'amélioration. Il garantit qu'aucun problème n'est déclaré résolu sans preuve d'exécution réelle.

**USE WHEN :**
- Vous avez des bugs récurrents qui "semblent résolus" sans tests réels
- Vous voulez une séparation stricte diagnostic / implémentation / validation
- Votre équipe d'agents doit collaborer sur des corrections complexes (multi-contexte)
- Vous avez besoin d'une mémoire des correctifs avec expiration (évite de retenter des solutions qui ont échoué)

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/sparkle.svg" width="28" height="28" alt=""> Ce que ça apporte

| Fonctionnalité | Détail |
|----------------|--------|
| **9 phases structurées** | PRE-INTAKE → INTAKE → ANALYST → FIXER → VALIDATOR → CHALLENGER → GATEKEEPER → REPORTER → META-REVIEW |
| **Sévérité adaptative** | S1 (critique) / S2 (important) / S3 (mineur) — processus allégé pour S3 |
| **Preuve obligatoire** | Zéro "done" sans exit_code + stdout + timestamp |
| **Challenger adversarial** | Tente activement de casser le fix avant approbation |
| **Mémoire des patterns** | Fix réussi → pattern sauvegardé (expiry 90j), reconnu automatiquement |
| **META-REVIEW** | Auto-analyse du cycle pour améliorer le workflow lui-même |
| **FER (Fix Evidence Record)** | Fichier YAML de traçabilité complète par session |
| **Multi-contexte** | 8 context_types : {{tech_stack_list}} + mix |
| **Guardrails destructifs** | Confirmation explicite avant toute commande à risque |
| **Circuit-breaker** | Escalade humaine si max_iterations atteint — jamais de boucle infinie |

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/folder-tree.svg" width="28" height="28" alt=""> Fichiers inclus

```
archetypes/fix-loop/
├── README.md                                    ← Ce fichier
├── agents/
│   └── fix-loop-orchestrator.tpl.md            ← Agent Loop (orchestrateur)
└── workflows/
    └── workflow-closed-loop-fix.tpl.md         ← Workflow v2.6 universalisé
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> Installation dans votre projet

```bash
# Depuis la racine du kit
./bmad-init.sh --archetype fix-loop --name "Mon Projet" --user "Alice"

# Ou manuellement :
cp archetypes/fix-loop/agents/fix-loop-orchestrator.tpl.md \
   [projet]/_bmad/_config/custom/agents/fix-loop-orchestrator.md

cp archetypes/fix-loop/workflows/workflow-closed-loop-fix.tpl.md \
   [projet]/_bmad/bmb/workflows/fix-loop/workflow-closed-loop-fix.md
```

Puis remplacer les `{{placeholders}}` :

| Placeholder | Description | Exemple |
|-------------|-------------|---------|
| `{{tech_stack_list}}` | Technologies du projet | `"ansible, terraform, docker"` |
| `{{ops_agent_name}}` | Nom de l'agent ops (Fixer délégué infra) | `"Forge"` |
| `{{ops_agent_tag}}` | Tag de l'agent ops | `"ops-engineer"` |
| `{{debug_agent_name}}` | Nom de l'agent debug (Fixer délégué system) | `"Probe"` |
| `{{debug_agent_tag}}` | Tag de l'agent debug | `"systems-debugger"` |

Si vous n'avez pas d'agents ops/debug → laisser le mode SOLO (défaut, aucun placeholder requis).

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Avec l'archétype infra-ops

L'archétype fix-loop est **complémentaire** à infra-ops. Combinaison recommandée :

```bash
./bmad-init.sh --archetype infra-ops --add-module fix-loop --name "Infra Prod"
```

Le fix-loop délègue automatiquement :
- Problèmes Ansible/Terraform/Docker → **Forge** (ops-engineer)
- Problèmes système/kernel/réseau → **Probe** (systems-debugger)

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Comment ça marche en pratique

```
Guilhem : "Le playbook deploy-monitoring.yml plante sur le handler grafana"

[Loop PRE-INTAKE] → Infère : context_type=ansible, environment=prod
[Loop INTAKE] → Confirme, classifie S1 (service down en prod)
[Loop ANALYST] → Root cause : le handler grafana n'est pas notifié correctement
                  Écrit la DoD AVANT le fix : 3 tests précis avec commandes exactes
[Loop FIXER] → Modifie le task yaml
[Loop VALIDATOR] → Exécute les 3 tests de la DoD + routing table ansible
[Loop CHALLENGER] → Tente de reproduire le bug original → "non reproductible"
[Loop GATEKEEPER] → Checklist mécanique → approved
[Loop REPORTER] → Rapport certifié avec preuves, pattern sauvegardé
[Loop META-REVIEW] → Analyse le cycle, propose d'améliorer le workflow
```
