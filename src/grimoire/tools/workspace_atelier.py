"""Routes de l'Atelier de spec, en test (lot 1 de l'épique #1197, issue #1240).

Module voisin de :mod:`grimoire.tools.workspace_routes` (au plafond du cliquet de lignes) : celui-ci lui
délègue tout chemin que :func:`handles` reconnaît, et le cockpit lui délègue ``PUT`` (:func:`put_http`).

Le drapeau ``cockpit.test_features.atelier`` de ``project-context.yaml`` (faux par défaut) commande tout :
éteint, chaque route ``atelier/*`` répond 404 et ce module n'importe ni :mod:`grimoire.core.chantiers` ni le
découpage en blocs. ``features`` (lecture et réglage du drapeau) reste toujours joignable : c'est lui qui
permet de l'allumer.

Routes (préfixe ``/api/workspace/``) :

- ``GET features`` ; ``POST features`` ``{"atelier": bool}`` (drapeau du PROJET) ou ``{"mascotte": bool}`` (préférence
  de cockpit de la MACHINE : tous les projets, la vue globale ; elle reprend l'ancien drapeau par projet tant
  qu'elle n'est pas posée)
- ``GET atelier/chantiers`` ; ``POST atelier/chantiers`` ``{"titre", "slug"?}``
- ``GET atelier/chantiers/<slug>`` : cinq phases en blocs typés
- ``PUT atelier/chantiers/<slug>/blocs/<id>`` ``{"text", "expected"}`` : réécrit la seule plage du bloc ;
  contrôle PAR BLOC : ``block_fp`` (empreinte du bloc lue avec ``GET``, ``fp``), ``prev`` et ``next`` (celles de ses
  voisins, ``""`` au bord), ``base`` (le texte lu, pour la fusion). Un autre bloc modifié entre-temps ne gêne pas ;
  le même bloc modifié, ou des blocs jumeaux, rendent 409 avec ``submitted``, ``current``, ``base``, ``merged``
  (fusion à trois voies proposée, jamais appliquée) et ``reason`` (``bloc-modifie``, ``bloc-ambigu``,
  ``bloc-introuvable``). Client ancien : ``expected`` seul (empreinte du fichier de phase), un fichier changé = 409.
- ``GET|POST atelier/chantiers/<slug>/suggestions``, ``POST …/suggestions/<id>`` : lot 2, voir
  :mod:`grimoire.tools.workspace_atelier_suggestions` (``POST`` rend ses refus par :func:`post_http`).
- ``GET|POST atelier/chantiers/<slug>/comments``, ``POST …/comments/<id>`` : lot 3, les commentaires ancrés, voir
  :mod:`grimoire.tools.workspace_atelier_comments` (l'auteur est toujours « humain » ici ; l'agent passe par l'outil MCP).
- ``GET atelier/chantiers/<slug>/entretien``, ``POST …/assistant`` : lot 5, l'assistant qui guide le cadrage, voir
  :mod:`grimoire.tools.workspace_atelier_assistant` (le geste est un tour du chat ; la consigne vient d'une liste fermée).
- ``GET atelier/chantiers/<slug>/trace``, ``GET|POST …/decoupage`` : lot 4, le document vivant, voir
  :mod:`grimoire.tools.workspace_atelier_trace`. Aucune route ne valide une exigence : c'est un geste de la MOA, au terminal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from grimoire.core.cadrage import PHASES, cadrage_dir
from grimoire.core.cadrage import status as cadrage_status

PREFIX = "/api/workspace/"
FEATURES = f"{PREFIX}features"
ATELIER = f"{PREFIX}atelier/"
CONTEXT_FILE = "project-context.yaml"
FLAG = ("cockpit", "test_features", "atelier")
#: Mascotte vocale du chat (épique #1197, vocal #1343) : même mécanisme, rien de plus côté serveur.
MASCOTTE_FLAG = ("cockpit", "test_features", "mascotte")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Le texte d'un bloc : plafond large, un bloc est une page au plus.
MAX_BLOCK_CHARS = 20_000


class ConflictError(Exception):
    """Le fichier de phase a changé depuis la lecture (409). ``reason`` précise la cause quand on la connaît."""

    reason = ""
    #: Champs ajoutés à la réponse 409 (édition par bloc : version courante, texte soumis, fusion proposée).
    payload: ClassVar[dict[str, Any]] = {}

    def __init__(self, message: str = "", payload: dict[str, Any] | None = None, reason: str = "") -> None:
        super().__init__(message)
        if payload is not None:
            self.payload = payload  # type: ignore[misc]
        if reason:
            self.reason = reason


def handles(path: str) -> bool:
    return path == FEATURES or path.startswith(ATELIER)


# ── Drapeau ─────────────────────────────────────────────────────────────────


def _load(root: Path) -> Any:
    from ruamel.yaml import YAML

    path = root / CONTEXT_FILE
    if not path.is_file():
        return None
    try:
        return YAML().load(path.read_text(encoding="utf-8"))
    except Exception:  # fichier illisible : le drapeau reste éteint, la lecture ne casse pas
        return None


def enabled(root: Path, flag: tuple[str, ...] = FLAG) -> bool:
    """Vrai seulement si ``cockpit.test_features.atelier`` (ou le drapeau donné) vaut exactement ``true``."""
    node: Any = _load(root)
    for key in flag:
        if not hasattr(node, "get"):
            return False
        node = node.get(key)
    return node is True


#: Les fonctions en test qui valent pour TOUT le cockpit de la machine (préférence de cockpit, ``cockpit.json``), pas pour
#: un projet : la mascotte suit la personne d'un projet à l'autre et dans la vue « Tous les projets ».
MACHINE_FEATURES_KEY = "test_features"


def machine_pref(name: str) -> bool | None:
    """La préférence de cockpit de la machine pour ``name`` : ``True`` ou ``False`` si elle a été posée, sinon ``None``."""
    from grimoire.tools.project_registry import read_state

    node = read_state().get(MACHINE_FEATURES_KEY)
    value = node.get(name) if isinstance(node, dict) else None
    return value if isinstance(value, bool) else None


_LEGACY_SEEN: dict[str, tuple[float, bool]] = {}


def _legacy_project_flag(path: str) -> bool:
    """Le drapeau par projet d'avant (``cockpit.test_features.mascotte``), mis en cache par date du fichier."""
    file = Path(path) / CONTEXT_FILE
    try:
        stamp = file.stat().st_mtime
    except OSError:
        return False
    seen = _LEGACY_SEEN.get(path)
    if seen is not None and seen[0] == stamp:
        return seen[1]
    value = enabled(Path(path), MASCOTTE_FLAG)
    _LEGACY_SEEN[path] = (stamp, value)
    return value


def mascotte_on(root: Path) -> bool:
    """La mascotte est-elle allumée pour ce cockpit ?

    La préférence de la machine, si elle a été posée, décide pour tous les projets. Sinon on reprend l'ancien drapeau
    par projet : celui du projet servi, ou de n'importe quel projet du registre. Qui l'avait allumé dans un projet ne
    perd donc pas son choix en regardant un autre projet, ni dans la vue « Tous les projets ». Lecture seule.
    """
    pref = machine_pref("mascotte")
    if pref is not None:
        return pref
    if enabled(root, MASCOTTE_FLAG):
        return True
    from grimoire.tools.project_registry import load_registry

    for entry in load_registry():
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and path and _legacy_project_flag(path):
            return True
    return False


def features(root: Path) -> dict[str, Any]:
    return {"atelier": enabled(root), "mascotte": mascotte_on(root), "mascotte_scope": "machine"}


def set_mascotte(root: Path, on: Any) -> dict[str, Any]:
    """Écrit ``cockpit.test_features.mascotte`` DU PROJET (l'ancien réglage ; le cockpit n'écrit plus que :func:`set_mascotte_machine`)."""
    if not isinstance(on, bool):
        raise ValueError("`mascotte` (booléen) requis : true active, false éteint")
    return set_atelier(root, on, MASCOTTE_FLAG)


def set_mascotte_machine(root: Path, on: Any) -> dict[str, Any]:
    """Pose la préférence de cockpit « mascotte » : allumée ou éteinte pour tous les projets de cette machine.

    Elle prime sur l'ancien drapeau par projet, qu'on ne réécrit pas : éteindre ici éteint partout, sans toucher
    aux ``project-context.yaml``.
    """
    if not isinstance(on, bool):
        raise ValueError("`mascotte` (booléen) requis : true active, false éteint")
    from grimoire.tools.project_registry import read_state, write_state

    state = read_state()
    node = state.get(MACHINE_FEATURES_KEY)
    state[MACHINE_FEATURES_KEY] = {**(node if isinstance(node, dict) else {}), "mascotte": on}
    write_state(state)
    return features(root)


def set_atelier(root: Path, on: Any, flag: tuple[str, ...] = FLAG) -> dict[str, Any]:
    """Écrit le drapeau dans ``project-context.yaml`` (commentaires et ordre conservés)."""
    import io

    from ruamel.yaml import YAML

    from grimoire.core.fsio import atomic_write, locked

    if not isinstance(on, bool):
        raise ValueError("`atelier` (booléen) requis : true active, false éteint")
    path = root / CONTEXT_FILE
    if not path.is_file():
        raise ValueError(f"{CONTEXT_FILE} introuvable : le projet n'est pas initialisé")
    with locked(path):
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(path.read_text(encoding="utf-8"))
        if not hasattr(data, "get"):
            raise ValueError(f"{CONTEXT_FILE} illisible : un dictionnaire est attendu")
        node = data
        for key in flag[:-1]:
            if not hasattr(node.get(key), "get"):
                node[key] = {}
            node = node[key]
        node[flag[-1]] = on
        out = io.StringIO()
        yaml.dump(data, out)
        atomic_write(path, out.getvalue().encode("utf-8"))
    return features(root)


# ── Lectures ────────────────────────────────────────────────────────────────


def _need_flag(root: Path) -> None:
    if not enabled(root):
        raise FileNotFoundError("atelier : fonction en test, désactivée pour ce projet")


def _slug(raw: str) -> str:
    if raw != "principal" and not _SLUG.match(raw):
        raise FileNotFoundError(f"chantier inconnu : {raw!r}")
    return raw


def _phase_path(root: Path, slug: str, filename: str) -> Path:
    """Chemin d'un fichier de phase, confiné au dossier des cadrages.

    Le slug sert seulement à retrouver un chantier existant ; le chemin est
    construit depuis le slug que rend :func:`list_chantiers`, puis refusé si un maillon entre la racine du
    projet et le fichier est un lien symbolique.
    """
    from grimoire.core.chantiers import list_chantiers

    if slug == "principal":
        known: str | None = None
    else:
        known = next((c.slug for c in list_chantiers(root) if c.slug == slug), None)
        if known is None:
            raise FileNotFoundError(f"chantier inconnu : {slug!r}")
    raw = cadrage_dir(root, known) / filename
    # Même garde que `cadrage_interview._load`, étendue à toute la chaîne : aucun maillon entre la racine du
    # projet et le fichier n'est un lien symbolique (la base n'est pas résolue : un parent en lien sort du projet).
    try:
        parts = raw.relative_to(root).parts
    except ValueError as exc:
        raise FileNotFoundError(f"chantier inconnu : {slug!r}") from exc
    walk = root
    for part in parts:
        walk = walk / part
        if walk.is_symlink():
            raise FileNotFoundError(f"chantier inconnu : {slug!r}")
    return raw


def _compact(avancement: dict[str, Any]) -> dict[str, Any]:
    """L'avancement allégé d'une ligne de la liste : le même calcul, sans le détail rubrique par rubrique."""
    keep = ("etat", "resume", "a_preciser", "a_confirmer", "angles", "complet_phases", "erreur")
    out = {k: avancement[k] for k in keep}
    out["phases"] = [{k: p[k] for k in ("id", "complete", "remplis", "vides", "je_ne_sais_pas", "sans_objet", "total")}
                     for p in avancement["phases"]]
    return out


def _asking(root: Path, slug: str, agent_ok: bool) -> bool:
    """Une question d'entretien attend-elle la personne ? Celle de l'agent, ou la question guidée quand aucun agent ne joue."""
    from grimoire.tools import workspace_atelier_guide as guide
    from grimoire.tools.workspace_chat import _conversation

    if _conversation(root).atelier_facts(slug)[1]:
        return True
    shown = guide.view(root, slug)
    return not agent_ok and bool(shown.get("started")) and not shown.get("finished") and bool(shown.get("available"))


def _chantier_row(root: Path, chantier: Any, agent_ok: bool | None = None) -> dict[str, Any]:
    row: dict[str, Any] = chantier.to_dict()
    name = None if chantier.default else chantier.slug
    row["phases"] = [r["state"] for r in cadrage_status(root, name)["phases"]]
    if chantier.error:
        row["chemin"] = (cadrage_dir(root, name).relative_to(root) / "chantier.yaml").as_posix()
    else:
        from grimoire.core.cadrage_progress import progress
        from grimoire.tools import workspace_atelier_assistant as assistant
        from grimoire.tools import workspace_atelier_comments as comments
        from grimoire.tools import workspace_atelier_loop as loop
        from grimoire.tools import workspace_atelier_suggestions as sugg

        row["suggestions"] = sugg.pending_count(root, chantier.slug)  # en attente, bloc visé encore là
        row["commentaires"] = comments.open_count(root, chantier.slug)  # fils ouverts (lot 3)
        row["avancement"] = _compact(progress(root, name))  # UN calcul : celui de l'assistant et de la page
        if agent_ok is None:
            agent_ok = bool(assistant.agent_status(root)["available"])
        row["attente"] = loop.waiting(
            root, chantier.slug, suggestions=row["suggestions"], asking=_asking(root, chantier.slug, agent_ok)
        )
    return dict(row)


def list_view(root: Path) -> dict[str, Any]:
    from grimoire.core.chantiers import ETATS, list_chantiers
    from grimoire.tools import workspace_atelier_assistant as assistant

    agent_ok = bool(assistant.agent_status(root)["available"])
    return {"chantiers": [_chantier_row(root, c, agent_ok) for c in list_chantiers(root)], "etats": list(ETATS)}


def create(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    from grimoire.core.chantiers import new_chantier
    from grimoire.core.exceptions import GrimoireError

    slug = body.get("slug")
    try:
        made = new_chantier(root, str(body.get("titre") or ""), slug=str(slug) if slug else None)
    except GrimoireError as exc:
        raise ValueError(str(exc)) from exc
    return {"chantier": _chantier_row(root, made)}


def _phase_view(root: Path, name: str | None, phase: Any) -> dict[str, Any]:
    from grimoire.core.cadrage import phase_report
    from grimoire.tools import atelier_blocs, atelier_conflict

    # Le nom vient du chantier retrouvé par `list_chantiers`, jamais de l'URL.
    report = phase_report(root, phase, name)
    path = _phase_path(root, name or "principal", phase.filename)
    out: dict[str, Any] = {
        "id": phase.id,
        "title": phase.title,
        "file": report["file"],
        "state": report["state"],
        "sections": report["sections"],
        "fingerprint": "",
        "blocks": [],
        "readable": True,
    }
    if not path.is_file():
        return out
    raw = path.read_bytes()
    out["fingerprint"] = atelier_blocs.fingerprint(raw.decode("utf-8", errors="replace"))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        out["readable"] = False  # lisible dans `cadrage status`, pas éditable bloc par bloc
        return out
    out["fingerprint"] = atelier_blocs.fingerprint(text)
    blocks = atelier_blocs.split(phase.id, text)
    marks = atelier_conflict.neighbour_fields(blocks)  # empreintes que le client renvoie à l'écriture (par bloc)
    out["blocks"] = [{**b.to_dict(), **mark} for b, mark in zip(blocks, marks, strict=True)]
    return out


def document(root: Path, slug: str) -> dict[str, Any]:
    from grimoire.core.chantiers import list_chantiers

    slug = _slug(slug)
    found = next((c for c in list_chantiers(root) if c.slug == slug), None)
    if found is None and slug == "principal":
        from grimoire.core.chantiers import read_chantier

        found = read_chantier(root)
    if found is None:
        raise FileNotFoundError(f"chantier inconnu : {slug!r}")
    from grimoire.tools import workspace_atelier_suggestions as sugg

    phases = [_phase_view(root, None if found.default else found.slug, p) for p in PHASES]
    waiting = sugg.pending(root, slug)
    for phase in phases:
        phase["suggestions"] = [s for s in waiting if s["phase"] == phase["id"]]
    from grimoire.tools import workspace_atelier_comments as comments

    comments.attach(root, slug, phases)  # lot 3 : les fils de chaque phase, avec leur ancre d'aujourd'hui
    from grimoire.core.cadrage_progress import progress
    from grimoire.tools import workspace_atelier_loop as loop

    return {
        "chantier": _chantier_row(root, found),
        "phases": phases,
        "avancement": progress(root, None if found.default else found.slug),  # le calcul unique, en détail
        "taches": _safe_tasks(root, found.slug),  # ce que « Valider et découper » a créé
        "avertissements": loop.duplicate_requirements(phases),
        "auteur": loop.author(root),  # le nom que porte un commentaire humain (MOA, sinon utilisateur du projet)
    }


def _safe_tasks(root: Path, slug: str) -> list[dict[str, Any]]:
    """Les tâches du chantier ; un ledger illisible ne casse pas la page (liste vide)."""
    from grimoire.tools import workspace_atelier_loop as loop

    try:
        return loop.tasks(root, slug)
    except Exception:
        return []


# ── Édition d'un bloc ───────────────────────────────────────────────────────

_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_RULE = re.compile(r"^\s{0,3}---\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def _refuse_structure(text: str) -> None:
    """Un bloc ne réécrit pas la structure du fichier (même règle que `cadrage_interview._fold`).

    Refusés : les caractères de contrôle (sauf saut de ligne et tabulation) et les séparateurs de ligne
    Unicode ; un bloc de code resté ouvert (il avalerait la suite du fichier) ; hors des blocs de code
    fermés, toute ligne de titre ``#`` ou filet ``---`` (elle ferait naître une rubrique ou un front matter).
    """
    import unicodedata

    for char in text:
        if char not in "\n\t" and unicodedata.category(char) in {"Cc", "Zl", "Zp"}:
            raise ValueError(f"caractère de contrôle refusé (U+{ord(char):04X}) : un bloc reste du texte simple")
    fence: str | None = None
    for line in text.split("\n"):
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            continue
        opened = _FENCE.match(line)
        if opened:
            fence = opened.group(1)
        elif _HEADING.match(line) or _RULE.match(line):
            raise ValueError(
                "un bloc ne peut pas contenir de titre (#) ni de filet (---) : il structurerait le fichier"
            )
    if fence is not None:
        raise ValueError("bloc de code non fermé : il avalerait la suite du fichier")


def _locate_block(blocks: list[Any], body: dict[str, Any], text: str, filename: str) -> Any:
    """Le bloc à réécrire d'après son empreinte lue (contrôle par bloc), ou un 409 qui porte tout ce qu'il faut
    pour ne rien perdre : le texte soumis, la version courante du bloc, la base lue et une fusion proposée."""
    from grimoire.tools import atelier_conflict as ac
    from grimoire.tools.atelier_blocs import fingerprint

    found = ac.locate(blocks, body["block_fp"], body.get("prev") or "", body.get("next") or "")
    if found.kind == ac.SAME:
        return blocks[found.index]
    payload: dict[str, Any] = {"submitted": text, "base": body.get("base"), "current": None, "merged": None}
    if found.kind == ac.CHANGED:
        mark = ac.neighbour_fields(blocks)[found.index]
        current = blocks[found.index]
        payload["current"] = {"id": current.id, "text": current.text, "editable": current.editable, **mark}
        if isinstance(body.get("base"), str):
            payload["merged"] = ac.merge3(body["base"], text, current.text)
        if payload["merged"] is not None and fingerprint(payload["merged"]) == fingerprint(current.text):
            payload["merged"] = None  # la fusion ne ferait rien de plus que la version courante
        raise ConflictError(
            f"le bloc a changé dans {filename} depuis la lecture : rien n'est écrit, ta saisie est conservée",
            payload,
            "bloc-modifie",
        )
    if found.kind == ac.AMBIGUOUS:
        raise ConflictError(
            f"plusieurs blocs identiques dans {filename} : impossible de savoir lequel tu modifies, rien n'est écrit",
            payload,
            "bloc-ambigu",
        )
    raise ConflictError(
        f"le bloc n'existe plus dans {filename} : rien n'est écrit, ta saisie est conservée",
        payload,
        "bloc-introuvable",
    )


def edit_block(root: Path, slug: str, block_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Réécrit la plage du bloc ``block_id`` ; rien d'autre dans le fichier ne bouge, d'un seul octet."""
    from grimoire.core.fsio import atomic_write, locked
    from grimoire.tools import atelier_blocs

    slug = _slug(slug)
    phase = next((p for p in PHASES if p.id == block_id.split(".", 1)[0]), None)
    if phase is None:
        raise FileNotFoundError(f"bloc inconnu : {block_id!r}")
    text, expected = body.get("text"), body.get("expected")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("`text` requis : un bloc ne se vide pas")
    if len(text) > MAX_BLOCK_CHARS:
        raise ValueError(f"`text` trop long ({MAX_BLOCK_CHARS} caractères au plus)")
    per_block = body.get("block_fp") is not None
    if per_block:
        for key in ("block_fp", "prev", "next", "base"):
            if body.get(key) is not None and not isinstance(body[key], str):
                raise ValueError(f"`{key}` doit être une chaîne")
        if not body["block_fp"]:
            raise ValueError("`block_fp` requis : l'empreinte du bloc lue avec le chantier")
        if len(body.get("base") or "") > MAX_BLOCK_CHARS:
            raise ValueError(f"`base` trop long ({MAX_BLOCK_CHARS} caractères au plus)")
    elif not isinstance(expected, str) or not expected:
        raise ValueError("`expected` requis : l'empreinte du fichier de phase lue avec le chantier")
    path = _phase_path(root, slug, phase.filename)
    if not path.is_file():
        raise FileNotFoundError(f"phase absente : {phase.filename}")
    with locked(path):
        try:
            current = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{phase.filename} n'est pas de l'UTF-8 : modifiez-le hors du cockpit") from exc
        blocks = atelier_blocs.split(phase.id, current)
        if per_block:
            block = _locate_block(blocks, body, text, phase.filename)
        else:  # client ancien : l'empreinte du fichier entier fait foi, comme avant
            if atelier_blocs.fingerprint(current) != expected:
                raise ConflictError(f"{phase.filename} a changé depuis la lecture : bloc rechargé")
            try:
                block = atelier_blocs.find_block(blocks, block_id)
            except atelier_blocs.BlockNotFoundError as exc:
                raise ConflictError(f"le bloc {block_id!r} n'existe plus dans {phase.filename}") from exc
        if not block.editable:
            raise PermissionError(f"le bloc {block.kind} ne se modifie pas d'ici : il structure le fichier")
        _refuse_structure(text.replace("\r\n", "\n"))
        updated = atelier_blocs.replace_block(current, block, text)
        if updated != current:
            atomic_write(path, updated.encode("utf-8"))
    from grimoire.tools import workspace_atelier_comments as comments

    comments.resync_after_edit(
        root, slug
    )  # lot 3 : les fils retrouvés suivent leur passage (sans effet sur l'écriture)
    from grimoire.tools import workspace_atelier_loop as loop

    view = _phase_view(root, None if slug == "principal" else slug, phase)
    return {"ok": True, "phase": view, "avertissements": loop.duplicate_requirements([view])}


# ── Branchement des hôtes ───────────────────────────────────────────────────


def get(root: Path, path: str, query: dict[str, list[str]]) -> Any:
    if path == FEATURES:
        return features(root)
    _need_flag(root)
    rest = path[len(ATELIER) :].strip("/").split("/")
    if rest == ["chantiers"]:
        return list_view(root)
    if len(rest) == 2 and rest[0] == "chantiers":
        return document(root, rest[1])
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] == "suggestions":
        from grimoire.tools import workspace_atelier_suggestions as sugg

        return sugg.listing(root, rest[1])
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] == "comments":
        from grimoire.tools import workspace_atelier_comments as comments

        return comments.listing(root, rest[1])
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] == "entretien":
        from grimoire.tools import workspace_atelier_assistant as assistant

        return assistant.entretien_view(root, rest[1])  # lot 5 : l'assistant, lecture seule
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] in ("trace", "decoupage"):
        from grimoire.tools import workspace_atelier_trace as live

        return (
            live.trace_view(root, rest[1], (query.get("req") or [""])[0])
            if rest[2] == "trace"
            else live.decoupage_preview(root, rest[1])
        )
    raise FileNotFoundError(f"route inconnue : {path}")


#: Les écritures de l'Atelier dans un chantier qui remettent à jour sa date de modification et font avancer son état.
_WRITING = ("suggestions", "comments", "decoupage")


def post(root: Path, path: str, body: dict[str, Any]) -> Any:
    result = _post(root, path, body)
    rest = path[len(ATELIER) :].strip("/").split("/") if path.startswith(ATELIER) else []
    if len(rest) >= 3 and rest[0] == "chantiers" and rest[2] in _WRITING and not (
        isinstance(result, dict) and result.get("already")  # un découpage rejoué n'a rien écrit
    ):
        from grimoire.tools import workspace_atelier_loop as loop

        loop.after_write(root, rest[1])  # écriture réussie seulement : une lecture ne passe jamais ici
    return result


def _post(root: Path, path: str, body: dict[str, Any]) -> Any:
    if path == FEATURES:
        if "mascotte" in body and "atelier" not in body:
            return set_mascotte_machine(root, body.get("mascotte"))
        return set_atelier(root, body.get("atelier"))
    _need_flag(root)
    if path.rstrip("/") == f"{ATELIER}chantiers":
        return create(root, body)
    rest = path[len(ATELIER) :].strip("/").split("/")
    if len(rest) >= 3 and rest[0] == "chantiers" and rest[2] == "suggestions":
        from grimoire.tools import workspace_atelier_suggestions as sugg

        if len(rest) == 3:
            return sugg.deposit(root, rest[1], body)
        if len(rest) == 4:
            return sugg.decide(root, rest[1], rest[3], body)
    if len(rest) in (3, 4) and rest[0] == "chantiers" and rest[2] == "comments":
        from grimoire.tools import workspace_atelier_comments as comments

        # Lot 3 : l'auteur est celui du canal (cette route = l'humain), jamais un champ du corps.
        return (
            comments.create(root, rest[1], body, "humain")
            if len(rest) == 3
            else comments.act(root, rest[1], rest[3], body)
        )
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] == "assistant":
        from grimoire.tools import workspace_atelier_assistant as assistant

        return assistant.act(root, rest[1], body)  # lot 5 : un geste = un tour du chat ; aucune validation ici
    if len(rest) == 3 and rest[0] == "chantiers" and rest[2] == "decoupage":
        from grimoire.tools import workspace_atelier_trace as live

        return live.decoupage_apply(root, rest[1], body)  # lot 4 ; aucune route de validation : voir ce module
    raise FileNotFoundError(f"route inconnue : {path}")


def post_http(root: Path, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``POST`` complet sous ``atelier/`` : code HTTP et charge, refus du domaine traduits (dont le 409)."""
    try:
        result = post(root, path, body)
        return 200, result if isinstance(result, dict) else {"ok": True, "result": result}
    except ConflictError as exc:
        return 409, {"ok": False, "conflict": True, "reason": exc.reason, "error": str(exc)}
    except FileNotFoundError as exc:
        return 404, {"ok": False, "error": f"introuvable : {exc}"}
    except PermissionError as exc:
        return 403, {"ok": False, "error": str(exc)}
    except TimeoutError:  # le verrou du magasin ou du fichier n'est pas venu : réessayer
        return 503, {
            "ok": False,
            "error": "le fichier est verrouillé par une autre écriture : réessaie dans un instant",
        }
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}


def put_http(
    root: Path, path: str, raw: bytes, confirms: Callable[[Any], bool] | None = None
) -> tuple[int, dict[str, Any]]:
    """``PUT`` complet : le code HTTP et la charge, avec les refus du domaine déjà traduits."""
    try:
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ValueError("corps JSON attendu : un objet")
        if confirms is not None and not confirms(body):
            return 403, {"ok": False, "error": "le projet du corps ne correspond pas à l'URL"}
        _need_flag(root)
        rest = path[len(ATELIER) :].strip("/").split("/") if path.startswith(ATELIER) else []
        if len(rest) != 4 or rest[0] != "chantiers" or rest[2] != "blocs":
            raise FileNotFoundError(f"route inconnue : {path}")
        edited = edit_block(root, rest[1], rest[3], body)
        from grimoire.tools import workspace_atelier_loop as loop

        loop.after_write(root, rest[1])
        return 200, edited
    except ConflictError as exc:
        return 409, {"ok": False, "conflict": True, "reason": exc.reason, "error": str(exc), **exc.payload}
    except FileNotFoundError as exc:
        return 404, {"ok": False, "error": f"introuvable : {exc}"}
    except PermissionError as exc:
        return 403, {"ok": False, "error": str(exc)}
    except TimeoutError:
        return 503, {
            "ok": False,
            "error": "le fichier est verrouillé par une autre écriture : réessaie dans un instant",
        }
    except ValueError as exc:  # JSONDecodeError en est un
        return 400, {"ok": False, "error": str(exc)}
