"""Registre local des projets Grimoire — source de vérité partagée.

Un seul registre par machine (``~/.grimoire/cockpit/registry.json``), lu et
écrit par les deux hôtes locaux : ``grimoire cockpit`` (portefeuille, lecture
seule multi-projets) et ``grimoire serve`` (atelier, écriture sur le projet
courant). Le module ne dépend ni de Typer ni de Rich : il est appelable depuis
un serveur HTTP comme depuis une commande.

Il porte aussi la découverte : ``crawl_projects`` (scan borné d'une racine) et
``browse`` (navigation dossier par dossier pour un sélecteur manuel).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.scanner import _is_excluded_dir

# Marqueurs qui font d'un dossier un projet : un dépôt, ou une trace Grimoire.
GRIMOIRE_MARKERS = (".git", "project-context.yaml", "_grimoire", ".github/copilot-instructions.md")

# Profondeur de scan par défaut — assez pour ``~/Projets/<org>/<repo>``, pas
# assez pour parcourir un home entier.
DEFAULT_SCAN_DEPTH = 4

# Plafond dur : la profondeur vient d'une requête HTTP. Un scan de ``/`` à
# profondeur arbitraire immobiliserait un thread du serveur pendant des minutes
# pour un résultat que personne n'attend.
MAX_SCAN_DEPTH = 8

# Un scan ne doit jamais rendre une liste ingérable ni tourner sans fin.
MAX_SCAN_RESULTS = 500


def allowed_roots(*extra: Path | str | None) -> list[Path]:
    """Racines sous lesquelles la découverte par HTTP a le droit de regarder.

    Le répertoire personnel, plus le dossier parent de chaque projet déjà au
    registre — c'est ainsi qu'un dépôt hors de ``$HOME`` (``/mnt/...``) reste
    atteignable sans ouvrir la machine entière.

    Un chemin venu d'une requête ne doit pas pouvoir désigner n'importe quel
    dossier du système : le garde d'hôte empêche une page tierce d'appeler ces
    routes, mais un contrôle d'accès n'est pas une raison de laisser la surface
    illimitée. Pour ouvrir une racine nouvelle, on passe par la CLI
    (``grimoire cockpit add <chemin>``), qui n'est pas exposée au réseau.
    """
    roots = [Path.home().resolve()]
    # Le projet servi est autorisé par définition : c'est celui qu'on a ouvert.
    # Son dossier parent l'est aussi, sinon on ne pourrait pas découvrir ses
    # voisins — le cas de figure exact d'un premier scan.
    for candidate in extra:
        if not candidate:
            continue
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            continue
        roots.append(resolved)
        if resolved.parent != resolved:
            roots.append(resolved.parent)
    for entry in load_registry():
        raw = str(entry.get("path", "")).strip()
        if not raw:
            continue
        try:
            registered = Path(raw).resolve()
        except OSError:
            continue
        roots.append(registered)
        if registered.parent != registered:
            roots.append(registered.parent)
    return roots


def resolve_within_allowed(raw: str | Path | None, *extra: Path | str | None) -> Path:
    """Chemin demandé, résolu et vérifié comme contenu dans une racine permise.

    Résolution d'abord — ``..`` et liens symboliques compris — puis comparaison
    de préfixe sur les chemins canoniques : l'ordre inverse laisserait passer
    ``~/../../etc``. La comparaison ajoute le séparateur pour que ``/home/uX``
    ne soit pas accepté comme contenu dans ``/home/u``.

    La forme ``realpath`` + ``startswith`` est aussi celle que l'analyse
    statique reconnaît comme assainissement ; l'écrire autrement laissait la
    vérification invisible à l'outil qui l'exige.
    """
    home = str(Path.home())
    requested = str(raw) if raw else home
    # Expansion du tilde par substitution de texte, sans toucher au disque :
    # la première opération de fichier appliquée à une valeur venue d'une
    # requête doit être celle qui l'assainit, pas une commodité d'écriture.
    # Les formes ``~autre-utilisateur`` ne sont donc pas reconnues, et c'est
    # aussi bien : la découverte parle du compte courant.
    if requested == "~" or requested.startswith("~/"):
        requested = home + requested[1:]
    # ``realpath`` plutôt que ``Path.resolve`` : c'est la forme que l'analyse
    # statique reconnaît comme assainissement d'un chemin venu d'une requête.
    resolved = os.path.realpath(requested)
    for root in allowed_roots(*extra):
        root_path = os.path.realpath(str(root))
        if resolved == root_path or resolved.startswith(root_path + os.sep):
            return Path(resolved)
    msg = f"chemin hors des racines autorisées : {resolved}"
    raise PermissionError(msg)


# ── Chemins ──────────────────────────────────────────────────────────────────

def registry_home() -> Path:
    """Racine de l'état local. ``GRIMOIRE_COCKPIT_HOME`` prime (tests, isolation)."""
    env = os.environ.get("GRIMOIRE_COCKPIT_HOME")
    return Path(env).expanduser() if env else Path.home() / ".grimoire" / "cockpit"


def registry_file() -> Path:
    return registry_home() / "registry.json"


def state_file() -> Path:
    return registry_home() / "cockpit.json"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "project"


# ── Registre ─────────────────────────────────────────────────────────────────

def load_registry() -> list[dict[str, str]]:
    f = registry_file()
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_registry(projects: list[dict[str, str]]) -> None:
    f = registry_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(projects, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_state() -> dict[str, Any]:
    f = state_file()
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def looks_grimoire(p: Path) -> bool:
    """Vrai si le dossier porte au moins un marqueur de projet."""
    return any((p / marker).exists() for marker in GRIMOIRE_MARKERS)


def is_grimoire_managed(p: Path) -> bool:
    """Vrai si le projet est *initialisé* Grimoire (pas seulement un dépôt git)."""
    return (p / "project-context.yaml").exists() or (p / "_grimoire").exists()


def register_project(path: Path, name: str | None = None) -> str | None:
    """Enregistre un projet (idempotent).

    Renvoie le slug attribué si l'entrée est neuve, ``None`` si le chemin n'est
    pas un dossier ou est déjà au registre.
    """
    proot = path.expanduser().resolve()
    if not proot.is_dir():
        return None
    projects = load_registry()
    if any(p.get("path") == str(proot) for p in projects):
        return None
    disp = name or proot.name
    slug = slugify(disp)
    existing = {p.get("slug") for p in projects}
    base_slug, n = slug, 2
    while slug in existing:
        slug = f"{base_slug}-{n}"
        n += 1
    projects.append({"name": disp, "path": str(proot), "slug": slug})
    save_registry(projects)
    return slug


def slug_for_path(path: Path) -> str | None:
    """Slug du projet enregistré à ce chemin, ou ``None`` s'il est inconnu."""
    target = str(path.expanduser().resolve())
    for p in load_registry():
        if p.get("path") == target:
            return str(p.get("slug", "")) or None
    return None


def path_for_slug(slug: str) -> Path | None:
    for p in load_registry():
        if p.get("slug") == slug:
            return Path(str(p.get("path", "")))
    return None


def primary_slug() -> str:
    projects = load_registry()
    return str(projects[0].get("slug", "")) if projects else ""


def selected_slug() -> str:
    """Projet courant : choix explicite s'il est encore valide, sinon primaire."""
    chosen = str(read_state().get("selected_project", ""))
    if chosen and any(p.get("slug") == chosen for p in load_registry()):
        return chosen
    return primary_slug()


def set_selected_slug(slug: str) -> bool:
    """Persiste le projet courant. ``False`` si le slug n'est pas au registre."""
    if not any(p.get("slug") == slug for p in load_registry()):
        return False
    state = read_state()
    state["selected_project"] = slug
    write_state(state)
    return True


def projects_payload(*, selected: str | None = None) -> dict[str, Any]:
    """Registre tel que servi à l'UI — chaque entrée dit si son chemin existe encore."""
    projects = [
        {
            "slug": str(p.get("slug", "")),
            "name": str(p.get("name", "")),
            "path": str(p.get("path", "")),
            "exists": Path(str(p.get("path", ""))).is_dir(),
            "is_grimoire": looks_grimoire(Path(str(p.get("path", "")))),
            "managed": is_grimoire_managed(Path(str(p.get("path", "")))),
        }
        for p in load_registry()
    ]
    return {
        "projects": projects,
        "selected": selected if selected is not None else selected_slug(),
        "primary": primary_slug(),
    }


def classify_registry(
    projects: list[dict[str, str]], *, stale: bool = False
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Sépare le registre en (à garder, à retirer).

    Par défaut, seule l'absence du chemin justifie un retrait : un répertoire
    qui existe encore peut avoir été enrôlé délibérément, et supprimer une
    entrée valide coûte plus cher que d'en garder une douteuse. ``stale``
    élargit aux chemins présents mais sans marqueur Grimoire.
    """
    keep: list[dict[str, str]] = []
    drop: list[dict[str, str]] = []
    for entry in projects:
        raw = str(entry.get("path", "")).strip()
        if not raw:
            drop.append(entry)
            continue
        path = Path(raw)
        if not path.is_dir() or (stale and not looks_grimoire(path)):
            drop.append(entry)
            continue
        keep.append(entry)
    return keep, drop


# ── Découverte ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    """Un projet découvert sous une racine."""

    path: Path
    managed: bool  # initialisé Grimoire vs simple dépôt git


def crawl_projects(root: Path, max_depth: int = DEFAULT_SCAN_DEPTH) -> list[Candidate]:
    """Trouve les projets candidats sous ``root``, borné par ``max_depth``.

    Un candidat est un dossier portant ``.git``, ``project-context.yaml`` ou
    ``_grimoire/``. Un projet détecté est une feuille (jamais parcourue) ; les
    dossiers de dépendances / build sont sautés, les liens symboliques ne sont
    jamais suivis et les dossiers illisibles sont ignorés.
    """
    found: list[Candidate] = []

    def _walk(directory: Path, depth: int) -> None:
        if len(found) >= MAX_SCAN_RESULTS:
            return
        managed = is_grimoire_managed(directory)
        if managed or (directory / ".git").exists():
            found.append(Candidate(path=directory, managed=managed))
            return  # un dépôt = une feuille
        if depth >= max_depth:
            return
        try:
            children = sorted(
                (p for p in directory.iterdir() if not p.is_symlink() and p.is_dir()),
                key=lambda p: p.name,
            )
        except (PermissionError, OSError):
            return
        for child in children:
            if _is_excluded_dir(child.name):
                continue
            _walk(child, depth + 1)

    _walk(root, 0)
    return found


def scan_payload(
    root: str | Path, depth: int = DEFAULT_SCAN_DEPTH, *extra: Path | str | None
) -> dict[str, Any]:
    """Résultat d'un scan, prêt pour l'UI — rien n'est enrôlé ici.

    L'enrôlement reste un acte explicite : le scan propose, l'utilisateur
    dispose. Un scan qui enrôle tout seul finit par remplir le registre de
    dossiers jetables, comme l'a montré la pollution par les campagnes d'évals.
    """
    base = resolve_within_allowed(root, *extra)
    if not base.is_dir():
        msg = f"pas un dossier : {base}"
        raise FileNotFoundError(msg)
    depth = max(1, min(int(depth), MAX_SCAN_DEPTH))
    registered = {p.get("path") for p in load_registry()}
    candidates = crawl_projects(base, depth)
    return {
        "root": str(base),
        "depth": depth,
        "truncated": len(candidates) >= MAX_SCAN_RESULTS,
        "candidates": [
            {
                "path": str(c.path),
                "name": c.path.name,
                "managed": c.managed,
                "registered": str(c.path) in registered,
            }
            for c in candidates
        ],
    }


def browse(path: str | Path | None = None, *extra: Path | str | None) -> dict[str, Any]:
    """Liste les sous-dossiers d'un chemin, pour une navigation manuelle.

    Lecture seule et sans récursion : l'UI descend un niveau à la fois. Les
    liens symboliques ne sont pas suivis et les dossiers cachés sont écartés —
    un sélecteur de projet n'a rien à faire dans ``.git`` ou ``.venv``.
    """
    base = resolve_within_allowed(path, *extra)
    if not base.is_dir():
        msg = f"pas un dossier : {base}"
        raise FileNotFoundError(msg)
    try:
        children = sorted(
            (p for p in base.iterdir() if p.is_dir() and not p.is_symlink()),
            key=lambda p: p.name.lower(),
        )
    except (PermissionError, OSError) as exc:
        msg = f"dossier illisible : {base}"
        raise PermissionError(msg) from exc
    entries = [
        {
            "name": p.name,
            "path": str(p),
            "isProject": looks_grimoire(p),
            "managed": is_grimoire_managed(p),
        }
        for p in children
        if not p.name.startswith(".") and not _is_excluded_dir(p.name)
    ]
    parent = base.parent
    return {
        "path": str(base),
        "parent": str(parent) if parent != base else None,
        "home": str(Path.home()),
        "isProject": looks_grimoire(base),
        "entries": entries,
    }
