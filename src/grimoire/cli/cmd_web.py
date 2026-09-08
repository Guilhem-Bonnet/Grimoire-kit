"""CLI ``grimoire web`` — récupérer une page comme une donnée, pas une consigne.

C'est le point d'entrée que les agents doivent appeler pour atteindre le web.
Il existe parce que le chemin nu ne suffisait pas : ``web-browser.py`` rend son
flux tel quel, et un agent qui colle ce flux dans son contexte ne peut plus
distinguer ce qu'il a lu de ce qu'on lui a demandé. C'est OWASP LLM01, et la
revue adversariale de la PR #324 a montré que l'enveloppe existait sans avoir
un seul appelant : du code mort ne protège personne.

``grimoire web fetch`` exécute le navigateur en sous-processus — le script est
en zone gelée et n'est jamais importé — et rend sa sortie encadrée par deux
marqueurs portant un identifiant tiré au hasard, que la page ne peut pas
connaître. La sortie JSON est elle-même étiquetée ``origin: external`` : le
résultat d'un fetch ne peut pas relayer une approbation.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from grimoire.tools.untrusted import fetch_untrusted, tag_event_payload

web_app = typer.Typer(
    help="Web : récupérer une page comme donnée externe, enveloppée et non exécutable.",
    no_args_is_help=True,
)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option(None, "--project-root", help="Racine du projet.", show_default=False)
_SELECTOR_OPTION = typer.Option("", "--selector", help="Sélecteur CSS à extraire.")
_TIMEOUT_OPTION = typer.Option(60, "--timeout", help="Délai maximal du navigateur, en secondes.")
_JSON_OPTION = typer.Option(False, "--json", help="Sortie JSON (contenu enveloppé + provenance).")


@web_app.command("fetch")
def fetch(
    url: str = typer.Argument(..., help="URL à récupérer."),
    project_root: Path = _PROJECT_ROOT_OPTION,
    selector: str = _SELECTOR_OPTION,
    timeout: int = _TIMEOUT_OPTION,
    as_json: bool = _JSON_OPTION,
) -> None:
    """Récupérer une page et la rendre enveloppée « donnée externe, pas instruction ».

    À utiliser à la place d'un appel direct à ``web-browser.py`` : le script nu
    rend un flux que rien ne distingue d'une consigne une fois dans un contexte.
    """
    root = (project_root or Path()).resolve()
    content = fetch_untrusted(url, project_root=root, timeout=timeout, selector=selector)

    if content.tampering:
        console.print(
            "[yellow]La source a tenté de recopier le marqueur d'enveloppe — "
            "neutralisé, et signalé ici.[/yellow]",
            highlight=False,
        )

    if as_json:
        # Le résultat lui-même porte sa provenance : un agent qui le relit sait
        # qu'il tient de la donnée externe, et `tag_event_payload` refuserait un
        # payload qui prétendrait porter une approbation.
        payload = tag_event_payload({**content.to_dict(), "content": content.render()}, origin="external")
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        typer.echo(content.render())

    if content.exit_code != 0:
        raise typer.Exit(1)
