<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/microscope.svg" width="32" height="32" alt=""> Modèles de décision (Jev et clones /v1/systemone) — verdict et récolte (2026-09)

> Brainstorm en deux tours (lecture directe des politiques publiques et cinq
> sous-agents : faisabilité sur les données du banc `kit-gov`, prototype CPU,
> plan d'architecture, pré-mortem, patterns des harnais Jev existants).
> Sources datées lues le 2026-09-27 (liste en bas de page). Étiquettes de
> provenance identiques à `framework/agentic-industry-reference.md` §0 :
> `[doc]` documentation officielle, `[blog]` billet d'ingénierie, `[mesuré]`
> chiffre issu d'un rejeu ou d'un banc local reproduit dans ce dépôt.
>
> Segment jugé : un modèle de décision typé, hébergé ou auto-hébergeable,
> appelé en préflight ou en gate par le kit — pas les modèles de génération
> de code eux-mêmes.

<img src="../assets/divider.svg" width="100%" alt="">

## Cadrage

Un « System One model » (Jev et sa famille de clones, endpoint compatible
`/v1/systemone`) est un modèle spécialisé dans la décision binaire ou
catégorielle rapide plutôt que dans la génération de texte : un score de
confiance ou une étiquette de classe en sortie, pas une réponse en langage
naturel. Contrat d'API : requête avec le texte à juger et éventuellement un
contexte court, réponse avec une étiquette et un score de confiance entre 0
et 1. Prix et limites publics de l'offre hébergée TypeSafe AI `[doc]` :
facturation à l'appel, quota par clé, pas de mode batch documenté au
2026-09-27.

Confidentialité, telle que lue dans la politique publique `typesafe.ai/privacy`
`[doc]` : collecte des entrées envoyées à l'API, hébergement aux États-Unis,
**aucune mention de ZDR (zero data retention) niveau entreprise** dans le
texte de la politique — l'affirmation « ZDR entreprise » qui circule vient
d'un blog tiers, pas de la source primaire.

## Tableau des candidats

| Candidat | Taille / matériel | Exactitude jabr v2 (si disponible) | Verdict |
|---|---|---|---|
| Jev (TypeSafe AI, hébergé) | Non documenté publiquement (API fermée) | Non disponible (pas d'accès direct au poids) | Écarter — confidentialité non garantie (aucun ZDR mentionné, hébergement US), aucune mesure locale possible |
| Von 1.2.3 (clone CPU, poids ouverts) | 3,0 Go de poids, RSS 3,4-3,9 Go en fonctionnement | Non publié par le projet ; mesuré localement ici (voir plus bas) | Écarter pour l'instant — mesures locales défavorables |
| Laya | Non mesuré dans ce tour de veille | Non disponible | Non instruit — hors des cinq mesures ci-dessous |
| Kev | Non mesuré dans ce tour de veille | Non disponible | Non instruit |
| Nimble | Non mesuré dans ce tour de veille | Non disponible | Non instruit |
| SemIf | Non mesuré dans ce tour de veille | Non disponible | Non instruit |
| OpenJev | Non mesuré dans ce tour de veille | Non disponible | Non instruit |
| NanoJev | Non mesuré dans ce tour de veille | Non disponible | Non instruit |

Seul Von a fait l'objet d'un prototype et d'un banc CPU locaux dans ce tour
de veille ; les six autres clones cités (Laya, Kev, Nimble, SemIf, OpenJev,
NanoJev) restent des candidats non mesurés — leur ligne du tableau des
idées à récolter (plus bas) porte sur leurs harnais d'intégration lus dans
leurs dépôts, pas sur une exactitude mesurée.

<img src="../assets/divider.svg" width="100%" alt="">

## Ce que les mesures ont établi

**Banc `kit-gov` (lot J, 60 runs, 20 tâches × 3 répétitions)** `[mesuré]` :
42/60 runs « mandat pur » coûtent 0,04 $ médian de plus que le nu ; 18 runs
portent 103 % du surcoût total. Déclencheurs identifiés : le WARN
`acceptance.no_tests_collected` (15/15 runs JavaScript, le message enjoint
« écris un test ou justifie » alors que le dépôt de tâche ne contient aucun
fichier de test à écrire) et le repli `task_id: bootstrap` (1 run
rust/poker). Supprimer ces deux excursions ramène le coût de 118 % à 99 % du
nu **sans retirer un seul mandat de gouvernance**. Aucune feature textuelle
de `TASK.md` ne prédit le nombre de tours (R² LOO ≤ 0 pour toutes les
features testées) ; la taille du run nu ne prédit pas non plus le coût
gouverné (R² 0,04, `nu_c → J_c`).

**Von 1.2.3, CPU, 16 threads, sans GPU** `[mesuré]` : 3,0 Go de poids, RSS
3,4-3,9 Go, démarrage 2,6-24 s selon l'état du cache, 196 ms médian par
commande shell classée à chaud, 669 ms médian par énoncé de tâche complet.
Politique d'outils : les 5 pièges du jeu de test sont détectés 5/5, mais à
une confiance de 0,72-0,78 — dans la même bande que des lectures légitimes,
non séparable d'un seuil fixe — contre 16/17 pour la policy à base de regex
déjà dans le kit, à 0 faux positif. Dosage du mandat de gouvernance : 7/12
correct sur le banc (proche du hasard), 7/9 sur des tâches rédigées dans le
style du kit, confiance 0,15-0,56 non discriminante entre les deux issues.
Claim-ledger (contrôle de cohérence des affirmations) : verdict proche du
hasard. Instruction en français : effondrement total de l'exactitude (le
modèle n'a pas été entraîné sur du français).

**Doctrine du kit appliquée au verdict** : un verdict probabiliste ajouté
au-dessus d'une gate déterministe existante ne peut, par construction, que
resserrer la gouvernance — jamais l'assouplir sans renoncer au fail-closed.
Un modèle qui doserait le mandat de gouvernance à la baisse serait donc en
contradiction directe avec cette doctrine, quelle que soit son exactitude.

**Harnais Jev existants (lecture de code, cinq dépôts)** `[mesuré]`/`[doc]` :
aucun harnais observé n'appelle le modèle hébergé sous ~245 ms p50 ; tous
placent un gate déterministe en amont qui rend l'appel au modèle rare
(jev-belay : 17,7 % des arrêts déclenchent l'appel ; pi-warden : 0,9 % des
holds). Le comportement par défaut en cas d'échec de l'appel est fail-open
dans la majorité des harnais lus. Les seuils de décision vivent dans un
fichier de configuration, jamais dans le code — une dérive mesurée de 0,94
à 0,78 a été observée entre deux réentraînements successifs d'un même
harnais. Le routage de sous-agents par Jev mesuré dans un harnais tiers
donne +69,7 % de coût par rapport à un seul agent fort sur la même tâche.

## Verdict : écarter pour l'instant

Aucun des trois points d'ancrage envisagés pour le kit — dosage du mandat de
gouvernance, politique d'outils (PreToolUse), claim-ledger — ne tient face
aux mesures ci-dessus. Le seul chemin cohérent avec la doctrine du kit (un
verdict probabiliste ne peut que resserrer la gouvernance, jamais l'assouplir)
va à l'inverse de l'objectif recherché, qui était de réduire le coût mesuré
au lot J. Adopter un modèle de décision aujourd'hui ajouterait de la latence,
un risque de confidentialité (pour l'offre hébergée) ou une exactitude non
discriminante (pour le clone CPU auto-hébergé), sans lever aucune des trois
excursions qui portent l'essentiel du surcoût mesuré.

### Conditions de réouverture

- Un second jeu de tâches hétérogènes (V1/V2, plus de 10 tours en nu),
  distinct du banc du lot J.
- Une ligne de base publiée et étiquetée : heuristique d'abord, puis un
  modèle MiniLM avec régression logistique en aval, avec matrice de
  confusion et ECE (erreur de calibration attendue) publiés.
- L'asymétrie de décision et le fail-closed écrits dans le code, avec un
  test rouge d'abord — jamais dans la configuration seule.
- Usage local et anglais uniquement (l'effondrement en français est total).
- Jamais dans le hook `PreToolUse` — seulement en aval d'une gate
  déterministe existante, jamais à sa place.
- Le critère de coût du kit re-basé par Guilhem avant toute nouvelle mesure
  (le seuil historique « ≤ 70 % du nu » est actuellement en lecture de
  parité, cf. `docs/plan-2026-q4.md`).

Si le sujet est rouvert, l'architecture proposée par le sous-agent Plan est :
une capacité `decision` déclarée dans `llm-provider-registry.yaml` (pas un
`provider_type` dédié), une fenêtre de décision déterministe en amont, un
cache par empreinte de commande, un timeout inférieur ou égal à 40 ms, et un
bras de banc `kit-gov-dose` avec préflight avant toute mesure d'effet.

<img src="../assets/divider.svg" width="100%" alt="">

## Récolte sans modèle

Ce que l'exploration a permis d'identifier comme utile **sans** embarquer de
modèle de décision — un mécanisme adopté ou adapté, jamais le format tiers
recopié tel quel.

| Idée | Source | Ce que le kit a déjà | Issue |
|---|---|---|---|
| Reformuler le WARN `no_tests_collected` et corriger le repli `--task-id bootstrap` | Mesuré sur le lot J (`docs/bench/rejeu-lot-j-2026-09-18.md`) | `no_tests_collected.py`, `gate_test_run.py`, `cmd_standard.py` | [#642](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/642) (lot K, phase 2bis) |
| Corriger la classification `tool_facts` (`find … -delete`, heredoc redirigé) | Défauts trouvés en lisant le code du kit pendant cette veille | `src/grimoire/hosts/decisions/tool_facts.py` | [#643](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/643) |
| Gate « done » déterministe au Stop : mutation après le dernier check vert refusée, veto dur « un check frais réussi ne bloque jamais », attente du flush du transcript avant de conclure | jev-belay, pi-warden (harnais lus, gate déterministe en amont de tout appel modèle) | `standard_checks/gate_test_run.py`, hook `Stop` | [#644](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/644) |
| Mémoire de contenu non fiable côté `PostToolUse` → `PreToolUse` : état anti-auto-autorisation limité au dernier tour humain, à la commande et au répertoire de travail — jamais au raisonnement de l'agent | jev-guard (harnais lu) | Rien d'équivalent aujourd'hui (phase 5) | [#645](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/645) |
| Répétition exacte détectée par hachage appel+sortie sur une fenêtre de 12, seuil 3 échecs, pour repérer « tourne en rond » | jev-guard (harnais lu) | Non instruit dans une issue séparée — à rattacher au lot 4.4 du plan produit | — |
| Arming rules : une édition en apparence inoffensive qui arme une commande ultérieure comme destructive | Spécification pi-warden (lue dans le dépôt) | Non instruit dans une issue séparée | — |

## Défauts du kit trouvés en passant

Deux défauts vérifiés directement sur le code du kit, `main` à
`30f76921`, sans rapport avec les modèles de décision eux-mêmes — trouvés en
lisant `src/grimoire/hosts/decisions/tool_facts.py` pendant l'instruction de
cette veille. Aucun test `tests/test_tool_facts*.py` n'existe pour ces deux
cas.

1. `find` fait partie de `_READ_ONLY_LEADING_COMMANDS` ; ses actions
   (`-delete`, `-exec`) ne sont jamais inspectées par
   `is_read_only_command`, donc :

   ```python
   is_read_only_command('find . -name "*.pyc" -delete')
   # → True
   ```

2. `_strip_heredoc_bodies` capture, avec le corps du heredoc, la fin de la
   ligne d'ouverture qui le suit — la redirection de sortie disparaît avant
   même d'atteindre `_WRITE_REDIRECTION_RE`, donc :

   ```python
   is_read_only_command('cat <<EOF > /etc/motd\n...\nEOF')
   # → True
   ```

Suivi : [#643](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/643).

<img src="../assets/divider.svg" width="100%" alt="">

## Sources consultées le 2026-09-27

- [typesafe.ai](https://typesafe.ai) — site officiel TypeSafe AI `[doc]`
- [typesafe.ai/privacy](https://typesafe.ai/privacy) — politique de
  confidentialité publique `[doc]`
- [Jev (AI model) — Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)) `[doc]`
- [github.com/jabr/classifier-benchmark](https://github.com/jabr/classifier-benchmark) `[doc]`
- [Jev alternatives, bench en](https://sotaaz.com/post/jev-alternatives-bench-en) `[blog]`
- [Jev clones measured (2026-09-20)](https://ayourtch-llm.github.io/apchat-blog/posts/2026-09-20-jev-clones-measured/) `[blog]`
- [pinggy.io blog](https://pinggy.io/blog) `[blog]`
- [github.com/wfzyx/von](https://github.com/wfzyx/von) — Von `[doc]`
- [github.com/NandhaKishorM/laya](https://github.com/NandhaKishorM/laya) — Laya `[doc]`
- [github.com/jaredpalmer/kev](https://github.com/jaredpalmer/kev) — Kev `[doc]`
- [github.com/bespokelabsai/nimble](https://github.com/bespokelabsai/nimble) — Nimble `[doc]`
- [github.com/razorback16/openjev](https://github.com/razorback16/openjev) — OpenJev `[doc]`
- [github.com/ollaya-dev/ollaya](https://github.com/ollaya-dev/ollaya) — Ollaya `[doc]`
- [github.com/cobanov/awesome-jev](https://github.com/cobanov/awesome-jev) `[doc]`
- [github.com/valentynkit/jev-belay](https://github.com/valentynkit/jev-belay) — jev-belay `[doc]`
- [github.com/DevMortimer/pi-warden](https://github.com/DevMortimer/pi-warden) — pi-warden `[doc]`
- [github.com/leepokai/jev-guard](https://github.com/leepokai/jev-guard) — jev-guard `[doc]`
- [github.com/eugeniughelbur/jev-engineering](https://github.com/eugeniughelbur/jev-engineering) `[doc]`
- [github.com/VeridicalTech/Edward](https://github.com/VeridicalTech/Edward) — Edward `[doc]`
