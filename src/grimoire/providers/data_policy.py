"""La classe de données d'une tâche décide des fournisseurs qui peuvent la recevoir (#1133).

Échec fermé : sans classe déclarée, ou sans classe admise par le fournisseur, pas de route. Seul un fournisseur
``provider_type: local`` y échappe, rien n'y sortant de la machine. Le repli du worker sur le modèle de l'hôte est
un appel hébergé comme un autre : :func:`hosted_fallback_refusal` le ferme quand la classe déclarée n'est admise
par aucun fournisseur hébergé activé.

Une classe reste une déclaration : elle n'empêche pas un worker routé de lire un fichier avec Bash. Une vraie barrière
demande un environnement sans les données (voir ``docs/reference/cli/fournisseurs.md``).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.providers.registry import ProviderSpec, read_default_data_class, read_registry


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


def hosted_fallback_refusal(root: Path, task_class: str) -> str:
    """Le motif qui ferme le repli sur le modèle de l'hôte ; vide s'il reste permis.

    Le modèle de l'hôte est un modèle hébergé : quand la classe déclarée (tâche, à défaut projet)
    n'est admise par **aucun** fournisseur ``hosted`` activé, y retomber ferait sortir des données
    que la politique refuse ailleurs. La tâche attend alors un fournisseur admis au lieu de partir
    en silence ; toute classe admise par un fournisseur hébergé laisse le repli ouvert. Sans classe
    déclarée, rien ne change (#1133) : un projet qui ne règle rien garde son comportement.
    """
    data_class, origin = declared_data_class(root, task_class)
    if not data_class:
        return ""
    hosted = tuple(p for p in read_registry(root) if p.enabled and p.provider_type != "local")
    if any(not data_policy_refusal(root, provider, task_class) for provider in hosted):
        return ""
    return (
        f"la classe {data_class!r} (déclarée par : {origin}) n'est admise par aucun fournisseur hébergé : "
        "le repli sur le modèle de l'hôte est refusé"
    )
