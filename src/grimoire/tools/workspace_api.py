"""Lectures de la vue de travail — ce que les six espaces consomment.

La coque (``web/workspace/``) est servie à l'identique par ``grimoire serve``
et ``grimoire cockpit serve``. Pour que ce soit vrai, ses lectures passent
toutes par ce module : des fonctions pures qui prennent une racine de projet et
rendent un dictionnaire JSON-sérialisable. Aucun état, aucun ``ForgeAPI``,
aucune notion d'hôte — c'est l'appelant qui a résolu le projet (l'atelier n'en
a qu'un, le cockpit le résout depuis ``?project=``), et c'est ce qui rend
vérifiable la clause « chaque route honore la cible » de la spécification.

Trois familles :

``glossary``
    Le glossaire du kit, source unique des infobulles. Un projet peut le
    surcharger comme n'importe quel fichier du kit ; la résolution passe donc
    par :mod:`grimoire.core.layout` avant de retomber sur le kit installé.

``tasks``
    Une projection en lecture de :class:`~grimoire.missions.service.TaskService`
    et de :func:`~grimoire.missions.trace.build_task_timeline`. Les écritures
    ne sont pas ici : elles sont dans :mod:`grimoire.tools.workspace_routes`,
    parce qu'elles passent le gate de preuve et ne doivent exister que sur
    l'hôte mono-projet.

``files``
    L'espace Source : les fichiers d'un projet rangés par étage, leur empreinte
    confrontée au catalogue des digests du kit, et le diff d'un override contre
    son homologue du kit.

Rien ici ne crée de dossier, ne sème de donnée de démonstration, ni ne rend un
zéro là où la réponse est « pas encore mesurée ». Une source absente est dite
absente.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "FILE_TEXT_LIMIT",
    "MAX_FILES_PER_TIER",
    "TIERS",
    "WorkspacePathError",
    "blueprints_view",
    "file_diff",
    "file_history",
    "file_usage",
    "file_view",
    "files_view",
    "glossary_view",
    "safe_relpath",
    "task_trace_view",
    "task_view",
    "tasks_view",
    "tier_of",
]

#: Au-delà, le contenu n'est pas rendu : l'éditeur de l'espace Source n'a pas à
#: charger un artefact de build par accident.
FILE_TEXT_LIMIT = 512 * 1024
#: Plafond d'énumération par étage. Un `.claude/` peuplé dépasse vite le millier.
MAX_FILES_PER_TIER = 2000

#: Les étages de la spécification §4, dans l'ordre où l'espace Source les montre.
#: ``roots`` est relatif à la racine du projet ; ``editable`` dit si écrire là
#: est un geste que le kit respectera à la prochaine mise à jour ; ``term`` est
#: l'entrée du glossaire qui définit l'étage — l'interface la cite plutôt que de
#: la déduire d'un identifiant, et tests/unit/test_workspace_glossary.py vérifie
#: que chacune existe.
TIERS: tuple[dict[str, Any], ...] = (
    {
        "id": "overrides",
        "term": "override",
        "label": "Overrides",
        "note": "possédés par le projet",
        "roots": ("_grimoire/overrides",),
        "editable": True,
    },
    {
        "id": "kit",
        "term": "kit",
        "label": "Kit",
        "note": "généré, écrasé à chaque mise à jour",
        "roots": ("_grimoire/kit", "_grimoire/standard"),
        "editable": False,
    },
    {
        "id": "projections",
        "term": "projection",
        "label": "Projections des hôtes",
        "note": "régénérées depuis le kit",
        "roots": (".claude", ".github"),
        "editable": False,
    },
)

_TIER_BY_ID = {tier["id"]: tier for tier in TIERS}


class WorkspacePathError(PermissionError):
    """Un chemin sort du projet servi, ou n'appartient à aucun étage."""


# ── Chemins ─────────────────────────────────────────────────────────────────


def safe_relpath(project_root: Path, raw: str | None) -> Path:
    """Résout *raw* sous *project_root*, ou refuse.

    Le refus est une :class:`WorkspacePathError` (donc un 403 côté transport)
    et non un 404 : un chemin hors projet n'est pas « introuvable », il est
    interdit, et la distinction évite d'en faire un oracle d'existence.
    """
    if not raw:
        raise WorkspacePathError("chemin requis")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise WorkspacePathError("chemin absolu refusé")
    root = project_root.resolve()
    target = (root / candidate).resolve()
    if not target.is_relative_to(root):
        raise WorkspacePathError("chemin refusé")
    return target


def tier_of(project_root: Path, target: Path) -> dict[str, Any] | None:
    """L'étage auquel appartient *target*, ou ``None`` s'il n'en a aucun."""
    root = project_root.resolve()
    try:
        rel = target.relative_to(root).as_posix()
    except ValueError:
        return None
    for tier in TIERS:
        for tier_root in tier["roots"]:
            if rel == tier_root or rel.startswith(f"{tier_root}/"):
                return tier
    return None


# ── Glossaire ───────────────────────────────────────────────────────────────

_GLOSSARY_RELPATH = "framework/glossary.yaml"
_GLOSSARY_KEYS = (("id", "id"), ("nom", "nom"), ("raccourci", "raccourci"), ("doc", "doc"))


def _kit_glossary_path() -> Path | None:
    """Le glossaire livré par le kit, wheel ou dépôt de développement."""
    from grimoire.data import framework_path

    try:
        candidate = framework_path() / "glossary.yaml"
    except (OSError, RuntimeError):  # pragma: no cover — dépend de l'installation
        return None
    return candidate if candidate.is_file() else None


def glossary_view(project_root: Path) -> dict[str, Any]:
    """Le glossaire servi aux infobulles : une entrée par concept.

    Résolution : l'override du projet d'abord (``_grimoire/overrides/``), le
    kit installé ensuite. Un glossaire absent rend une liste vide et le dit —
    une infobulle sans définition doit se taire, pas inventer.
    """
    from grimoire.core import layout
    from grimoire.tools._common import load_yaml

    source = layout.resolve(project_root, _GLOSSARY_RELPATH) or _kit_glossary_path()
    if source is None or not source.is_file():
        return {"schema": "grimoire-glossary/v1", "source": None, "count": 0, "entries": []}
    try:
        # ruamel (dépendance déclarée) avec repli PyYAML : une installation nue
        # de la wheel n'a pas `yaml`, et le glossaire sert les six espaces.
        raw = load_yaml(source) or {}
    except (OSError, ValueError) as exc:
        return {
            "schema": "grimoire-glossary/v1",
            "source": str(source),
            "count": 0,
            "entries": [],
            "error": f"glossaire illisible : {exc}",
        }
    entries: list[dict[str, Any]] = []
    for item in raw.get("entries") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        entry: dict[str, Any] = {key: str(item.get(src, "") or "") for key, src in _GLOSSARY_KEYS}
        # Clé accentuée dans le YAML — c'est le vocabulaire du produit, pas une
        # concession : l'API la rend sous son nom ASCII pour le client.
        entry["definition"] = " ".join(str(item.get("définition", "") or "").split())
        entry["termes"] = [str(t) for t in (item.get("termes") or []) if t]
        entries.append(entry)
    return {
        "schema": str(raw.get("schema", "grimoire-glossary/v1")),
        "source": str(source),
        "count": len(entries),
        "entries": entries,
    }


# ── Tâches ──────────────────────────────────────────────────────────────────


def _service(project_root: Path) -> Any:
    from grimoire.missions.service import TaskService

    return TaskService(project_root)


def _task_json(task: Any) -> dict[str, Any]:
    from grimoire.missions.board import board_status_of

    data: dict[str, Any] = task.to_dict()
    data["board"] = board_status_of(task.status)
    return data


def tasks_view(project_root: Path, *, mission: str | None = None, status: str | None = None) -> dict[str, Any]:
    """Les tâches du projet et les colonnes du board gouverné.

    ``ledger: false`` n'est pas une erreur : un projet peut n'avoir jamais
    ouvert de tâche. La vue le dit et nomme la commande qui en ouvre une, au
    lieu de rendre un board vide qui ressemblerait à une panne.
    """
    from grimoire.missions.board import BOARD_LIFECYCLE
    from grimoire.missions.schemas import TaskState

    service = _service(project_root)
    payload: dict[str, Any] = {
        "columns": list(BOARD_LIFECYCLE),
        "states": [s.value for s in TaskState],
        "ledger": bool(service.has_ledger),
        "tasks": [],
        "count": 0,
    }
    if not service.has_ledger:
        payload["note"] = "aucun Mission Ledger — `grimoire task add` en ouvre un"
        return payload
    tasks = service.list_tasks(mission or None, status or None)
    payload["tasks"] = [_task_json(t) for t in tasks]
    payload["count"] = len(tasks)
    return payload


def task_view(project_root: Path, task_id: str) -> dict[str, Any]:
    """Une tâche, et ce que chaque pas suivant exigera comme preuve.

    Même forme que l'outil MCP ``task_show`` : les deux surfaces montrent la
    même chose, donc un agent et un humain lisent la même porte.
    """
    from grimoire.missions.board import board_status_of
    from grimoire.missions.gates import declared_transitions

    service = _service(project_root)
    task = service.require(task_id)
    here = board_status_of(task.status)
    payload = _task_json(task)
    payload["next_moves_require"] = {
        to: list(entry.get("required_evidence", []) or [])
        for (src, to), entry in declared_transitions(service.project_root).items()
        if src == here
    }
    return payload


def task_trace_view(project_root: Path, task_id: str) -> dict[str, Any]:
    """La timeline unifiée d'une tâche — l'onglet Timeline du dock.

    C'est exactement ce que rend ``grimoire task trace`` : Mission Ledger,
    TraceLedger, runtime et preuves recousus. ``sources`` nomme les journaux
    absents, pour que l'état vide dise d'où viendrait la donnée.
    """
    from grimoire.missions.trace import build_task_timeline

    return build_task_timeline(project_root, task_id).to_dict()


# ── Fichiers par étage ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Catalog:
    """Le catalogue des digests, chargé une fois par requête."""

    digests: dict[str, dict[str, Any]]

    def lookup(self, digest: str) -> dict[str, Any] | None:
        entry = self.digests.get(digest)
        return entry if isinstance(entry, dict) else None


def _catalog() -> _Catalog:
    from grimoire.core.kit_hashes import load_catalog

    return _Catalog(load_catalog())


def _digest(path: Path) -> str:
    from grimoire.core.kit_hashes import digest_of

    return digest_of(path)


def _file_entry(project_root: Path, path: Path, tier: dict[str, Any], catalog: _Catalog) -> dict[str, Any]:
    rel = path.relative_to(project_root).as_posix()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    digest = _digest(path)
    shipped = catalog.lookup(digest)
    entry: dict[str, Any] = {
        "path": rel,
        "tier": tier["id"],
        "size": size,
        "digest": digest,
        # Le kit a-t-il livré CE contenu exact, à une version quelconque ?
        "shipped_by_kit": shipped is not None,
        "kit_version": shipped.get("version") if shipped else None,
        "editable": bool(tier["editable"]),
    }
    if tier["id"] == "kit":
        override = _override_for(project_root, rel)
        entry["override_path"] = override.as_posix() if override else None
        entry["overridden"] = bool(override and (project_root / override).is_file())
    if tier["id"] == "overrides":
        # Le kit qu'un override masque, s'il existe encore à cet emplacement.
        # C'est ce que l'arbre affiche comme badge de dérive : un override dont
        # le kit a changé depuis n'est pas signalé par `shipped_by_kit` (qui ne
        # dit que « ce contenu exact a déjà été livré, à une version
        # quelconque ») — il faut confronter l'un à l'autre, ici, pas deviner.
        from grimoire.core import layout

        prefix = f"{layout.OVERRIDES_DIR}/"
        kit_rel = f"{layout.KIT_DIR}/{rel[len(prefix) :]}" if rel.startswith(prefix) else None
        kit_path = (project_root / kit_rel) if kit_rel else None
        masks = bool(kit_path and kit_path.is_file())
        entry["kit_counterpart"] = kit_rel
        entry["masks_kit"] = masks
        entry["diverges"] = masks and _digest(kit_path) != digest if kit_path else False
    return entry


def _override_for(project_root: Path, rel: str) -> Path | None:
    """Le chemin d'override qui masquerait *rel*, ou ``None`` hors étage kit."""
    from grimoire.core import layout

    prefix = f"{layout.KIT_DIR}/"
    if not rel.startswith(prefix):
        return None
    return Path(layout.OVERRIDES_DIR) / rel[len(prefix) :]


def _walk(root: Path, limit: int) -> tuple[list[Path], bool]:
    if not root.is_dir():
        return [], False
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(found) >= limit:
            return found, True
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found, False


def files_view(project_root: Path, *, tier: str | None = None) -> dict[str, Any]:
    """L'arbre de l'espace Source : chaque étage, ses racines, ses fichiers.

    Un étage dont aucune racine n'existe est rendu avec ``exists: false`` et
    zéro fichier — il reste visible, parce que « ce projet n'a pas d'override »
    est une information, pas une absence de section.
    """
    root = project_root.resolve()
    catalog = _catalog()
    wanted = [_TIER_BY_ID[tier]] if tier and tier in _TIER_BY_ID else list(TIERS)
    if tier and tier not in _TIER_BY_ID:
        raise WorkspacePathError(f"étage inconnu : {tier}")
    tiers: list[dict[str, Any]] = []
    for spec in wanted:
        files: list[dict[str, Any]] = []
        truncated = False
        exists = False
        budget = MAX_FILES_PER_TIER
        for tier_root in spec["roots"]:
            base = root / tier_root
            exists = exists or base.is_dir()
            found, cut = _walk(base, budget)
            truncated = truncated or cut
            budget -= len(found)
            files.extend(_file_entry(root, p, spec, catalog) for p in found)
            if budget <= 0:
                truncated = True
                break
        tiers.append(
            {
                "id": spec["id"],
                "term": spec["term"],
                "label": spec["label"],
                "note": spec["note"],
                "roots": list(spec["roots"]),
                "editable": spec["editable"],
                "exists": exists,
                "truncated": truncated,
                "count": len(files),
                "files": files,
            }
        )
    return {"projectRoot": str(root), "tiers": tiers}


def file_view(project_root: Path, raw_path: str | None) -> dict[str, Any]:
    """Le contenu d'un fichier et sa provenance.

    ``editable`` est le verdict que l'éditeur affiche : un fichier de l'étage
    kit se lit mais ne s'écrit pas — le modifier veut dire créer son override,
    et ``override_path`` dit lequel.
    """
    root = project_root.resolve()
    target = safe_relpath(root, raw_path)
    tier = tier_of(root, target)
    if tier is None:
        raise WorkspacePathError("ce chemin n'appartient à aucun étage de la vue Source")
    if not target.is_file():
        raise FileNotFoundError(f"introuvable : {raw_path}")
    entry = _file_entry(root, target, tier, _catalog())
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(f"illisible : {raw_path}") from exc
    if len(raw) > FILE_TEXT_LIMIT:
        entry.update(text=None, truncated=True, binary=False)
        return entry
    try:
        entry.update(text=raw.decode("utf-8"), truncated=False, binary=False)
    except UnicodeDecodeError:
        entry.update(text=None, truncated=False, binary=True)
    return entry


def file_diff(project_root: Path, raw_path: str | None) -> dict[str, Any]:
    """Le diff d'un fichier contre ce que le kit livre au même emplacement.

    Deux cas seulement sont comparables ligne à ligne :

    - un **override**, confronté à son homologue de l'étage kit ;
    - un fichier de l'étage **kit** qui a un override, vu depuis l'autre bord.

    Un fichier du kit modifié sur place n'est **pas** comparable : le catalogue
    ne garde que des empreintes, pas des contenus. La réponse le dit
    (``comparable: false`` et la raison) plutôt que d'afficher un diff vide qui
    passerait pour « identique ».
    """
    from grimoire.core import layout

    root = project_root.resolve()
    target = safe_relpath(root, raw_path)
    tier = tier_of(root, target)
    if tier is None:
        raise WorkspacePathError("ce chemin n'appartient à aucun étage de la vue Source")
    if not target.is_file():
        raise FileNotFoundError(f"introuvable : {raw_path}")
    rel = target.relative_to(root).as_posix()
    catalog = _catalog()
    digest = _digest(target)
    shipped = catalog.lookup(digest)

    if rel.startswith(f"{layout.OVERRIDES_DIR}/"):
        kit_rel = f"{layout.KIT_DIR}/{rel[len(layout.OVERRIDES_DIR) + 1 :]}"
        return _unified(root, kit_rel, rel, base_label="kit", head_label="override")
    override = _override_for(root, rel)
    if override is not None and (root / override).is_file():
        return _unified(root, rel, override.as_posix(), base_label="kit", head_label="override")
    return {
        "path": rel,
        "comparable": False,
        "identical": shipped is not None,
        "kit_version": shipped.get("version") if shipped else None,
        "reason": (
            f"identique à ce que le kit a livré en {shipped['version']}"
            if shipped
            else "contenu inconnu du catalogue du kit : ce fichier n'a pas été livré tel quel, "
            "et le catalogue ne garde que des empreintes — créez un override pour comparer"
        ),
    }


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return []


def _unified(root: Path, base_rel: str, head_rel: str, *, base_label: str, head_label: str) -> dict[str, Any]:
    base = root / base_rel
    head = root / head_rel
    base_lines = _read_lines(base) if base.is_file() else []
    head_lines = _read_lines(head)
    diff = list(
        difflib.unified_diff(
            base_lines, head_lines, fromfile=f"{base_label}/{base_rel}", tofile=f"{head_label}/{head_rel}"
        )
    )
    return {
        "path": head_rel,
        "against": base_rel,
        "comparable": True,
        "base_exists": base.is_file(),
        "identical": not diff,
        "unified": "".join(diff),
        "added": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
        "removed": sum(1 for line in diff if line.startswith("-") and not line.startswith("---")),
    }


# ── Concevoir : blueprints ───────────────────────────────────────────────────


def blueprints_view(project_root: Path) -> dict[str, Any]:
    """Les blueprints du projet, enrichis pour le niveau Projet de Concevoir.

    Réutilise :func:`grimoire.tools.project_health.flows` — même inventaire que
    la vue santé, pour ne jamais répondre différemment sur le même dossier — et
    ajoute ce que la Liste de la spec §4 réclame et que ``flows`` ne porte pas :
    le genre (v1 classique ou Studio v2), les agents délégués (nodes
    ``extension-node``), l'équipe déclarée dans ``meta.team`` s'il y en a une,
    et la date de dernière modification du fichier.

    Un blueprint illisible reste dans la liste (via ``flows``) mais sans ses
    champs enrichis : une erreur de parsing ici ne doit pas faire disparaître
    l'entrée que la vue santé montre déjà.
    """
    import json
    from datetime import UTC, datetime

    from grimoire.tools.project_health import BLUEPRINTS_RELPATH, flows

    root = project_root.resolve()
    base = root / BLUEPRINTS_RELPATH
    containers: list[dict[str, Any]] = []
    for base_entry in flows(root):
        bp_id = str(base_entry["id"])
        path = base / f"{bp_id}.blueprint.json"
        genre = "blueprint"
        agents: list[str] = []
        team = ""
        modified_at: str | None = None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if raw:
            nodes = [n for n in (raw.get("nodes") or []) if isinstance(n, dict)]
            if raw.get("blueprintVersion") == 2 or (nodes and all("pins" not in n for n in nodes)):
                genre = "studio"
            seen: set[str] = set()
            for node in nodes:
                if node.get("kind") == "extension-node":
                    ext_id = str(node.get("ref", "")).split("/")[0]
                    if ext_id and ext_id not in seen:
                        seen.add(ext_id)
                        agents.append(ext_id)
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            team = str(meta.get("team", "") or "")
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        except OSError:
            modified_at = None
        containers.append(
            {
                "id": bp_id,
                "type": "blueprint",
                "name": base_entry.get("name") or bp_id,
                "genre": genre,
                "agents": agents,
                "team": team,
                "nodes": base_entry.get("nodes", 0),
                "edges": base_entry.get("edges", 0),
                "validated": bool(base_entry.get("validated", False)),
                "compiled_at": base_entry.get("compiledAt"),
                "modified_at": modified_at,
            }
        )
    containers.sort(key=lambda c: str(c["name"]).lower())
    return {"count": len(containers), "blueprints": containers}


# ── Provenance : projeté vers, chargé par, historique ───────────────────────

#: Où chercher une trace de projection : les arbres que les hôtes régénèrent,
#: plus le fichier d'entrée qu'ils citent tous deux.
_PROJECTION_ROOTS = (".claude", ".github")
_PROJECTION_ROOT_FILES = ("AGENTS.md",)

#: Bornes du grep « chargé par » : un projet gouverné a des milliers de
#: fichiers sous `.claude/` seul. Sans plafond, l'inspecteur deviendrait le
#: point le plus lent de l'espace Source pour la question la moins critique.
_REFERENCE_SCAN_LIMIT = 4000
_REFERENCE_RESULT_LIMIT = 20


def _find_projections(root: Path, name: str, stem: str) -> list[str]:
    """Fichiers projetés par les hôtes qui semblent dérivés de ce fichier.

    Aucun émetteur n'inscrit la source dans le fichier qu'il régénère
    (:mod:`grimoire.hosts.emitters.base`) : le rapprochement se fait donc sur
    le nom, pas sur une référence garantie. C'est une heuristique honnête —
    elle peut manquer un renommage, elle n'invente jamais une projection.
    """
    hits: list[str] = []
    for tree in _PROJECTION_ROOTS:
        base = root / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            # `concierge.md` ↔ `concierge.agent.md` ↔ `concierge.md` : le
            # premier segment du nom de fichier, avant tout point, doit
            # correspondre à la racine du fichier source.
            head = path.name.split(".", 1)[0]
            if head == stem or path.name == name:
                hits.append(path.relative_to(root).as_posix())
    for rel_file in _PROJECTION_ROOT_FILES:
        entry_file = root / rel_file
        if not entry_file.is_file():
            continue
        try:
            text = entry_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if stem and stem in text:
            hits.append(f"{rel_file} · entrée")
    return hits


def _find_references(root: Path, rel: str, name: str) -> dict[str, Any]:
    """Fichiers du projet qui citent le nom de *rel* — chargé par, au sens large.

    Un grep littéral sur le nom de fichier, pas une résolution d'import : le
    kit cite ses propres fichiers par chemin ou par nom dans des balises
    `<step>`, des `@fichier` ou des listes, jamais par un mécanisme qu'on
    pourrait résoudre statiquement dans tous les cas.
    """
    hits: list[dict[str, Any]] = []
    scanned = 0
    truncated = False
    for tier in TIERS:
        if truncated:
            break
        for tier_root in tier["roots"]:
            if truncated:
                break
            base = root / tier_root
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                prel = path.relative_to(root).as_posix()
                if prel == rel:
                    continue
                scanned += 1
                if scanned > _REFERENCE_SCAN_LIMIT:
                    truncated = True
                    break
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if name not in text:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if name in line:
                        hits.append({"path": prel, "line": lineno, "text": line.strip()[:200]})
                        break
                if len(hits) >= _REFERENCE_RESULT_LIMIT:
                    truncated = True
                    break
    return {"entries": hits, "truncated": truncated}


def file_usage(project_root: Path, raw_path: str | None) -> dict[str, Any]:
    """L'onglet « Utilisé par » : projections des hôtes et fichiers qui citent celui-ci.

    Les deux listes viennent d'un grep littéral, plafonné — jamais d'un
    « aucune référence trouvée » présenté comme une preuve d'absence : la
    réponse dit ce qui a été cherché et jusqu'où (:data:`_REFERENCE_SCAN_LIMIT`),
    pas plus.
    """
    root = project_root.resolve()
    target = safe_relpath(root, raw_path)
    tier = tier_of(root, target)
    if tier is None:
        raise WorkspacePathError("ce chemin n'appartient à aucun étage de la vue Source")
    if not target.is_file():
        raise FileNotFoundError(f"introuvable : {raw_path}")
    rel = target.relative_to(root).as_posix()
    return {
        "path": rel,
        "projections": _find_projections(root, target.name, target.stem),
        "loaded_by": _find_references(root, rel, target.name),
    }


def file_history(project_root: Path, raw_path: str | None) -> dict[str, Any]:
    """L'onglet « Historique » : le journal git du fichier, ou un vide honnête.

    Un projet qui n'est pas un dépôt git n'a pas d'historique à montrer — ce
    n'est pas une panne, et l'interface ne doit pas prétendre qu'elle a
    cherché et rien trouvé.
    """
    import subprocess

    root = project_root.resolve()
    target = safe_relpath(root, raw_path)
    tier = tier_of(root, target)
    if tier is None:
        raise WorkspacePathError("ce chemin n'appartient à aucun étage de la vue Source")
    if not target.is_file():
        raise FileNotFoundError(f"introuvable : {raw_path}")
    rel = target.relative_to(root).as_posix()
    if not (root / ".git").exists():
        return {"path": rel, "is_repo": False, "commits": []}
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--max-count=50",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%ad%x1f%an%x1f%s",
                "--",
                rel,
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"path": rel, "is_repo": True, "commits": [], "error": "git indisponible ou trop lent"}
    if proc.returncode != 0:
        return {"path": rel, "is_repo": True, "commits": []}
    commits: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            sha, date, author, subject = parts
            commits.append({"sha": sha, "date": date, "author": author, "subject": subject})
    return {"path": rel, "is_repo": True, "commits": commits}
