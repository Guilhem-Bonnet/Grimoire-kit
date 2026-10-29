---
audience: utilisateur
---

# Être prévenu quand une décision t'attend

Les agents tournent seuls ; quand l'un d'eux pose une question, il s'arrête et attend ta réponse. Ce guide dit comment être rappelé sans garder le cockpit sous les yeux.

## Dans le navigateur

Trois signaux, du plus discret au plus fort :

- **Le titre de l'onglet** devient « (2) Grimoire — vue de travail » quand deux éléments attendent. À zéro, il redevient « Grimoire — vue de travail ».
- **La pastille sur l'icône de l'onglet**, qui disparaît aussi à zéro.
- **Une notification** par nouvelle décision, si tu actives **Me prévenir dans le navigateur** (bouton de la barre d'état, en bas). Le navigateur demande alors sa permission ; un clic sur la notification ouvre `#travail?view=decisions`.

Ce qu'il faut savoir sur l'interrupteur :

- Il n'annonce que les **nouvelles** questions : celles qui attendaient déjà quand tu l'as activé ne déclenchent rien. Chaque question n'est annoncée qu'une fois, même si tu recharges la page (le navigateur garde la liste des identifiants déjà annoncés).
- Actif, il garde le flux de mise à jour ouvert **même onglet masqué**. Coupé, un onglet masqué ne fait aucune requête, comme avant.
- Si le navigateur refuse la permission, le bouton reste coupé et le dit. Autorise les notifications pour `127.0.0.1` dans les réglages du site, puis réactive.

## Hors du navigateur : `notify.command`

Pour être prévenu même navigateur fermé, `grimoire tick` lance une commande de ton choix, une fois par question en attente. Déclare-la dans **Configuration › Fournisseurs et budgets › Commande de notification**, ou dans `_grimoire/standard/standard-profile.yaml` :

```yaml
options:
  notify:
    command: notify-send {title} {message}
```

La commande n'est **pas** passée à un shell : elle est découpée comme le fait `shlex`, puis lancée telle quelle. `{title}` et `{message}` sont remplacés ; les variables `GRIMOIRE_NOTIFY_TASK`, `GRIMOIRE_NOTIFY_QUESTION_ID`, `GRIMOIRE_NOTIFY_KIND`, `GRIMOIRE_NOTIFY_TITLE` et `GRIMOIRE_NOTIFY_MESSAGE` sont aussi posées. `GRIMOIRE_NOTIFY_COMMAND` dans l'environnement prime sur le profil.

Le bouton **Tester** lance la commande avec une décision d'essai et dit si elle est sortie avec le code 0 ; il n'écrit rien au ledger. `grimoire doctor` signale une commande illisible (guillemet non fermé, par exemple).

### Recette `notify-send` (Linux)

```yaml
command: notify-send --urgency=normal {title} {message}
```

### Recette `ntfy` (téléphone, via ntfy.sh ou ton serveur)

```yaml
command: curl -s -H "Title: {title}" -d {message} https://ntfy.sh/mon-sujet-secret
```

Choisis un sujet impossible à deviner : sur ntfy.sh, quiconque connaît le nom lit les messages. Le texte de la question part tel quel : ne l'envoie pas à un service que tu ne maîtrises pas si tes questions contiennent des secrets.

### Recette macOS

```yaml
command: osascript -e 'display notification "{message}" with title "{title}"'
```
