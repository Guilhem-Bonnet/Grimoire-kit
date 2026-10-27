---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Parité de coût du lot K — estimation sans nouveau rejeu payant — 2026-10-27

> Issue [#642](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/642), lot K. **Aucun run `claude -p` n'a été lancé pour ce rapport** :
> le propriétaire a refusé tout nouveau banc payant (directive du 2026-10-26, « adapte pour faire sans et estimer sans »).
> Les chiffres ci-dessous sont donc des **estimations tirées des campagnes déjà mesurées**, pas une mesure nouvelle.
> Sources : [rejeu-lot-k-2026-09-29.md](rejeu-lot-k-2026-09-29.md) (60 runs `kit-gov` après le correctif #651, 33,72 $) et
> [rejeu-lot-l-2026-10-02.md](rejeu-lot-l-2026-10-02.md) (`nu` et `kit-gov` le même jour, 120 runs, 22,84 $).

## 1. Ce qui est livré et vérifié par test

| Critère | Preuve | Statut |
|---|---|---|
| Message `acceptance.no_tests_collected` sans « écris un test » ni « justifie » | `tests/test_agentic_standard.py` (assertion sur le message, repli exit 1 sans test collecté) | livré par [#651](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/651), test vert |
| `gate check --strict` sans `--task-id` ni carte `in_progress` échoue en nommant le board | `test_cli_gate_check_without_task_id_fails_when_bootstrap_is_not_on_the_board` | livré par #651, test vert |
| Entrée `[Unreleased]` / CHANGELOG pour #642 | `CHANGELOG.md` (entrées « fix(standard) … lot K » et rapports K/L) | présente |

## 2. Critères chiffrés — estimation

| Critère | Cible | Dernière mesure | Estimation à code courant | Atteint ? |
|---|---|---|---|---|
| Part de runs avec excursion | < 10 % | K : 26,7 % (16/60) ; L : 16,7 % binaire, **8,3 % coûteuses** (5/60) | 8-17 % selon la définition ; < 10 % seulement pour les excursions coûteuses | **partiel** (non démontré au sens binaire) |
| Coût médian / tâche résolue | ≤ 105 % du nu | K : ≈ 122 % ; L : ≈ 124 % | ≈ 120-125 % — le surcoût (≈ +0,04 $ par run) est structurel, non lié aux excursions | **non** |
| `gate check` vert au dernier appel | 60/60 | K : 60/60 ; L : 60/60 | 60/60 (aucune modification du gate depuis) | oui (deux campagnes) |

## 3. Limites de cette estimation

- Les lots K et L diffèrent par le modèle par défaut de `claude -p` (voir §1.3 du rapport L) : l'écart de 122 % à 124 % n'est pas significatif.
- Aucune mesure n'a été faite sur le code de `main` du 2026-10-27 ; l'estimation suppose qu'aucun changement postérieur au lot L n'a touché le chemin chaud du gate.
- La cible de coût ≤ 105 % **n'est pas atteignable par ce lot** : elle demande de réduire le surcoût fixe de la gouvernance, pas de corriger un message.

## 4. Décision

**Critère 3 non démontré — clôture en l'état avec dérogation humaine** (décision de Guilhem, 2026-10-27, `dir-21054a88c51b`).
Le lot K est clos sur ses deux correctifs mécaniques (livrés par #651, testés). Les cibles chiffrées (excursions < 10 %, coût médian ≤ 105 % du nu)
ne sont ni mesurées ni atteintes ; seul « 60/60 gate check verts » est établi, par les campagnes K et L. Le critère de coût reste ouvert dans #642
et ne se tranchera qu'avec un rejeu explicitement demandé (≈ 34 $ pour `kit-gov` seul). La dérogation est actée par le canal humain, pas par le relecteur.
