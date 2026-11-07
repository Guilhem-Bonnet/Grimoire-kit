"""Tests de ``memory_inclusions`` rempli par le backend lexical (#1134).

Rouge-avant : ``build_context_bundle`` ne listait que les types déclarés par
``memory-policy.yaml`` — aucune entrée de mémoire, donc un agent devait lire
les fichiers de mémoire en entier. Les tests ci-dessous vérifient que les
entrées liées aux fichiers et aux mots de la tâche sont servies, sous le
budget du ``context-contract.yaml``, avec une référence pour lire la suite.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from grimoire.core.agentic_standard import build_context_bundle
from grimoire.memory.backends.lexical import LexicalMemoryBackend

_TASK_ID = "context-memory"
_TASK_FILE = "src/grimoire/core/agentic_standard.py"
_STORE_REL = Path("_grimoire/_memory/grimoire_lexical.sqlite3")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


def _write_task_board(tmp_path: Path, task: str) -> None:
    _write(
        tmp_path / "_grimoire/standard/task-board.yaml",
        f"""
        $schema: "grimoire-agentic-standard-task-board/v1"
        tasks:
        {task}
        """,
    )


def _write_memory_policy(tmp_path: Path) -> None:
    _write(
        tmp_path / "_grimoire/standard/memory-policy.yaml",
        """
        $schema: "grimoire-agentic-standard-memory-policy/v1"
        memory_types:
          - type: "project"
            scope: "Repository-local persistent memory."
            freshness: "review-before-critical-use"
            trust_level: "high"
        """,
    )


def _write_context_contract(
    tmp_path: Path,
    *,
    max_memory_items: int = 8,
    max_memory_tokens: int | None = 1200,
) -> None:
    token_line = "" if max_memory_tokens is None else f"  max_memory_tokens: {max_memory_tokens}\n"
    _write(
        tmp_path / "_grimoire/standard/context-contract.yaml",
        (
            '$schema: "grimoire-agentic-standard-context-contract/v1"\n'
            "source_priority:\n"
            "  - task_board\n"
            "  - project_memory\n"
            "budget:\n"
            "  max_context_items: 24\n"
            f"  max_memory_items: {max_memory_items}\n"
            f"{token_line}"
            "  max_knowledge_sources: 8\n"
            "redaction:\n"
            "  required: true\n"
            "  forbidden_data_classes:\n"
            "    - secrets\n"
        ),
    )


def _store(
    tmp_path: Path,
    entries: list[dict[str, object]],
    *,
    store_rel: Path = _STORE_REL,
) -> list[str]:
    """Écrit *entries* dans le store lexical et rend leurs identifiants."""
    backend = LexicalMemoryBackend(tmp_path / store_rel)
    try:
        ids: list[str] = []
        for entry in entries:
            stored = backend.store(
                str(entry["text"]),
                tags=tuple(entry.get("tags") or ()),
                metadata=dict(entry.get("metadata") or {}),
            )
            ids.append(stored.id)
        return ids
    finally:
        backend.close()


def _bundle(tmp_path: Path) -> dict[str, object]:
    return build_context_bundle(tmp_path, task_id=_TASK_ID).data


def test_entry_linked_to_a_task_file_is_included(tmp_path: Path) -> None:
    """Critère #1134 : une entrée liée à un fichier de la tâche figure dans le bundle."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Remplir le bundle de contexte"
            status: "in_progress"
            files:
              - "{_TASK_FILE}"
            acceptance_criteria:
              - "Le bundle cite l'entrée pertinente."
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path)
    (entry_id,) = _store(
        tmp_path,
        [{
            "text": "Le bundle ne doit pas lire la mémoire en entier.",
            "metadata": {"type": "project", "source_uri": _TASK_FILE},
        }],
    )

    data = _bundle(tmp_path)
    inclusions = data["memory_inclusions"]

    assert [inclusion["id"] for inclusion in inclusions] == [entry_id]
    assert inclusions[0]["matched_files"] == [_TASK_FILE]
    assert inclusions[0]["type"] == "project"
    assert inclusions[0]["trust_level"] == "high"
    assert inclusions[0]["ref"] == f"{_STORE_REL.as_posix()}#{entry_id}"
    assert data["decision_inputs"]["memory_store_ref"] == _STORE_REL.as_posix()


def test_entry_matching_task_words_is_included(tmp_path: Path) -> None:
    """Une entrée que le texte de la tâche nomme est servie par le backend lexical."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Corriger le cache lexical du bundle"
            status: "in_progress"
            description: "Le cache lexical se vide au redémarrage."
            acceptance_criteria:
              - "Le cache lexical survit."
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path)
    (entry_id,) = _store(
        tmp_path,
        [{"text": "Le cache lexical se reconstruit depuis les entrées.", "metadata": {"type": "project"}}],
    )

    inclusions = _bundle(tmp_path)["memory_inclusions"]

    assert [inclusion["id"] for inclusion in inclusions] == [entry_id]
    assert inclusions[0]["matched_files"] == []


def test_retracted_or_superseded_entries_are_not_served(tmp_path: Path) -> None:
    """Une mémoire retirée ou remplacée n'entre pas dans le contexte d'une tâche."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Cache lexical"
            status: "in_progress"
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path)
    _store(
        tmp_path,
        [
            {"text": "Le cache lexical garde les faits retirés.", "metadata": {"type": "project", "retracted": {"at": "2026-01-01"}}},
            {"text": "Le cache lexical garde les faits remplacés.", "metadata": {"type": "project", "superseded_by": "other-id"}},
            {"text": "Le cache lexical survit aux redémarrages.", "metadata": {"type": "project"}},
        ],
    )

    inclusions = _bundle(tmp_path)["memory_inclusions"]

    assert len(inclusions) == 1
    assert inclusions[0]["text"] == "Le cache lexical survit aux redémarrages."


def test_memory_item_budget_is_enforced(tmp_path: Path) -> None:
    """Critère #1134 : le budget d'items du contrat est respecté."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Cache lexical"
            status: "in_progress"
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path, max_memory_items=2)
    _store(
        tmp_path,
        [
            {"text": f"Le cache lexical numéro {index} se reconstruit.", "metadata": {"type": "project"}}
            for index in range(6)
        ],
    )

    inclusions = _bundle(tmp_path)["memory_inclusions"]

    assert 0 < len(inclusions) <= 2


def test_memory_token_budget_is_enforced(tmp_path: Path) -> None:
    """Critère #1134 : le budget de jetons du contrat est respecté."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Cache lexical"
            status: "in_progress"
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path, max_memory_items=8, max_memory_tokens=40)
    _store(
        tmp_path,
        [
            {"text": ("Le cache lexical se reconstruit. " * 60), "metadata": {"type": "project"}}
            for _ in range(4)
        ],
    )

    inclusions = _bundle(tmp_path)["memory_inclusions"]

    assert inclusions
    assert sum(inclusion["token_estimate"] for inclusion in inclusions) <= 40
    for inclusion in inclusions:
        assert inclusion["token_estimate"] == max(1, (len(inclusion["text"]) + 3) // 4)


def test_missing_lexical_store_leaves_inclusions_empty_without_creating_one(tmp_path: Path) -> None:
    """Sans store lexical, le bundle reste valide : la mémoire est optionnelle."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Cache lexical"
            status: "in_progress"
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path)

    data = _bundle(tmp_path)

    assert data["memory_inclusions"] == []
    assert not (tmp_path / _STORE_REL).exists()


def test_collection_prefix_from_project_config_is_followed(tmp_path: Path) -> None:
    """Le store cherché est celui que le projet configure, pas un chemin figé."""
    _write_task_board(
        tmp_path,
        f"""
          - task_id: "{_TASK_ID}"
            title: "Cache lexical"
            status: "in_progress"
            files:
              - "{_TASK_FILE}"
        """,
    )
    _write_memory_policy(tmp_path)
    _write_context_contract(tmp_path)
    _write(
        tmp_path / "project-context.yaml",
        """
        project:
          name: "demo"
        memory:
          collection_prefix: "custom"
        """,
    )
    custom_store = Path("_grimoire/_memory/custom_lexical.sqlite3")
    (entry_id,) = _store(
        tmp_path,
        [{"text": "Le cache lexical du projet.", "metadata": {"type": "project", "source_uri": _TASK_FILE}}],
        store_rel=custom_store,
    )

    data = _bundle(tmp_path)

    assert [inclusion["id"] for inclusion in data["memory_inclusions"]] == [entry_id]
    assert data["decision_inputs"]["memory_store_ref"] == custom_store.as_posix()
