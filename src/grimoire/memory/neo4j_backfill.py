"""P15b — rattrapage de la clé de projet et des identifiants préfixés d'un graphe Neo4j existant.

Les nœuds écrits avant la clé de projet n'ont ni propriété ``project`` ni identifiant
préfixé ``<projet>::``. Ce module les attribue au projet courant, **sans jamais**
toucher un nœud déjà attribué à un autre projet, et ne renomme rien qui entrerait en
collision avec un nœud déjà préfixé (le conflit est listé, le nœud laissé en l'état).

Aperçu par défaut : sans *apply*, seules des lectures sont lancées. Rejouable : une
seconde passe ne trouve plus rien à attribuer.

Attribution :

1. ``GrimoireMemory`` : ``weaviate_collection`` dans les collections du projet, puis
   ``metadata_json.project_name`` égal au projet. Un souvenir dont ``project_name``
   nomme un autre projet n'est jamais attribué (compté à part).
2. Faits, journaux, ``WeaviateObject`` : par leur souvenir (``SOURCED_FROM``,
   ``RELATED_TO``, ``VECTORIZED_AS``), une fois celui-ci attribué.
3. Étiquettes, entités, agents : seulement s'ils ne relient que des nœuds du
   projet ; un nom partagé avec un autre projet ne se scinde pas, il est compté.
4. ``CodeNode`` : par préfixe de ``file_path`` **et** fichier présent dans la racine de
   code du projet (deux projets partagent souvent ``src/``) ; fantômes marqués externes.

Les nœuds du ledger (missions, tâches, incidents…) n'ont pas de propriétaire déductible
du graphe : ils ne sont pas rattrapés ici.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

#: Conflits détaillés dans l'aperçu : au-delà, le compte reste exact, la liste est tronquée.
CONFLICT_SAMPLE = 50
_BATCH = 500


class BackfillRefusalError(ValueError):
    """Le rattrapage refuse de s'appliquer (racine de code non propre au projet…)."""


def resolve_code_prefixes(project_root: Path, paths: list[str]) -> list[str]:
    """Préfixes de ``file_path`` de la racine de code du projet, ou :class:`BackfillRefusalError`.

    Une racine de code propre à un projet est un sous-dossier existant du projet : la racine
    elle-même (``.``, vide) attribuerait tout ce que la base contient, et un dossier hors du
    projet ou inexistant n'identifie rien.
    """
    root = project_root.resolve()
    prefixes: list[str] = []
    for raw in paths:
        item = raw.strip()
        if not item:
            continue
        candidate = Path(item)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if resolved == root:
            raise BackfillRefusalError(f"Racine de code '{item}' = racine du projet : elle n'est pas propre au projet.")
        if not resolved.is_relative_to(root):
            raise BackfillRefusalError(f"Racine de code '{item}' hors du projet {root}.")
        if not resolved.is_dir():
            raise BackfillRefusalError(f"Racine de code '{item}' introuvable dans le projet ({resolved}).")
        prefixes.append(resolved.relative_to(root).as_posix().rstrip("/") + "/")
    if not prefixes:
        raise BackfillRefusalError("Aucune racine de code donnée (--paths) : refus d'attribuer des nœuds de code.")
    return prefixes


# ── prédicats Cypher ─────────────────────────────────────────────────────────

def _free(var: str, label: str, key: str) -> str:
    """Le nœud peut recevoir l'identifiant préfixé sans collision."""
    return f"NOT EXISTS {{ MATCH (d:{label} {{{key}: $prefix + {var}.{key}}}) }}"


def _mine_memory(var: str) -> str:
    """Souvenir du projet : déjà attribué, ou attribuable par collection / ``project_name``."""
    return f"(coalesce({var}.project = $project, false) OR {_attributable_memory(var)})"


def _attributable_memory(var: str) -> str:
    meta = f"coalesce({var}.metadata_json, '')"
    return (
        f"({var}.project IS NULL AND ({var}.weaviate_collection IN $collections OR {meta} CONTAINS $needle) "
        f"AND (NOT {meta} CONTAINS '\"project_name\":' OR {meta} CONTAINS $needle) "
        f"AND {_free(var, 'GrimoireMemory', 'id')})"
    )


# (label, clé d'identité, relation vers un nœud déjà du projet — motif Cypher sur `{var}` et `m`)
_LINKED_BY_MEMORY: tuple[tuple[str, str, str, str], ...] = (
    ("GrimoireFact", "id", "facts", "({var})-[:SOURCED_FROM]->(m:GrimoireMemory)"),
    ("GrimoireDiary", "id", "diaries", "({var})-[:RELATED_TO]->(m:GrimoireMemory)"),
    ("WeaviateObject", "id", "weaviate_objects", "(m:GrimoireMemory)-[:VECTORIZED_AS]->({var})"),
)


def _attributable_linked(var: str, label: str, key: str, pattern: str) -> str:
    return (
        f"({var}.project IS NULL AND EXISTS {{ MATCH {pattern.format(var=var)} WHERE {_mine_memory('m')} }} "
        f"AND {_free(var, label, key)})"
    )


def _mine_linked(var: str, label: str, key: str, pattern: str) -> str:
    return f"(coalesce({var}.project = $project, false) OR {_attributable_linked(var, label, key, pattern)})"


def _mine_fact(var: str) -> str:
    return _mine_linked(var, *_LINKED_BY_MEMORY[0][:2], _LINKED_BY_MEMORY[0][3])


def _mine_diary(var: str) -> str:
    return _mine_linked(var, *_LINKED_BY_MEMORY[1][:2], _LINKED_BY_MEMORY[1][3])


# (label, clé, rubrique, motif de lien vers les nœuds porteurs, prédicat « porteur du projet »)
_SHARED_NAMES: tuple[tuple[str, str, str, str, Any], ...] = (
    ("GrimoireTag", "name", "tags", "(o:GrimoireMemory)-[:TAGGED_WITH]->({var})", _mine_memory),
    ("GrimoireEntity", "name", "entities", "(o:GrimoireFact)-[:FACT_SUBJECT|FACT_OBJECT]-({var})", _mine_fact),
    ("GrimoireAgent", "name", "agents", "({var})-[:WROTE_DIARY]->(o:GrimoireDiary)", _mine_diary),
)


def _attributable_shared(var: str, label: str, key: str, pattern: str, mine: Any) -> str:
    """Un nom partagé n'est attribuable que si tous ses porteurs sont du projet (et il en a un)."""
    link = pattern.format(var=var)
    return (
        f"({var}.project IS NULL AND EXISTS {{ MATCH {link} }} "
        f"AND NOT EXISTS {{ MATCH {link} WHERE NOT {mine('o')} }} "
        f"AND {_free(var, label, key)})"
    )


# ── rattrapage ───────────────────────────────────────────────────────────────

def backfill_project(
    graph: Neo4jMemoryGraph,
    *,
    collections: list[str],
    project_root: Path,
    code_prefixes: list[str],
    apply: bool = False,
) -> dict[str, Any]:
    """Attribue au projet du *graph* ce qui l'est sans ambiguïté ; rapport chiffré, conflits listés.

    *code_prefixes* vient de :func:`resolve_code_prefixes`. Sans *apply* : lectures seules.
    """
    project = graph.project
    if not project:
        raise ValueError("backfill_project needs a project key")
    params: dict[str, Any] = {
        "collections": collections,
        "needle": f'"project_name": {json.dumps(project, ensure_ascii=False)}',
    }
    report: dict[str, Any] = {}
    conflicts: list[dict[str, str]] = []

    def count(statement: str) -> int:
        rows = graph._read(statement, **params)
        return int(rows[0]["count"]) if rows else 0

    # 1. Souvenirs. Les deux étapes sont disjointes : l'aperçu ne compte jamais deux fois.
    by_collection = (
        "MATCH (n:GrimoireMemory) WHERE n.project IS NULL AND n.weaviate_collection IN $collections "
        "AND (NOT coalesce(n.metadata_json, '') CONTAINS '\"project_name\":' "
        "OR coalesce(n.metadata_json, '') CONTAINS $needle) AND " + _free("n", "GrimoireMemory", "id")
    )
    by_metadata = (
        "MATCH (n:GrimoireMemory) WHERE n.project IS NULL AND NOT coalesce(n.weaviate_collection, '') IN $collections "
        "AND coalesce(n.metadata_json, '') CONTAINS $needle AND " + _free("n", "GrimoireMemory", "id")
    )
    report["memories_by_collection"] = count(f"{by_collection} RETURN count(n) AS count")
    report["memories_by_metadata"] = count(f"{by_metadata} RETURN count(n) AS count")
    report["memories_other_project"] = count(
        "MATCH (n:GrimoireMemory) WHERE n.project IS NULL AND n.weaviate_collection IN $collections "
        "AND coalesce(n.metadata_json, '') CONTAINS '\"project_name\":' "
        "AND NOT coalesce(n.metadata_json, '') CONTAINS $needle RETURN count(n) AS count"
    )
    _conflicts(graph, conflicts, params, "GrimoireMemory", "id", "n.project IS NULL AND " + _entry_match())
    report["memories_id_prefixed_later"] = count(
        "MATCH (n:GrimoireMemory) WHERE n.project = $project AND NOT n.id STARTS WITH $prefix "
        f"AND {_free('n', 'GrimoireMemory', 'id')} RETURN count(n) AS count"
    )
    if apply:
        for match in (by_collection, by_metadata):
            graph._execute(f"{match} SET n.project = $project", params)
        _prefix_ids(graph, params, "GrimoireMemory", "id", "n.project = $project")

    # 2. Faits, journaux, objets Weaviate : par leur souvenir (déjà attribué quand on applique).
    for label, key, name, pattern in _LINKED_BY_MEMORY:
        attributable = _attributable_linked("n", label, key, pattern)
        report[name] = count(f"MATCH (n:{label}) WHERE {attributable} RETURN count(n) AS count")
        _link_conflicts(graph, conflicts, params, label, key, pattern, "m", _mine_memory("m"))
        if apply:
            graph._execute(f"MATCH (n:{label}) WHERE {attributable} SET n.project = $project", params)
            _prefix_ids(graph, params, label, key, "n.project = $project")

    # 3. Noms partagés : étiquettes, entités, agents (exclusifs au projet, sinon comptés).
    for label, key, name, pattern, mine in _SHARED_NAMES:
        attributable = _attributable_shared("n", label, key, pattern, mine)
        report[name] = count(f"MATCH (n:{label}) WHERE {attributable} RETURN count(n) AS count")
        report[f"{name}_shared"] = count(
            f"MATCH (n:{label}) WHERE n.project IS NULL AND EXISTS {{ MATCH {pattern.format(var='n')} }} "
            f"AND NOT {attributable} AND {_free('n', label, key)} RETURN count(n) AS count"
        )
        _shared_conflicts(graph, conflicts, params, label, key, pattern, mine)
        if apply:
            # Le libellé lisible est conservé avant que le nom (clé) reçoive le préfixe.
            graph._execute(
                f"MATCH (n:{label}) WHERE {attributable} SET n.label = coalesce(n.label, n.{key}), n.project = $project",
                params,
            )
            _prefix_ids(graph, params, label, key, "n.project = $project", keep_label=True)

    _backfill_code(graph, report, conflicts, params, project_root, code_prefixes, apply)

    report["conflicts_total"] = len({(c["label"], c["id"]) for c in conflicts})
    report["conflicts"] = conflicts[:CONFLICT_SAMPLE]
    return report


def _entry_match() -> str:
    return (
        "(n.weaviate_collection IN $collections OR coalesce(n.metadata_json, '') CONTAINS $needle) "
        "AND (NOT coalesce(n.metadata_json, '') CONTAINS '\"project_name\":' "
        "OR coalesce(n.metadata_json, '') CONTAINS $needle)"
    )


def _prefix_ids(
    graph: Neo4jMemoryGraph,
    params: dict[str, Any],
    label: str,
    key: str,
    scope: str,
    *,
    keep_label: bool = False,
) -> None:
    """Préfixe l'identifiant des nœuds du projet encore nus. Un nœud en conflit garde le sien."""
    match = (
        f"MATCH (n:{label}) WHERE {scope} AND NOT n.{key} STARTS WITH $prefix AND {_free('n', label, key)}"
    )
    graph._execute(f"{match} SET n.local_id = n.{key}", params)
    graph._execute(f"{match} SET n.{key} = $prefix + n.{key}", params)


def _conflicts(
    graph: Neo4jMemoryGraph,
    out: list[dict[str, str]],
    params: dict[str, Any],
    label: str,
    key: str,
    where: str,
) -> None:
    """Nœuds qu'on attribuerait si leur identifiant préfixé n'existait pas déjà."""
    rows = graph._read(
        f"MATCH (n:{label}) WHERE {where} AND NOT {_free('n', label, key)} "
        f"RETURN n.{key} AS id LIMIT {CONFLICT_SAMPLE}",
        **params,
    )
    out.extend({"label": label, "id": str(r["id"])} for r in rows)
    rows = graph._read(
        f"MATCH (n:{label}) WHERE n.project = $project AND NOT n.{key} STARTS WITH $prefix "
        f"AND NOT {_free('n', label, key)} RETURN n.{key} AS id LIMIT {CONFLICT_SAMPLE}",
        **params,
    )
    out.extend({"label": label, "id": str(r["id"])} for r in rows)


def _link_conflicts(
    graph: Neo4jMemoryGraph,
    out: list[dict[str, str]],
    params: dict[str, Any],
    label: str,
    key: str,
    pattern: str,
    other: str,
    mine: str,
) -> None:
    rows = graph._read(
        f"MATCH (n:{label}) WHERE n.project IS NULL AND NOT {_free('n', label, key)} "
        f"AND EXISTS {{ MATCH {pattern.format(var='n')} WHERE {mine} }} RETURN n.{key} AS id LIMIT {CONFLICT_SAMPLE}",
        **params,
    )
    out.extend({"label": label, "id": str(r["id"])} for r in rows)


def _shared_conflicts(
    graph: Neo4jMemoryGraph,
    out: list[dict[str, str]],
    params: dict[str, Any],
    label: str,
    key: str,
    pattern: str,
    mine: Any,
) -> None:
    link = pattern.format(var="n")
    rows = graph._read(
        f"MATCH (n:{label}) WHERE n.project IS NULL AND NOT {_free('n', label, key)} "
        f"AND EXISTS {{ MATCH {link} }} AND NOT EXISTS {{ MATCH {link} WHERE NOT {mine('o')} }} "
        f"RETURN n.{key} AS id LIMIT {CONFLICT_SAMPLE}",
        **params,
    )
    out.extend({"label": label, "id": str(r["id"])} for r in rows)


def _backfill_code(
    graph: Neo4jMemoryGraph,
    report: dict[str, Any],
    conflicts: list[dict[str, str]],
    params: dict[str, Any],
    project_root: Path,
    code_prefixes: list[str],
    apply: bool,
) -> None:
    root = project_root.resolve()
    rows = graph._read(
        "MATCH (n:CodeNode) WHERE n.project IS NULL AND coalesce(n.placeholder, false) = false "
        "AND any(p IN $code_prefixes WHERE n.file_path STARTS WITH p) "
        "RETURN n.id AS id, n.file_path AS path",
        code_prefixes=code_prefixes,
    )
    # Un fichier absent de la racine de ce projet est celui d'un autre projet de la base.
    owned = [str(row["id"]) for row in rows if (root / str(row["path"])).is_file()]
    report["code_nodes_in_root"] = len(owned)
    report["code_nodes_not_in_project"] = len(rows) - len(owned)
    taken: set[str] = set()
    for start in range(0, len(owned), _BATCH):
        chunk = owned[start : start + _BATCH]
        found = graph._read(
            "MATCH (n:CodeNode) WHERE n.id IN $ids AND NOT " + _free("n", "CodeNode", "id") + " RETURN n.id AS id",
            ids=chunk,
        )
        taken.update(str(r["id"]) for r in found)
    free = [i for i in owned if i not in taken]
    report["code_nodes"] = len(free)
    report["code_nodes_conflicts"] = len(taken)
    conflicts.extend({"label": "CodeNode", "id": i} for i in sorted(taken)[:CONFLICT_SAMPLE])
    ghosts = (
        "MATCH (n:CodeNode) WHERE n.project IS NULL AND coalesce(n.placeholder, false) = true "
        "AND coalesce(n.external, false) = false"
    )
    rows = graph._read(f"{ghosts} RETURN count(n) AS count", **params)
    report["ghosts_external"] = int(rows[0]["count"]) if rows else 0
    if not apply:
        return
    for start in range(0, len(free), _BATCH):
        chunk = free[start : start + _BATCH]
        match = "MATCH (n:CodeNode) WHERE n.project IS NULL AND n.id IN $ids"
        graph._execute(f"{match} SET n.local_id = n.id", {"ids": chunk})
        graph._execute(f"{match} SET n.project = $project, n.external = false, n.id = $prefix + n.id", {"ids": chunk})
    graph._execute(f"{ghosts} SET n.external = true")
