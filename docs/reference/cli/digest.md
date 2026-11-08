---
audience: référence
---

# Textes longs : `grimoire digest`

`grimoire digest` résume un texte volumineux (journal de CI, sortie du tick,
transcript) avec le fournisseur LLM le moins cher du registre, pour que l'agent
interactif n'ait pas à payer la lecture intégrale. Les lignes d'erreur, elles,
ne passent jamais par le modèle : elles sont extraites du texte source et citées
mot pour mot.

```text
grimoire digest [FICHIER|-] [--goal "<ce que je cherche>"] [--max-words 200]
                [--provider <id>] [--project-root <chemin>]
```

| Option | Effet |
| --- | --- |
| `FICHIER` / `-` | Texte à résumer ; `-` (défaut) lit l'entrée standard. |
| `--goal` | Ce que la lecture doit trouver ; oriente les prompts de résumé. |
| `--max-words` | Borne du résumé, en mots (200 par défaut). |
| `--provider` | Force un fournisseur du registre (capacité `chat`, palier `cheap`). |

## Exemple

```bash
pytest -q > /tmp/pytest.log 2>&1 || true
grimoire digest /tmp/pytest.log --goal "pourquoi la CI est rouge" --max-words 120
```

```text
La CI échoue sur la suite unitaire : deux tests de routage ne trouvent plus de
fournisseur admissible après le changement de politique de données.
...
Lignes d'erreur :
  412: FAILED tests/unit/test_routing.py::test_choose_prefers_local
  987: AssertionError: expected 'local', got None
```

## Choix du fournisseur

La commande interroge le registre (`llm-provider-registry.yaml`) : fournisseur
activé et disponible, capacité `chat`, modèle affecté au palier `cheap`.
`currency: local` est préféré ; à défaut, le premier fournisseur admis par
l'ordre du registre est appelé.

La politique de données de `grimoire task dispatch` (#1133) s'applique : sans
classe déclarée pour le projet (`routing.default_data_class`), aucun fournisseur
externe n'est appelé. Un fournisseur `provider_type: local` reste admissible —
rien ne sort de la machine.

Un texte plus long que 6 000 caractères est découpé en morceaux : un résumé par
morceau, puis la synthèse de ces résumés.

## Repli déterministe

Si aucun fournisseur n'est admissible, ou si l'appel échoue, la commande rend
un repli sans modèle — le motif (`résumé non généré : …`), le début et la fin du
texte, et les lignes d'erreur — puis **sort en 0**. Le texte est toujours lu ;
seule une entrée illisible (fichier absent) fait sortir en 1.

La sortie de tout fournisseur est débarrassée de ses séquences ANSI (CSI et
OSC) avant d'être lue : `ollama run` colore sa sortie même hors terminal, et le
message d'erreur doit rester citable tel quel. Tout `grimoire task dispatch`
vers un fournisseur local en hérite.
