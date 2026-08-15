"""Diff structurel entre deux versions d'un blueprint (P3.4).

L'invariant du Studio — « se valide, se simule, se compile, n'exécute jamais » —
repose sur une promesse de revue : *le diff git est la revue*. Encore faut-il
que ce diff soit lisible. Comparer deux `.blueprint.json` ligne à ligne montre
des accolades et des coordonnées ; ça ne dit pas qu'une porte a disparu du
chemin de déploiement.

Ce module produit le diff au niveau où la décision se prend : des nodes et des
liens ajoutés, retirés, modifiés — pas des lignes de JSON.

**Un déplacement n'est pas un changement.** Ranger le graphe modifie chaque
coordonnée sans rien changer au sens. Les champs de mise en page sont donc
exclus de la comparaison : sinon un simple auto-layout ferait apparaître le
blueprint entier comme modifié, et la revue redeviendrait illisible — le
problème même que cette tranche corrige.

Identité retenue :

- **node** : son `id`, stable d'une édition à l'autre (le Studio le conserve) ;
- **edge** : le quadruplet `(from, to, contract, channel)`, faute d'identifiant
  au schéma — deux liens identiques entre les mêmes prises sont indiscernables,
  et c'est correct : ils le sont aussi pour le lecteur.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

__all__ = ["diff_against_ref", "diff_blueprints", "edge_key", "read_at_ref", "summarize"]

#: Champs qui ne portent que de la mise en page. Les ignorer est ce qui rend le
#: diff lisible après un « ranger le graphe ».
LAYOUT_FIELDS = frozenset({"x", "y", "w", "h", "width", "height", "color", "_w", "_h", "_pinTop"})


def _as_dict(value: Any) -> dict[str, Any]:
    """`value` si c'est un dict, sinon {} — narrowing pour mypy strict."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """`value` si c'est une liste, sinon [] — narrowing pour mypy strict."""
    return value if isinstance(value, list) else []


def _strip_layout(node: dict[str, Any]) -> dict[str, Any]:
    """Le node privé de sa mise en page et de son sous-graphe.

    Le sous-graphe est retiré parce qu'il est parcouru récursivement : le
    compter ici ferait apparaître un groupe comme « modifié » dès qu'un de ses
    enfants bouge, ce qui masquerait le vrai changement au lieu de le montrer.
    """
    return {k: v for k, v in node.items() if k not in LAYOUT_FIELDS and k != "sub"}


def edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    """Identité d'un lien : ses deux extrémités, son contrat, son canal."""
    return (
        str(edge.get("from", "")),
        str(edge.get("to", "")),
        str(edge.get("contract", "")),
        str(edge.get("channel", "happy")),
    )


def _node_label(node: dict[str, Any]) -> str:
    """De quoi nommer un node dans un rapport, sans dépendre du catalogue."""
    for field in ("name", "label", "ref"):
        value = node.get(field)
        if isinstance(value, str) and value:
            return value
    kind = node.get("kind")
    return str(kind) if isinstance(kind, str) and kind else str(node.get("id", "?"))


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Champs porteurs de sens dont la valeur diffère, triés."""
    a, b = _strip_layout(before), _strip_layout(after)
    return sorted({k for k in set(a) | set(b) if a.get(k) != b.get(k)})


def _walk(
    before: dict[str, Any],
    after: dict[str, Any],
    path: tuple[str, ...],
    acc: dict[str, list[dict[str, Any]]],
) -> None:
    """Compare un niveau, puis descend dans les sous-flows communs."""
    b_nodes = {str(n.get("id")): n for n in _as_list(before.get("nodes")) if _as_dict(n)}
    a_nodes = {str(n.get("id")): n for n in _as_list(after.get("nodes")) if _as_dict(n)}

    for nid in a_nodes.keys() - b_nodes.keys():
        acc["nodes_added"].append({"id": nid, "label": _node_label(a_nodes[nid]), "path": list(path)})
    for nid in b_nodes.keys() - a_nodes.keys():
        acc["nodes_removed"].append({"id": nid, "label": _node_label(b_nodes[nid]), "path": list(path)})
    for nid in a_nodes.keys() & b_nodes.keys():
        fields = _changed_fields(b_nodes[nid], a_nodes[nid])
        if fields:
            acc["nodes_changed"].append(
                {"id": nid, "label": _node_label(a_nodes[nid]), "fields": fields, "path": list(path)}
            )

    b_edges = {edge_key(e): e for e in _as_list(before.get("edges")) if _as_dict(e)}
    a_edges = {edge_key(e): e for e in _as_list(after.get("edges")) if _as_dict(e)}
    for key in a_edges.keys() - b_edges.keys():
        acc["edges_added"].append({"from": key[0], "to": key[1], "contract": key[2],
                                   "channel": key[3], "path": list(path)})
    for key in b_edges.keys() - a_edges.keys():
        acc["edges_removed"].append({"from": key[0], "to": key[1], "contract": key[2],
                                     "channel": key[3], "path": list(path)})

    # Sous-flows présents des deux côtés : le contenu se compare au bon niveau.
    for nid in a_nodes.keys() & b_nodes.keys():
        b_sub, a_sub = _as_dict(b_nodes[nid].get("sub")), _as_dict(a_nodes[nid].get("sub"))
        if b_sub or a_sub:
            _walk(b_sub, a_sub, (*path, nid), acc)


def summarize(diff: dict[str, Any]) -> str:
    """Une ligne lisible : ce qu'un relecteur lit avant d'ouvrir le détail."""
    parts = []
    for singulier, pluriel, key in (
        ("node ajouté", "nodes ajoutés", "nodes_added"),
        ("node retiré", "nodes retirés", "nodes_removed"),
        ("node modifié", "nodes modifiés", "nodes_changed"),
        ("lien ajouté", "liens ajoutés", "edges_added"),
        ("lien retiré", "liens retirés", "edges_removed"),
    ):
        count = len(_as_list(diff.get(key)))
        if count:
            parts.append(f"{count} {pluriel if count > 1 else singulier}")
    return " · ".join(parts) if parts else "aucun changement de structure"


def diff_blueprints(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff structurel de `before` vers `after`, sous-flows compris.

    `changed` vaut faux quand seules des coordonnées ont bougé : c'est le
    signal qu'une relecture n'a rien à examiner.
    """
    acc: dict[str, list[dict[str, Any]]] = {
        "nodes_added": [], "nodes_removed": [], "nodes_changed": [],
        "edges_added": [], "edges_removed": [],
    }
    _walk(_as_dict(before), _as_dict(after), (), acc)
    result: dict[str, Any] = dict(acc)
    result["changed"] = any(acc[k] for k in acc)
    result["summary"] = summarize(result)
    return result

def read_at_ref(root: Path, relpath: Path, ref: str = "HEAD") -> dict[str, Any] | None:
    """Le blueprint tel qu'il était à `ref`, ou None s'il n'y était pas.

    None n'est pas une erreur : c'est le cas d'un blueprint jamais commité,
    que l'appelant doit distinguer d'un blueprint inchangé.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{relpath.as_posix()}"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        parsed = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def diff_against_ref(root: Path, path: Path, ref: str = "HEAD") -> dict[str, Any]:
    """Diff d'un blueprint entre `ref` et son état courant sur le disque."""
    try:
        relpath = path.relative_to(root)
    except ValueError:
        relpath = path
    before = read_at_ref(root, relpath, ref)
    after: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            after = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            after = {}
    result = diff_blueprints(before or {}, after)
    result["ref"] = ref
    # Un blueprint absent de `ref` n'a pas « tout ajouté » : il est neuf.
    result["tracked"] = before is not None
    if before is None:
        result["summary"] = "jamais commité — rien à comparer"
    return result

