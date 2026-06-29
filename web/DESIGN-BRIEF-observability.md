# Brief design — Observability / Insights (Grimoire Kit)

Document unique à transmettre à claude.ai/design (projet « Grimoire »), et référence
de câblage des données. Tout le contenu listé ici est **réel**, généré par
`scripts/gen-site-data.py` dans `web/data/*.json`.

> Esprit de ce brief : il donne l'**intention**, les **contraintes dures** et la
> **palette de données** — pas un gabarit figé. Tu as la main sur la composition,
> les regroupements, les types de visualisation, la navigation et ce que tu choisis
> de mettre en avant ou d'omettre. Prends les meilleures décisions selon le besoin.

---

## 1. Intention & audience

Rendre lisible, d'un coup d'œil, **ce que fait le runtime agentique de Grimoire Kit**
et **ce que coûte / produit le projet**. Lecteurs : un dev ou un évaluateur qui veut
juger en 30 secondes la santé, le coût, les performances et la **gouvernance** du
système. L'observabilité **est** l'argument produit (« tout est tracé, vérifiable,
rejouable ») — la page doit incarner ça, pas l'affirmer.

## 2. Contraintes dures (non négociables)

- **Design system Forge** : fond `#0B0C0E`, élévations `#121418`/`#1A1D22`/`#22262C`,
  accent orange `#FF6B3D` ; data-couleurs cyan `#6EE7FF`, violet `#A78BFA`,
  ambre `#FCD34D`, vert `#34D399`, rouge `#F87171` ; typo Geist (titres) +
  Geist Mono (chiffres/labels) ; bordures fines `#ffffff14`, halo orange au survol,
  fond blueprint (grille SVG très faible opacité).
- **ZÉRO emoji Unicode** — icônes SVG maison ou marqueurs ASCII uniquement.
- **Fichier autoporté** (styles inline ou `<style>`), pas de dépendance JS externe.
- **Markup répétable** : une section = un bloc clair, une carte/ligne = un motif
  identique réutilisable (je remplace les exemples statiques par un rendu data-driven).
- **Honnêteté des données** : trois statuts à distinguer visuellement —
  `réel` (live/local) · `snapshot démo` (vitrine) · `privé` (jamais sur la vitrine
  publique). Ne jamais inventer un chiffre.

## 3. Ta latitude (décide librement)

- La **composition** : ordre, regroupement et hiérarchie des sections.
- Les **visualisations** : donut, barres, jauge, radar, waterfall, sparkline,
  heatmap, table… choisis ce qui sert le mieux chaque donnée.
- La **navigation** : page longue, onglets, sections repliables, « above the fold ».
- La **densité** : ce que tu mets en avant (hero metrics) vs en détail secondaire.
- **Omettre** ce qui n'apporte pas, **fusionner** ce qui se recoupe.
- Les **filtres temporels** (jour/mois/année) là où la donnée est datée.
- L'**interaction** (survol/focus/drill-down) cohérente avec le reste du site.

Seules les contraintes du §2 sont fermes. Le reste, c'est ton métier.

## 4. Palette de données réelles

Quatre fichiers JSON, lus par `fetch`. Exemples = vraies valeurs actuelles.

### `meta.json` — identité du projet

```jsonc
{ "version":"3.16.0",
  "counts": { "tools":108,"agents":33,"archetypes":10,"patterns":36,
              "pattern_categories":11,"profiles":5,"artifact_types":41,"tests":5939 },
  "archetypes":[...], "profiles":["starter","controlled","orchestrated","governed","production"],
  "inventory": { "agents_by_archetype": { "meta":["agent-optimizer","memory-keeper",...],
                                          "infra-ops":["pipeline-architect","k8s-navigator",...] } },
  "links": { "github","pypi","docs","license" } }
```

### `observatory.json` — runtime (réel local / snapshot démo)

```jsonc
{ "is_demo": true,
  "traces":[{timestamp,agent,event_type,session,payload}],   // ACTION|DECISION|HANDOFF|CHECKPOINT|REMEMBER|WARN|ERROR
  "agents":[{id,persona,capabilities[],metrics:{traces}}],
  "relationships":[{from_agent,to_agent,type,strength,interactions,avg_trust}], // handoff|spec|memory|escalation
  "spans":[{span_id,parent_span_id,trace_id,tool,operation,agent,duration_ms,
            tokens,input_tokens,output_tokens,model,provider,cost_usd,retries,status}],
  "metrics": { total_tokens, total_cost_usd, total_input_tokens, total_output_tokens,
               p50_latency_ms, p95_latency_ms, p99_latency_ms, throughput_per_min,
               error_rate, retry_rate, avg_cost_per_trace, avg_tokens_per_span,
               avg_duration_ms, avg_spans_per_trace, avg_trust, span_count, trace_count,
               by_model, by_provider, cost_by_model },
  "perf": { p50_ms, p95_ms, p99_ms, avg_ms,
            by_tool:{tool:{count,total_ms,avg_ms,p50_ms,p95_ms}}, by_agent:{}, by_model:{},
            slowest:[{label,agent,model,duration_ms,cost_usd,status}] },
  "graph_stats": { nodes, edges, avg_degree, density, most_central:{agent,degree} },
  "sessions":[], "event_types":[] }
```
Valeurs démo : 18 traces · 7 agents · coût $0.27 · p50/p95/p99 1300/4650/5090 ms ·
3 modèles (opus-4-8 $0.231, gpt-5.3-codex $0.022, gemini-3-pro $0.0065).
Spans groupés par `trace_id` = arbres causaux parent→enfant (waterfall).

### `activity.json` — projet & coût

```jsonc
{ "git": { commits_total:343, commits_7d, commits_30d, avg_per_day_30d,
           per_day:[{date,count}], contributor_count, contributors:[{name,commits}], last_commit:{} },
  "pulls":[{number,title,url,state,author,created_at}], "pulls_open":2,
  "repo":{stars:4,forks:1,url,name}, "releases":[{tag,date}],
  "context_pressure":{ peak_pct:0.0575, avg_pct, window:200000, by_day:[{date,peak_pct,avg_pct}] },
  "economy": {
    "rtk": { total_commands:454, saved_tokens:3608801, savings_pct:96.1, input_tokens, output_tokens,
             monthly:[{month,commands,saved_tokens,savings_pct}], weekly:[{week,...}], daily:[{date,...}] },
    "ccusage": {}   // PRIVÉ — vide en public ; en local opt-in :
    //   { total_cost, by_model, models_used, days:[{date,cost,input,output,total}],
    //     cache:{ read_tokens, creation_tokens, hit_ratio } }
  },
  "tracking": { ci:[{name,status,conclusion,event,branch,created_at,url}], ci_status:"success",
                coverage:{percent:65.92}, pypi:{last_day:100,last_week:109,last_month:589} },
  "delivery": { deploy_freq_7d:13, change_failure_rate:0.0, ci_avg_duration_s:57.8,
                pr_lead_time_median_h:132.97, pr_merged_sample:24 } }
```

### `insights.json` — métriques avancées

```jsonc
{ "governance": { "antifragile": { score:42, level:"ROBUST", evidence:12, summary,
                                   dimensions:{ "Récupération":80, "Tendance signaux SIL":70,
                                   "Qualité des décisions":50, "Non-récurrence patterns":43.3,
                                   "Résolution contradictions":0, "Vélocité d'apprentissage":0 } } },
  "bench": { report_count:17, reports:[dates], latest:[{agent,score,failures,ac_pass}], as_of },
  "routing": { samples:43, by_model:{"gpt-4o":43}, by_task_type:{"coding":43}, by_complexity:{}, est_cost_total },
  "memory": { contradictions:4, failures:5, decisions:0, learnings_files:8, backends:[] },
  "code": { loc:104184, py_files, test_loc, tests_code_ratio:0.57, tags_total:187,
            releases_30d:21, churn_top:[{file,changes}], issues_open:1, issues_closed },
  "efficiency": { commits_per_release:1.9, tests_per_kloc:57.1, tests_code_loc_ratio:0.57,
                  rtk_saved_per_command, rtk_savings_pct:96.8 },
  "freshness": { days_since_commit:0, days_since_release:4, days_since_bench:0, generated_at } }
```

## 5. Trame indicative (à réorganiser librement)

Hero + badge état (LIVE / SNAPSHOT DÉMO) · KPIs runtime (traces/agents/sessions/types) ·
métriques perf (coût, tokens, p50/p95/p99) · multi-LLM (coût & % par modèle/provider) ·
constellation d'agents (relations pondérées) · chaînes causales (waterfall de spans) ·
gouvernance (jauge antifragile + radar dimensions) · inventaire (agents par archétype +
familles) · économie RTK (96.1%) · activité projet (commits/jour, contributeurs, PRs,
releases) · travail suivi (CI, couverture 65.92%, PyPI) · bench (rapports/tendance) ·
routing · mémoire · code (LOC, churn, ratio tests). Trace log filtrable.

## 6. Contrat de câblage

Produis des **exemples statiques réalistes** dans le markup. Je remplace ensuite chaque
exemple par une boucle sur le JSON correspondant (live en local, snapshot démo sur la
vitrine), en gardant **exactement** ton CSS et tes classes. Markup répétable = câblage
propre. Une section qui n'a pas de données (`is_demo` faux + vide, `ccusage` vide,
`bench.latest` vide) doit se masquer proprement.

## 7. Pistes complémentaires (optionnelles — à toi de juger l'intérêt)

**Implémenté depuis (voir §4)** : efficacité du cache (`economy.ccusage.cache`, privé),
livraison DORA (`activity.delivery`), ratios d'efficience (`insights.efficiency`),
fraîcheur des signaux (`insights.freshness`), centralité du graphe (`observatory.graph_stats`),
tendance RTK (`economy.rtk.weekly/daily`).

Restantes, à ajouter si utiles (légende : ✅ réel · 🔶 câblage en plus · 🔒 privé) :

- **Concentration fournisseurs** ✅ — répartition % par provider (risque vendor),
  diversité des modèles (dérivable de `by_provider` / routing).
- **Posture des hooks de gouvernance** 🔶 — hooks par mode enforced/shadow/canary :
  **absent de ce dépôt** (`.github/hooks/` vide) — à activer si un registre apparaît.
- **Couverture documentaire** 🔶 — % de docstrings, nb de docs, fraîcheur du README.
- **Budget d'erreur / SLO** ✅ — error rate vs une cible, tendance.
- **Anomalies** ✅ — drapeaux dérivés : spans au-delà du p95, pics de coût, clusters d'erreurs.

---

*Généré comme référence par l'assistant — câblage assuré au retour du design.*
