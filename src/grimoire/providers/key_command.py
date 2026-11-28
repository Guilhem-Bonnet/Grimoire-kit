"""Le programme qu'une commande d'invocation lancerait, résolu et empreint (liaison d'une clé à un sous-processus).

La clé du magasin n'est remise qu'à un programme que la saisie a résolu et vu : son chemin absolu et le contenu de
son fichier (et, derrière un interpréteur connu, ceux du script lancé) sont liés. Règle simple et sûre : un programme
ou un script situé sous la racine du projet ne reçoit JAMAIS une clé, puisque le dépôt peut le réécrire. Un nom nu est
résolu par ``shutil.which`` au moment de la saisie, puis de nouveau à l'usage : un autre binaire du même nom, plus tôt
dans le ``PATH``, a une autre empreinte. Rien ici ne lit une valeur de clé.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
from pathlib import Path

from grimoire.providers.secrets import KeyEndpointError, provider_key_var

#: Interpréteurs dont le premier argument non option est un script (lié, lui aussi).
_INTERPRETER_RE = re.compile(r"(python[0-9.]*|pypy[0-9.]*|sh|bash|dash|zsh|ksh|node|nodejs|deno|bun|ruby|perl|php)(\.exe)?", re.IGNORECASE)

_UNDER_ROOT = (
    "la commande d'invocation de ce fournisseur lance un programme ou un script situé sous la racine du projet : le "
    "dépôt peut le réécrire, une clé du magasin ne lui est jamais remise ; place-le hors du projet puis saisis la clé "
    "(`grimoire providers set-key {provider} {var}`)."
)
_UNRESOLVED = (
    "la commande d'invocation de ce fournisseur ne désigne pas un programme que le magasin peut résoudre "
    "(introuvable, illisible ou mal écrite) : aucune clé ne lui est remise ; corrige-la puis saisis la clé "
    "(`grimoire providers set-key {provider} {var}`)."
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(token: str, root: Path) -> Path | None:
    """Le fichier réel que *token* désigne (liens suivis) ; ``None`` s'il n'existe pas. Relatif : depuis *root*."""
    if os.sep in token or (os.altsep and os.altsep in token):
        path = Path(token)
        found: str | None = str(path if path.is_absolute() else root / path)
    else:
        found = shutil.which(token)
    if not found:
        return None
    real = Path(found).resolve()
    return real if real.is_file() else None


def resolve_program(template: str, root: Path, provider_id: str) -> tuple[Path, ...]:
    """Le programme (et le script d'un interpréteur) que *template* lancerait, chemins réels.

    :class:`KeyEndpointError` si la commande est illisible, introuvable, ou lance quelque chose sous *root*.
    """
    names = {"provider": provider_id, "var": provider_key_var(provider_id)}
    try:
        tokens = shlex.split(template)
    except ValueError:
        tokens = []
    program = _resolve(tokens[0], root) if tokens else None
    if program is None:
        raise KeyEndpointError(_UNRESOLVED.format(**names))
    found = [program]
    if _INTERPRETER_RE.fullmatch(program.name) or _INTERPRETER_RE.fullmatch(Path(tokens[0]).name):
        script = None
        for arg in tokens[1:]:
            if arg in ("-m", "-c"):  # `-m` importe depuis le répertoire de travail (le projet) ; `-c` n'a pas de script
                if arg == "-m":
                    raise KeyEndpointError(_UNDER_ROOT.format(**names))
                break
            if not arg.startswith("-"):
                script = _resolve(arg, root)
                if script is None:
                    raise KeyEndpointError(_UNRESOLVED.format(**names))
                found.append(script)
                break
    base = root.resolve()
    if any(path == base or base in path.parents for path in found):
        raise KeyEndpointError(_UNDER_ROOT.format(**names))
    return tuple(found)


def command_fingerprint(template: str, root: Path, provider_id: str) -> str:
    """L'empreinte des programmes résolus (chemins et contenus) de *template* ; refus : voir :func:`resolve_program`."""
    parts = [f"{path}\0{_sha256(path)}" for path in resolve_program(template, root, provider_id)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
