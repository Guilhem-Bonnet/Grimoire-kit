"""Le moteur à modèle de la conversation de cadrage : le protocole ``Moteur``, chaque geste porté par un appel borné (lot B2).

Chaque geste est UN appel à sortie JSON validée par le schéma du geste (:mod:`~grimoire.tools.atelier_engines.gestes`).
Quand un usage n'a aucun modèle (non autorisé, indisponible, écarté par une garde, simplement proposé), le geste est
rendu par le moteur sans modèle, sans appel et sans note : la décision et sa raison sont dites par la vue. Quand l'appel
échoue (délai, réseau, sortie hors cadre), le moteur LÈVE une :class:`EngineError` : le gardien rend le geste par le
moteur sans modèle pour ce tour et note la raison (le nom de l'erreur) dans la vue. Ce moteur est de toute façon traité
comme hostile : le gardien revalide chaque sortie, et rien n'entre dans le document sans le « oui » de l'humain.

Usages (ceux de :mod:`grimoire.providers.direct_use`) : ``conversation`` (le modèle rapide : classer, extraire, formuler,
reformuler, répondre) et ``brainstorm`` (le modèle plus fort : proposer des pistes). ``redaction`` n'a pas encore de geste.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from grimoire.core.cadrage_conversation_engine import (
    ElementExtrait,
    EntreeClassement,
    EntreeExtraction,
    EntreePistes,
    EntreeQuestion,
    EntreeReformulation,
    MoteurSansModele,
)
from grimoire.core.cadrage_conversation_moteur_vue import reflexion
from grimoire.tools.atelier_engines import gestes
from grimoire.tools.atelier_engines.schema import decoder
from grimoire.tools.atelier_engines.transport import Backend, EngineRefus

__all__ = ["MoteurModeles"]

#: Les genres de question qu'un modèle reformule ; les autres (pause, fin, document…) sont des phrases du programme.
_GENRES_REDITS = frozenset({"amorce", "brainstorm"})


class MoteurModeles:
    """Un moteur dont chaque usage a, ou non, un modèle ; ``cle`` : le chantier, pour dire que la mascotte réfléchit."""

    def __init__(
        self, ident: str, libelle: str, backends: Mapping[str, Backend | None], *, cle: tuple[str, str] | None = None,
        duree_reflexion_s: float = 10.0, sortie_de_texte: bool = False,
    ) -> None:
        self.id = ident
        self.libelle = libelle
        self.backends = dict(backends)
        self.cle = cle
        self.duree_reflexion_s = duree_reflexion_s
        self.sortie_de_texte = sortie_de_texte
        self.repli = MoteurSansModele()

    def _appel(self, usage: str, geste: gestes.Geste) -> Any:
        backend = self.backends[usage]
        if backend is None:  # l'appelant a vérifié : jamais un appel sans modèle
            raise EngineRefus("aucun modèle pour cet usage")
        if usage == "brainstorm":  # le modèle fort est plus lent : la vue dit que la mascotte réfléchit pendant l'appel
            with reflexion(self.cle, self.duree_reflexion_s):
                texte, _ = backend.completer(geste.systeme, geste.utilisateur, geste.schema, geste.max_jetons)
        else:
            texte, _ = backend.completer(geste.systeme, geste.utilisateur, geste.schema, geste.max_jetons)
        return decoder(texte)

    def _a(self, usage: str) -> bool:
        return self.backends.get(usage) is not None

    # ── les gestes ─────────────────────────────────────────────────────────

    def formuler_question(self, entree: EntreeQuestion) -> str:
        if not self._a("conversation") or entree.kind not in _GENRES_REDITS:
            return self.repli.formuler_question(entree)
        return gestes.lire_question(self._appel("conversation", gestes.formuler(entree)))

    def classer_reponse(self, entree: EntreeClassement) -> str:
        if not self._a("conversation"):
            return self.repli.classer_reponse(entree)
        return gestes.lire_classe(self._appel("conversation", gestes.classer(entree)))

    def extraire_elements(self, entree: EntreeExtraction) -> list[ElementExtrait]:
        if not self._a("conversation"):
            return self.repli.extraire_elements(entree)
        return gestes.lire_elements(self._appel("conversation", gestes.extraire(entree)), entree)

    def reformuler(self, entree: EntreeReformulation) -> str:
        if not self._a("conversation"):
            return self.repli.reformuler(entree)
        return gestes.lire_texte(self._appel("conversation", gestes.reformuler(entree)))

    def proposer_pistes(self, entree: EntreePistes) -> list[str]:
        if not self._a("brainstorm"):
            return self.repli.proposer_pistes(entree)
        return gestes.lire_pistes(self._appel("brainstorm", gestes.pistes(entree)))

    def repondre_question(self, texte: str, pourquoi: str) -> str:
        if not self._a("conversation") or ("pourquoi" in texte.lower() and pourquoi):  # le « pourquoi » est une phrase du programme
            return self.repli.repondre_question(texte, pourquoi)
        return gestes.lire_texte(self._appel("conversation", gestes.repondre(texte, pourquoi)))

    def est_tour_lourd(self, texte: str, etape: str) -> bool:
        return etape == "brainstorm" and self._a("brainstorm")  # les pistes viennent du modèle fort, plus lent

    def limites(self) -> tuple[str, ...]:
        if not any(self.backends.values()):
            return self.repli.limites()
        sortie = (
            "Ce que tu écris est envoyé à un service hors de ta machine." if self.sortie_de_texte
            else "Ce que tu écris reste sur ta machine."
        )
        return (
            f"Je parle avec un modèle d'IA ({self.libelle}) : je peux me tromper ou mal te comprendre.",
            "Rien n'entre dans le document sans ton « oui » : je ne fais que proposer.",
            sortie,
            "Si le modèle tarde, répond n'importe quoi ou n'est plus disponible, je reprends en questions guidées, sans lui, "
            "et je te dis pourquoi.",
        )
