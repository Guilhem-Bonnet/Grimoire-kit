# Teaser de la vitrine : sources

Le teaser (48 s, 1920×1080, 30 i/s) est une scène HTML pilotée par `window.__seek(ms)`, rendue image par image, puis assemblée avec un son synthétisé. Ce dossier contient de quoi le refaire. Il ne contient ni les rushes (environ 580 Mo à télécharger), ni les images extraites (`frames/`), ni la vidéo finale, ni les fichiers son : tout cela se régénère et reste ignoré par git (voir `.gitignore`).

Tous les scripts travaillent par défaut dans leur propre dossier : lancez-les depuis `web/teaser/`, sans argument.

## Contenu

| Fichier | Rôle |
|---|---|
| `scene4.tpl.html` | La scène. Les marqueurs `/*ICONS*/`, `/*TERM*/`, `/*CLIPS*/` sont remplacés par `build4.py`. |
| `build4.py` | Assemble `scene4.html` (icônes, sorties de terminal, manifeste des rushes, quads d'écran). |
| `prep_footage.py` | Extrait les rushes en séquences JPEG à 30 i/s dans `frames/<id>/` et écrit `clips.manifest.json`. |
| `detect_green.py` | Détecte les coins des écrans verts et écrit `screens.json`. |
| `render.mjs` | Rendu image par image (Playwright, Chromium) vers un MP4 via ffmpeg. |
| `shots.mjs` | Images clés à des instants donnés (pour contrôler la scène). |
| `cues.mjs` | Exporte les repères sonores de la scène dans `cues.json`. |
| `audio4.py`, `master.sh` | Synthèse du son (numpy, déterministe) calée sur `cues.json`, puis normalisation à -14 LUFS. |
| `storyboard.py` | Page de storyboard à partir des images clés de `shots.mjs`. |
| `clips.json` | Quel rush, quelle entrée et quelle durée pour chaque plan. |
| `screens.json` | Coins des écrans incrustés (sortie de `detect_green.py`). |
| `cues.json` | Repères sonores (sortie de `cues.mjs`). |
| `terminal.json` | Sorties de terminal affichées dans la scène (voir plus bas). |
| `footage-sources.json` | Les 25 rushes : page, URL du fichier, auteur, licence, durée, définition. |
| `icons/` | Les six icônes que la scène utilise (`board`, `fleet`, `inbox`, `pr`, `sliders`, `tag`). |
| `fonts/` | Polices en woff2 (Instrument Sans, JetBrains Mono) et leurs licences OFL. |

## Ordre de rendu

Prérequis : Python 3 avec numpy, Node avec Playwright (`NODE_PATH` pointe sur le `node_modules` qui le contient), ffmpeg, un GPU utilisable par Chromium.

1. **Rushes.** Télécharger chaque fichier de `footage-sources.json` (champ `url_fichier`) vers `footage/<plan>/` sous le nom donné par le champ `fichier` (par exemple `footage/jeu/pexels-5901406-mains-manette.mp4`). Le champ `fichier` commence déjà par le nom du plan.
2. `python3 -I prep_footage.py` : extrait `frames/<id>/0001.jpg` et suivantes, écrit `clips.manifest.json`. L'option `--placeholders` fabrique des séquences grises pour les plans sans fichier (utile pour un essai sans rushes).
3. `python3 -I detect_green.py` : recalcule `screens.json` depuis les images extraites.
4. `python3 -I build4.py` : écrit `scene4.html`.
5. **Rendu sur GPU.** `render.mjs` lance Chromium avec `--enable-gpu --use-angle=vulkan --ignore-gpu-blocklist --allow-file-access-from-files` ; ces drapeaux sont déjà dans le script, il faut un GPU et un pilote Vulkan.

   ```bash
   NODE_PATH=/chemin/vers/node_modules node render.mjs "$PWD/scene4.html" teaser-muet.mp4 1920 1080 30
   ```

   Le chemin de la page doit être absolu. Arguments facultatifs après la sortie : largeur, hauteur, images par seconde, paramètre de requête, fichier son.
6. **Son.** `NODE_PATH=... node cues.mjs "$PWD/scene4.html" cues.json` (repères de la scène), puis `./master.sh` : synthèse (`bande-son-brute.wav`) puis normalisation en deux passes (`bande-son.wav`, -14 LUFS, -1,5 dBTP).
7. **Mixage.** Soit en passant `bande-son.wav` en dernier argument de `render.mjs` (ffmpeg encode alors l'audio en AAC 192 kb/s pendant le rendu), soit après coup :

   ```bash
   ffmpeg -i teaser-muet.mp4 -i bande-son.wav -c:v copy -c:a aac -b:a 192k -shortest teaser.mp4
   ```

Contrôle visuel sans rendu complet : `NODE_PATH=... node shots.mjs "$PWD/scene4.html" shots 4 12 30`, puis `python3 -I storyboard.py` (les images clés sont lues dans `shots/`, la page est écrite dans `storyboard/`). Créez d'abord le dossier `shots/`.

## Licences des rushes

Les 25 rushes viennent de quatre banques : Pexels (14), Pixabay (6), Coverr (3), Mixkit (2). Leurs licences autorisent l'usage commercial sans attribution, mais pas la revente des fichiers tels quels. Le détail de chaque rush (page, auteur, licence) est dans `footage-sources.json`. Les rushes ne sont pas redistribués ici : on les télécharge depuis leur page d'origine.

## Terminal

Les sorties de `terminal.json` (`uv tool install`, `grimoire init . --lite -y`, `grimoire standard init . --profile governed`) ont été capturées avec grimoire-kit 3.62.1. Deux coupes y sont signalées « extrait » dans la scène : ce ne sont pas des sorties intégrales.

## Polices

`fonts/` contient Instrument Sans (400, 500, 600) et JetBrains Mono (Regular, SemiBold), sous licence SIL OFL 1.1 (`fonts/OFL-InstrumentSans.txt`, `fonts/OFL-JetBrainsMono.txt`).
