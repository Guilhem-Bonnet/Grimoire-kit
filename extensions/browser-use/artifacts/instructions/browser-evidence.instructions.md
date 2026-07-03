---
applyTo: "**"
description: Contrat de preuve UI pour toute tâche de navigation web
created: 2026-07-02
extension: browser-use
---

# Contrat de preuve UI

Ces règles s'appliquent à toute tâche pilotant un navigateur (pattern QUA-11
du catalogue).

## Règles

1. **Une action, une preuve** : chaque action significative produit URL + capture ou extrait DOM, archivés dans les artefacts de la mission.
2. **Allowlist obligatoire** : le périmètre de navigation est déclaré avant exécution ; hors périmètre, la tâche s'arrête et le signale.
3. **Irréversible = simulé d'abord** : paiement, suppression, publication ou envoi à effet externe passent par une description validée avant exécution (GOV-02).
4. **Pas de secrets dans le navigateur** : l'authentification est fournie par le projet hôte (session, token), jamais saisie par l'agent.
5. **L'affirmation n'est pas la preuve** : « la page indique que c'est fait » exige la capture de la page qui l'indique.
