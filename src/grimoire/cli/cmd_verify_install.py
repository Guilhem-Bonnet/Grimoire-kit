"""CLI ``grimoire verify-install`` — l'installation courante est-elle celle que la release a publiée ?

Hors ligne : les fichiers installés correspondent à leur ``RECORD``.
Avec ``--online`` : la wheel de la même version est retéléchargée, vérifiée
contre PyPI et le ``SHA256SUMS`` de la release GitHub, puis son ``RECORD`` est
comparé à celui de l'installation. Code de sortie 1 au moindre écart.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from grimoire.tools import install_trust as trust

console = Console()

_ONLINE_OPTION = typer.Option(
    "--online", help="Comparer aussi à la wheel publiée (PyPI et SHA256SUMS de la release).",
)
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")


def _verify_online(version: str) -> trust.Verdict:
    """Chaîne complète pour *version* ; :class:`trust.TrustError` si un maillon casse."""
    opener = urllib.request.urlopen
    try:
        with opener(f"https://pypi.org/pypi/{trust.DIST_NAME}/{version}/json", timeout=10) as resp:
            pypi_data: dict[str, Any] = json.loads(resp.read())
    except Exception as exc:
        raise trust.TrustError(f"PyPI injoignable ({type(exc).__name__})") from None
    ref = trust.select_wheel(pypi_data, version)
    with tempfile.TemporaryDirectory(prefix="grimoire-verify-") as scratch:
        wheel = trust.download_wheel(ref, Path(scratch), opener)
        sums = trust.fetch_sums(version, opener)
        published = trust.check_wheel(wheel, version=version, pypi_sha256=ref.sha256, sums=sums)
        gaps = trust.same_as_wheel(wheel)
    if gaps:
        return trust.Verdict("modified", "l'installation diffère de la wheel publiée", gaps[:20])
    return trust.Verdict(published.level, f"installation identique à la wheel publiée ; {published.detail}")


def verify_install_command(
    online: Annotated[bool, _ONLINE_OPTION] = False,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Vérifie l'installation de grimoire-kit (empreintes locales, puis release avec --online)."""
    dist = trust.installed_distribution()
    verdict = trust.local_trust(dist)
    if online and dist is not None and verdict.ok and verdict.level != "dev":
        try:
            verdict = _verify_online(dist.version)
        except trust.TrustError as exc:
            verdict = trust.Verdict("modified", f"vérification en ligne refusée : {exc}")
    if json_output:
        typer.echo(json.dumps(
            {"trust_level": verdict.level, "detail": verdict.detail, "problems": list(verdict.problems)},
            indent=2, ensure_ascii=False,
        ))
    else:
        style = {"verified": "green", "pypi-only": "yellow", "intact": "yellow"}.get(verdict.level, "red")
        if verdict.level in ("dev", "unknown"):
            style = "dim"
        console.print(f"[{style}]trust_level: {verdict.level}[/{style}] — {escape(verdict.detail)}")
        for problem in verdict.problems:
            console.print(f"  [red]{escape(problem)}[/red]")
    if not verdict.ok:
        raise typer.Exit(1)
