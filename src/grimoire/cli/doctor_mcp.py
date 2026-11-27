"""Contrôles ``doctor`` du serveur MCP partagé (issue #723).

Séparés de ``cmd_up.py`` pour ne pas le faire grossir au-delà du cliquet :
une entrée ``type: http`` de ``.mcp.json`` est-elle servie, et combien de
serveurs stdio tournent sur le poste.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from grimoire.cli.env_checks import EnvCheck
from grimoire.core.safe_sources import Exclusion, describe_exclusions, exclusion_lines


def check_mcp_spec_http(server_name: str, spec: dict[str, Any]) -> EnvCheck | None:
    """Le contrôle d'une entrée http pure (``url`` sans ``command``), sinon ``None``."""
    url = str(spec.get("url", "")).strip()
    if url and not str(spec.get("command", "")).strip():
        return check_mcp_http_entry(server_name, url)
    return None


_SCHEME_PORTS = {"http": 80, "https": 443}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def check_mcp_http_entry(server_name: str, url: str) -> EnvCheck:
    """Une entrée ``type: http`` : le serveur répond-il ?

    Sans port explicite, le port est celui du schéma (80, 443) : un serveur
    tiers en https n'en écrit jamais. Injoignable n'est pas une panne : l'hôte
    échoue cette entrée et garde les autres (l'entrée stdio ``grimoire`` reste
    le défaut). WARN, pas FAIL. Seule une URL inutilisable est un FAIL.
    """
    try:
        parsed = urlparse(url)
        port = parsed.port or _SCHEME_PORTS.get(parsed.scheme)
    except ValueError:
        parsed, port = None, None
    if parsed is None or port is None or parsed.scheme not in _SCHEME_PORTS or not parsed.hostname:
        return EnvCheck(
            f"mcp_{server_name}",
            passed=False,
            level="fail",
            detail=f".mcp.json server '{server_name}': invalid url '{url}' (expected http(s)://host[:port]/path)",
            remedy="fix the url in .mcp.json, or grimoire mcp register . --url http://127.0.0.1:7331/mcp",
            optional=False,
        )
    from grimoire.cli.cmd_up import _tcp_reachable  # résolu à l'appel : simulable sur cmd_up

    if _tcp_reachable(url, port):
        return EnvCheck(f"mcp_{server_name}", passed=True, level="ok", detail=f".mcp.json server '{server_name}' answers at {url}")
    local = parsed.hostname in _LOCAL_HOSTS
    return EnvCheck(
        f"mcp_{server_name}",
        passed=True,
        level="warn",
        detail=f".mcp.json server '{server_name}' (http) is not reachable at {url}",
        remedy=(
            "grimoire mcp start (shared MCP server), or grimoire mcp register . --remove"
            if local
            else "check the network or the server url; the host skips this entry until it answers"
        ),
    )


def check_mcp_skills(target: Path) -> EnvCheck:
    """Combien de skills le serveur MCP sert par l'extension officielle, et lesquels il écarte.

    Information seulement, jamais bloquante : un skill écarté reste émis en
    fichier pour les hôtes qui ne lisent pas l'extension. Même catalogue que le
    serveur (:func:`grimoire.mcp.skills_catalogue.build_catalogue`), sans le SDK.
    """
    from grimoire.mcp.skills_catalogue import build_catalogue

    try:
        catalogue = build_catalogue(target)
    except Exception as exc:  # une sonde de doctor ne lève jamais
        return EnvCheck("mcp_skills", passed=True, level="info", detail=f"skills servis par MCP : non mesuré ({exc.__class__.__name__})")
    detail = f"skills servis par MCP : {len(catalogue.served)} (extension io.modelcontextprotocol/skills)"
    if catalogue.served:
        kit = sum(1 for s in catalogue.served if s.origin == "kit")
        detail += f" ; origine : kit {kit}, projet {len(catalogue.served) - kit}"
    if catalogue.excluded:
        # Borné : un projet hostile peut produire des centaines d'exclusions (10 lignes, puis « … et N autres »).
        skipped = ", ".join(exclusion_lines((e.slug, e.reason) for e in catalogue.excluded))
        detail += f" ; non servis : {skipped}"
    return EnvCheck("mcp_skills", passed=True, level="info", detail=detail)


def check_project_sources(target: Path) -> list[EnvCheck]:
    """Les skills, commandes et agents du projet que la collecte a ignorés, et pourquoi.

    Même lecture que ``grimoire host sync`` (:mod:`grimoire.hosts.collect`) : un fichier illisible
    (lien, tube, trop gros, pas de l'UTF-8…) est exclu *seul* et dit ici, nom et raison assainis, au plus
    10 lignes. Rien à dire, rien d'affiché. Jamais bloquant, jamais une exception.
    """
    from grimoire.hosts import collect

    excluded: list[Exclusion] = []
    try:
        skills = collect.collect_skills(target, excluded=excluded)
        collect.collect_commands(target, excluded=excluded)
        collect.collect_agents(target, known_skills=frozenset(s.slug for s in skills), excluded=excluded)
    except Exception:  # noqa: S110 - un agent invalide est le sujet des autres contrôles ; les exclusions déjà vues restent dites
        pass
    header, lines = describe_exclusions(excluded)
    if not header:
        return []
    detail = f"{header} {', '.join(lines)}"
    return [EnvCheck("project_sources", passed=True, level="warn", detail=detail)]


def check_mcp_processes() -> EnvCheck:
    """Compte les serveurs MCP stdio vivants sur le poste (issue #723).

    Un par session d'hôte : au-delà de ``STDIO_WARN_THRESHOLD``, la RSS cumulée
    justifie le démon partagé. Sans ``/proc`` : « non mesuré », jamais deviné.
    """
    from grimoire.tools.mcp_processes import STDIO_WARN_THRESHOLD, scan, stdio_servers, total_rss_kb

    processes = scan()
    if processes is None:
        return EnvCheck("mcp_processes", passed=True, level="info", detail="grimoire-mcp processes: not measured (no /proc)")
    stdio = stdio_servers(processes)
    shared = [p for p in processes if p.shared]
    mb = total_rss_kb(stdio) / 1024
    detail = f"{len(stdio)} grimoire-mcp stdio process(es) alive, {mb:.0f} MB RSS"
    if shared:
        detail += f"; shared http server running (pid {shared[0].pid})"
    if len(stdio) > STDIO_WARN_THRESHOLD:
        return EnvCheck(
            "mcp_processes",
            passed=True,
            level="warn",
            detail=detail + f" — more than {STDIO_WARN_THRESHOLD} sessions each run their own server",
            remedy="grimoire mcp start, then grimoire mcp register <project> (one shared http server, project per request)",
        )
    return EnvCheck("mcp_processes", passed=True, level="ok" if stdio else "info", detail=detail)
