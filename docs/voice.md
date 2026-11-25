---
audience: utilisateur
---

# Mode vocal : ce que la voix peut faire, et ce qu'elle ne fait jamais

Le mode vocal (`interaction: {mode: voice}` dans `project-context.yaml`) ajoute au chat du cockpit un bouton « Dicter » et des messages parlés. Il sert à saisir et à écouter, pas à décider.

## Ce que la voix fait

- **Dicter un message** : l'enregistrement est transcrit localement, le texte s'affiche dans la saisie, corrigeable, puis part à l'orchestrateur avec la mention « dictée vocale, erreurs de reconnaissance possibles ». La mention n'est ajoutée que si le texte envoyé contient encore la dictée : un texte entièrement retapé au clavier part sans elle. Un envoi refusé rend le texte à la saisie avec sa mention.
- **Répondre à une question à options** : la réponse dictée part telle quelle, sans mention collée, pour que l'option soit reconnue.
- **Écouter un résumé** : l'orchestrateur termine sa réponse par un message parlé court ; le détail reste à l'écran.

## Ce que la voix ne fait jamais

- **Aucune approbation.** Une demande de permission de l'agent (outil, fichier, commande, fusion, suppression) se tranche par un clic à l'écran, et seulement ainsi. Tant qu'une demande attend, un message dicté est refusé, même « oui », « autorise » ou « approve ». Une question de nature permission ne se tranche pas non plus par une dictée.
- Le message parlé d'une permission suit un gabarit fixe : la nature de l'outil, le nom du fichier visé, le début de la commande, puis « cliquez à l'écran ». Le titre et les explications rédigés par l'agent ne sont jamais lus.
- Aucun LLM ne réécrit la dictée.

## Limites

| Réglage | Défaut | Bornes |
|---|---|---|
| `voice.max_record_seconds` | 120 s | 1 à 300 s |
| `voice.spoken_max_seconds` | 45 s | 5 à 300 s |
| `voice.idle_unload_seconds` | 300 s | 0 = jamais |

L'enregistrement accepté est un WAV PCM 16 bits, mono, à 16 kHz. Le serveur lit l'en-tête : un autre format (8 bits, 8 kHz, 44,1 kHz, stéréo, audio compressé), une taille déclarée qui ne correspond pas aux octets reçus, ou une durée au-delà du plafond est refusé avant d'atteindre le moteur. Les 300 s maximales tiennent sous le plafond du corps HTTP de la transcription (16 Mio).

## Où tourne le calcul

Tout tourne sur la machine, dans le processus du cockpit : reconnaissance (faster-whisper) et synthèse (Qwen3-TTS, Pocket TTS) sont locales ; seule la voix du navigateur dépend du système. Aucun modèle n'est téléchargé sans `voice.allow_download: true`. Un seul moteur de reconnaissance et un seul moteur de synthèse sont chargés pour tout le cockpit, quel que soit le nombre de projets enrôlés ; ils se déchargent après `idle_unload_seconds` sans usage.

## Ce qui est gardé

- L'audio entrant n'existe sur disque que le temps de la transcription, dans un fichier temporaire supprimé ensuite, y compris en cas d'échec.
- La synthèse se fait en mémoire ; l'audio part dans la réponse au navigateur et nulle part ailleurs.
- Le fil du chat, le ledger, les traces et le journal du cockpit ne reçoivent que du texte et des métadonnées (texte transcrit, langue, durée), jamais d'audio.
- Les messages parlés restent en mémoire, perdus au redémarrage du cockpit.

Voir aussi [Synthèse vocale par clonage](voice-clone.md).
