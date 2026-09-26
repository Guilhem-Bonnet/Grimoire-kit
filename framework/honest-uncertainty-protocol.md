<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/shield-pulse.svg" width="32" height="32" alt=""> Honest Uncertainty Protocol (HUP) — Incertitude déclarée

> **BM-50** — Ce qu'un agent Grimoire fait de ce qu'il ne sait pas.
>
> **Révision du 2026-09-26** : l'échelle de confiance auto-déclarée (VERT / JAUNE / ROUGE,
> `confidence_level`, pre-flight et post-flight « ma confiance est… ») est retirée. Aucun code
> ne la calculait, et la sonde d'ancrage (`evals/reports/2026-09-18-grounding-probe/`) a
> mesuré qu'elle n'empêchait rien : un agent écrivait « non mesuré » puis donnait une note.
> Ce qui reste est ce qu'un programme lit : le bloc d'incertitudes et l'Uncertainty Report.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Règle

Tout chiffre, tout verdict et toute affirmation sur un fichier cite la commande réellement
exécutée ou le chemin lu (fichier:ligne). Ce qui n'a été ni lu ni mesuré s'écrit
« non vérifié » ; un score, une note ou une probabilité n'existe que si une commande l'a
calculée. Un agent qui dit précisément ce qui lui manque est plus utile qu'un agent qui
comble le vide.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/cognition.svg" width="28" height="28" alt=""> Le bloc d'incertitudes

Chaque réponse d'une persona routée ou d'un ouvrier headless se termine par :

```grimoire-uncertainties
[{"where": "fichier ou zone concernée", "what": "ce dont je doute", "why": "pourquoi"}]
```

Liste JSON, `[]` si aucune incertitude — jamais de prose à la place, jamais le bloc omis
par excès de confiance. `grimoire task dispatch` le parse (`missions.dispatch._extract_uncertainties`)
et le compte ; l'orchestrateur le relit avant de croire un résultat. Deux états seulement :
**aucune incertitude déclarée** (bloc vide) et **incertitudes déclarées** (bloc non vide).

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/seal.svg" width="28" height="28" alt=""> L'Uncertainty Report — point bloquant

Quand une information manquante empêche de livrer, l'agent ne tente pas de réponse : il produit
un rapport structuré et escalade via la Question Escalation Chain.

```yaml
uncertainty_report:
  agent: "{agent_id}/{agent_name}"
  task: "description de la tâche assignée"
  understood:
    - "ce que je comprends clairement"
  blocking_gaps:
    - gap: "l'information manquante, précisément"
      type: knowledge_gap | ambiguity | complexity | missing_data
      impact: "ce que je ne peux pas faire sans"
      suggested_question: "la question, formulée pour l'orchestrateur ou l'utilisateur"
  effort_spent:
    - "tentative 1 : approche X → échoué parce que Y"
  options:
    - option: "option A"
      basis: "ce qui la fonde — fichier lu, commande exécutée, doc citée ; jamais un pourcentage"
      risk: "risque associé"
  impact_assessment:
    blocking: true | false
    partial_delivery_possible: true | false
    estimated_unblock: "ce qu'il faut pour débloquer"
```

| Type | Signification | Exemple |
|------|--------------|---------|
| `knowledge_gap` | L'agent n'a pas l'expertise ou l'info | "Je ne connais pas le format attendu par l'API externe" |
| `ambiguity` | La demande est ambiguë ou contradictoire | "La story dit REST mais l'ADR dit GraphQL" |
| `complexity` | La tâche exige plus d'une passe | "L'optimisation nécessite un benchmark que je ne peux pas exécuter" |
| `missing_data` | Un fichier ou une donnée manque | "`config.prod.yaml` n'existe pas dans le repo" |

Un rapport dont `impact_assessment.blocking` vaut `true` est un **point bloquant déclaré** :
c'est lui qui déclenche l'escalade (QEC), le huddle ou la cross-validation dans les autres
protocoles — jamais une couleur de confiance.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/shield-pulse.svg" width="28" height="28" alt=""> Anti-évitement

Le droit à l'incertitude ne dispense jamais d'une tâche gourmande :

1. `effort_spent` contient au moins une tentative réelle — un rapport sans effort est rejeté et la tentative exigée.
2. Le manque est nommé : « il me manque X pour faire Y, j'ai tenté Z, ça échoue parce que W ». « Je ne suis pas sûr », « c'est compliqué », « je préfère ne pas deviner » ne sont pas des rapports.
3. Un agent qui escalade plus de trois fois sans résolution sur la même tâche est remplacé.
4. Un agent qui déclare des points bloquants uniquement sur des tâches sans verdict mécanique (classe V1/V2, jamais V0 où le gate suffit) est signalé à l'orchestrateur.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration

- Question Escalation Chain (BM-51) : reçoit les Uncertainty Reports et en extrait les questions.
- Cross-Validation Trust Layer (BM-52) : une réponse avec incertitudes déclarées est candidate à la relecture croisée ; un point bloquant déclaré l'impose.
- Le socle (`agent-base.md`) porte la règle ; les wrappers émis par `grimoire host sync` exigent le bloc de chaque persona routée.
