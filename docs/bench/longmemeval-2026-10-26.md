---
audience: décision
---

# LongMemEval — 2026-10-26

- Score recall_any@k (session) @ k=5 : **66.7%** sur 3 questions
- Jeu : sample (grimoire.data/bench/longmemeval-sample.json), 4 questions
- Backend : local (lexical)
- Abstentions exclues : 1 ; sessions refusées par la politique d'écriture : 0

| Type de question | n | score |
|---|---|---|
| knowledge-update | 1 | 0.0% |
| multi-session | 1 | 100.0% |
| single-session-user | 1 | 100.0% |

> Récupération seule, sans LLM-juge : ne se compare pas au ~94,9 % de réponse cité pour Mastra.
> Échantillon embarqué : prouve que le harnais tourne, pas le niveau du système. Rejouer avec `--dataset` sur le jeu complet.
