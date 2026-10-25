"""Gestes de correction d'un souvenir (P23) : retirer sans effacer, retirer le retrait, réétiqueter.

Trois gestes, tous portés par :class:`~grimoire.memory.manager.MemoryManager`
(jamais par une route HTTP ni par le backend directement), donc journalisés dans
``_grimoire-output/memory-changes.jsonl`` (P14) comme toute mutation :

``retract``
    « Ne plus servir ce souvenir », sans le supprimer. Pose
    ``metadata["retracted"] = {"at", "by", "session_id", "reason"}`` (champs
    inconnus omis). Le souvenir reste lisible avec son état
    (``--include-retracted``, ``memory show``) mais n'est plus rendu par défaut
    par aucune lecture servie aux agents (recherche, hybride, taxonomie, rappel
    typé, liste). Un retrait **exige une raison**.

``unretract``
    Défait le retrait : la marque est vidée (``retracted: null``), pas retirée de
    la clé, pour qu'un backend qui *fusionne* les métadonnées (MemPalace) la
    remplace aussi. Qui l'avait posée reste dans le journal des écritures.

``update_tags``
    Ajoute, retire ou remplace les étiquettes. Les métadonnées ne bougent pas.

Règles communes :

* **Échec fermé.** Un backend qui ne sait pas modifier un souvenir
  (``update`` non implémenté), ou dont la relecture ne montre pas la marque, est
  refusé avec un message lisible — jamais une suppression ni un succès affiché
  sans effet. Rien n'est supprimé par ces gestes.
* **Cloisonnement par projet.** Un souvenir dont ``metadata.project_name`` est
  celui d'un *autre* projet est refusé, comme pour ``supersede`` et ``delete``.
  Un souvenir sans ``project_name`` (écrit avant la provenance) ne peut pas être
  attribué : il reste accessible, la limite est dite dans la doc.
* **La provenance d'origine est conservée** (``actor``, ``session_id``,
  ``task_ref`` de l'écriture initiale) : l'auteur du geste est dans la marque et
  dans le journal, pas à la place de l'auteur du souvenir.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import MemoryBackend, MemoryEntry
from grimoire.memory.journal import WriteContext, record_change
from grimoire.memory.reads_log import record_read
from grimoire.memory.supersession import (
    RETRACTED_KEY,
    SUPERSEDED,
    SUPERSEDED_KEY,
    is_retracted,
    retraction_of,
    supersession_state,
)
from grimoire.memory.validation import redact_secrets

__all__ = [
    "GESTURES",
    "MAX_REASON_CHARS",
    "MAX_TAGS",
    "MAX_TAG_CHARS",
    "MemoryGesturesMixin",
    "entry_state",
    "run_gesture",
]

#: Les gestes que la CLI et l'outil MCP exposent — une seule table, donc une seule garde.
GESTURES: tuple[str, ...] = ("retract", "unretract", "update_tags")

logger = logging.getLogger(__name__)

MAX_REASON_CHARS = 500
MAX_TAGS = 32
MAX_TAG_CHARS = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_reason(reason: str) -> str:
    """Raison obligatoire, bornée, sans secret : ce texte est écrit dans les métadonnées."""
    cleaned = " ".join(str(reason or "").split())
    if not cleaned:
        raise GrimoireMemoryError("Une raison est obligatoire pour retirer un souvenir (--reason).")
    redacted, _ = redact_secrets(cleaned)
    return redacted[:MAX_REASON_CHARS]


def _clean_tags(tags: Iterable[str], *, what: str) -> tuple[str, ...]:
    """Étiquettes propres : non vides, bornées, sans caractère de contrôle ni secret."""
    out: list[str] = []
    for raw in tags:
        tag = str(raw).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_CHARS or _CONTROL.search(tag):
            raise GrimoireMemoryError(
                f"Étiquette refusée ({what}) : {MAX_TAG_CHARS} caractères au plus, sans caractère de contrôle."
            )
        _, hits = redact_secrets(tag)
        if hits:
            raise GrimoireMemoryError(f"Étiquette refusée ({what}) : elle ressemble à un secret.")
        if tag not in out:
            out.append(tag)
    return tuple(out)


class MemoryGesturesMixin:
    """Gestes de correction et reçu de lecture, mélangés à ``MemoryManager``."""

    #: Surface qui lit : ``api`` par défaut, ``cli`` / ``mcp`` / ``cockpit`` / ``bench`` posés par l'appelant.
    surface: str = "api"
    #: Faux pour une lecture qui n'est pas un service rendu aux agents (cockpit en lecture seule, banc).
    journal_reads: bool = True

    _backend: MemoryBackend
    _project_name: str
    _project_root: Path | None
    hidden_stale: int

    if TYPE_CHECKING:
        def _write_context(self, actor: str, session_id: str, task_ref: str) -> WriteContext: ...
        def _sync_memory(self, entry: MemoryEntry) -> None: ...

    # ── cloisonnement ────────────────────────────────────────────────────

    def _require_own_project(self, entry: MemoryEntry) -> None:
        """Refuse d'agir sur le souvenir d'un autre projet (clé ``project_name``)."""
        owner = str((entry.metadata or {}).get("project_name") or "").strip()
        mine = (self._project_name or "").strip()
        if owner and mine and owner != mine:
            raise GrimoireMemoryError(
                f"Le souvenir {entry.id!r} appartient au projet {owner!r}, pas à {mine!r} : "
                "aucun geste n'agit sur le souvenir d'un autre projet."
            )

    # ── écriture d'une métadonnée de geste ───────────────────────────────

    def _write_gesture(
        self, entry_id: str, *, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """``backend.update`` ou un refus lisible ; rien d'autre n'est jamais écrit."""
        try:
            updated = self._backend.update(entry_id, tags=tags, metadata=metadata)
        except NotImplementedError:
            raise GrimoireMemoryError(
                f"Geste refusé : le backend {type(self._backend).__name__} ne sait pas modifier "
                "un souvenir. Rien n'a été changé, rien n'a été supprimé."
            ) from None
        if updated is None:
            raise GrimoireMemoryError(f"Souvenir introuvable : {entry_id!r}.")
        return updated

    # ── retirer / rétablir ───────────────────────────────────────────────

    def _clear_retraction(self, entry_id: str, metadata: dict[str, Any]) -> MemoryEntry:
        """Rend le souvenir au service : la clé ``retracted`` disparaît, relecture à l'appui.

        Un backend qui remplace les métadonnées (local, lexical, tantivy, Qdrant) perd la
        clé quand on ne la réécrit pas. Un backend qui les *fusionne* (MemPalace) la
        garderait : on y pose alors ``None``, qui la neutralise. Lève si le souvenir reste retiré.
        """
        base = {k: v for k, v in metadata.items() if k != RETRACTED_KEY}
        self._write_gesture(entry_id, metadata=base)
        reread = self._backend.recall(entry_id)
        if reread is not None and is_retracted(reread):
            self._write_gesture(entry_id, metadata={**base, RETRACTED_KEY: None})
            reread = self._backend.recall(entry_id)
        if reread is None or is_retracted(reread):
            raise GrimoireMemoryError(
                f"Rétablissement non confirmé par la relecture du backend {type(self._backend).__name__} : "
                "le souvenir reste retiré."
            )
        return reread

    def carry_state_marks(self, entry_id: str, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reporte le retrait et le remplacement d'un souvenir existant sur les métadonnées d'une réécriture.

        ``remember`` et ``upsert`` remplacent les métadonnées : sans ce report, réenregistrer
        le même texte remettrait en service un souvenir retiré (et ferait perdre sa raison).
        """
        prior = self._backend.recall(entry_id)
        if prior is None:
            return metadata
        marks = {k: prior.metadata[k] for k in (RETRACTED_KEY, SUPERSEDED_KEY) if prior.metadata.get(k)}
        return {**(metadata or {}), **marks} if marks else metadata

    def retract(
        self,
        entry_id: str,
        *,
        reason: str,
        actor: str = "",
        session_id: str = "",
        task_ref: str = "",
    ) -> MemoryEntry | None:
        """Retire ``entry_id`` du service sans le supprimer. ``None`` si l'identifiant n'existe pas."""
        why = _clean_reason(reason)
        existing = self._backend.recall(entry_id)
        if existing is None:
            return None
        self._require_own_project(existing)
        if is_retracted(existing):
            earlier = retraction_of(existing)
            raise GrimoireMemoryError(
                f"Souvenir {entry_id!r} déjà retiré ({earlier.get('at', 'date inconnue')}) ; "
                "le rétablir d'abord pour changer la raison."
            )
        context = self._write_context(actor, session_id, task_ref)
        mark: dict[str, Any] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "reason": why}
        if context.actor:
            mark["by"] = context.actor
        if context.session_id:
            mark["session_id"] = context.session_id
        updated = self._write_gesture(entry_id, metadata={**existing.metadata, RETRACTED_KEY: mark})
        # La relecture est la preuve : un backend qui perd la marque servirait encore le souvenir.
        reread = self._backend.recall(entry_id)
        if reread is None or not is_retracted(reread):
            origin = GrimoireMemoryError(
                f"Retrait non confirmé par la relecture du backend {type(self._backend).__name__} : "
                "la marque n'a pas été conservée."
            )
            try:
                self._clear_retraction(entry_id, existing.metadata)
            except Exception as undo_failure:
                # L'erreur d'origine reste la cause visible ; l'échec de l'annulation s'y ajoute.
                raise GrimoireMemoryError(f"{origin} Annulation impossible aussi : {undo_failure}") from origin
            raise origin
        self._sync_memory(reread)
        record_change(
            self._project_root, op="retract", context=context, before_id=entry_id,
            content=existing.text, reason=why, extra={"retracted_at": mark["at"]},
        )
        return updated if is_retracted(updated) else reread

    def unretract(
        self,
        entry_id: str,
        *,
        reason: str = "",
        actor: str = "",
        session_id: str = "",
        task_ref: str = "",
    ) -> MemoryEntry | None:
        """Annule un retrait : le souvenir est de nouveau servi. ``None`` si l'identifiant n'existe pas."""
        existing = self._backend.recall(entry_id)
        if existing is None:
            return None
        self._require_own_project(existing)
        if not is_retracted(existing):
            raise GrimoireMemoryError(f"Souvenir {entry_id!r} non retiré : rien à rétablir.")
        context = self._write_context(actor, session_id, task_ref)
        was = retraction_of(existing)
        reread = self._clear_retraction(entry_id, existing.metadata)
        self._sync_memory(reread)
        record_change(
            self._project_root, op="unretract", context=context, before_id=entry_id,
            content=existing.text, reason=" ".join(reason.split())[:MAX_REASON_CHARS],
            extra={"was_retracted": was},
        )
        return reread

    # ── réétiqueter ──────────────────────────────────────────────────────

    def update_tags(
        self,
        entry_id: str,
        *,
        add: Iterable[str] = (),
        remove: Iterable[str] = (),
        replace: Iterable[str] | None = None,
        reason: str = "",
        actor: str = "",
        session_id: str = "",
        task_ref: str = "",
    ) -> MemoryEntry | None:
        """Ajoute, retire ou remplace les étiquettes. ``None`` si l'identifiant n'existe pas.

        ``replace`` est exclusif de ``add`` et ``remove``. Un geste sans effet ne
        réécrit rien et n'est pas journalisé.
        """
        adds = _clean_tags(add, what="ajout")
        removes = _clean_tags(remove, what="retrait")
        replaced = None if replace is None else _clean_tags(replace, what="remplacement")
        if replaced is not None and (adds or removes):
            raise GrimoireMemoryError("replace est exclusif de add et remove.")
        if replaced is None and not adds and not removes:
            raise GrimoireMemoryError("Rien à faire : donner des étiquettes à ajouter, à retirer ou à remplacer.")
        existing = self._backend.recall(entry_id)
        if existing is None:
            return None
        self._require_own_project(existing)
        before = tuple(existing.tags)
        after = replaced if replaced is not None else tuple(
            t for t in (*before, *adds) if t not in removes
        )
        after = tuple(dict.fromkeys(after))
        if len(after) > MAX_TAGS:
            raise GrimoireMemoryError(f"Trop d'étiquettes : {len(after)} (maximum {MAX_TAGS}).")
        if after == before:
            return existing
        context = self._write_context(actor, session_id, task_ref)
        self._write_gesture(entry_id, tags=after)
        reread = self._backend.recall(entry_id)
        if reread is None or tuple(reread.tags) != after:
            self._write_gesture(entry_id, tags=before)
            raise GrimoireMemoryError(
                f"Réétiquetage non confirmé par la relecture du backend {type(self._backend).__name__} : "
                "les étiquettes d'origine sont rétablies."
            )
        self._sync_memory(reread)
        record_change(
            self._project_root, op="update_tags", context=context, before_id=entry_id,
            content=existing.text, reason=" ".join(reason.split())[:MAX_REASON_CHARS],
            extra={
                "tags_added": [t for t in after if t not in before],
                "tags_removed": [t for t in before if t not in after],
            },
        )
        return reread

    # ── reçu de lecture ──────────────────────────────────────────────────

    def journal_read(
        self,
        mode: str,
        query: str,
        served: list[MemoryEntry],
        *,
        filters: Mapping[str, Any] | None = None,
        lanes: Mapping[str, Any] | None = None,
        task_ref: str = "",
    ) -> None:
        """Une ligne dans ``memory-reads.jsonl`` ; ne lève jamais (voir ``reads_log``)."""
        if self._project_root is None or not self.journal_reads:
            return
        try:
            context = self._write_context("", "", task_ref)
            record_read(
                self._project_root, mode=mode, surface=self.surface, served=served, query=query,
                filters=filters, lanes=lanes, actor=context.actor, session_id=context.session_id,
                task_ref=context.task_ref, hidden_stale=int(getattr(self, "hidden_stale", 0) or 0),
            )
        except Exception:
            logger.warning("reçu de lecture mémoire non écrit", exc_info=True)


def entry_state(entry: MemoryEntry, recall: Callable[[str], MemoryEntry | None]) -> str:
    """``retracted``, ``superseded``, ``orphan`` (marque de remplacement sans remplaçant) ou ``live``."""
    if is_retracted(entry):
        return "retracted"
    state = supersession_state(entry, recall)
    return "superseded" if state == SUPERSEDED else state


def run_gesture(
    mgr: MemoryGesturesMixin,
    gesture: str,
    entry_id: str,
    *,
    reason: str = "",
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
    replace: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Exécute un geste et rend sa charge utile ; lève ``GrimoireMemoryError`` sinon.

    Point d'entrée unique de la CLI et de l'outil MCP : mêmes validations, même
    cloisonnement par projet, mêmes refus, même journal. Un identifiant inconnu
    est un refus, jamais un succès vide.
    """
    if gesture not in GESTURES:
        raise GrimoireMemoryError(f"Geste inconnu : {gesture!r} (attendu : {', '.join(GESTURES)}).")
    if gesture == "retract":
        entry = mgr.retract(entry_id, reason=reason)
    elif gesture == "unretract":
        entry = mgr.unretract(entry_id, reason=reason)
    else:
        entry = mgr.update_tags(entry_id, add=add, remove=remove, replace=replace, reason=reason)
    if entry is None:
        raise GrimoireMemoryError(f"Souvenir introuvable : {entry_id!r}.")
    recall = mgr._backend.recall
    payload: dict[str, Any] = {
        "ok": True, "gesture": gesture, "id": entry.id, "state": entry_state(entry, recall),
        "tags": list(entry.tags),
    }
    if is_retracted(entry):
        payload["retracted"] = retraction_of(entry)
    return payload
