---
audience: décision
---

# Source : piste 2 (petit modèle local) — note de cadrage

Date : 2026-10-26 · Issue #280, piste 2 · Tâche GAO-r49-note-de-001

## Question

La suggestion de contenu par un petit modèle local (Ollama), livrée derrière
l'IntelliSense déterministe (`src/grimoire/tools/source_assist.py`, opt-in
`source.assist.model`), apporte-t-elle un gain mesurable à l'éditeur Source ?
Faut-il investir davantage ?

## Mesure

Protocole : projet jetable avec `source.assist.model` déclaré, trois fichiers du
kit copiés dans `_grimoire/overrides/agents/`. Dans chacun, la valeur de
`dont_use_when` est vidée (`dont_use_when: "`), le curseur est placé sur cette
ligne, intention `complete-clause`. Deux appels par fichier et par modèle, via
`assist_view` (le chemin réel de `POST /api/workspace/assist`). Modèles résidents
en mémoire, Ollama local, machine du développeur.

Fichiers cités :

- `archetypes/meta/agents/concierge.md`
- `archetypes/meta/agents/agent-optimizer.md`
- `archetypes/meta/agents/security-auditor.md`

| Fichier | qwen2.5:3b | qwen2.5:7b | Latence (appel chaud) |
|---|---|---|---|
| concierge | recopie `use_when` (2/2) | recopie `use_when` (2/2) | 0,4 à 0,8 s |
| agent-optimizer | 2/2 : clause `dont_use_when` de la bonne forme, mais elle reformule `use_when` (« auditer la qualité d'un agent… ») au lieu de dire quand ne pas l'employer | recopie `use_when` (2/2) | 1,0 à 1,3 s |
| security-auditor | 2/2 : recopie `use_when`, puis une `dont_use_when` qui reformule `tool_boundary` (« pas d'accès à la configuration d'infrastructure ») | recopie `use_when` (2/2) | 0,5 à 0,9 s |

Référence attendue (clause actuelle du kit) : agent-optimizer → « Créer un nouvel
agent de zéro (voir agent-builder) ou fuzzer une surface d'entrée (voir
security-auditor) » ; security-auditor → « Durcissement de configuration… (voir
security-hardener) ». Aucune suggestion ne nomme un autre agent ni ne s'en approche.

**Gain mesuré : nul.** Sur 12 appels (3 fichiers × 2 modèles × 2), aucun ne produit
une clause « ne pas employer » exploitable : 8 recopient `use_when`, 4 produisent
une clause de la bonne forme mais au contenu faux. Le modèle réémet une clause voisine déjà
présente dans la fenêtre de contexte. Le garde d'identifiants (`unknown`) est resté
vide partout : il ne protégeait de rien, faute d'identifiant inventé, mais il
n'a pas non plus rendu le texte juste. Une clause correcte (`dont_use_when`)
exige de connaître le périmètre des autres agents, ce que le prompt, volontairement
borné au fichier courant, ne contient pas.

Limite de la mesure : échantillon de 3 fichiers et une seule intention
(`complete-clause`). `draft-body` et `explain-diagnostic` n'ont pas été mesurées ;
l'explication d'un diagnostic est déjà couverte par le message déterministe.

## Preuve et rejeu

Rejeu du 2026-10-28 (mode jeu off, Ollama local) : `python scripts/bench/source_assist_replay.py --out journal.jsonl`
(`PYTHONPATH=src`). Journal brut des 12 appels — prompt exact, réponse brute,
latence, résultat d'`assist_view` — dans
`docs/audits/source-assist-piste2-2026-10-28-journal.jsonl`. Le tableau
ci-dessus est tiré de ce journal ; il remplace celui de la première mesure (2026-10-26),
dont les sorties exactes variaient légèrement (échantillonnage non figé), sans
changer la conclusion.

## Coût

- Mémoire : 1,9 Go (3b) à 4,7 Go (7b) résidents, 18 Go pour les modèles « coder » présents.
- Latence : sous la seconde une fois le modèle chargé ; le premier appel affiche
  « charge encore » le temps du chargement (#450).
- Code : ~500 lignes à maintenir (`source_assist.py`), opt-in, garde de bouclage,
  refroidissement via `providers.state`.
- Risque : une suggestion plausible mais fausse (ici : recopie), à relire à chaque fois.

## Repli

Déjà en place : l'assistance est désactivée tant que `source.assist.model` est
vide, et toute panne (Ollama absent, modèle manquant, délai, refroidissement)
rend un refus discret sans toucher à l'IntelliSense déterministe, qui reste
seul à faire autorité. Désactiver = retirer la clé. Aucun repli supplémentaire
à construire.

## Décision (2026-10-26)

**Ne pas investir davantage dans la piste 2.** Garder le code livré en l'état,
opt-in, sans l'étendre (pas de nouvelle intention, pas de prompt élargi au reste du
projet : cela romprait la doctrine « jamais le contenu d'un autre fichier »).

Condition de réouverture : une nouvelle mesure, sur au moins 3 fichiers, où la
suggestion n'est pas une recopie d'une ligne voisine, par exemple avec une
intention qui reçoit explicitement la liste des clauses `use_when` des autres agents
et sous un contrôle écrit d'avance (comparaison à la clause de référence).
