<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/microscope.svg" width="32" height="32" alt=""> Grimoire Trace — Audit Trail des Sessions (BM-28)

> Chaque décision d'agent, chaque handoff, chaque `remember` Qdrant, chaque action critique
> est loggé en append-only dans `Grimoire_TRACE.md`. Résultat : **sessions rejouables**, 
> **debugging post-mortem**, **confiance enterprise**.
>
> **Inspiré de** : OpenTelemetry traces, LangSmith traces, Anthropic Claude Projects audit log.

<img src="../docs/assets/divider.svg" width="100%" alt="">


## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Format d'une entrée TRACE

```
[2026-02-27T14:32:01Z] [dev/Amelia]       [ACTION:implement]   story: US-042 | file: src/auth.ts
[2026-02-27T14:32:15Z] [dev/Amelia]       [DECISION]           Utilisé JWT stateless — raison: scalabilité horizontale
[2026-02-27T14:32:30Z] [dev/Amelia]       [REMEMBER]           type: decisions | "JWT stateless pour auth"
[2026-02-27T14:33:00Z] [dev/Amelia]       [HANDOFF→qa/Quinn]   "Implémentation US-042 terminée, tests unitaires OK"
[2026-02-27T14:33:05Z] [qa/Quinn]         [ACTIVATED]          context: handoff from dev
[2026-02-27T14:35:22Z] [qa/Quinn]         [TEST:run]           suite: auth.spec.ts | result: 12/12 PASS
[2026-02-27T14:35:25Z] [qa/Quinn]         [REMEMBER]           type: agent-learnings | "JWT tests : vérifier exp claim"
[2026-02-27T14:35:30Z] [qa/Quinn]         [HANDOFF→sm/Bob]     "QA validé US-042"
[2026-02-27T14:35:31Z] [workflow-engine]  [CHECKPOINT]         id: a3f9b2 | step: 4/6 | status: running
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/microscope.svg" width="28" height="28" alt=""> Structure du fichier `Grimoire_TRACE.md`

Localisé à la racine du projet Grimoire : `_grimoire-output/Grimoire_TRACE.md`

```markdown
# Grimoire Trace — {project_name}
# Généré automatiquement — NE PAS ÉDITER MANUELLEMENT
# Format : [timestamp] [agent/persona] [type:action] payload

## Session {session_id} — {date}

[...entrées append-only...]

## Session suivante...
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Types d'événements

| Type | Description | Généré par |
|------|-------------|-----------|
| `ACTIVATED` | Agent activé avec son contexte | Agent au démarrage |
| `ACTION:implement` | Implémentation d'une tâche | Dev, Quick-Flow |
| `ACTION:review` | Revue de code ou d'artefact | QA, Architect |
| `DECISION` | Décision technique ou produit | Tout agent |
| `REMEMBER` | Écriture en mémoire typée | grimoire memory (fallback mem0-bridge.py) |
| `RECALL` | Lecture depuis mémoire typée | grimoire memory (fallback mem0-bridge.py) |
| `HANDOFF` | Transfert de contrôle inter-agent | Workflow engine |
| `CHECKPOINT` | Sauvegarde d'état de workflow | workflow engine |
| `CHECKPOINT:resume` | Reprise depuis un checkpoint | grimoire-init resume |
| `TOOL:call` | Appel outil MCP / bash | Agents via MCP |
| `RULE:triggered` | Règle DNA ou `.agent-rules` activée | Context Router |
| `WARN` | Alerte non-bloquante | Tout agent |
| `ERROR` | Erreur bloquante | Tout agent |
| `HUP:preflight` | Vérification pré-réponse de confiance | HUP (BM-50) |
| `HUP:postflight` | Validation post-réponse anti-hallucination | HUP (BM-50) |
| `HUP:escalation` | Escalade incertitude ROUGE | HUP (BM-50) |
| `QEC:received` | Question reçue d'un sub-agent | QEC (BM-51) |
| `QEC:auto-resolved` | Question auto-résolue par contexte | QEC (BM-51) |
| `QEC:presented` | Lot de questions présenté à l'utilisateur | QEC (BM-51) |
| `QEC:user-resolved` | Réponse utilisateur redistribuée | QEC (BM-51) |
| `CVTL:requested` | Validation croisée demandée | CVTL (BM-52) |
| `CVTL:verdict` | Verdict du validateur avec trust score | CVTL (BM-52) |
| `CVTL:adversarial` | Revue adversariale déclenchée | CVTL (BM-52) |
| `SOG:routed` | Requête routée vers agent(s) | SOG (BM-53) |
| `SOG:clarify` | Clarification demandée à l'utilisateur | SOG (BM-53) |
| `SOG:aggregated` | Résultats agrégés et livrés | SOG (BM-53) |
| `PCE:mode` | Mode de discussion sélectionné | PCE (BM-54) |
| `PCE:divergence` | Score de divergence vérifié | PCE (BM-54) |
| `PCE:vote` | Vote déclenché ou résultat | PCE (BM-54) |
| `AMN:register` | Agent enregistré dans le mesh | AMN (BM-55) |
| `AMN:heartbeat` | Heartbeat reçu/manqué | AMN (BM-55) |
| `AMN:p2p` | Message P2P direct inter-agents | AMN (BM-55) |
| `SHP:huddle` | Huddle démarré/terminé | SHP (BM-56) |
| `SHP:escalate` | Huddle escaladé en party mode | SHP (BM-56) |
| `ARG:update` | Profil ou relation agent mis à jour | ARG (BM-57) |
| `ARG:synergy` | Synergie détectée entre agents | ARG (BM-57) |
| `HPE:schedule` | DAG planifié et dispatché | HPE (BM-58) |
| `HPE:critical-path` | Alerte sur chemin critique | HPE (BM-58) |
| `HPE:checkpoint` | Checkpoint DAG sauvegardé | HPE (BM-58) |
| `ELSS:emit` | Événement émis sur l'event bus | ELSS (BM-59) |
| `ELSS:observe` | Observation reçue par un agent | ELSS (BM-59) |
| `ELSS:conflict` | Conflit d'état partagé détecté | ELSS (BM-59) |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Protocole d'écriture (pour les agents)

Les agents loguent les événements critiques via le format standardisé :

```bash
# Fonction bash helper (disponible dans grimoire-init.sh comme utilitaire)
grimoire_trace() {
    local agent="$1"   # ex: "dev/Amelia"
    local type="$2"    # ex: "DECISION"
    local payload="$3" # ex: "Utilisé JWT — raison: scalabilité"
    local trace_file="${Grimoire_OUTPUT_DIR:-_grimoire-output}/Grimoire_TRACE.md"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[${ts}] [${agent}] [${type}] ${payload}" >> "$trace_file"
}
```

En Python (depuis mem0-bridge.py ou tout script) :

```python
import datetime
import os

TRACE_FILE = "_grimoire-output/Grimoire_TRACE.md"

def grimoire_trace(agent: str, event_type: str, payload: str):
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [{agent}] [{event_type}] {payload}\n"
    os.makedirs(os.path.dirname(TRACE_FILE), exist_ok=True)
    with open(TRACE_FILE, "a") as f:
        f.write(line)
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/microscope.svg" width="28" height="28" alt=""> Protocole de lecture (replay / forensics)

```bash
# Voir toutes les actions d'un agent
grep "\[dev/Amelia\]" _grimoire-output/Grimoire_TRACE.md

# Voir tous les handoffs
grep "\[HANDOFF" _grimoire-output/Grimoire_TRACE.md

# Voir toutes les décisions
grep "\[DECISION\]" _grimoire-output/Grimoire_TRACE.md

# Voir les checkpoints d'un run
grep "\[CHECKPOINT\]" _grimoire-output/Grimoire_TRACE.md | grep "step:"

# Dernières 50 entrées
tail -50 _grimoire-output/Grimoire_TRACE.md

# Replay d'une session (visualisation)
grep "Session abc123" _grimoire-output/Grimoire_TRACE.md -A 999 | head -200
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration dans `grimoire-init.sh`

La commande `grimoire-init.sh trace` permet de gérer le trace file :

```bash
# Afficher les dernières N entrées
grimoire-init.sh trace --tail 50

# Filtrer par agent
grimoire-init.sh trace --agent dev

# Filtrer par type d'événement
grimoire-init.sh trace --type DECISION

# Filtrer par session branch
grimoire-init.sh trace --branch feature-auth

# Archiver la trace courante (avant 7 jours)
grimoire-init.sh trace --archive

# Vider la trace (reset) — DESTRUCTIF
grimoire-init.sh trace --reset --confirm
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration dans `mem0-bridge.py`

Chaque `remember` et `recall` est automatiquement tracé :

```python
# Dans cmd_remember() :
grimoire_trace("mem0-bridge", "REMEMBER", f"type: {memory_type} | agent: {agent_id} | \"{text[:60]}...\"")

# Dans cmd_recall() :
grimoire_trace("mem0-bridge", "RECALL", f"type: {memory_type} | query: \"{query[:60]}\" | results: {len(results)}")
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Spans, coût & causalité (Synapse BM-46)

Au-delà de la ligne `[ts] [agent] [type] payload`, le middleware `synapse-trace.py`
capture des **spans** structurés enrichissant chaque opération outillée. Une entrée
Synapse porte, en plus de la durée et des tokens :

| Champ | Sens |
|------|------|
| `span_id` | Identifiant unique de l'opération |
| `parent_span_id` | Span déclencheur — reconstitue l'arbre parent→enfant |
| `trace_id` | Racine corrélant tout l'arbre d'une requête |
| `cost_usd` | Coût estimé (par modèle si in/out connus, sinon taux plat) |
| `retries` | Tentatives avant l'issue finale |
| `status` | Issue : `ok` \| `error` \| `timeout` |
| `model` | Modèle invoqué (ex : `claude-opus-4-8`, `gpt-5.3-codex`, `gemini-3-pro`) |
| `provider` | `anthropic` \| `openai` \| `google` \| `local` (déduit du modèle) |
| `input_tokens` / `output_tokens` | Tokens en entrée / sortie (coût détaillé) |
| `pattern` | Pattern gouverné mobilisé (optionnel) |

La causalité est capturée **automatiquement** via un context manager qui empile les
spans actifs : un span ouvert à l'intérieur d'un autre hérite de son `parent_span_id`
et de son `trace_id`.

```python
# framework/tools/synapse-trace.py
tracer = SynapseTracer(project_root)  # taux: cost_per_1k=0.003 par défaut

with tracer.span("orchestrator", "execute", agent="dev") as root:
    root.tokens_estimated = 1800
    with tracer.span("router", "classify") as child:      # parent = root, même trace_id
        child.tokens_estimated = 300
    with tracer.span("llm", "call", retries=2) as call:    # outcome + retries capturés
        call.tokens_estimated = 4200
# À la sortie de chaque bloc : durée, status (error sur exception), coût et lien
# causal sont enregistrés. Le décorateur @synapse_traced passe par ce même mécanisme.
```

Le coût est estimé via `estimate_cost(tokens, per_1k)` (estimation indicative,
surchargeable par tracer). Les agrégats `total_cost_usd` / `total_retries` sont
exposés par `tracer.get_stats()` et l'action MCP `grimoire_synapse_trace status`.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/rocket.svg" width="28" height="28" alt=""> Utilisation enterprise

Le `Grimoire_TRACE.md` est un artefact exploitable pour :
- **Onboarding** : comprendre les décisions prises par les agents précédents
- **Audit** : prouver que les pratiques (TDD, validations) ont été respectées
- **Debugging** : identifier où un workflow a divergé du plan
- **Métriques** : compter les handoffs, décisions, actions par sprint


*BM-28 Grimoire Trace Audit Trail | framework/grimoire-trace.md*
