---
audience: référence
---

# La mémoire du projet

## Mémoire

Le groupe `grimoire memory` pilote le sous-système mémoire. Concepts, backends
et projections : [Système de mémoire](../../explications/memoire.md).

| Commande | Description |
| --- | --- |
| `grimoire memory status` | Santé du backend, nombre d'entrées, configuration |
| `grimoire memory up` | Mettre en place la stack mémoire complète ; `--start` démarre (Docker) les services manquants — consentement explicite |
| `grimoire memory doctor` | Entretien minimal : santé des couches, pile démarrée ou non, dérive du graphe (nœuds orphelins), purge sur `--apply` |
| `grimoire memory remember <texte>` | Écriture typée idempotente — même texte et même agent n'écrivent qu'une fois ; `--source` (chemin, `task:<id>`, `session:<id>`, `commit:<sha>`) dit d'où vient le fait, sans quoi c'est une saisie libre, jamais « à jour » |
| `grimoire memory recall <requête>` | Rechercher parmi les mémoires typées |
| `grimoire memory search <requête>` | Recherche par mot-clé ou similarité sémantique — fusionne vectoriel et BM25 (RRF) dès que le projet possède les deux ; `--no-hybrid` force le backend seul. Chaque résultat porte sa source, ses dates et sa fraîcheur (à jour, périmé, non vérifié, hypothèse) ; `--include-stale` ajoute les sources disparues, `--include-superseded` les décisions remplacées |
| `grimoire memory supersede <ancien> <nouveau>` | Remplacer une décision : l'ancienne n'est plus servie mais reste lisible avec `--include-superseded` |
| `grimoire memory list` | Lister les mémoires stockées, paginées |
| `grimoire memory delete <id>` | Supprimer une entrée |
| `grimoire memory gc` | Consolider et compacter |
| `grimoire memory export` / `import` | Exporter vers JSON, réimporter |
| `grimoire memory reindex-lexical` | Reconstruire l'index lexical compagnon |
| `grimoire memory gate` | Gate de parité Memory OS entre Weaviate et Neo4j |
| `grimoire memory graph` / `vector` | Synchroniser et vérifier les projections |
| `grimoire memory bundle` | Construire, installer et vérifier les bundles de modèles d'embedding |
| `grimoire memory shared` | Mémoire transverse : ce qui reste vrai d'un projet à l'autre |
| `grimoire memory rules add` / `list` / `check` | Règles parsables par couche (`ALWAYS\|NEVER <verbe> <objet> [@portée]`) : conflits décidés par le code, plafond `rules.max_tokens_per_layer`, écriture via la frontière de confiance |
| `grimoire memory rules explain <chemin>` | Règles qui valent pour un fichier : rang, couche d'origine (`organization` > `team` > `project` > `agent`), ce qui la fait céder, et si `host sync` la livre (couches `team` et `project` seulement) |
| `grimoire memory rules pack list` / `show <id>` / `install <id> [--layer]` / `verify` | Packs de règles par stack (`python`) : chaque règle porte un vérificateur lu sur le code du projet, ou se déclare `advisory` (un pack de plus de 50 % d'advisory est refusé). `install` écrit les règles par la frontière de confiance ; `verify` rend `pass`, `fail` ou `unevaluated` par règle et sort en code 1 si l'une est violée |
| `grimoire memory rules propose` / `proposals [--pending]` / `accept <id> [--layer]` / `reject <id>` | Règles candidates tirées des refus de gate observés (TraceLedger, recette `grimoire.task-gate`) : une signature (transition, preuve manquante) vue sur au moins 2 tâches distinctes devient `ALWAYS provide <preuve> before <transition>`, avec ses citations de preuve. Rejouer `propose` ne réécrit rien ; `accept` passe par les contrôles de `rules add` (un refus n'écrit rien) ; une proposition refusée ne revient qu'une fois ses tâches doublées. Aucune question n'est posée pendant un flow |
| `grimoire memory rules effect [--min-tasks 5]` | Effet de chaque règle acceptée sur sa signature : `before_rate`, `after_rate`, `n_before`, `n_after`, `status` (`effective`, `unproven`, `harmful`, test exact de Fisher unilatéral à 5 %). Sous le seuil de tâches de chaque côté, `unproven` quel que soit l'écart ; `retire_suggested` sur `harmful` ou sur `unproven` après 20 tâches. JSON reproductible, aucun score de confiance |
| `grimoire memory facts` / `diary` | Graphe de faits temporels, journaux d'agents |
| `grimoire memory migrate` | Planifier et exporter les migrations Memory OS |
| `grimoire memory migrate run [--source B] [--source-collection C] [--project P] [--overwrite] [--all-projects] [--apply]` | Copier les souvenirs d'un autre backend, ou d'une autre collection du même backend, **par upsert sous leur identifiant d'origine** (`superseded_by` valide, `created_at` conservé). Ne crée que les identifiants **absents** de la cible : une relance ne défait pas une décision revue ; `--overwrite` remplace explicitement (l'aperçu en donne le nombre). Un registre des identifiants déjà copiés (`_grimoire/_memory/migration/copied-*.json`, par couple source/cible) empêche de recréer un souvenir copié puis supprimé dans la cible, même avec `--overwrite`. Un souvenir refusé par la cible (filtre d'injection, identifiant non UUID vers Qdrant, réponse 400) est listé, l'aperçu l'annonce, la copie continue ; si tout est refusé la commande sort en code 1. Une source inexistante est refusée sans rien créer. `--project` ne copie que `metadata.project_name = P` ; sur un segment partagé (`--source-collection`), sans `--project` la commande refuse (souvenirs sans `project_name` compris) tant que `--all-projects` n'est pas passé. Aperçu sans `--apply` |
| `grimoire memory split [--source-collection C] [--projects A,B] [--export F] [--allow-unassigned] [--apply]` | Découper un segment partagé entre projets en une collection `<C>-<projet>` par `project_name`, identifiants conservés. Aperçu par défaut ; `--apply` refuse si une cible désigne le même store que la source (avec Weaviate, `weaviate_collection` est résolu), si deux projets ne diffèrent que par la casse ou la ponctuation (même collection cible), si la source n'existe pas ou s'il reste des souvenirs sans `project_name` (ils deviendraient invisibles ; `--allow-unassigned` le dit explicitement). Puis : export JSON de la source, copie par upsert, vérification des comptes et des identifiants, `memory.collection_prefix` repointé dans chaque projet du registre qui lisait ce segment (tout ou rien : tout est rendu et écrit à côté avant le moindre remplacement). La source n'est jamais supprimée |
| `grimoire memory graph sync-code --backend neo4j\|codegraph-neo4j` | Destination du graphe de code : `neo4j` (défaut, graphe mémoire, celui que lisent `verify` et la fraîcheur) ou `codegraph-neo4j` (`Neo4jCodeGraphBackend`, requêtes d'impact Cypher) |
| `grimoire memory taxonomy` | Taxonomie aile / salle / pièce |

---
