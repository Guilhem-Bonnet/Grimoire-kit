"""Chantiers du cadrage — plusieurs cadrages nommés par projet (lot 0, épique #1197).

Un chantier vit sous ``_grimoire/cadrage/<slug>/`` : les cinq fichiers de phase de
:mod:`grimoire.core.cadrage` plus un ``chantier.yaml`` (slug, titre, état, mission, dates).

Le **chantier par défaut** (``principal``) est formé des fichiers déjà présents à la racine de
``_grimoire/cadrage/`` : on ne les déplace pas, on ne les réécrit pas, et on ne leur crée pas de
``chantier.yaml`` sans action explicite (:func:`set_state` ou :func:`link_mission` sur ``principal``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.cadrage import (
    CADRAGE_DIR,
    DEFAULT_CHANTIER,
    PHASES,
    cadrage_dir,
    scaffold,
    status,
)
from grimoire.core.exceptions import GrimoireValidationError

__all__ = [
    "DEFAULT_CHANTIER",
    "ETATS",
    "MAX_SLUG",
    "Chantier",
    "has_only_named_chantiers",
    "link_mission",
    "list_chantiers",
    "new_chantier",
    "reachable_states",
    "read_chantier",
    "set_state",
    "slugify",
]

CHANTIER_FILE = "chantier.yaml"
MAX_SLUG = 48
ETATS: tuple[str, ...] = (
    "idee", "en-discussion", "pret-a-valider", "valide", "en-cours", "livre", "archive",
)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class Chantier:
    """Un chantier : identité, état, mission liée, progression des phases."""

    slug: str
    titre: str = ""
    etat: str = "idee"
    mission: str = ""
    cree: str = ""
    modifie: str = ""
    progress: str = f"0/{len(PHASES)}"
    default: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "slug": self.slug, "titre": self.titre, "etat": self.etat, "mission": self.mission,
            "cree": self.cree, "modifie": self.modifie, "progress": self.progress, "default": self.default,
        }
        if self.error:
            data["error"] = self.error
        return data


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(titre: str) -> str:
    """Minuscules ASCII, chiffres et tirets, au plus :data:`MAX_SLUG` caractères."""
    folded = unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:MAX_SLUG].strip("-")


def _check_slug(slug: str) -> str:
    if slug == DEFAULT_CHANTIER:
        raise GrimoireValidationError(f"« {DEFAULT_CHANTIER} » est réservé au chantier par défaut")
    if not slug or len(slug) > MAX_SLUG or not _SLUG_RE.match(slug):
        raise GrimoireValidationError(
            f"slug invalide : {slug!r} — minuscules ASCII, chiffres et tirets, {MAX_SLUG} caractères au plus"
        )
    return slug


def reachable_states(etat: str) -> tuple[str, ...]:
    """Les états atteignables depuis *etat* : l'avant dans l'ordre, ``pret-a-valider`` → ``en-discussion``, et
    ``archive`` depuis n'importe quel autre état."""
    if etat not in ETATS or etat == "archive":
        return ()
    index = ETATS.index(etat)
    out = list(ETATS[index + 1:])
    if etat == "pret-a-valider":
        out.insert(0, "en-discussion")
    if "archive" not in out:
        out.append("archive")
    return tuple(out)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise GrimoireValidationError(f"{path.name} illisible : {exc}") from exc
    if not isinstance(data, dict):
        raise GrimoireValidationError(f"{path.name} illisible : un dictionnaire est attendu")
    return data


def _dump(data: dict[str, str]) -> str:
    # json.dumps donne des scalaires YAML valides (guillemets doubles) ; l'ordre des champs est fixe.
    return "".join(f"{key}: {json.dumps(data[key], ensure_ascii=False)}\n" for key in
                   ("slug", "titre", "etat", "mission", "cree", "modifie"))


def _progress(root: Path, chantier: str | None) -> str:
    return str(status(root, chantier)["progress"])


def read_chantier(root: Path, slug: str | None = None) -> Chantier:
    """Lit un chantier. Le défaut sans ``chantier.yaml`` en déduit son état des phases (sans rien écrire)."""
    default = slug is None or slug == DEFAULT_CHANTIER
    directory = cadrage_dir(root, slug)
    name = DEFAULT_CHANTIER if default else str(slug)
    progress = _progress(root, None if default else slug)
    path = directory / CHANTIER_FILE
    if not path.is_file():
        if not default:
            raise GrimoireValidationError(f"chantier inconnu : {name!r}")
        report = status(root)
        started = any(r["state"] in ("partiel", "rempli") for r in report["phases"])
        return Chantier(
            DEFAULT_CHANTIER, "Principal", "en-discussion" if started else "idee", progress=progress, default=True,
        )
    try:
        data = _read_yaml(path)
    except GrimoireValidationError as exc:
        return Chantier(name, name, progress=progress, default=default, error=str(exc))
    etat = str(data.get("etat") or "idee")
    if etat not in ETATS:
        return Chantier(name, str(data.get("titre") or name), progress=progress, default=default,
                        error=f"{CHANTIER_FILE} : état inconnu {etat!r}")
    return Chantier(
        name, str(data.get("titre") or name), etat, str(data.get("mission") or ""),
        str(data.get("cree") or ""), str(data.get("modifie") or ""), progress, default,
    )


def has_only_named_chantiers(root: Path) -> bool:
    """Vrai si ``_grimoire/cadrage/`` ne porte aucune phase à sa racine mais au moins un chantier nommé."""
    base = root / CADRAGE_DIR
    if not base.is_dir() or any((base / p.filename).is_file() for p in PHASES):
        return False
    return any(child.is_dir() and (child / CHANTIER_FILE).is_file() for child in base.iterdir())


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_chantiers(root: Path) -> list[Chantier]:
    """Le chantier par défaut s'il est initialisé, puis les chantiers nommés, du plus récemment modifié au plus
    ancien. Un ``chantier.yaml`` illisible donne une entrée en erreur, la liste ne casse pas."""
    base = root / CADRAGE_DIR
    out: list[Chantier] = []
    if status(root)["initialized"] or (base / CHANTIER_FILE).is_file():
        out.append(read_chantier(root))
    if not base.is_dir():
        return out
    named = [c for c in base.iterdir() if c.is_dir() and (c / CHANTIER_FILE).is_file()]
    named.sort(key=lambda c: (-_mtime(c / CHANTIER_FILE), c.name))
    out.extend(read_chantier(root, c.name) for c in named)
    return out


def _write(root: Path, slug: str | None, current: Chantier, **changes: str) -> Chantier:
    directory = cadrage_dir(root, slug)
    data = {
        "slug": current.slug, "titre": current.titre, "etat": current.etat, "mission": current.mission,
        "cree": current.cree or _now(), "modifie": _now(),
    }
    data.update(changes)
    (directory / CHANTIER_FILE).write_text(_dump(data), encoding="utf-8")
    return read_chantier(root, slug)


def new_chantier(root: Path, titre: str, *, slug: str | None = None, mission: str = "") -> Chantier:
    """Crée ``_grimoire/cadrage/<slug>/`` : les cinq gabarits et ``chantier.yaml``. Un slug pris est refusé."""
    titre = titre.strip()
    if not titre:
        raise GrimoireValidationError("un titre est requis")
    slug = _check_slug(slug if slug is not None else slugify(titre))
    directory = cadrage_dir(root, slug)
    if directory.exists():
        raise GrimoireValidationError(f"le chantier {slug!r} existe déjà — choisissez un autre --slug")
    scaffold(root, project_name=titre, chantier=slug)
    now = _now()
    data = {"slug": slug, "titre": titre, "etat": "idee", "mission": mission.strip(), "cree": now, "modifie": now}
    (directory / CHANTIER_FILE).write_text(_dump(data), encoding="utf-8")
    return read_chantier(root, slug)


def _existing(root: Path, slug: str | None) -> Chantier:
    current = read_chantier(root, slug)
    if current.default and not status(root)["initialized"] and not (cadrage_dir(root) / CHANTIER_FILE).is_file():
        raise GrimoireValidationError("cadrage par défaut non initialisé — lancez `grimoire cadrage init`")
    if current.error:
        raise GrimoireValidationError(f"chantier {slug!r} : {current.error}")
    return current


def set_state(root: Path, slug: str, etat: str) -> Chantier:
    """Change l'état d'un chantier si la transition est permise. ``principal`` reçoit alors un ``chantier.yaml``."""
    if etat not in ETATS:
        raise GrimoireValidationError(f"état inconnu : {etat!r} — parmi {', '.join(ETATS)}")
    current = _existing(root, None if slug == DEFAULT_CHANTIER else _check_slug(slug))
    if etat not in reachable_states(current.etat):
        reachable = ", ".join(reachable_states(current.etat)) or "aucun (état final)"
        raise GrimoireValidationError(
            f"transition refusée : {current.etat} → {etat} — états atteignables depuis {current.etat} : {reachable}"
        )
    return _write(root, None if current.default else slug, current, etat=etat)


def link_mission(root: Path, slug: str, mission: str) -> Chantier:
    """Rattache un chantier à une mission du ledger (vide : détache)."""
    current = _existing(root, None if slug == DEFAULT_CHANTIER else _check_slug(slug))
    return _write(root, None if current.default else slug, current, mission=mission.strip())
