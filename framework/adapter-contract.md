<!-- markdownlint-disable MD025 MD007 MD026 MD036 MD060 -->

# Grimoire Adapter Contract — v1

> Contrat d'interface neutre entre le **coeur grimoire** (source de verite) et les **adaptateurs de harness** (prises).
> Companion de [grimoire-core.manifest.yaml](grimoire-core.manifest.yaml).
> Phase 0 de la direction D (bridge agnostique). Statut : draft.

## 1. Objet

Ce contrat definit **ce qu'un adaptateur doit exposer** pour brancher grimoire sur un harness, et **comment** il traduit les verbes neutres du coeur vers les primitives de ce harness. Tout adaptateur conforme (grimoire-MCP, pi-package, opencode-pack, copilot-extension) consomme le meme coeur et n'ajoute aucune logique metier.

Regle d'or : **le coeur decide, l'adaptateur traduit.** Si un adaptateur contient une regle d'orchestration, de routing ou de validation qui n'est pas dans le coeur, c'est un bug de conception.

## 2. Les verbes neutres

L'API du coeur se reduit a huit verbes. Chaque adaptateur les expose dans les termes de son harness (outil MCP, extension TS, agent natif...). Schemas en pseudo-JSON, neutres de tout langage.

### 2.1 `list_skills`

Retourne l'index des capacites disponibles.

```jsonc
// in:  { "domain"?: string }            // filtre optionnel par domaine
// out: { "skills": SkillDescriptor[] }
```

### 2.2 `run_skill`

Execute une skill nommee.

```jsonc
// in:  { "id": string, "input": object, "context"?: ContextRef }
// out: { "ok": boolean, "result": object, "evidence"?: Evidence[] }
```

### 2.3 `route`

Classe une intention et retourne une decision de routing auditable (intent-routing + ARG).

```jsonc
// in:  { "intent": string, "candidates"?: string[] }
// out: {
//   "target": { "kind": "skill"|"agent"|"profile", "id": string },
//   "confidence": number,        // 0..1
//   "rationale": string,
//   "fallback": { "kind": string, "id": string } | null
// }
```

### 2.4 `orchestrate`

Dispatch SOG d'un objectif vers un ou plusieurs sous-agents, avec agregation.

```jsonc
// in:  { "goal": string, "constraints"?: object, "autonomy"?: "L1"|"L2"|"L3" }
// out: {
//   "plan": Step[],              // etapes dispatchees
//   "results": object,           // agrege
//   "handoffs": Handoff[],       // transitions ARG effectuees
//   "open_questions": Question[] // remontees via escalate_questions
// }
```

### 2.5 `validate`

Verifie une sortie (CVTL + HUP + trust scoring). Aucune claim ne passe sans ce verbe sur les sorties critiques.

```jsonc
// in:  { "output": object, "level": "light"|"standard"|"critical" }
// out: {
//   "trust_score": number,       // 0..1
//   "uncertainty": string[],     // points declares incertains (HUP)
//   "cross_checked": boolean,    // CVTL applique ?
//   "verdict": "pass"|"revise"|"block",
//   "notes": string
// }
```

### 2.6 `escalate_questions`

Batch de questions a l'utilisateur (QEC) au lieu d'interruptions par agent.

```jsonc
// in:  { "questions": Question[] }     // Question = { id, prompt, options?[] }
// out: { "answers": { [id: string]: string } }
```

### 2.7 `recall` / `remember`

Acces a la memoire operationnelle (Memory OS).

```jsonc
// recall   in: { "query": string, "k"?: number }   out: { "hits": MemoryHit[] }
// remember in: { "fact": string, "type": string, "tags"?: string[] } out: { "id": string }
```

## 3. Schemas de descripteurs

```jsonc
SkillDescriptor = {
  "id": string,           // slug = nom du dossier SKILL.md
  "name": string,
  "description": string,  // frontmatter ; porte les triggers "Use when:"
  "domain": string,       // regroupement neutre (cf. manifeste)
  "path": string,         // .github/skills/<id>/SKILL.md
  "model_invocable": boolean  // false si disable-model-invocation
}

ProtocolDescriptor = { "id": string, "category": string, "path": string, "summary": string }
ArchetypeDescriptor = { "id": string, "kind": "builtin"|"stack"|"feature", "summary": string }
ContextRef = { "cwd": string, "files"?: string[], "session"?: string }
Evidence    = { "kind": string, "ref": string }   // preuve (test, lint, diff...)
```

## 4. Exigences de conformite d'un adaptateur

Un adaptateur est **conforme** s'il satisfait toutes ces regles :

1. **Source unique** : il lit skills/protocoles/archetypes depuis le coeur (`framework/` + `.github/skills/`), jamais une copie divergente.
2. **Couverture des verbes** : il expose au minimum `list_skills`, `run_skill`, `route`, `validate`. Les verbes `orchestrate`, `escalate_questions`, `recall`, `remember` sont requis pour une integration profonde.
3. **Pas de logique metier** : aucune regle d'orchestration/routing/validation locale. Traduction seulement.
4. **Provider-neutre** : tout appel modele passe par le moteur unifie (`@earendil-works/pi-ai`), declare avant execution.
5. **Surface MCP bornee** (adaptateurs MCP) : exposer les verbes de haut niveau, pas un outil par skill. Le long tail passe par `list_skills`/`run_skill` (anti-bloat, cf. these section 16).
6. **Tracabilite** : emettre les evenements de trace du coeur (`grimoire-trace`) pour audit.
7. **Degradation gracieuse** : si le harness ne supporte pas un verbe (ex. pas d'UI pour `escalate_questions`), le declarer et fournir un repli documente.

## 5. Mapping verbe -> primitive de harness

Comment chaque adaptateur realise les verbes. (`spec-only`/`planned` = a implementer.)

| Verbe | grimoire-MCP (universel) | pi-package | opencode-pack | copilot-extension |
|---|---|---|---|---|
| `list_skills` | outil MCP `grimoire.list_skills` | `ctx` + dossier skills | `.opencode/skills` discovery | commande `@grimoire /skills` |
| `run_skill` | outil MCP `grimoire.run_skill` | skill auto-invocable pi | skill OpenCode | `@grimoire /run skill` |
| `route` | outil MCP `grimoire.route` | extension d'orchestration | agent custom + tool | webhook routing |
| `orchestrate` | outil MCP `grimoire.orchestrate` | exemple `subagent/` + `handoff.ts` | agents natifs + permissions | Copilot agents |
| `validate` | outil MCP `grimoire.validate` | hook event-bus + double-call pi-ai | plugin lifecycle hook | webhook gate |
| `escalate_questions` | outil MCP `grimoire.ask` | `ctx.ui.select` | `opencode` prompt UI | Copilot chat prompt |
| `recall`/`remember` | outils MCP memoire | extension + Memory OS | plugin + Memory OS | webhook + Memory OS |

## 6. Tests de conformite (cible Phase 5)

Le CI multi-target devra verifier, pour chaque adaptateur :

- **Parite de catalogue** : `list_skills` retourne exactement les 44 skills du manifeste (ni plus, ni moins).
- **Determinisme du routing** : un jeu d'intentions de reference produit les memes `target` que le coeur.
- **Gate de validation** : une sortie volontairement fausse declenche `verdict: revise|block`.
- **Pas de drift** : un diff du coeur sans regeneration des adaptateurs fait echouer le CI.
- **Provider-neutre** : aucun appel modele hors `pi-ai` (lint d'imports).

## 7. Versionnement

- Contrat versionne (`v1`). Tout changement incompatible incremente la version majeure.
- Le manifeste (`grimoire-core.manifest.yaml`) declare la version de contrat qu'il cible.
- Les adaptateurs declarent la version de contrat qu'ils implementent ; le CI refuse un adaptateur en retard de version majeure.

---

## Prochaines etapes (sortie de Phase 0)

1. Geler ce contrat v1 apres revue.
2. Phase 1 : implementer `grimoire-mcp` couvrant `list_skills`, `run_skill`, `route`, `validate` ; brancher sur OpenCode ; moteur = pi-ai.
3. Ajouter au manifeste les chemins reels (`spec` des adaptateurs) au fil de leur implementation.
