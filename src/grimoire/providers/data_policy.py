"""La classe de données d'une tâche décide des fournisseurs qui peuvent la recevoir (#1133).

Échec fermé : sans classe déclarée, ou sans classe admise par le fournisseur, pas de route. Seul un fournisseur
``provider_type: local`` y échappe, rien n'y sortant de la machine.

Une classe reste une déclaration : elle n'empêche pas un worker routé de lire un fichier avec Bash. Une vraie barrière
demande un environnement sans les données (voir ``docs/reference/cli/fournisseurs.md``).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.providers.registry import ProviderSpec, read_default_data_class


def declared_data_class(root: Path, task_class: str) -> tuple[str, str]:
    """``(classe, origine)`` retenues pour une tâche : la sienne, à défaut celle du projet, à défaut ``("", "")``."""
    if task_class.strip():
        return task_class.strip(), "tâche"
    default = read_default_data_class(root)
    return (default, "projet") if default else ("", "")


def split_by_data_policy(
    root: Path, providers: tuple[ProviderSpec, ...], task_class: str
) -> tuple[tuple[ProviderSpec, ...], dict[str, str]]:
    """Les fournisseurs admis pour cette tâche, puis le motif de chaque fournisseur écarté."""
    motifs = {provider.id: data_policy_refusal(root, provider, task_class) for provider in providers}
    return tuple(p for p in providers if not motifs[p.id]), {pid: why for pid, why in motifs.items() if why}


def data_policy_refusal(root: Path, provider: ProviderSpec, task_class: str) -> str:
    """Le motif qui ferme *provider* à cette tâche, vide s'il peut la recevoir."""
    if provider.provider_type == "local":
        return ""
    data_class, _ = declared_data_class(root, task_class)
    if not data_class:
        return f"{provider.id} : aucune classe de données déclarée, ni par la tâche, ni par routing.default_data_class"
    if data_class in provider.forbidden_data_classes:
        return f"{provider.id} : la classe {data_class!r} est interdite par sa data_policy"
    if data_class not in provider.allowed_data_classes:
        return f"{provider.id} : la classe {data_class!r} n'est pas admise par sa data_policy"
    return ""
