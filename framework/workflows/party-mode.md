<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

name: party-mode
description: Discussion multi-agents sur un même sujet — chaque persona parle depuis sa spécialité, l'utilisateur arbitre
triggers:
 - décision qui engage plusieurs domaines
 - avis contradictoires attendus
 - cadrage flou avant de choisir une direction

# <img src="../../docs/assets/icons/team.svg" width="32" height="32" alt=""> Workflow Party Mode

**Type :** playbook — le LLM lit et suit ces instructions, aucune exécution programmatique (voir [taxonomie](../../docs/workflow-taxonomy.md)).

## <img src="../../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> À quoi ça sert

Une seule session LLM joue successivement plusieurs agents installés du projet, chacun depuis sa spécialité. Le but n'est pas de simuler une réunion : c'est d'obtenir des angles qu'un agent seul ne produit pas, puis de trancher.

**Utiliser quand :** la décision engage plusieurs domaines, ou vous attendez des avis qui se contredisent.

**Ne pas utiliser quand :** la question a une seule bonne réponse technique. Un agent spécialisé répond mieux et plus vite qu'un panel.

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Déroulé

### 1. Cadrer la question

Reformuler le sujet en une question qui admet plusieurs réponses défendables. Si la reformulation donne une question fermée, arrêter ici et router vers l'agent compétent.

Annoncer : `[PARTY MODE] Sujet : [question]`

### 2. Composer le panel

Lire `_grimoire/kit/agent-manifest.csv` et retenir **3 à 5 agents** dont la spécialité touche réellement le sujet. Écarter les agents qui n'auraient rien à dire de spécifique — un panel de complaisance produit un consensus vide.

Annoncer la composition et pourquoi chaque agent est là :

```
[PARTY MODE] Panel : [Agent] (angle), [Agent] (angle), [Agent] (angle)
```

### 3. Premier tour — positions indépendantes

Chaque agent s'exprime **une fois**, sans avoir lu les autres. C'est ce qui garantit la diversité : un agent qui répond après avoir lu ses collègues converge vers eux.

Format par intervention, 5 lignes maximum :

```
**[Nom] ([tag])** — [position en une phrase]
Raison : [ce que sa spécialité voit que les autres ne voient pas]
Risque principal : [le risque qu'il identifie]
```

### 4. Second tour — désaccords uniquement

Ne relancer que les agents dont la position est **incompatible** avec une autre. Un agent d'accord ne reprend pas la parole.

```
**[Nom]** → **[Nom]** : [le point de désaccord, pas une reformulation]
```

Si aucun désaccord n'émerge au premier tour, le dire franchement — c'est une information : `[PARTY MODE] Aucun désaccord réel. Le sujet n'appelait pas un panel.`

### 5. Synthèse

Ne pas moyenner les positions. Restituer :

- **Ce qui fait accord** — les points qu'aucun agent ne conteste
- **Ce qui reste ouvert** — les arbitrages qui dépendent d'une préférence, pas d'un fait
- **Ce que chaque option coûte** — pour chaque direction, ce qu'on accepte de perdre

### 6. Arbitrage

L'arbitrage revient à {{user_name}}, jamais au panel. Présenter les options numérotées et attendre.

Une fois la décision prise, l'écrire dans `_grimoire/_memory/decisions-log.md` :

```
- [YYYY-MM-DD] [party-mode] Sujet : [question] | Décision : [choix] | Écarté : [options] | Panel : [agents]
```

## <img src="../../docs/assets/icons/shield-pulse.svg" width="28" height="28" alt=""> Garde-fous

| Règle | Pourquoi |
|---|---|
| 3 à 5 agents, pas plus | Au-delà, les interventions se diluent et se répètent |
| Premier tour sans lecture croisée | Un panel qui se lit converge et perd sa raison d'être |
| Pas de vote | Compter les voix remplace l'argument par la majorité |
| Aucun agent ne tranche | Le panel éclaire, {{user_name}} décide |
| Désaccord nul = le dire | Un faux consensus coûte le temps du panel sans rien produire |

## <img src="../../docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Sortie attendue

Une synthèse, une liste d'options chiffrée, et une entrée dans `decisions-log.md` une fois l'arbitrage rendu. Pas de fichier intermédiaire.
