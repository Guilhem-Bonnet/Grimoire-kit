"""Règles d'ancrage partagées par tout ce que le kit émet (#613).

Un utilisateur Copilot a vu ses personas produire des chiffres et des
pronostics sur des fichiers jamais lus. Les wrappers disaient seulement
« signale comme non vérifié » ; ils disent désormais ce qui compte comme
vérifié. Ces deux textes sont émis à l'identique par les émetteurs d'hôte
(Claude Code, Copilot, Codex, Cursor, Gemini), le prompt des ouvriers
headless (`missions.dispatch.build_prompt`) et les instructions du serveur
MCP — une seule source, pour que la garde `scripts/check-emitted-prose.py`
et `tests/unit/test_hosts.py` vérifient la même phrase partout.
"""

from __future__ import annotations

#: Ce qui compte comme vérifié : une commande réellement exécutée ou un
#: fichier lu, cité. Le reste s'écrit « non vérifié », jamais estimé ni noté.
GROUNDING_RULE = (
    "Rends un résultat vérifiable : tout chiffre, tout verdict et toute "
    "affirmation sur un fichier cite la commande que tu as réellement exécutée "
    "ou le chemin que tu as lu (fichier:ligne). Ce que tu n'as ni lu ni mesuré, "
    "tu ne l'estimes pas : tu l'écris « non vérifié ». Un score, une note ou une "
    "probabilité n'existe que si une commande l'a calculée ; sinon tu donnes les "
    "constats et tu écris « non mesuré », même si on te demande un chiffre."
)

#: Même bloc que `grimoire task dispatch` exige des ouvriers headless —
#: porté dans le fichier de chaque persona routée, pas seulement dans un
#: README qu'elle ne lit pas.
UNCERTAINTIES_BLOCK_RULE = (
    "Termine ta réponse par un bloc ```grimoire-uncertainties``` : une liste "
    'JSON d\'objets `{"where": ..., "what": ..., "why": ...}`, un par point '
    "que tu n'as pas pu vérifier, `[]` si aucun — jamais de prose à la place, "
    "jamais le bloc omis par excès de confiance."
)

#: Version anglaise courte pour les instructions du serveur MCP.
GROUNDING_RULE_EN = (
    "Any number, verdict or statement about a project comes from a tool "
    "result or a file actually read (cite it); what was not measured or read "
    "is reported as unverified, never estimated."
)

__all__ = ["GROUNDING_RULE", "GROUNDING_RULE_EN", "UNCERTAINTIES_BLOCK_RULE"]
