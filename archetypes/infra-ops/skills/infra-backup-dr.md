<!-- ARCHETYPE: infra-ops — Adaptez les {{placeholders}} et exemples à votre infrastructure -->
---
description: "Concevoir, tester ou auditer une stratégie de sauvegarde et de reprise après sinistre : snapshots Proxmox, backups Longhorn, plan DR, rétention, sécurisation des clés. À utiliser dès qu'une opération touche des snapshots, une politique de rétention ou une restauration — pas pour l'exploitation d'infrastructure courante sans enjeu backup."
tools: ["read", "edit", "execute"]
---

# Backup & disaster recovery (ex-agent Phoenix)

Ancien agent dédié (`backup-dr-specialist`, faisceau outils identique à `ops-engineer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `ops-engineer` au lieu d'occuper un agent à part.

## Principes

- Règle 3-2-1 : 3 copies, 2 supports différents, 1 hors-site — minimum.
- Un backup non testé n'est pas un backup — valider par restauration régulière.
- RPO/RTO sont des contrats, pas des aspirations — les mesurer en continu (cibles : RPO < 24h, RTO < 2h par service).
- Les clés de chiffrement (age) sont le single point of failure ultime — backup hors-site obligatoire.
- OSS-first : avant une solution de backup custom, vérifier une solution établie (Velero, Restic, BorgBackup, Proxmox Backup Server).

## Garde-fou

Suppression de snapshots/backups, réduction de rétention, purge de données TSDB/Loki : afficher l'impact (données perdues, période couverte) et demander confirmation.

## Audit de couverture backup

Raisonnement : inventorier tous les services et données du projet → pour chacun, vérifier existence/fréquence/localisation/dernière exécution du backup → calculer le RPO effectif vs cible → identifier les trous → produire le rapport avec un score de résilience.

Format :

```
## Audit Backup — [date]
| Service | Données | Backup | Fréquence | Dernier | RPO effectif | Statut |
```

## Snapshots Proxmox VE (vzdump)

Identifier le LXC/VM et la fréquence → vérifier les schedules `vzdump` existants et l'espace disponible → planifier (config vzdump ou playbook Ansible) → valider (snapshot créé, taille, rétention).

## Backups Longhorn (cluster K3s)

Identifier les PVC à protéger et leur criticité → vérifier les RecurringJobs et le backup target → planifier schedule/rétention/export → valider snapshot/backup et intégrité.

## Plan de disaster recovery

Inventorier les services avec leur criticité → documenter la procédure de restauration étape par étape par service → estimer le RTO vs cible → identifier les dépendances (ordre de restauration) → documenter dans un fichier structuré (service, criticité, RTO cible, données, source backup, procédure, dépendances, dernier test).

## Rétention

Inventorier les stores à rétention (Prometheus TSDB, Loki, Longhorn, vzdump) → comparer config actuelle et besoin réel → optimiser. Réduction de rétention → afficher la période de données perdues.

## Test de restauration

Identifier le service/donnée à tester → préparer un environnement isolé → restaurer selon le plan DR → valider (service fonctionnel, données intègres, RTO mesuré) → documenter le résultat.

## Sécurisation hors-site des clés

Inventorier les clés critiques (age, SOPS, SSH, kubeconfig) → vérifier stockage et nombre de copies hors-site → planifier un backup sécurisé hors-site → documenter la procédure de récupération sans accès au homelab. Sans les clés age, tous les secrets SOPS sont irrécupérables.
