"""Entrées de mémoire injectées dans le ``context-bundle`` (#1134).

``memory_inclusions`` ne listait que les types déclarés par
``memory-policy.yaml`` : aucune entrée n'y figurait, et un agent devait lire
les fichiers de mémoire en entier (OpenKenshi mesurait 62,8 Ko lus par
session). Ce module remplit la section avec les entrées *pertinentes* — celles
liées aux fichiers et aux mots de la tâche — trouvées par le backend lexical
déjà présent (``_grimoire/_memory/<prefix>_lexical.sqlite3``), sous le budget
du ``context-contract.yaml``.

Chaque inclusion porte son ``id`` et sa ``ref`` : l'agent lit l'extrait, puis
ouvre l'entrée complète à la demande. Aucune lecture existante n'est retirée
par ce module — il ajoute un canal borné, il ne remplace rien.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.supersession import carries_state_mark

__all__ = [
    "DEFAULT_COLLECTION_PREFIX",
    "DEFAULT_MEMORY_TOKEN_BUDGET",
    "TaskMemoryTerms",
    "extract_task_memory_terms",
    "lexical_store_path",
    "memory_store_ref",
    "select_memory_inclusions",
]

#: Préfixe de collection par défaut (voir ``MemoryConfig.collection_prefix``).
DEFAULT_COLLECTION_PREFIX = "grimoire"
#: Même convention d'estimation que ``grimoire.missions.recall`` : ~4 caractères
#: par token. Un rappel est une estimation déclarée, pas un décompte de tokenizer.
CHARS_PER_TOKEN = 4
#: Budget de jetons par défaut quand le context-contract n'en déclare pas : un
#: rappel, pas un rapport.
DEFAULT_MEMORY_TOKEN_BUDGET = 1200
#: Longueur d'un extrait (~100 tokens) : assez pour juger de la pertinence,
#: jamais assez pour remplacer la lecture complète.
EXCERPT_CHARS = 400
#: Nombre d'items par défaut quand le context-contract ne le déclare pas.
DEFAULT_MEMORY_ITEM_BUDGET = 8

#: Champs du board où une tâche peut nommer explicitement ses fichiers.
_FILE_FIELDS = ("files", "paths", "touched_files", "deliverables")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "into", "your",
    "une", "des", "les", "dans", "pour", "avec", "sur", "aux", "que",
    "qui", "sans", "est", "son", "ses", "par", "plus", "tout", "aussi",
    "leur", "cette", "vers", "sous", "afin", "ainsi", "mais", "comme",
    "dont", "elle", "ils", "nous", "vous", "ont", "être", "avoir",
})
_WORD_RE = re.compile(r"[a-zà-öø-ÿ0-9]+")
#: Un chemin plausible : au moins un point (extension) et des séparateurs.
_PATH_RE = re.compile(r"[A-Za-z0-9_@.\-/\\]+\.[A-Za-z0-9]+")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")


@dataclass(frozen=True, slots=True)
class TaskMemoryTerms:
    """Ce que la tâche nomme : ses fichiers, et les mots qui la décrivent."""

    files: tuple[str, ...] = ()
    words: tuple[str, ...] = ()

    @property
    def query(self) -> str:
        """Requête du backend lexical — mots d'abord, puis fichiers."""
        return " ".join([*self.words, *self.files]).strip()


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            kept.append(value)
    return tuple(kept)


def _as_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _normalize_path(value: str) -> str:
    """Un chemin comparable : sans backticks, ni ``./``, ni suffixe ``:ligne``."""
    candidate = value.strip().strip("`'\"()[]<>,;")
    candidate = _LINE_SUFFIX_RE.sub("", candidate)
    candidate = candidate.replace("\\", "/").lstrip("./")
    candidate = candidate.rstrip(".,;:")
    if not candidate or _URL_RE.match(candidate):
        return ""
    return candidate


def extract_task_memory_terms(task: Mapping[str, Any]) -> TaskMemoryTerms:
    """Les fichiers nommés par *task* et les mots de son brief.

    Un fichier peut être déclaré (``files``, ``paths``, ``touched_files``,
    ``deliverables``) ou écrit dans le titre, la description, les critères
    d'acceptation, les garde-fous ou les preuves attendues.
    """
    texts: list[str] = []
    for key in ("title", "description"):
        texts.extend(_as_texts(task.get(key)))
    for key in ("acceptance_criteria", "guardrails", "expected_evidence"):
        texts.extend(_as_texts(task.get(key)))

    files: list[str] = []
    for key in _FILE_FIELDS:
        files.extend(_normalize_path(item) for item in _as_texts(task.get(key)))
    for text in texts:
        files.extend(_normalize_path(match) for match in _PATH_RE.findall(text))

    words = [
        word
        for word in _WORD_RE.findall(" ".join(texts).lower())
        if len(word) > 2 and word not in _STOPWORDS
    ]
    return TaskMemoryTerms(files=_dedupe(files), words=_dedupe(words))


def lexical_store_path(project_root: Path) -> Path:
    """Le store du backend lexical du projet, qu'il existe ou non.

    Suit ``memory.collection_prefix`` de ``project-context.yaml`` quand il est
    lisible ; retombe sur le préfixe par défaut sinon. Aucun fichier n'est créé.
    """
    root = project_root.resolve()
    prefix = DEFAULT_COLLECTION_PREFIX
    config_file = root / "project-context.yaml"
    if config_file.is_file():
        try:
            prefix = GrimoireConfig.from_yaml(config_file).memory.collection_prefix or prefix
        except Exception:  # la mémoire reste une couche optionnelle
            prefix = DEFAULT_COLLECTION_PREFIX
    return root / "_grimoire" / "_memory" / f"{prefix}_lexical.sqlite3"


def memory_store_ref(project_root: Path) -> str:
    """Le store lexical relatif à la racine — la référence pour lire une entrée entière."""
    root = project_root.resolve()
    return lexical_store_path(root).relative_to(root).as_posix()


def _entry_paths(entry: MemoryEntry) -> tuple[str, ...]:
    """Les chemins qu'une entrée désigne : source déclarée, tags, puis texte."""
    raw: list[str] = []
    metadata = entry.metadata or {}
    for key in ("source_uri", "source"):
        value = metadata.get(key)
        if isinstance(value, str):
            raw.append(value)
    raw.extend(str(tag) for tag in entry.tags)
    raw.extend(_PATH_RE.findall(entry.text))
    return _dedupe(_normalize_path(value) for value in raw)


def _path_matches(task_file: str, entry_file: str) -> bool:
    """Vrai quand les deux chemins désignent le même fichier (absolu ou suffixe)."""
    if not task_file or not entry_file:
        return False
    left = task_file.rstrip("/")
    right = entry_file.rstrip("/")
    if not left or not right:
        return False
    return left == right or left.endswith(f"/{right}") or right.endswith(f"/{left}")


def _matched_files(terms: TaskMemoryTerms, entry: MemoryEntry) -> tuple[str, ...]:
    entry_paths = _entry_paths(entry)
    return tuple(
        task_file
        for task_file in terms.files
        if any(_path_matches(task_file, entry_path) for entry_path in entry_paths)
    )


def _budget_int(budget: Mapping[str, Any], key: str, default: int) -> int:
    try:
        value = int(budget.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(value, 0)


def _memory_type(entry: MemoryEntry) -> str:
    metadata = entry.metadata or {}
    return str(metadata.get("type") or metadata.get("memory_type") or "")


def _trust_level(memory_type: str, memory_policy: Mapping[str, Any]) -> str:
    for declared in memory_policy.get("memory_types", []) or []:
        if not isinstance(declared, Mapping):
            continue
        if str(declared.get("type") or declared.get("memory_id") or "") == memory_type:
            return str(declared.get("trust_level") or "")
    return ""


def _read_entries(store: Path, query: str, *, search_limit: int) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
    """Toutes les entrées vivantes, et le classement lexical pour *query*.

    Le backend lexical est celui qui existe déjà : rien n'est créé ni migré ici.
    Une erreur de lecture (fichier corrompu, schéma absent) rend un bundle sans
    inclusions — la mémoire est une couche optionnelle, jamais un prérequis.
    """
    from grimoire.memory.backends.lexical import LexicalMemoryBackend

    backend: LexicalMemoryBackend | None = None
    try:
        backend = LexicalMemoryBackend(store)
        everything = [entry for entry in backend.get_all() if not carries_state_mark(entry.metadata)]
        searched = backend.search(query, limit=search_limit) if query else []
        searched = [entry for entry in searched if not carries_state_mark(entry.metadata)]
    except (sqlite3.Error, OSError, ValueError):
        return [], []
    finally:
        if backend is not None:
            backend.close()
    return everything, searched


def _excerpt(entry: MemoryEntry, max_chars: int) -> str:
    text = " ".join(entry.text.split())
    return text[:max_chars].rstrip()


def select_memory_inclusions(
    project_root: Path,
    task: Mapping[str, Any],
    *,
    budget: Mapping[str, Any] | None = None,
    memory_policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Les entrées de mémoire pertinentes pour *task*, sous le budget déclaré.

    Ordre déterministe : d'abord les entrées liées à un fichier de la tâche (le
    lien le plus fort), puis le classement BM25 du backend lexical ; à égalité,
    l'identifiant départage. Les entrées remplacées ou retirées ne sont jamais
    servies.
    """
    budget = budget or {}
    memory_policy = memory_policy or {}
    max_items = _budget_int(budget, "max_memory_items", DEFAULT_MEMORY_ITEM_BUDGET)
    max_tokens = _budget_int(budget, "max_memory_tokens", DEFAULT_MEMORY_TOKEN_BUDGET)
    if max_items <= 0 or max_tokens <= 0:
        return []

    terms = extract_task_memory_terms(task)
    if not terms.files and not terms.words:
        return []

    store = lexical_store_path(project_root)
    if not store.is_file():
        return []

    everything, searched = _read_entries(store, terms.query, search_limit=max(max_items * 4, max_items))
    if not everything and not searched:
        return []

    ranked: list[tuple[MemoryEntry, tuple[str, ...]]] = []
    seen: set[str] = set()
    linked: list[tuple[MemoryEntry, tuple[str, ...]]] = []
    for entry in everything:
        matched = _matched_files(terms, entry)
        if matched:
            linked.append((entry, matched))
    linked.sort(key=lambda pair: (-len(pair[1]), pair[0].id))
    for entry, matched in linked:
        seen.add(entry.id)
        ranked.append((entry, matched))
    for entry in searched:
        if entry.id in seen:
            continue
        seen.add(entry.id)
        ranked.append((entry, ()))

    store_ref = store.relative_to(project_root.resolve()).as_posix()
    inclusions: list[dict[str, Any]] = []
    used_tokens = 0
    for entry, matched in ranked:
        if len(inclusions) >= max_items:
            break
        remaining = max_tokens - used_tokens
        if remaining <= 0:
            break
        excerpt = _excerpt(entry, min(EXCERPT_CHARS, remaining * CHARS_PER_TOKEN))
        if not excerpt:
            continue
        tokens = max(1, (len(excerpt) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)
        if tokens > remaining:  # l'arrondi ne doit jamais déborder le budget
            continue
        memory_type = _memory_type(entry)
        inclusions.append({
            "id": entry.id,
            "type": memory_type,
            "trust_level": _trust_level(memory_type, memory_policy),
            "text": excerpt,
            "tags": list(entry.tags),
            "matched_files": list(matched),
            "ref": f"{store_ref}#{entry.id}",
            "token_estimate": tokens,
        })
        used_tokens += tokens
    return inclusions
