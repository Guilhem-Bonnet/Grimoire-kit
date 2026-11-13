"""Routes de l'Atelier de spec, en test (lot 1 de l'épique #1197, issue #1240).

Module voisin de :mod:`grimoire.tools.workspace_routes` (au plafond du cliquet de lignes) : celui-ci lui
délègue tout chemin que :func:`handles` reconnaît, et le cockpit lui délègue ``PUT`` (:func:`put_http`).

Le drapeau ``cockpit.test_features.atelier`` de ``project-context.yaml`` (faux par défaut) commande tout :
éteint, chaque route ``atelier/*`` répond 404 et ce module n'importe ni :mod:`grimoire.core.chantiers` ni le
découpage en blocs. ``features`` (lecture et réglage du drapeau) reste toujours joignable : c'est lui qui
permet de l'allumer.

Routes (préfixe ``/api/workspace/``) :

- ``GET features`` ; ``POST features`` ``{"atelier": bool}``
- ``GET atelier/chantiers`` ; ``POST atelier/chantiers`` ``{"titre", "slug"?}``
- ``GET atelier/chantiers/<slug>`` : cinq phases en blocs typés
- ``PUT atelier/chantiers/<slug>/blocs/<id>`` ``{"text", "expected"}`` : réécrit la seule plage du bloc ;
  ``expected`` est l'empreinte du fichier de phase lue avec ``GET`` ; un fichier changé depuis = 409.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES, cadrage_dir
from grimoire.core.cadrage import status as cadrage_status

PREFIX = "/api/workspace/"
FEATURES = f"{PREFIX}features"
ATELIER = f"{PREFIX}atelier/"
CONTEXT_FILE = "project-context.yaml"
FLAG = ("cockpit", "test_features", "atelier")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Le texte d'un bloc : plafond large, un bloc est une page au plus.
MAX_BLOCK_CHARS = 20_000


class ConflictError(Exception):
    """Le fichier de phase a changé depuis la lecture (409)."""


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


def enabled(root: Path) -> bool:
    """Vrai seulement si ``cockpit.test_features.atelier`` vaut exactement ``true``."""
    node: Any = _load(root)
    for key in FLAG:
        if not hasattr(node, "get"):
            return False
        node = node.get(key)
    return node is True


def features(root: Path) -> dict[str, Any]:
    return {"atelier": enabled(root)}


def set_atelier(root: Path, on: Any) -> dict[str, Any]:
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
        for key in FLAG[:-1]:
            if not hasattr(node.get(key), "get"):
                node[key] = {}
            node = node[key]
        node[FLAG[-1]] = on
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


def _chantier_row(root: Path, chantier: Any) -> dict[str, Any]:
    row = chantier.to_dict()
    name = None if chantier.default else chantier.slug
    row["phases"] = [r["state"] for r in cadrage_status(root, name)["phases"]]
    if chantier.error:
        row["chemin"] = (cadrage_dir(root, name).relative_to(root) / "chantier.yaml").as_posix()
    return row


def list_view(root: Path) -> dict[str, Any]:
    from grimoire.core.chantiers import ETATS, list_chantiers

    return {"chantiers": [_chantier_row(root, c) for c in list_chantiers(root)], "etats": list(ETATS)}


def create(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    from grimoire.core.chantiers import new_chantier
    from grimoire.core.exceptions import GrimoireError

    slug = body.get("slug")
    try:
        made = new_chantier(root, str(body.get("titre") or ""), slug=str(slug) if slug else None)
    except GrimoireError as exc:
        raise ValueError(str(exc)) from exc
    return {"chantier": _chantier_row(root, made)}


def _phase_view(root: Path, slug: str, phase: Any) -> dict[str, Any]:
    from grimoire.core.cadrage import phase_report
    from grimoire.tools import atelier_blocs

    name = None if slug == "principal" else slug
    report = phase_report(root, phase, name)
    path = root / report["file"]
    out: dict[str, Any] = {
        "id": phase.id, "title": phase.title, "file": report["file"], "state": report["state"],
        "sections": report["sections"], "fingerprint": "", "blocks": [], "readable": True,
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
    out["blocks"] = [b.to_dict() for b in atelier_blocs.split(phase.id, text)]
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
    return {"chantier": _chantier_row(root, found), "phases": [_phase_view(root, slug, p) for p in PHASES]}


# ── Édition d'un bloc ───────────────────────────────────────────────────────


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
    if not isinstance(expected, str) or not expected:
        raise ValueError("`expected` requis : l'empreinte du fichier de phase lue avec le chantier")
    path = cadrage_dir(root, None if slug == "principal" else slug) / phase.filename
    if not path.is_file():
        raise FileNotFoundError(f"phase absente : {phase.filename}")
    with locked(path):
        try:
            current = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{phase.filename} n'est pas de l'UTF-8 : modifiez-le hors du cockpit") from exc
        if atelier_blocs.fingerprint(current) != expected:
            raise ConflictError(f"{phase.filename} a changé depuis la lecture : bloc rechargé")
        try:
            block = atelier_blocs.find_block(atelier_blocs.split(phase.id, current), block_id)
        except atelier_blocs.BlockNotFoundError as exc:
            raise ConflictError(f"le bloc {block_id!r} n'existe plus dans {phase.filename}") from exc
        if not block.editable:
            raise PermissionError(f"le bloc {block.kind} ne se modifie pas d'ici : il structure le fichier")
        updated = atelier_blocs.replace_block(current, block, text)
        if updated != current:
            atomic_write(path, updated.encode("utf-8"))
    return {"ok": True, "phase": _phase_view(root, slug, phase)}


# ── Branchement des hôtes ───────────────────────────────────────────────────


def get(root: Path, path: str, _query: dict[str, list[str]]) -> Any:
    if path == FEATURES:
        return features(root)
    _need_flag(root)
    rest = path[len(ATELIER):].strip("/").split("/")
    if rest == ["chantiers"]:
        return list_view(root)
    if len(rest) == 2 and rest[0] == "chantiers":
        return document(root, rest[1])
    raise FileNotFoundError(f"route inconnue : {path}")


def post(root: Path, path: str, body: dict[str, Any]) -> Any:
    if path == FEATURES:
        return set_atelier(root, body.get("atelier"))
    _need_flag(root)
    if path.rstrip("/") == f"{ATELIER}chantiers":
        return create(root, body)
    raise FileNotFoundError(f"route inconnue : {path}")


def put_http(root: Path, path: str, raw: bytes) -> tuple[int, dict[str, Any]]:
    """``PUT`` complet : le code HTTP et la charge, avec les refus du domaine déjà traduits."""
    try:
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise ValueError("corps JSON attendu : un objet")
        _need_flag(root)
        rest = path[len(ATELIER):].strip("/").split("/") if path.startswith(ATELIER) else []
        if len(rest) != 4 or rest[0] != "chantiers" or rest[2] != "blocs":
            raise FileNotFoundError(f"route inconnue : {path}")
        return 200, edit_block(root, rest[1], rest[3], body)
    except ConflictError as exc:
        return 409, {"ok": False, "conflict": True, "error": str(exc)}
    except FileNotFoundError as exc:
        return 404, {"ok": False, "error": f"introuvable : {exc}"}
    except PermissionError as exc:
        return 403, {"ok": False, "error": str(exc)}
    except ValueError as exc:  # JSONDecodeError en est un
        return 400, {"ok": False, "error": str(exc)}
