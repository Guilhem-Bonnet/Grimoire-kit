# Brief design — Memory Manager (Grimoire Kit)

Page dédiée `memory.html` : voir et comprendre la mémoire agentique (et, en local
uniquement, la piloter). À transmettre à claude.ai/design, et référence de câblage.
Données réelles dans `web/data/memory.json` (généré par `scripts/gen-site-data.py`).

> Même esprit que le brief observability : **intention + contraintes dures + palette
> de données + latitude**. Tu décides composition, visualisations, navigation.

---

## 1. Intention

La mémoire Grimoire **est** une base de connaissances (141 entrées : décisions,
learnings, contradictions, failures, handoffs). La page est un **« Obsidian pour la
mémoire agentique »** : on voit le contenu, ses liens, son graphe, sa santé, et le
backend en place. C'est l'incarnation du pitch « la mémoire est inspectable et
vérifiable ».

## 2. Les deux visages (contrainte d'architecture — fondamentale)

| | **Vitrine** (GitHub Pages) | **Cockpit local** (`serve-site.sh`) |
|---|---|---|
| Nature | statique, **public**, lecture seule | localhost, **privé** |
| Inspecter / browse / graphe | oui (snapshot démo) | oui (réel) |
| Éditer / init / upgrade / migrer | **non** (impossible + jamais) | oui, **gated** (lot ultérieur) |

**Ce lot = lecture seule** (inspection + vault browse + graphe + redirections). L'édition
et le pilotage d'infra sont un **cockpit local séparé** (lot suivant). Règles dures :
toute écriture passera par l'**API Memory OS** (jamais de SQL/raw), **localhost-only**,
**jamais sur Pages**. La page doit détecter son mode et masquer toute action si elle
n'est pas en cockpit local.

## 3. Contraintes dures

- Design system **Forge** (fond `#0B0C0E`, accent `#FF6B3D`, data-couleurs cyan/violet/
  ambre/vert/rouge, Geist + Geist Mono, blueprint grid).
- **ZÉRO emoji Unicode** — SVG maison ou ASCII.
- **Fichier autoporté**, pas de JS externe, **markup répétable**.
- **Honnêteté** : `réel` / `snapshot démo` / `privé-local`. La projection vectorielle
  actuelle est **démo** (le store n'a pas encore de vecteurs) — l'indiquer.

## 4. Ta latitude

Composition, choix des visualisations (graphe force-directed, scatter, treemap,
listes, cartes…), navigation (vault + détail, onglets, panneau), ce que tu mets en
avant, ce que tu omets. Seul le §2 (lecture seule, boundary) et le §3 sont fermes.

## 5. Palette de données — `memory.json`

```jsonc
{
  "backend": {
    "active": "qdrant",                       // détecté (qdrant|local|…)
    "modules": ["local","ollama","qdrant_local","qdrant_server"],
    "extras_installed": { "qdrant":true,"weaviate":false,"neo4j":false,"redis":false,"ollama":true }
  },
  "store": { "total_entries": 141,
             "sample": [{ "id","text","tags":[],"created_at" }] },   // échantillon vault (40)
  "counts_by_type": { "memories":141,"contradictions":4,"failures":5,"learnings":8,"decisions":0 },
  "graph": { "nodes":[{id,label,tags}], "edges":[{from,to,tag}] },   // arêtes = tags partagés (réel, peut être creux)
  "vector_projection": { "is_demo": true, "note":"…",
                         "points":[{ "x","y","type" }] },            // scatter 2D — DÉMO tant qu'il n'y a pas d'embeddings
  "lint": { "version","files_scanned","entries_scanned","summary","issues":[] },  // memory-lint réel
  "consoles": { "qdrant":{label,url}, "neo4j":{…}, "redis":{…}, "weaviate":{…} }   // redirections natives
}
```

## 6. Sections (à réorganiser librement)

- **En-tête backend** : backend actif + badge santé + extras installés (chips
  on/off) + entrées totales (141).
- **Topologie** : les couches du Memory OS (vector store · graph store ·
  shared-context · learnings · decisions).
- **Graphe** (pièce maîtresse) : entrées = nœuds, liens = tags partagés ; navigable.
  *(le scatter `vector_projection` est une vue complémentaire, marquée démo.)*
- **Répartition par type** : memories / contradictions / failures / learnings / decisions.
- **Vault browser** : liste/recherche de l'échantillon d'entrées (id, texte, date, tags) ;
  clic → détail.
- **Santé / lint** : findings `memory-lint` (drift, doublons, contradictions non résolues),
  résumé.
- **Consoles** : par backend détecté, un bouton de **redirection** vers la console native
  (Qdrant `:6333/dashboard`, Neo4j `:7474`, RedisInsight, Weaviate) — on ne réimplémente
  pas l'admin brut. Affiche seulement les consoles pertinentes selon `extras_installed`.
- **Zone « actions »** (init/upgrade/édition) : à **prévoir visuellement mais désactivée /
  marquée “disponible en cockpit local”** sur la vitrine. C'est le hook pour le lot suivant.

## 7. Principe directeur

On **possède la couche sémantique** (browse, lien, graphe, recherche, et plus tard
édition gouvernée des souvenirs). On **redirige** vers les consoles natives pour l'admin
brut du moteur (Redis = pur cache → redirection seule). La page reflète cette division.

## 8. Lot suivant (hors de ce brief — cockpit local)

Édition gouvernée (CRUD via Memory OS API + résolution de contradictions, promotion de
learnings), init/upgrade de backend (extras pip), migration (`memory-sync`). Tout cela
**local, gated, jamais public** — fera l'objet d'un design + d'une API séparés.

## 9. Câblage

Exemples statiques réalistes dans le markup ; je remplace par une boucle sur
`memory.json` (réel en local, snapshot démo sur la vitrine), CSS/classes conservés.
Sections sans données (graphe vide, lint vide) : se masquer proprement.

---

*Référence de câblage — généré par l'assistant.*
