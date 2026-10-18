"""Ce que ``uv`` et ``pipx`` retiennent de l'installation, avant et après ``grimoire self update``.

``self update`` réinstalle depuis la wheel vérifiée (``<outil> install --force <wheel>``).
Deux conséquences que l'outil ne gère pas seul, observées sur uv 0.13.1 et pipx 1.15.0 :

* **Avant** : ``uv tool install --force`` repart du seul spécifieur donné. Les
  ``--with`` (et ``--python``) de l'installation d'origine, consignés dans
  ``uv-receipt.toml``, sont perdus ; ``uv tool upgrade`` les gardait. On les relit
  et on les rejoue ; une option qu'on ne sait pas rejouer (contraintes,
  surcharges, index...) refuse la mise à jour plutôt que de la perdre en silence.
  pipx, lui, garde les paquets ``inject``-és quand il réinstalle dans le venv existant.
* **Après** : le reçu pointe alors vers la wheel du dossier temporaire, supprimé à
  la fin de la commande. ``uv tool upgrade`` / ``pipx upgrade`` échouent ensuite
  (« Distribution not found »), et même avec un fichier durable ils ne verraient
  jamais une version plus récente. On retire le chemin du reçu : il redevient une
  installation du registre, à l'identique de ce qu'il serait sans ``self update``.

Les deux fichiers (``uv-receipt.toml``, ``pipx_metadata.json``) sont le format interne
de l'outil, pas une interface publique. La réécriture est donc chirurgicale (une seule
occurrence retirée), vérifiée par une relecture qui doit donner exactement le contenu
attendu, atomique (``fsio.atomic_write``), et ne touche à rien dans le moindre doute : la
raison est alors rendue à l'appelant, qui affiche la commande de réparation.
"""

from __future__ import annotations

import json
import re
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from grimoire.core.fsio import atomic_write

DIST = "grimoire-kit"
_UV_REQ_KEYS = frozenset({"name", "specifier", "extras", "marker"})
_UV_TOOL_KEYS = frozenset({"requirements", "python", "entrypoints"})
_PIPX_FILE_SPEC = re.compile(r"^(grimoire-kit(?:\[[^\]]*\])?)\s*@\s*(file:\S+)$")


class UnsupportedReceiptError(Exception):
    """Le reçu contient des options que la réinstallation ne sait pas reproduire."""


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _same_file(left: str, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except (OSError, ValueError):
        return False


# ── uv ────────────────────────────────────────────────────────────────────────


def uv_receipt_path(tool_dir: Path) -> Path:
    return tool_dir / DIST / "uv-receipt.toml"


def _render_requirement(entry: Any) -> str:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        raise UnsupportedReceiptError("une exigence du reçu uv est illisible")
    unknown = sorted(set(entry) - _UV_REQ_KEYS)
    if unknown:
        raise UnsupportedReceiptError(
            f"l'exigence « {entry['name']} » du reçu uv porte {', '.join(unknown)}, que la réinstallation ne sait pas rejouer"
        )
    extras, specifier, marker = entry.get("extras"), entry.get("specifier", ""), entry.get("marker")
    if not (extras is None or (isinstance(extras, list) and all(isinstance(e, str) for e in extras))) \
            or not isinstance(specifier, str) or not (marker is None or isinstance(marker, str)):
        raise UnsupportedReceiptError(f"l'exigence « {entry['name']} » du reçu uv est illisible")
    text = entry["name"] + (f"[{','.join(extras)}]" if extras else "") + specifier
    return f"{text}; {marker}" if marker else text


def uv_carried_options(receipt: Path) -> list[str]:
    """Options ``uv tool install`` qui reproduisent l'installation d'origine, hors grimoire-kit.

    ``[]`` sans reçu. Lève :class:`UnsupportedReceiptError` pour tout ce qu'on ne sait pas rejouer.
    """
    if not receipt.is_file():
        return []
    try:
        tool = tomllib.loads(receipt.read_text(encoding="utf-8")).get("tool", {})
    except (OSError, ValueError):
        raise UnsupportedReceiptError("le reçu uv est illisible") from None
    if not isinstance(tool, dict):
        raise UnsupportedReceiptError("le reçu uv est illisible")
    unknown = sorted(set(tool) - _UV_TOOL_KEYS)
    if unknown:
        raise UnsupportedReceiptError(
            f"l'installation uv porte {', '.join(unknown)}, que la réinstallation ne sait pas rejouer"
        )
    options: list[str] = []
    python = tool.get("python")
    if python is not None:
        if not isinstance(python, str):
            raise UnsupportedReceiptError("le reçu uv est illisible (python)")
        options.append(f"--python={python}")
    requirements = tool.get("requirements", [])
    if not isinstance(requirements, list):
        raise UnsupportedReceiptError("le reçu uv est illisible (requirements)")
    for entry in requirements:
        if isinstance(entry, dict) and _norm(str(entry.get("name", ""))) == DIST:
            continue  # remplacé par la wheel vérifiée
        options.append(f"--with={_render_requirement(entry)}")
    return options


def pin_uv_receipt(receipt: Path, wheel: Path) -> str | None:
    """Retire du reçu le chemin de *wheel*. ``None`` : fait ou sans objet ; sinon la raison de l'échec."""
    if not receipt.is_file():
        return None
    try:
        text = receipt.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except (OSError, ValueError):
        return "reçu uv illisible"
    requirements = data.get("tool", {}).get("requirements") if isinstance(data.get("tool"), dict) else None
    if not isinstance(requirements, list):
        return "reçu uv sans liste d'exigences"
    index = next(
        (i for i, e in enumerate(requirements)
         if isinstance(e, dict) and _norm(str(e.get("name", ""))) == DIST and "path" in e),
        None,
    )
    if index is None:
        return None
    raw = requirements[index]["path"]
    if not isinstance(raw, str) or not _same_file(raw, wheel):
        return "le reçu uv pointe vers un autre fichier que la wheel installée"
    expected = deepcopy(data)
    del expected["tool"]["requirements"][index]["path"]
    for literal in (json.dumps(raw, ensure_ascii=False), f"'{raw}'"):
        for needle in (f", path = {literal}", f"path = {literal}, "):
            if text.count(needle) != 1:
                continue
            rewritten = text.replace(needle, "", 1)
            try:
                if tomllib.loads(rewritten) != expected:
                    continue
                atomic_write(receipt, rewritten)
            except (ValueError, OSError):
                return "reçu uv : réécriture impossible"
            return None
    return "forme du reçu uv non reconnue"


# ── pipx ──────────────────────────────────────────────────────────────────────


def pipx_metadata_path(venvs_dir: Path) -> Path:
    return venvs_dir / DIST / "pipx_metadata.json"


def pin_pipx_metadata(metadata_file: Path, wheel: Path) -> str | None:
    """Remplace ``grimoire-kit[extras] @ file://...`` par ``grimoire-kit[extras]`` dans la métadonnée pipx."""
    if not metadata_file.is_file():
        return None
    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "métadonnée pipx illisible"
    main: Any = data.get("main_package") if isinstance(data, dict) else None
    spec = main.get("package_or_url") if isinstance(main, dict) else None
    if not isinstance(main, dict) or not isinstance(spec, str):
        return "métadonnée pipx sans paquet principal"
    match = _PIPX_FILE_SPEC.match(spec.strip())
    if match is None:
        return None
    if not _same_file(unquote(urlparse(match.group(2)).path), wheel):
        return "la métadonnée pipx pointe vers un autre fichier que la wheel installée"
    main["package_or_url"] = match.group(1)
    try:
        atomic_write(metadata_file, json.dumps(data, indent=4))
    except OSError:
        return "métadonnée pipx : réécriture impossible"
    return None
