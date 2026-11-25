---
audience: utilisateur
---

# Mode vocal : ce que la voix peut faire, et ce qu'elle ne fait jamais

Le mode vocal (`interaction: {mode: voice}` dans `project-context.yaml`) ajoute au chat du cockpit un bouton « Dicter » et des messages parlés. Il sert à saisir et à écouter, pas à décider.

## Ce que la voix fait

- **Dicter un message** : l'enregistrement est transcrit localement, le texte s'affiche dans la saisie, corrigeable, puis part à l'orchestrateur avec la mention « dictée vocale, erreurs de reconnaissance possibles ». La mention n'est ajoutée que si le texte envoyé contient encore la dictée : un texte entièrement retapé au clavier part sans elle. Un envoi refusé rend le texte à la saisie avec sa mention.
- **Répondre à une question que le pilote a reportée** : la réponse dictée part telle quelle, sans mention collée, pour que l'option soit reconnue.
- **Écouter un résumé** : l'orchestrateur termine sa réponse par un message parlé court ; le détail reste à l'écran.

## Ce que la voix ne fait jamais

- **Aucune approbation.** Une demande de permission de l'agent (outil, fichier, commande, fusion, suppression) se tranche par un clic à l'écran, et seulement ainsi. Tant qu'une demande attend, un message dicté est refusé, même « oui », « autorise » ou « approve ». Une question de nature permission ou fusion, ou d'une catégorie réservée à l'humain (identifiants, secrets, dépense, données utilisateur, mandat, réglages du dépôt, fusion, publication, déploiement, autorisation), ne se tranche pas non plus par une dictée : répondez au clavier ou par un clic.
- Le message parlé d'une permission suit un gabarit fixe : « la confirmation se fait à l'écran », la nature de l'outil, le nom du programme lancé (seulement s'il s'écrit comme un exécutable en minuscules, sinon « un programme » ; une enveloppe comme `sudo`, `env` ou `bash -c` est dite « par l'intermédiaire de sudo », sans lire la suite) et l'extension courte du fichier visé, puis « cliquez à l'écran ». Ni le titre, ni les explications, ni la commande complète, ni le nom du fichier, tous rédigés par l'agent, ne sont lus : le détail est à l'écran.
- Aucun LLM ne réécrit la dictée.

## Limites

| Réglage | Défaut | Bornes |
|---|---|---|
| `voice.max_record_seconds` | 120 s | 1 à 300 s |
| `voice.spoken_max_seconds` | 45 s | 5 à 300 s |
| `voice.idle_unload_seconds` | 300 s | 0 = jamais |
| `voice.incoming_ttl_seconds` | 300 s | 30 à 86 400 s |

L'enregistrement accepté est un WAV PCM 16 bits, mono, à 16 kHz, d'au moins 0,1 s. Le serveur lit l'en-tête : un autre format (8 bits, 8 kHz, 44,1 kHz, stéréo, audio compressé), une taille déclarée qui ne correspond pas aux octets reçus, ou une durée au-delà du plafond est refusé avant d'atteindre le moteur. Les 300 s maximales tiennent sous le plafond du corps HTTP de la transcription (16 Mio).

## Où tourne le calcul

Tout tourne sur la machine, dans le processus du cockpit : reconnaissance (faster-whisper) et synthèse (Qwen3-TTS, Pocket TTS) sont locales ; seule la voix du navigateur dépend du système. Aucun modèle n'est téléchargé sans `voice.allow_download: true`. Un seul moteur de reconnaissance et un seul moteur de synthèse sont chargés pour tout le cockpit, quel que soit le nombre de projets enrôlés ; ils se déchargent après `idle_unload_seconds` sans usage. Les reconnaissances passent une à la fois pour tout le processus, les synthèses aussi : deux projets qui dictent ou écoutent en même temps attendent leur tour. Des projets aux réglages vocaux différents se partagent l'unique emplacement, qui recharge le modèle à chaque alternance.

## Ce qui est gardé

- L'audio entrant n'existe sur disque que le temps de la transcription : un fichier lisible par vous seul (0600) sous `_grimoire-runtime-output/voice/incoming/` dans le projet, dossier ignoré par git par son propre `.gitignore`, supprimé ensuite, y compris en cas d'échec. Un fichier laissé par un arrêt brutal est purgé passé `voice.incoming_ttl_seconds`, à l'ouverture du chat et à chaque transcription ; la purge ne suit aucun lien symbolique, et un dossier vocal en lien symbolique est refusé.
- La synthèse se fait en mémoire ; l'audio part dans la réponse au navigateur et nulle part ailleurs.
- Le fil du chat, le ledger, les traces et le journal du cockpit ne reçoivent que du texte et des métadonnées (texte transcrit, langue, durée), jamais d'audio.
- Les messages parlés restent en mémoire, perdus au redémarrage du cockpit.

Voir aussi [Synthèse vocale par clonage](voice-clone.md).
