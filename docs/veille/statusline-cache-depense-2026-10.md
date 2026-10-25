---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Statusline : état du cache de prompt et plafond de dépense (2026-10)

> Tâche `GAO-veille-stat-001`. Sources ouvertes le 2026-10-25. `claude` n'a pas
> été lancé : le contenu réel du JSON reçu par une statusline n'est **pas observé**.

## Ce que dit la référence officielle

Page [Customize your status line](https://code.claude.com/docs/en/statusline) `[doc]`, lue le 2026-10-25 (champs et sections « Spend limit fields » et « Prompt cache fields »).

**`prompt_cache`** (« Requires Claude Code v2.1.251 or later ») : statistiques du cache de prompt de la conversation principale, calculées à partir des jetons de cache des réponses d'API, donc valables sur tous les fournisseurs ; absent avant la première réponse ; les requêtes de sous-agents ne sont pas comptées. Champs : `warm`, `caching_observed`, `ttl` (`"5m"` ou `"1h"`), `expires_at`, `requests`, `misses`, `expected_rebuilds`, `hit_ratio` (0 à 1, `null` si tout est à zéro), `cache_write_tokens`, `miss_recache_tokens`, `last_miss_at`, `recache_tokens_if_cold`, et, à partir de `2.1.260`, `last_miss_cause` (causes comme `tools_changed`, `system_prompt_changed`, `ttl_expired_5m`, `likely_server_side`) et `miss_causes`. La page précise que `warm` et `hit_ratio` résument le mieux l'état. Une « miss » est une requête qui retraite plus de 5 % et au moins 2 000 jetons de ce que le cache tenait, sans compactage ni nettoyage de résultats d'outils pour l'expliquer.

**`rate_limits.spend_limit`** : n'existe que **derrière une passerelle « Claude apps gateway » qui fixe un plafond de dépense** ; `rate_limits` n'apparaît sinon que pour les abonnés Pro et Max. `used_percentage` (peut dépasser 100) et `resets_at` (secondes epoch) : `2.1.251`. `used_usd`, `limit_usd`, `period` (`daily`, `weekly`, `monthly`) : estimation de la passerelle, jusqu'à environ cinq minutes de retard, **optionnels**, `2.1.284` requis côté Claude Code **et** passerelle, absents si `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` est posé. La page demande de lire chacun avec une valeur de repli.

## État dans le kit

- `git grep -n "prompt_cache\|spend_limit" -- src docs framework` : aucun résultat.
- `statusline_segment` (`src/grimoire/traces/transcript_usage.py:424`) construit `<jetons> tok | <coût>` à partir des **traces du ledger** seulement ; il écrit `n/a` plutôt qu'un coût inventé (`:421`, `STATUSLINE_UNKNOWN`). Ses seuls usages sont ses tests (`tests/unit/test_statusline_segment.py`) : aucun appelant dans `src/`, et aucun réglage `statusLine` n'est émis par `src/grimoire/hosts`. Autrement dit, le segment existe mais le kit n'installe aucune statusline.
- Le journal de la livraison W4-06 (`docs/_archive/changelog-3.63.0-detail.md:241`) le dit déjà : le kit n'a pas de statusline.

## Analyse

- Pour le **cache** : le kit a un intérêt direct, noté dans la veille de septembre (chaque octet injecté par un hook risque de casser un point de coupure de cache). `last_miss_cause` (`system_prompt_changed`, `tools_changed`) est la mesure de ce risque, fournie par l'hôte, sans que le kit lise un journal. Mais ces champs n'existent que dans le JSON passé à un script de statusline, donc il faut qu'une statusline du kit soit installée, ce que rien n'impose et qui touche un réglage personnel de l'utilisateur.
- Pour la **dépense** : `spend_limit` ne vaut que derrière une passerelle d'entreprise ; pour l'utilisateur type du kit (poste seul), le champ est absent. Aucun intérêt général.
- Les champs de coût de l'hôte (`cost.*`) et le segment du kit mesurent des choses différentes (session entière contre tâche) ; ne pas les confondre.

## Ce que ça change pour le kit

**Plafond de dépense** : rien, abandonné (champ réservé aux passerelles, absent chez l'utilisateur visé). Raison consignée.

**Cache de prompt** : pas de statusline installée par le kit (réglage personnel, hors doctrine). Une tâche utile et petite à la place : que `statusline_segment` accepte, optionnel, l'objet `prompt_cache` reçu sur stdin et ajoute `cache <hit_ratio en %>` ou `cache froid` quand `warm` est faux, sans jamais inventer (absent → rien). Tâche proposée (prête, S), module `src/grimoire/traces/transcript_usage.py`, test dans `tests/unit/test_statusline_segment.py`. Critères (`--check`) :

- sans `prompt_cache`, le segment est identique au rendu actuel (test existant inchangé) ;
- avec `prompt_cache.hit_ratio = 0.93` et `warm = true`, le segment finit par `| cache 93 %` ; avec `hit_ratio = null`, aucun suffixe ;
- avec `warm = false`, le suffixe est `cache froid` ; un champ d'un autre type est ignoré sans exception.

Elle ne se justifie que si Guilhem veut qu'une statusline du kit existe : sinon c'est du code sans appelant de plus. À trancher avec lui avant de la lancer.

## Sources consultées le 2026-10-25

- [Customize your status line](https://code.claude.com/docs/en/statusline) — Claude Code Docs `[doc]`
