"""Suggestions de l'Atelier de spec, en test (lot 2 de l'épique #1197, rôles MOA/MOE #1342).

L'agent (MOE) dépose, l'humain (MOA) tranche. Le dépôt n'écrit **jamais** dans un fichier de phase ; seule
l'acceptation le fait, par le chemin du lot 1 (:func:`workspace_atelier.edit_block`), avec le même contrôle
d'empreinte. Tout est derrière le drapeau ``cockpit.test_features.atelier`` : éteint, chaque route répond 404
et l'outil MCP refuse.

Routes (préfixe ``/api/workspace/atelier/chantiers/<slug>/``) :

- ``GET suggestions`` : toutes les suggestions du chantier, avec leur état à jour ;
- ``POST suggestions`` ``{"block_id", "texte", "motif"?, "posture"?}`` : dépôt (l'auteur est toujours « agent » :
  l'humain écrit directement dans le bloc, il ne dépose pas de suggestion) ;
- ``POST suggestions/<id>`` ``{"decision": "accept"|"reject", "motif"?}`` : décision humaine.

Autorité : la décision traverse la garde d'écriture commune des deux hôtes (jeton de session de la page,
origine, type JSON : ``request_guard``) et refuse un corps issu d'une dictée (``sent_as_dictation``, comme
``chat/permission``). L'outil MCP, lui, ne sait que déposer : voir :func:`suggest`.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from grimoire.core import atelier_suggestions as store
from grimoire.core.atelier_suggestions import Suggestion
from grimoire.core.cadrage import PHASES
from grimoire.tools import atelier_blocs
from grimoire.tools import workspace_atelier as wa

DECISIONS = ("accept", "reject")
_LABEL = {"proposee": "proposée", "acceptee": "acceptée", "refusee": "refusée", "perimee": "périmée"}
_STALE = "le bloc a changé depuis le dépôt"


def _dir(root: Path, slug: str) -> Path:
    """Dossier du chantier, confiné comme un fichier de phase (aucun lien symbolique, chantier connu)."""
    return wa._phase_path(root, slug, PHASES[0].filename).parent


def _phase_of(block_id: str) -> Any:
    phase = next((p for p in PHASES if p.id == str(block_id).split(".", 1)[0]), None)
    if phase is None:
        raise FileNotFoundError(f"bloc inconnu : {block_id!r}")
    return phase


def _conflict(message: str, reason: str) -> wa.ConflictError:
    """Un 409 qui dit pourquoi : ``perimee`` (le bloc a changé) ou ``deja-tranchee`` (un autre geste a déjà décidé)."""
    error = wa.ConflictError(message)
    error.reason = reason
    return error


def _context(blocks: list[atelier_blocs.Block], index: int) -> tuple[int, str]:
    """Ce qui distingue un bloc de ses homonymes : leur nombre et l'empreinte des voisins immédiats."""
    same = sum(1 for b in blocks if b.text == blocks[index].text)
    before = blocks[index - 1].text if index > 0 else ""
    after = blocks[index + 1].text if index + 1 < len(blocks) else ""
    return same, atelier_blocs.fingerprint(before + "\0" + after)


def _live(blocks: list[atelier_blocs.Block], item: Suggestion) -> bool:
    """Le bloc visé existe encore, tel qu'il était au dépôt, et ce n'est pas un homonyme qui a pris sa place.

    L'identifiant d'un bloc ne compte que les homonymes qui le précèdent : quand le premier de deux blocs
    identiques est réécrit, le second hérite de son identifiant. Un doublon au dépôt exige donc le même nombre
    d'homonymes et les mêmes voisins ; au moindre doute la suggestion est périmée.
    """
    index = next((i for i, b in enumerate(blocks) if b.id == item.block_id), None)
    if index is None or atelier_blocs.fingerprint(blocks[index].text) != item.block_fingerprint:
        return False
    return item.homonymes <= 1 or _context(blocks, index) == (item.homonymes, item.voisins)


def _blocks(root: Path, slug: str, phase: Any) -> list[atelier_blocs.Block] | None:
    """Les blocs de la phase, ou ``None`` si le fichier ne se lit pas (on ne conclut rien d'un échec de lecture)."""
    path = wa._phase_path(root, slug, phase.filename)
    try:
        return atelier_blocs.split(phase.id, path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _refresh(root: Path, slug: str, items: list[Suggestion]) -> bool:
    """Enregistre « périmée » pour les suggestions en attente dont le bloc a changé ou disparu. Vrai si l'une a bougé."""
    cache: dict[str, list[atelier_blocs.Block] | None] = {}
    changed = False
    for item in items:
        if item.etat != "proposee":
            continue
        if item.phase not in cache:
            cache[item.phase] = _blocks(root, slug, _phase_of(item.phase))
        blocks = cache[item.phase]
        if blocks is not None and not _live(blocks, item):
            _settle(item, "perimee", _STALE)
            changed = True
    return changed


def _state_view(root: Path, slug: str, items: list[Suggestion]) -> list[dict[str, Any]]:
    """Les suggestions avec l'état que le fichier d'aujourd'hui leur donne (rien n'est écrit en lecture)."""
    cache: dict[str, list[atelier_blocs.Block] | None] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        row = item.to_dict()
        if item.etat == "proposee":
            if item.phase not in cache:
                cache[item.phase] = _blocks(root, slug, _phase_of(item.phase))
            if not _live(cache[item.phase] or [], item):
                row["etat"] = "perimee"
        out.append(row)
    return out


def _read(chantier: Path) -> tuple[list[Suggestion], int, bytes | None]:
    """Lecture tolérante pour l'affichage : un magasin illisible n'a aucune suggestion et ne casse rien."""
    try:
        return store.read(chantier)
    except store.StoreError:
        return [], 0, None


def listing(root: Path, slug: str) -> dict[str, Any]:
    wa._need_flag(root)
    slug = wa._slug(slug)
    items, ignored, damaged = _read(_dir(root, slug))
    return {"suggestions": _state_view(root, slug, items), "ignored": ignored, "damaged": damaged is not None}


def pending(root: Path, slug: str) -> list[dict[str, Any]]:
    """Les suggestions en attente dont le bloc est encore là : ce que le document affiche."""
    return [s for s in listing(root, slug)["suggestions"] if s["etat"] == "proposee"]


def pending_count(root: Path, slug: str) -> int:
    """Compteur de la bibliothèque (slug rendu par ``list_chantiers``) ; 0 sans magasin, sans lire de phase."""
    from grimoire.core.cadrage import cadrage_dir

    if not store.store_path(cadrage_dir(root, None if slug == "principal" else slug)).is_file():
        return 0
    return len(pending(root, slug))


def _clean(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise ValueError(f"`{name}` requis")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"`{name}` : une chaîne est attendue")
    clean: str = value.strip()
    if len(clean) > limit:
        raise ValueError(f"`{name}` trop long ({limit} caractères au plus)")
    return clean


def _refuse_invisible(value: str, name: str) -> None:
    """Refuse les caractères de catégorie Cf (bidi, espace sans chasse, « tags »…) : l'humain valide ce qu'il voit."""
    for char in value:
        if unicodedata.category(char) == "Cf":
            raise ValueError(
                f"`{name}` : caractère invisible refusé (U+{ord(char):04X}) ; une suggestion reste lisible tel quel"
            )


def _persist(chantier: Path, items: list[Suggestion], ignored: int, damaged: bytes | None) -> str:
    """Réécrit le magasin ; un magasin abîmé ou des lignes ignorées sont d'abord mis de côté sous un nom daté."""
    aside = ""
    if damaged is not None:
        aside = store.set_aside(chantier, damaged)
    elif ignored:
        aside = store.set_aside(chantier, store.store_path(chantier).read_bytes())
    store.save(chantier, items)
    return aside


def deposit(root: Path, slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Dépose une suggestion sur un bloc. N'écrit que dans le magasin du chantier, jamais dans une phase."""
    wa._need_flag(root)
    slug = wa._slug(slug)
    block_id = _clean(body.get("block_id"), "block_id", 200, required=True)
    texte = body.get("texte")
    if not isinstance(texte, str) or not texte.strip():
        raise ValueError("`texte` requis : une suggestion propose un texte non vide")
    if len(texte) > wa.MAX_BLOCK_CHARS:
        raise ValueError(f"`texte` trop long ({wa.MAX_BLOCK_CHARS} caractères au plus)")
    posture = _clean(body.get("posture"), "posture", 20) or "explorer"
    if posture not in store.POSTURES:
        raise ValueError(f"`posture` inconnue : {', '.join(store.POSTURES)}")
    motif = _clean(body.get("motif"), "motif", store.MOTIF_MAX)
    _refuse_invisible(texte, "texte")
    _refuse_invisible(motif, "motif")
    phase = _phase_of(block_id)
    path = wa._phase_path(root, slug, phase.filename)
    if not path.is_file():
        raise FileNotFoundError(f"phase absente : {phase.filename}")
    try:
        blocks = atelier_blocs.split(phase.id, path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{phase.filename} n'est pas de l'UTF-8 : aucune suggestion possible") from exc
    try:
        block = atelier_blocs.find_block(blocks, block_id)
    except atelier_blocs.BlockNotFoundError as exc:
        raise wa.ConflictError(
            f"le bloc {block_id!r} n'existe plus dans {phase.filename} : relire le document"
        ) from exc
    if not block.editable:
        raise PermissionError(f"le bloc {block.kind} ne reçoit pas de suggestion : il structure le fichier")
    wa._refuse_structure(texte.replace("\r\n", "\n"))
    if texte.strip() == block.text.strip():
        raise ValueError("la suggestion est identique au bloc : rien à proposer")
    homonymes, voisins = _context(blocks, blocks.index(block))
    chantier = _dir(root, slug)
    with store.locked_store(chantier):
        items, ignored, damaged = store.read(chantier)
        stale = _refresh(root, slug, items)  # le plafond ne compte que ce qui est encore valide
        for item in items:  # redéposer la même chose est sans effet : on rend l'existante
            if item.etat == "proposee" and item.block_id == block_id and item.texte == texte:
                if stale or ignored or damaged is not None:
                    _persist(chantier, items, ignored, damaged)
                return {"ok": True, "suggestion": item.to_dict(), "already": True}
        if sum(1 for i in items if i.etat == "proposee") >= store.MAX_PENDING:
            if stale or ignored or damaged is not None:
                _persist(chantier, items, ignored, damaged)
            raise ValueError(f"{store.MAX_PENDING} suggestions en attente : l'humain en tranche avant d'en déposer")
        while len(items) >= store.MAX_ENTRIES:  # les plus anciennes déjà tranchées ou périmées partent d'abord
            old = next(i for i, x in enumerate(items) if x.etat != "proposee")
            del items[old]
        made = store.new_suggestion(
            phase=phase.id,
            block_id=block_id,
            block_fingerprint=atelier_blocs.fingerprint(block.text),
            avant=block.text,
            texte=texte,
            motif=motif,
            posture=posture,
            auteur="agent",
            homonymes=homonymes,
            voisins=voisins,
        )
        aside = _persist(chantier, [*items, made], ignored, damaged)
    out: dict[str, Any] = {"ok": True, "suggestion": made.to_dict()}
    if aside:
        out["ecarte"] = aside  # l'ancien magasin, mis de côté sous ce nom dans le dossier du chantier
    return out


def suggest(
    project_path: str, slug: str, block_id: str, texte: str, motif: str = "", posture: str = "explorer"
) -> dict[str, Any]:
    """Le seul geste que l'outil MCP offre : déposer. Il ne tranche pas : aucun chemin vers :func:`decide`."""
    return deposit(
        Path(project_path).resolve(),
        slug,
        {"block_id": block_id, "texte": texte, "motif": motif, "posture": posture},
    )


def _settle(found: Suggestion, etat: str, motif: str) -> None:
    found.etat = etat
    found.decide_le = store.now()
    found.decide_motif = motif


def decide(root: Path, slug: str, suggestion_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Geste humain : accepter (réécrit la seule plage du bloc) ou refuser (le fichier n'est pas touché)."""
    from grimoire.voice.dictation import sent_as_dictation
    from grimoire.voice.safety import VOICE_CANNOT_APPROVE

    wa._need_flag(root)
    slug = wa._slug(slug)
    decision = body.get("decision")
    if decision not in DECISIONS:
        raise ValueError("`decision` requise : accept ou reject")
    if sent_as_dictation(root, body):  # comme `chat/permission` : seul le clic à l'écran tranche
        raise ValueError(VOICE_CANNOT_APPROVE)
    motif = _clean(body.get("motif"), "motif", store.MOTIF_MAX)
    chantier = _dir(root, slug)
    with store.locked_store(chantier):
        items, ignored, damaged = store.read(chantier)
        found = next((i for i in items if i.id == suggestion_id), None)
        if found is None:
            raise FileNotFoundError(f"suggestion inconnue : {suggestion_id!r}")
        if found.etat != "proposee":
            raise _conflict(
                f"suggestion déjà {_LABEL[found.etat]} : rien à décider",
                "perimee" if found.etat == "perimee" else "deja-tranchee",
            )
        if decision == "reject":
            _settle(found, "refusee", motif)
            _persist(chantier, items, ignored, damaged)
            return {"ok": True, "suggestion": found.to_dict()}
        phase = _phase_of(found.phase)
        path = wa._phase_path(root, slug, phase.filename)
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise wa.ConflictError(f"{phase.filename} illisible : la suggestion ne peut pas s'appliquer") from exc
        stale = not _live(atelier_blocs.split(phase.id, text), found)
        if not stale:
            try:
                result = wa.edit_block(
                    root, slug, found.block_id, {"text": found.texte, "expected": atelier_blocs.fingerprint(text)}
                )
            except wa.ConflictError:  # le fichier a bougé entre la lecture et l'écriture
                stale = True
        if stale:
            _settle(found, "perimee", _STALE)
            _persist(chantier, items, ignored, damaged)
            raise _conflict(
                f"le bloc {found.block_id!r} a changé depuis le dépôt : suggestion périmée, rien n'a été écrit",
                "perimee",
            )
        _settle(found, "acceptee", motif)
        _refresh(root, slug, items)  # les autres suggestions sur ce bloc ne visent plus rien : périmées
        _persist(chantier, items, ignored, damaged)
        return {"ok": True, "suggestion": found.to_dict(), "phase": result["phase"]}
