"""Actions mémoire du cockpit : liste blanche, mutations, arguments sûrs.

Sorti de ``cmd_cockpit`` pour tenir le cliquet de taille du module. Les
lectures sont sans effet ; chaque mutation correspond à une commande réelle de
``grimoire memory`` et n'est exécutée qu'avec ``confirm: true``. Les valeurs
positionnelles passent après ``--`` et sont refusées si elles ressemblent à une
option — la garde de la route vit dans ``cmd_cockpit``, pas ici.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_ACTIONS: dict[str, list[str]] = {
    "status": [],
    "gate": ["--soft"],
    "search": [],  # requires a query argument
    "list": [],
    "taxonomy": [],
}


@dataclass(frozen=True)
class Mutation:
    """A governed write action — runs only with explicit confirmation."""

    args: tuple[str, ...] = ()
    needs_id: bool = False
    subcommand: str | None = None  # defaults to the action name


# Each mutation maps to a real ``grimoire memory`` command and needs ``confirm: true``.
MUTATION_ACTIONS: dict[str, Mutation] = {
    "gc": Mutation(),  # consolidate / compact the store
    "delete": Mutation(args=("--yes",), needs_id=True),  # remove one entry by id
    # resync the Weaviate / Neo4j projections from the source store (reindex, no loss)
    "sync": Mutation(subcommand="gate", args=("--sync", "--soft")),
}

# Longueur d'argument bornée : une requête n'a pas à pousser un roman dans argv.
MAX_ARGUMENT_LEN = 512

MEMORY_TIMEOUT_S = 30


def is_plain_argument(value: str) -> bool:
    """Vrai si la valeur peut être passée à la CLI comme simple positionnel.

    Le dispatch n'utilise pas de shell, donc il n'y a pas d'injection de shell
    possible — mais une valeur commençant par ``-`` serait lue comme une option
    par la sous-commande. On refuse ce cas, les octets nuls et les longueurs
    déraisonnables, et on passe malgré tout les valeurs après ``--``.
    """
    return bool(value) and not value.startswith("-") and "\x00" not in value and len(value) <= MAX_ARGUMENT_LEN
