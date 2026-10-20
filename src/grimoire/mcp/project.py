"""Projet par requête pour le serveur MCP partagé (issue #723).

En stdio, un serveur par session : le projet est le ``cwd`` du processus,
``project_path="."`` suffit, et rien ici n'intervient. En HTTP partagé
(``grimoire mcp start``), un seul processus sert plusieurs projets : le
``cwd`` ne dit plus rien, et le servir quand même écrirait la mémoire ou les
audits d'un projet dans un autre. Chaque requête doit donc porter son projet :

- ``project_path`` absolu, et le dossier existe ;
- sinon la première racine ``file://`` que le client déclare (``roots/list``,
  Claude Code l'envoie) — ``"."`` est cette racine, un chemin relatif s'y joint ;
- sinon refus explicite (``isError``), jamais un projet par défaut.

Aucun état de projet ne survit d'une requête à l'autre : la résolution est une
fonction pure de l'argument et des racines du client, recalculée à chaque appel.

Le transport se lit dans ``GRIMOIRE_MCP_TRANSPORT`` (``stdio`` par défaut,
``http`` posé par ``grimoire mcp serve --http``) : une variable d'environnement
parce que l'enregistrement de la surface (prompts, ressources) se décide à
l'import de ``server.py``, avant tout ``main()``.
"""

from __future__ import annotations

import functools
import inspect
import os
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

TRANSPORT_ENV = "GRIMOIRE_MCP_TRANSPORT"
TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"
PROJECT_ARG = "project_path"
_CTX_KWARG = "grimoire_ctx"

ErrorRenderer = Callable[[dict[str, Any]], Any]


def transport() -> str:
    """``stdio`` ou ``http`` — toute autre valeur vaut ``stdio`` (jamais ``http`` par accident)."""
    raw = os.environ.get(TRANSPORT_ENV, "").strip().lower()
    return TRANSPORT_HTTP if raw == TRANSPORT_HTTP else TRANSPORT_STDIO


def is_shared() -> bool:
    """Vrai quand ce processus sert plusieurs projets (transport HTTP)."""
    return transport() == TRANSPORT_HTTP


class ProjectUnresolvedError(Exception):
    """Aucun projet résolu pour cette requête — l'appel est refusé, pas servi ailleurs."""

    def __init__(self, reason: str, *, raw: str, roots: Sequence[str]) -> None:
        super().__init__(reason)
        self.payload: dict[str, Any] = {
            "error": f"project not resolved: {reason}",
            "code": "mcp.project_unresolved",
            "project_path": raw,
            "client_roots": list(roots),
            "remedy": (
                "the shared MCP server (http) serves several projects: pass an absolute "
                "project_path, or let the client declare its workspace root (roots/list)"
            ),
        }


def root_dir(uri: str) -> Path | None:
    """Le dossier d'une racine MCP (``file://…``) ou d'un chemin nu ; ``None`` sinon."""
    text = str(uri).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme == "file":
        candidate = Path(url2pathname(unquote(parsed.path)))
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            return None
    elif Path(text).is_absolute():
        # Chemin nu, avant tout autre schéma : sous Windows, `urlparse` prend
        # la lettre de lecteur de `C:\…` pour un schéma (vu en CI, #723).
        candidate = Path(text)
    elif parsed.scheme:
        return None
    else:
        candidate = Path(text)
    if not candidate.is_absolute():
        return None
    try:
        return candidate.resolve() if candidate.is_dir() else None
    except OSError:
        return None


def resolve_project_path(raw: str, roots: Sequence[str] = ()) -> Path:
    """Racine absolue du projet d'une requête, ou :class:`ProjectUnresolvedError`.

    Fonction pure : ni ``cwd``, ni cache, ni état entre deux appels.
    """
    text = str(raw or "").strip()
    declared = [d for d in (root_dir(r) for r in roots) if d is not None]
    if text and text != ".":
        asked = Path(text).expanduser()
        if asked.is_absolute():
            if asked.is_dir():
                return asked.resolve()
            raise ProjectUnresolvedError(f"{text} is not a directory", raw=raw, roots=roots)
        if not declared:
            raise ProjectUnresolvedError(
                f"relative project_path {text!r} and the client declared no workspace root",
                raw=raw, roots=roots,
            )
        joined = (declared[0] / asked).resolve()
        if joined.is_dir():
            return joined
        raise ProjectUnresolvedError(f"{joined} is not a directory", raw=raw, roots=roots)
    if declared:
        return declared[0]
    if roots:
        raise ProjectUnresolvedError("none of the client roots is a local directory", raw=raw, roots=roots)
    raise ProjectUnresolvedError("project_path is relative and the client declared no workspace root", raw=raw, roots=roots)


async def client_roots(ctx: Any) -> list[str]:
    """Les racines que le client déclare (``roots/list``) ; vide s'il n'en a pas ou ne répond pas."""
    if ctx is None:
        return []
    try:
        session = ctx.session
        # La révision 2026-07-28 déprécie Roots (SEP-2577, retrait possible à
        # partir de 2027-07-28) : ce repli est une commodité de transition pour
        # les hôtes qui ne passent pas `project_path` ; le chemin durable est
        # l'argument absolu. Le SDK 2.x le signale à chaque appel, on le tait ici.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await session.list_roots()
    except Exception:  # un client sans capacité roots répond par une erreur : ce n'est pas une panne
        return []
    return [str(root.uri) for root in getattr(result, "roots", ()) or ()]


def _context_class() -> type[Any]:
    # Une seule des deux façades existe dans un environnement donné (voir
    # server.py) : l'analyse statique ne peut résoudre qu'un import sur deux.
    try:  # mcp >= 2
        from mcp.server.mcpserver import Context  # type: ignore[import-not-found,unused-ignore]
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import Context  # type: ignore[import-not-found,attr-defined,unused-ignore,no-redef]
    return cast(type[Any], Context)


def project_scoped(fn: Callable[..., Any], *, error: ErrorRenderer) -> Callable[..., Any]:
    """L'enveloppe enregistrée auprès du SDK à la place de l'outil *fn*.

    Même nom, même docstring, même schéma d'entrée (le paramètre de contexte
    est injecté par le SDK et exclu du schéma). En stdio elle appelle *fn*
    tel quel ; en HTTP elle résout d'abord le projet de la requête et refuse
    (``isError``) quand il n'y en a pas. *fn* lui-même reste intact — la suite
    de tests l'appelle en direct, comme avant.
    """
    context_cls = _context_class()
    signature = inspect.signature(fn, eval_str=True)
    ctx_param = inspect.Parameter(_CTX_KWARG, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=context_cls)

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.pop(_CTX_KWARG, None)
        if is_shared():
            raw = str(kwargs.get(PROJECT_ARG, "."))
            needs_roots = not (raw.strip() and raw.strip() != "." and Path(raw.strip()).expanduser().is_absolute())
            roots = await client_roots(ctx) if needs_roots else []
            try:
                kwargs[PROJECT_ARG] = str(resolve_project_path(raw, roots))
            except ProjectUnresolvedError as exc:
                return error(exc.payload)
        return fn(*args, **kwargs)

    wrapper.__signature__ = signature.replace(parameters=[*signature.parameters.values(), ctx_param])  # type: ignore[attr-defined]
    wrapper.__annotations__ = {**dict(getattr(fn, "__annotations__", {})), _CTX_KWARG: context_cls}
    return wrapper
