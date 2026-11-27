"""Les routes de la conversation de cadrage dans l'Atelier (épique #1477, lot A) — sans interface, sans modèle.

Préfixe ``/api/workspace/atelier/`` (branchées par :mod:`grimoire.tools.workspace_atelier`, donc SOUS les gardes
communes de toute écriture de l'Atelier : drapeau de l'Atelier, jeton de page, origine, JSON, journal des mutations) :

- ``GET chantiers/<slug>/conversation`` : l'état (vue Conversation) ; ``GET …/conversation/compris`` : ce qui est
  compris, par rubrique (vue Document) ; ``GET conversations`` : actives, autres, classées, en erreur ;
- ``POST …/conversation/demarrer`` ``{}`` : démarre ou reprend ;
- ``POST …/conversation/tour`` ``{"rev", "texte"}`` : un tour de l'humain, la réaction (accusé, étape, « tour lourd ») ;
- ``POST …/conversation/elements/<id>`` ``{"rev", "decision": "confirmer"|"corriger"|"ecarter", "texte"?}`` ;
- ``POST …/conversation/pause|terminer|classer|rouvrir`` ``{"rev"}``.

Aucune route n'accepte de contenu à écrire dans un fichier de phase : le corps ne porte que ``rev`` (la version vue :
écrire sur une conversation qui a changé donne 409), ``texte`` (le tour de l'humain, ou le texte qu'il corrige) et
``decision``. Le chantier est retrouvé dans la liste réelle des chantiers (``_phase_path``), jamais construit depuis le
slug ; le document n'est touché que par :mod:`grimoire.core.cadrage_conversation_document`.

Le moteur vient d'UN seul point d'injection, :func:`set_moteur` : par défaut le moteur sans modèle. Le lot B n'a qu'à
y poser d'autres moteurs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.core import cadrage_conversation_hygiene as hygiene
from grimoire.core import cadrage_conversation_turn as turn
from grimoire.core.cadrage import PHASES
from grimoire.core.cadrage_conversation import ConversationConflictError
from grimoire.core.cadrage_conversation_engine import Garde, MoteurSansModele
from grimoire.tools import workspace_atelier as wa

__all__ = ["get", "moteur_pour", "post", "reglages", "set_moteur"]

CONFIG_KEY = ("cockpit", "atelier_conversation")
#: Les champs que chaque geste accepte : tout autre champ est refusé (jamais de contenu de document dans un corps).
_FIELDS: dict[str, frozenset[str]] = {
    "demarrer": frozenset(),
    "tour": frozenset({"rev", "texte"}),
    "elements": frozenset({"rev", "decision", "texte"}),
    "pause": frozenset({"rev"}),
    "terminer": frozenset({"rev"}),
    "classer": frozenset({"rev"}),
    "rouvrir": frozenset({"rev"}),
}
_FABRIQUE: Callable[[Path, str], Any] | None = None


def set_moteur(factory: Callable[[Path, str], Any] | None) -> None:
    """LE point d'injection du moteur : ``factory(root, slug)`` rend un moteur ; ``None`` rétablit le moteur sans modèle."""
    global _FABRIQUE
    _FABRIQUE = factory


def moteur_pour(root: Path, slug: str) -> Garde:
    """Le moteur de ce chantier, enveloppé du gardien qui valide toutes ses sorties."""
    if _FABRIQUE is None:
        return Garde(MoteurSansModele())
    fabrique = _FABRIQUE
    return Garde.construire(lambda: fabrique(root, slug))  # une fabrique qui lève ou tarde : repli sans modèle, avec une note


def reglages(root: Path) -> hygiene.Reglages:
    """Les délais et le plafond réglés dans ``project-context.yaml`` (``cockpit.atelier_conversation``), bornés."""
    node: Any = wa._load(root)
    for key in CONFIG_KEY:
        if not hasattr(node, "get"):
            return hygiene.Reglages()
        node = node.get(key)
    return hygiene.Reglages.lire(node)


def _context(root: Path, slug: str) -> turn.Contexte:
    slug = wa._slug(slug)
    directory = wa._phase_path(root, slug, PHASES[0].filename).parent  # liste réelle des chantiers, aucun lien suivi
    return turn.Contexte(root, slug, directory, moteur_pour(root, slug), hygiene.maintenant(), reglages(root))


def _body(action: str, body: dict[str, Any]) -> dict[str, Any]:
    extra = set(body) - _FIELDS[action] - {"project"}  # ``project`` : le projet visé, confirmé par l'hôte comme pour les autres écritures
    if extra:
        raise ValueError(f"champ(s) refusé(s) : {', '.join(sorted(extra))} — la conversation n'accepte pas de contenu de document")
    return body


def _rev(body: dict[str, Any]) -> int:
    rev = body.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise ValueError("`rev` (entier) requis : la version de la conversation lue avec l'état")
    return rev


def get(root: Path, rest: list[str]) -> dict[str, Any]:
    """Lectures (rien n'est écrit). ``rest`` : le chemin après ``atelier/``, déjà découpé."""
    if rest == ["conversations"]:
        from grimoire.core.chantiers import list_chantiers

        entries: list[tuple[str, Path]] = []
        for chantier in list_chantiers(root):
            try:
                entries.append((chantier.slug, wa._phase_path(root, chantier.slug, PHASES[0].filename).parent))
            except FileNotFoundError:  # un lien symbolique en chemin : ce chantier n'est pas listé
                continue
        return hygiene.lister(entries, hygiene.maintenant(), reglages(root))
    if len(rest) == 3 and rest[2] == "conversation":
        return turn.vue(_context(root, rest[1]))
    if len(rest) == 4 and rest[2] == "conversation" and rest[3] == "compris":
        return turn.compris(_context(root, rest[1]))
    raise FileNotFoundError(f"route inconnue : atelier/{'/'.join(rest)}")


def post(root: Path, rest: list[str], body: dict[str, Any]) -> dict[str, Any]:
    """Écritures. ``rest`` : ``["chantiers", <slug>, "conversation", <geste>[, <id>]]``."""
    if len(rest) not in (4, 5) or rest[2] != "conversation" or rest[3] not in _FIELDS or (len(rest) == 5) != (rest[3] == "elements"):
        raise FileNotFoundError(f"route inconnue : atelier/{'/'.join(rest)}")
    action = rest[3]
    data = _body(action, body)
    ctx = _context(root, rest[1])
    try:
        if action == "demarrer":
            result = turn.demarrer(ctx)
        elif action == "tour":
            result = turn.recevoir(ctx, _rev(data), data.get("texte"))
        elif action == "elements":
            result = turn.decider(ctx, _rev(data), rest[4], str(data.get("decision", "")), data.get("texte"))
        else:
            result = getattr(turn, action)(ctx, _rev(data))
    except ConversationConflictError as exc:
        error = wa.ConflictError(str(exc))
        error.reason = exc.reason
        raise error from exc
    if result.get("document_modifie"):
        from grimoire.tools import workspace_atelier_loop as loop

        loop.after_write(root, ctx.slug)  # le document a changé : date de modification et état du chantier
    return {"ok": True, "reaction": result, "conversation": turn.vue(_context(root, rest[1]))}
