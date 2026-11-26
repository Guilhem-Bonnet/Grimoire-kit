"""Suggestions de l'Atelier de spec, en test (lot 2 de l'épique #1197, rôles MOA/MOE #1342).

L'agent (MOE) dépose, l'humain (MOA) tranche. Le dépôt n'écrit **jamais** dans un fichier de phase ; seule
l'acceptation le fait, par le chemin du lot 1 (:func:`workspace_atelier.edit_block`), avec le même contrôle
d'empreinte. Tout est derrière le drapeau ``cockpit.test_features.atelier`` : éteint, chaque route répond 404
et l'outil MCP refuse.

Routes (préfixe ``/api/workspace/atelier/chantiers/<slug>/``) :

- ``GET suggestions`` : toutes les suggestions du chantier, avec leur état à jour ;
- ``POST suggestions`` ``{"block_id", "texte", "motif"?, "posture"?, "auteur"?}`` : dépôt ;
- ``POST suggestions/<id>`` ``{"decision": "accept"|"reject", "motif"?}`` : décision humaine.

Autorité : la décision traverse la garde d'écriture commune des deux hôtes (jeton de session de la page,
origine, type JSON : ``request_guard``) et refuse un corps issu d'une dictée (``sent_as_dictation``, comme
``chat/permission``). L'outil MCP, lui, ne sait que déposer : voir :func:`suggest`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core import atelier_suggestions as store
from grimoire.core.atelier_suggestions import Suggestion
from grimoire.core.cadrage import PHASES
from grimoire.tools import atelier_blocs
from grimoire.tools import workspace_atelier as wa

DECISIONS = ("accept", "reject")
_LABEL = {"proposee": "proposée", "acceptee": "acceptée", "refusee": "refusée", "perimee": "périmée"}


def _dir(root: Path, slug: str) -> Path:
    """Dossier du chantier, confiné comme un fichier de phase (aucun lien symbolique, chantier connu)."""
    return wa._phase_path(root, slug, PHASES[0].filename).parent


def _phase_of(block_id: str) -> Any:
    phase = next((p for p in PHASES if p.id == str(block_id).split(".", 1)[0]), None)
    if phase is None:
        raise FileNotFoundError(f"bloc inconnu : {block_id!r}")
    return phase


def _live(blocks: list[atelier_blocs.Block], item: Suggestion) -> bool:
    """Le bloc visé existe encore, tel qu'il était au dépôt."""
    return any(b.id == item.block_id and atelier_blocs.fingerprint(b.text) == item.block_fingerprint for b in blocks)


def _blocks(root: Path, slug: str, phase: Any) -> list[atelier_blocs.Block]:
    path = wa._phase_path(root, slug, phase.filename)
    try:
        return atelier_blocs.split(phase.id, path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def _state_view(root: Path, slug: str, items: list[Suggestion]) -> list[dict[str, Any]]:
    """Les suggestions avec l'état que le fichier d'aujourd'hui leur donne (rien n'est écrit en lecture)."""
    cache: dict[str, list[atelier_blocs.Block]] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        row = item.to_dict()
        if item.etat == "proposee":
            if item.phase not in cache:
                cache[item.phase] = _blocks(root, slug, _phase_of(item.phase))
            if not _live(cache[item.phase], item):
                row["etat"] = "perimee"
        out.append(row)
    return out


def listing(root: Path, slug: str) -> dict[str, Any]:
    wa._need_flag(root)
    slug = wa._slug(slug)
    return {"suggestions": _state_view(root, slug, store.load(_dir(root, slug)))}


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
    auteur = _clean(body.get("auteur"), "auteur", store.AUTHOR_MAX) or "agent"
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
    chantier = _dir(root, slug)
    with store.locked_store(chantier):
        items = store.load(chantier)
        for item in items:  # redéposer la même chose est sans effet : on rend l'existante
            if item.etat == "proposee" and item.block_id == block_id and item.texte == texte:
                return {"ok": True, "suggestion": item.to_dict(), "already": True}
        if sum(1 for i in items if i.etat == "proposee") >= store.MAX_PENDING:
            raise ValueError(f"{store.MAX_PENDING} suggestions en attente : l'humain en tranche avant d'en déposer")
        made = store.new_suggestion(
            phase=phase.id,
            block_id=block_id,
            block_fingerprint=atelier_blocs.fingerprint(block.text),
            avant=block.text,
            texte=texte,
            motif=motif,
            posture=posture,
            auteur=auteur,
        )
        store.save(chantier, [*items, made])
    return {"ok": True, "suggestion": made.to_dict()}


def suggest(
    project_path: str, slug: str, block_id: str, texte: str, motif: str = "", posture: str = "explorer"
) -> dict[str, Any]:
    """Le seul geste que l'outil MCP offre : déposer. Il ne tranche pas : aucun chemin vers :func:`decide`."""
    return deposit(
        Path(project_path).resolve(),
        slug,
        {"block_id": block_id, "texte": texte, "motif": motif, "posture": posture, "auteur": "agent"},
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
        items = store.load(chantier)
        found = next((i for i in items if i.id == suggestion_id), None)
        if found is None:
            raise FileNotFoundError(f"suggestion inconnue : {suggestion_id!r}")
        if found.etat != "proposee":
            raise wa.ConflictError(f"suggestion déjà {_LABEL[found.etat]} : rien à décider")
        if decision == "reject":
            _settle(found, "refusee", motif)
            store.save(chantier, items)
            return {"ok": True, "suggestion": found.to_dict()}
        phase = _phase_of(found.block_id)
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
            _settle(found, "perimee", "le bloc a changé depuis le dépôt")
            store.save(chantier, items)
            raise wa.ConflictError(
                f"le bloc {found.block_id!r} a changé depuis le dépôt : suggestion périmée, rien n'a été écrit"
            )
        _settle(found, "acceptee", motif)
        store.save(chantier, items)
        return {"ok": True, "suggestion": found.to_dict(), "phase": result["phase"]}
