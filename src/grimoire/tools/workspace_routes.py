"""Le dispatch de ``/api/workspace/…`` — une table, deux hôtes, une cible.

La vue de travail est la même coque pour ``grimoire serve`` (un projet) et
``grimoire cockpit serve`` (une flotte). Ce module est le point où cette
promesse devient vérifiable : les lectures sont une table pure
``(méthode, chemin) → fonction(project_root, …)``, sans état ni notion d'hôte.
L'atelier l'appelle avec sa racine unique, le cockpit avec celle qu'il a
résolue depuis ``?project=`` — et le test
``tests/unit/test_workspace_routes.py`` prouve que chaque route honore la
cible qu'on lui donne.

Les écritures sont ailleurs dans le même fichier, mais derrière une porte
différente : :func:`workspace_post` reste agnostique de l'hôte appelant (il
exécute pour le ``project_root`` qu'on lui donne, un point c'est tout), mais
qui a le droit de l'appeler ne l'est pas. L'atelier le câble sans condition.
Le cockpit (``cmd_cockpit.py::_CockpitHandler.do_POST``) ne le câble que pour
le projet qu'il sert en direct — ``_HOME_SLUG``, résolu une fois au lancement,
jamais pour un autre projet du registre qu'on ne fait que regarder (#351/#356).
Lui donner de quoi réclamer une tâche ou créer un override dans un dépôt qu'il
ne sert pas serait une régression de gouvernance, pas une commodité.

Ajouter une lecture : une entrée dans :data:`GET_ROUTES`. Ajouter une écriture :
une entrée dans :data:`POST_ROUTES`. Les deux tables sont énumérées par les
tests, donc une route ajoutée sans test de cible se voit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.tools import source_assist, workspace_api, workspace_exec, workspace_language

__all__ = [
    "GET_ROUTES",
    "POST_ROUTES",
    "PREFIX",
    "WORKSPACE_UNHANDLED",
    "workspace_get",
    "workspace_post",
]

#: Tout ce que la vue de travail ajoute vit sous ce préfixe : aucune collision
#: possible avec les routes héritées, et un seul `startswith` à câbler par hôte.
PREFIX = "/api/workspace/"

#: Sentinelle, même contrat que ``forge_routes.API_GET_UNHANDLED`` : distingue
#: « chemin inconnu » d'une route qui rendrait légitimement ``None``.
WORKSPACE_UNHANDLED = object()

_Query = dict[str, list[str]]
_GetHandler = Callable[[Path, _Query], Any]
_PostHandler = Callable[[Path, dict[str, Any]], Any]


def _one(query: _Query, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _translate(exc: Exception) -> Exception:
    """Traduit une erreur du domaine en erreur que le transport sait coder.

    Les hôtes n'attrapent que ``FileNotFoundError`` (404), ``PermissionError``
    (403) et ``ValueError`` (400). Une :class:`GrimoireError` qui remonterait
    telle quelle deviendrait un 500 avec une trace — donc une panne là où il y
    a un refus explicable. La traduction se fait ici, dans notre couche, plutôt
    qu'en élargissant le ``except`` de deux fichiers hérités.
    """
    from grimoire.core.exceptions import GrimoireError, GrimoireMissionError

    if isinstance(exc, GrimoireMissionError):
        return FileNotFoundError(str(exc))
    if isinstance(exc, GrimoireError):
        return ValueError(str(exc))
    return exc


# ── Lectures ────────────────────────────────────────────────────────────────


def _glossary(project_root: Path, _query: _Query) -> Any:
    return workspace_api.glossary_view(project_root)


def _tasks(project_root: Path, query: _Query) -> Any:
    return workspace_api.tasks_view(
        project_root, mission=_one(query, "mission"), status=_one(query, "status")
    )


def _files(project_root: Path, query: _Query) -> Any:
    return workspace_api.files_view(project_root, tier=_one(query, "tier"))


def _file(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_view(project_root, _one(query, "path"))


def _file_diff(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_diff(project_root, _one(query, "path"))


def _blueprints(project_root: Path, _query: _Query) -> Any:
    return workspace_api.blueprints_view(project_root)


def _agents(project_root: Path, _query: _Query) -> Any:
    return workspace_api.agents_view(project_root)


def _proposals(project_root: Path, _query: _Query) -> Any:
    return workspace_api.proposals_view(project_root)


def _file_usage(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_usage(project_root, _one(query, "path"))


def _file_history(project_root: Path, query: _Query) -> Any:
    return workspace_api.file_history(project_root, _one(query, "path"))


def _commands(_project_root: Path, _query: _Query) -> Any:
    return {"commands": workspace_exec.catalogue(), "count": len(workspace_exec.ALLOWED)}


def _doctor(project_root: Path, _query: _Query) -> Any:
    return workspace_exec.doctor_view(project_root)


def _assist_status(project_root: Path, _query: _Query) -> Any:
    """``GET /api/workspace/assist`` — l'opt-in et la disponibilité, sans coût.

    Jamais d'appel à ``/api/generate`` ici : voir
    :func:`grimoire.tools.source_assist.assist_status`. L'éditeur l'appelle au
    montage pour savoir si le bouton « Suggérer » doit même apparaître
    (issue #280, voie 2 — « sinon l'interface ne montre rien et ne tente
    rien »).
    """
    return source_assist.assist_status(project_root)


def _language(project_root: Path, query: _Query) -> Any:
    """Tokens, diagnostics et complétions de l'éditeur Source (#280).

    ``text`` porte le brouillon affiché — s'il est absent, la lecture vient du
    disque, comme :func:`_file`. ``line``/``col`` sont 0-indexées ; les deux
    doivent être présentes pour obtenir des complétions, sinon la réponse n'a
    que ``tokens``/``diagnostics``.
    """
    line = _one(query, "line")
    col = _one(query, "col")
    return workspace_language.language_view(
        project_root,
        _one(query, "path"),
        text=_one(query, "text"),
        line=int(line) if line is not None else None,
        col=int(col) if col is not None else None,
    )


#: Chemins exacts. Les routes paramétrées par un identifiant de tâche sont
#: traitées à part dans :func:`workspace_get`, parce qu'un identifiant de ledger
#: n'est pas un segment fixe.
GET_ROUTES: dict[str, _GetHandler] = {
    f"{PREFIX}glossary": _glossary,
    f"{PREFIX}tasks": _tasks,
    f"{PREFIX}files": _files,
    f"{PREFIX}file": _file,
    f"{PREFIX}file/diff": _file_diff,
    f"{PREFIX}file/usage": _file_usage,
    f"{PREFIX}file/history": _file_history,
    f"{PREFIX}commands": _commands,
    f"{PREFIX}doctor": _doctor,
    f"{PREFIX}language": _language,
    f"{PREFIX}assist": _assist_status,
    f"{PREFIX}blueprints": _blueprints,
    f"{PREFIX}agents": _agents,
    f"{PREFIX}proposals": _proposals,
}


def _task_id(path: str, suffix: str = "") -> str:
    """Extrait l'identifiant de ``/api/workspace/tasks/<id>[/<suffix>]``."""
    rest = path[len(f"{PREFIX}tasks/") :]
    if suffix:
        rest = rest[: -(len(suffix) + 1)]
    task_id = rest.strip("/")
    if not task_id or "/" in task_id:
        raise ValueError("identifiant de tâche invalide")
    return task_id


def workspace_get(project_root: Path, path: str, query: _Query) -> Any:
    """Résout une lecture de la vue de travail pour *project_root*.

    Rend :data:`WORKSPACE_UNHANDLED` si le chemin n'est pas à nous, pour que
    l'hôte appelant poursuive sa propre chaîne (fichiers statiques, 404).
    """
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError

    try:
        handler = GET_ROUTES.get(path)
        if handler is not None:
            return handler(project_root, query)
        if path.startswith(f"{PREFIX}tasks/"):
            if path.endswith("/trace"):
                return workspace_api.task_trace_view(project_root, _task_id(path, "trace"))
            if path.endswith("/recall"):
                return workspace_api.task_recall_view(project_root, _task_id(path, "recall"))
            return workspace_api.task_view(project_root, _task_id(path))
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED


# ── Écritures — hôte mono-projet seulement ──────────────────────────────────


def _create_override(project_root: Path, body: dict[str, Any]) -> Any:
    """Copie un fichier de l'étage kit vers l'étage overrides.

    C'est le geste que la spécification attache à « éditer un fichier du kit » :
    on ne modifie pas le kit, on prend possession d'une copie qui prime et qui
    survit à ``grimoire up``. Un override déjà là n'est jamais écrasé — la
    réponse le dit et le geste est idempotent.
    """
    from grimoire.core import layout

    root = project_root.resolve()
    source = workspace_api.safe_relpath(root, body.get("path"))
    rel = source.relative_to(root).as_posix()
    prefix = f"{layout.KIT_DIR}/"
    if not rel.startswith(prefix):
        raise workspace_api.WorkspacePathError(
            "seul un fichier de l'étage kit se prend en override"
        )
    if not source.is_file():
        raise FileNotFoundError(f"introuvable : {rel}")
    dest_rel = f"{layout.OVERRIDES_DIR}/{rel[len(prefix) :]}"
    dest = root / dest_rel
    if dest.is_file():
        return {"created": False, "override_path": dest_rel, "note": "override déjà présent"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return {"created": True, "override_path": dest_rel, "from": rel}


def _write_file(project_root: Path, body: dict[str, Any]) -> Any:
    """Écrit un fichier d'un étage éditable — l'étage overrides, et lui seul.

    Écrire dans l'étage kit serait perdu à la prochaine mise à jour ; le refus
    nomme l'override à créer plutôt que de laisser l'utilisateur découvrir la
    perte trois jours plus tard.
    """
    root = project_root.resolve()
    target = workspace_api.safe_relpath(root, body.get("path"))
    tier = workspace_api.tier_of(root, target)
    if tier is None or not tier["editable"]:
        raise workspace_api.WorkspacePathError(
            "étage non éditable : prenez d'abord un override (POST /api/workspace/file/override)"
        )
    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("`text` requis")
    if len(text.encode("utf-8")) > workspace_api.FILE_TEXT_LIMIT:
        raise ValueError("contenu trop volumineux")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return workspace_api.file_view(root, target.relative_to(root).as_posix())


def _assist(project_root: Path, body: dict[str, Any]) -> Any:
    """Suggestion de contenu par un petit modèle local (issue #280, voie 2).

    Jamais à la place de l'IntelliSense déterministe (:mod:`workspace_language`,
    voie 1) : voir :mod:`grimoire.tools.source_assist` pour l'opt-in, la
    sonde Ollama et la vérification des identifiants cités. Toujours un 200
    côté transport — un refus (opt-in absent, Ollama indisponible, délai
    dépassé) est une valeur (``available: False``), pas une exception.
    """
    return source_assist.assist_view(project_root, body)


def _command(project_root: Path, body: dict[str, Any]) -> Any:
    argv = body.get("argv")
    if isinstance(argv, str):
        argv = argv.split()
    if not isinstance(argv, list):
        raise ValueError("`argv` doit être une liste de mots")
    return workspace_exec.run_command(
        project_root, argv, allow_mutation=body.get("confirm") is True
    )


def _task_action(project_root: Path, task_id: str, action: str, body: dict[str, Any]) -> Any:
    """Réclame, déplace, bloque ou ferme une tâche — gate de preuve compris.

    Le service est le même que celui du CLI et du serveur MCP : un gate
    contourné ici le serait partout, donc il n'y a qu'un endroit où il pourrait
    l'être, et ce n'est pas celui-ci.
    """
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve())
    actor = str(body.get("actor") or "workspace")
    if action == "claim":
        move = service.claim(task_id, actor, str(body.get("host") or "workspace"))
    elif action == "close":
        move = service.transition(task_id, TaskState.CLOSED, actor, str(body.get("reason") or ""))
    elif action == "block":
        reason = str(body.get("reason") or "")
        if not reason:
            raise ValueError("`reason` requis pour bloquer une tâche")
        move = service.transition(task_id, TaskState.BLOCKED, actor, reason)
    elif action == "move":
        target = body.get("to")
        try:
            state = TaskState(str(target))
        except ValueError as exc:
            raise ValueError(
                f"état inconnu : {target!r} — parmi {', '.join(s.value for s in TaskState)}"
            ) from exc
        move = service.transition(task_id, state, actor, str(body.get("reason") or ""))
    else:  # pragma: no cover — garde de complétude
        raise ValueError(f"action inconnue : {action}")
    return move.to_dict()


#: Écritures à chemin fixe. Les actions de tâche sont paramétrées, cf. plus bas.
POST_ROUTES: dict[str, _PostHandler] = {
    f"{PREFIX}file/override": _create_override,
    f"{PREFIX}file/write": _write_file,
    f"{PREFIX}command": _command,
    f"{PREFIX}assist": _assist,
}

#: Les verbes qu'une tâche accepte depuis l'interface.
TASK_ACTIONS = ("claim", "move", "block", "close")


# ── Agents (issue #374) ─────────────────────────────────────────────────────
#
# Toute écriture ici porte sur la couche ``overrides`` du projet, jamais sur
# le kit : un projet personnalise, il ne modifie pas ce que le kit livre
# (doctrine, ``docs/artifact-doctrine.md``). Le geste est celui de
# ``grimoire_add_agent`` (``src/grimoire/mcp/server.py``) et de
# ``_create_override`` ci-dessus, appliqué au frontmatter d'un agent déjà
# installé plutôt qu'à un fichier neuf ou une copie brute : on écrit d'abord
# dans l'override, puis on valide avec la même lecture que
# ``collect_agents`` — la garde qui refuse déjà un skill ou un contexte
# introuvable — et on annule l'écriture si elle échoue. Le message d'erreur
# est donc exactement celui que ``collect`` produit, jamais dupliqué ici.

#: Frontmatter d'un fichier agent : un commentaire HTML d'archétype optionnel,
#: un bloc YAML entre ``---``, puis le corps. Même forme que
#: ``grimoire.hosts.collect._FRONTMATTER_RE``, mais le commentaire est capturé
#: ici (pas seulement sauté) pour pouvoir le réécrire tel quel.
_AGENT_FM_RE = re.compile(
    r"\A(?P<bom>﻿)?(?P<comment>(?:<!--.*?-->\s*)?)---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)

#: Champs de frontmatter que le cockpit sait modifier. Le reste (``name``,
#: ``description``, ``model_affinity``…) n'est pas de la configuration au sens
#: de l'issue — le modifier romprait l'identité de l'agent, pas son emploi.
_AGENT_STRING_FIELDS = frozenset({"use_when", "dont_use_when", "tool_boundary", "tools"})
_AGENT_LIST_FIELDS = frozenset({"skills", "context"})


def _agent_target(project_root: Path, name: str) -> Path:
    """Fichier installé de l'agent *name*, nommé comme ``agents_view`` le nomme.

    Résolu via :func:`collect_agents`, la même lecture qu'``agents_view``
    emploie pour construire la liste affichée : un agent visible à la lecture
    doit rester résoluble à l'écriture, sous le même nom. Depuis #381,
    ``collect_agents`` partage avec ``layout.installed_agents`` (le
    diagnostic, la carte de routage) la même lecture d'identité
    (:func:`grimoire.core.layout.agent_identity`) ; un gabarit non rendu comme
    ``custom-agent.md`` (``name: "{{agent_tag}}"`` non substitué) n'a d'identité
    pour aucun des deux et n'apparaît donc pas ici — ce n'est pas un agent
    installé, seulement un modèle à compléter.
    """
    from grimoire.hosts import collect

    root = project_root.resolve()
    skills = collect.collect_skills(root)
    known_skills = frozenset(s.slug for s in skills)
    for agent in collect.collect_agents(root, known_skills=known_skills):
        if agent.name == name:
            return root / agent.definition_ref
    raise FileNotFoundError(f"agent introuvable : {name}")


def _agent_override_path(project_root: Path, source: Path) -> Path:
    """Le chemin overrides d'un agent installé à *source*.

    Même nom de fichier qu'à la source, quel que soit l'étage d'où elle vient
    (kit, ou un répertoire hérité) : les agents n'ont jamais qu'un seul niveau
    de sous-dossier (``agents/<tag>.md``), donc le nom de fichier suffit à
    reconstruire le chemin d'override sans connaître l'étage de départ.
    """
    from grimoire.core import layout

    return layout.overrides_dir(project_root) / layout.AGENTS_SUBDIR / source.name


def _load_agent_frontmatter(path: Path) -> tuple[str, str, Any, str]:
    """Lit *path* en ``(bom, commentaire, données YAML éditables, corps)``."""
    from ruamel.yaml import YAML

    text = path.read_text(encoding="utf-8")
    match = _AGENT_FM_RE.match(text)
    if match is None:
        raise ValueError(f"fichier agent sans frontmatter : {path}")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    data = yaml.load(match.group("yaml"))
    if not isinstance(data, dict):
        raise ValueError(f"frontmatter d'agent invalide : {path}")
    return match.group("bom") or "", match.group("comment"), data, match.group("body")


def _dump_agent_frontmatter(bom: str, comment: str, data: Any, body: str) -> str:
    """Réassemble un fichier agent après modification de son bloc YAML."""
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(data, buf)
    return f"{bom}{comment}---\n{buf.getvalue()}---\n{body}"


def _str_list(raw: Any) -> tuple[str, ...]:
    """Lit une clé frontmatter liste-de-chaînes, tolérante à une chaîne seule.

    Même règle que ``grimoire.hosts.collect._str_tuple`` — dupliquée plutôt
    qu'importée : quatre lignes, aucune logique métier, et l'import d'un nom
    privé d'un autre module aurait été le mauvais genre de réutilisation.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _apply_agent_updates(project_root: Path, name: str, updates: dict[str, Any]) -> Any:
    """Écrit *updates* dans l'override de l'agent *name*, valide, ou annule.

    Crée l'override s'il n'existe pas encore — un override **partiel**
    (``extends: kit``, issue #427) quand un agent kit de même nom existe déjà :
    seuls les champs de *updates* y apparaissent, le corps et le reste du
    frontmatter restent hérités du fichier kit, empreinte
    (``kit_source_hash``) à l'appui pour que `doctor`/le cockpit signalent une
    dérive future. Sans contrepartie kit (répertoire hérité), la seule
    personnalisation possible reste une copie intégrale, comme avant cette
    issue. Une valeur ``None``, chaîne vide ou liste vide retire la clé
    plutôt que d'écrire une déclaration vide. La validation est celle de
    ``collect_agents`` — skill ou contexte introuvable lève
    ``GrimoireAgentError``, translatée en ``ValueError`` par
    :func:`workspace_post`, avec le message que ``collect`` produit déjà.
    """
    from grimoire.core import layout
    from grimoire.core.exceptions import GrimoireAgentError
    from grimoire.core.override_drift import compute_kit_source_hash
    from grimoire.hosts import collect

    root = project_root.resolve()
    source = _agent_target(root, name)
    override_path = _agent_override_path(root, source)
    pre_existing = override_path.is_file()
    if not pre_existing:
        override_path.parent.mkdir(parents=True, exist_ok=True)
        if layout.is_kit_owned(root, source):
            skeleton = _dump_agent_frontmatter(
                "", "",
                {"extends": "kit", "kit_source_hash": compute_kit_source_hash(source)},
                "",
            )
            override_path.write_text(skeleton, encoding="utf-8")
        else:
            override_path.write_bytes(source.read_bytes())
    original_text = override_path.read_text(encoding="utf-8")

    bom, comment, data, body = _load_agent_frontmatter(override_path)
    is_partial = str(data.get("extends", "")).strip().lower() == "kit"
    kit_meta: dict[str, Any] = {}
    if is_partial:
        # `source` est déjà le fichier kit pour un override partiel — voir
        # `_agent_target` : son `definition_ref` pointe vers le kit depuis
        # l'issue #427. Nécessaire pour distinguer, en cas d'effacement, « le
        # kit ne déclare rien ici non plus » (retirer la clé, comme avant
        # cette issue) de « le kit déclare une valeur, et il ne faut pas
        # qu'elle refasse surface par simple absence » (garder la clé, vide
        # explicitement).
        from grimoire.hosts.collect import parse_frontmatter

        kit_meta, _ = parse_frontmatter(source.read_text(encoding="utf-8"))
    for key, value in updates.items():
        if value in (None, "", []):
            if is_partial and kit_meta.get(key) not in (None, "", []):
                data[key] = [] if key in _AGENT_LIST_FIELDS else ""
            else:
                data.pop(key, None)
        else:
            data[key] = value
    override_path.write_text(_dump_agent_frontmatter(bom, comment, data, body), encoding="utf-8")

    try:
        skills = collect.collect_skills(root)
        collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError:
        if pre_existing:
            override_path.write_text(original_text, encoding="utf-8")
        else:
            override_path.unlink(missing_ok=True)
        raise
    return workspace_api.agents_view(root)


def _agent_skill_action(project_root: Path, name: str, body: dict[str, Any]) -> Any:
    """Assigne ou retire un skill — l'écriture porte toujours sur la liste entière."""
    slug = str(body.get("skill", "")).strip()
    action = str(body.get("action", "")).strip()
    if not slug:
        raise ValueError("`skill` requis")
    if action not in {"assign", "remove"}:
        raise ValueError("`action` doit valoir « assign » ou « remove »")

    root = project_root.resolve()
    _, _, data, _ = _load_agent_frontmatter(_agent_target(root, name))
    current = _str_list(data.get("skills"))
    if action == "assign":
        updated = list(current) if slug in current else [*current, slug]
    else:
        updated = [s for s in current if s != slug]
    return _apply_agent_updates(root, name, {"skills": updated})


def _agent_fields_update(project_root: Path, name: str, body: dict[str, Any]) -> Any:
    """Modifie la clause d'emploi, les outils ou le contexte déclaré d'un agent.

    Chaque champ présent dans *body* est validé pour sa propre forme ; la
    validation croisée (skill connu, contexte qui existe sur disque) reste
    celle de ``collect_agents``, appliquée par :func:`_apply_agent_updates`.
    """
    from grimoire.hosts.surface import ToolVerb

    updates: dict[str, Any] = {}
    for field in ("use_when", "dont_use_when", "tool_boundary"):
        if field in body:
            updates[field] = str(body[field] or "").strip()

    if "tools" in body:
        raw = body["tools"]
        values = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(values, list):
            raise ValueError("`tools` doit être une liste, ou une chaîne séparée par des virgules")
        cleaned = [str(v).strip().lower() for v in values if str(v).strip()]
        known = {v.value for v in ToolVerb}
        unknown = [v for v in cleaned if v not in known]
        if unknown:
            raise ValueError(f"outil(s) inconnu(s) : {', '.join(unknown)} — parmi {', '.join(sorted(known))}")
        updates["tools"] = ", ".join(cleaned)

    if "context" in body:
        raw = body["context"]
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise ValueError("`context` doit être une liste de chemins")
        updates["context"] = [str(p).strip() for p in raw if str(p).strip()]

    unknown_fields = set(body) - _AGENT_STRING_FIELDS - _AGENT_LIST_FIELDS
    if unknown_fields:
        raise ValueError(f"champ(s) non modifiable(s) : {', '.join(sorted(unknown_fields))}")
    if not updates:
        raise ValueError("aucun champ à modifier")
    return _apply_agent_updates(project_root, name, updates)


def _agent_route(path: str) -> tuple[str, str] | None:
    """``(nom, action)`` depuis ``/api/workspace/agents/<nom>/<action>``, ou ``None``."""
    rest = path[len(f"{PREFIX}agents/") :]
    name, _, action = rest.partition("/")
    if not name or not action or "/" in action:
        return None
    return name, action


# ── Propositions d'artefact (issue #395) ────────────────────────────────────
#
# Même moteur que la CLI (``grimoire proposals``) : :mod:`grimoire.proposals`
# décide, écrit et valide — cette route ne fait que le brancher, comme
# ``_agent_skill_action``/``_agent_fields_update`` le font déjà pour les
# agents. Aucune création silencieuse : accepter écrit un artefact réel (et
# annule si la garde de distinction le refuse), refuser ne fait que marquer
# la proposition — jamais l'inverse.


def _proposal_route(path: str) -> tuple[str, str] | None:
    """``(slug, action)`` depuis ``/api/workspace/proposals/<slug>/<action>``, ou ``None``."""
    rest = path[len(f"{PREFIX}proposals/") :]
    slug, _, action = rest.partition("/")
    if not slug or not action or "/" in action:
        return None
    return slug, action


def _proposal_accept(project_root: Path, slug: str, _body: dict[str, Any]) -> Any:
    from grimoire.proposals import accept_proposal

    return accept_proposal(project_root, slug)


def _proposal_reject(project_root: Path, slug: str, _body: dict[str, Any]) -> Any:
    from grimoire.proposals import reject_proposal

    return reject_proposal(project_root, slug)


def workspace_post(project_root: Path, path: str, body: dict[str, Any]) -> Any:
    """Résout une écriture de la vue de travail pour ``project_root``.

    Agnostique de l'hôte appelant : c'est à l'appelant de décider s'il a le
    droit d'écrire ici (l'atelier, toujours ; le cockpit, seulement pour son
    projet de lancement — voir le docstring du module).
    """
    if not path.startswith(PREFIX):
        return WORKSPACE_UNHANDLED
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.service import TaskRefusedError

    try:
        handler = POST_ROUTES.get(path)
        if handler is not None:
            return handler(project_root, body)
        if path.startswith(f"{PREFIX}tasks/"):
            tail = path[len(f"{PREFIX}tasks/") :].strip("/")
            task_id, _, action = tail.partition("/")
            if task_id and action in TASK_ACTIONS:
                return _task_action(project_root, task_id, action, body)
        if path.startswith(f"{PREFIX}agents/"):
            parsed = _agent_route(path)
            if parsed is not None:
                agent_name, action = parsed
                if action == "skill":
                    return _agent_skill_action(project_root, agent_name, body)
                if action == "fields":
                    return _agent_fields_update(project_root, agent_name, body)
        if path.startswith(f"{PREFIX}proposals/"):
            parsed_proposal = _proposal_route(path)
            if parsed_proposal is not None:
                slug, action = parsed_proposal
                if action == "accept":
                    return _proposal_accept(project_root, slug, body)
                if action == "reject":
                    return _proposal_reject(project_root, slug, body)
    except TaskRefusedError as exc:
        # Un gate rouge n'est pas une panne du serveur : c'est la réponse. On
        # la rend telle quelle, avec la preuve manquante et son remède, comme
        # le fait déjà l'outil MCP `task_update`.
        return exc.to_dict()
    except GrimoireError as exc:
        raise _translate(exc) from exc
    return WORKSPACE_UNHANDLED
