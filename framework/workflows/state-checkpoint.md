---
kind: orchestration
description: "Persistance d'état entre sessions : un workflow long repart du step interrompu, variables comprises"
agents: [dev]
patterns: [ORC-09]
---
<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/branch.svg" width="32" height="32" alt=""> State Checkpoint & Resume Protocol — Obsolète

> **BM-06 — Obsolète.** Cette fiche décrivait un mécanisme de checkpoint en prose, doublon du
> kernel d'exécution réel : machine à états, checkpoints JSONL, reprise, seize tests.
>
> Implémentation réelle : [`src/grimoire/runtime/kernel.py`](../../src/grimoire/runtime/kernel.py).

<img src="../../docs/assets/divider.svg" width="100%" alt="">

*BM-06 State Checkpoint & Resume Protocol | framework/workflows/state-checkpoint.md*
