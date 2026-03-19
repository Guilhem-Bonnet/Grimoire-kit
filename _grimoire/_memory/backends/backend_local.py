"""
Backend Local — JSON fichier, zéro dépendance.

Stockage simple dans memory.json. Recherche plein texte (mots-clés).
Parfait pour débuter ou si aucune dépendance supplémentaire n'est souhaitée.
"""

from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path


_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory"
_MEMORY_FILE = _MEMORY_DIR / "memory.json"


class LocalBackend:
    """Backend JSON fichier — aucune dépendance externe."""

    def __init__(self, memory_file: Path | None = None):
        self._file = memory_file or _MEMORY_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._data: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ I/O
    def _load(self) -> None:
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self._data = []

    def _save(self) -> None:
        with open(self._file, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)

    # --------------------------------------------------------------- CONTRAT
    def add(self, text: str, user_id: str = "", metadata: dict | None = None) -> dict:
        entry = {
            "id": str(uuid.uuid4()),
            "memory": text,
            "user_id": user_id or "global",
            "metadata": metadata or {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._data.append(entry)
        self._save()
        return entry

    def search(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        """Recherche basique par correspondance de mots-clés (case-insensitive)."""
        keywords = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[float, dict]] = []
        for entry in self._data:
            text_lower = entry.get("memory", "").lower()
            words = set(re.findall(r"\w+", text_lower))
            score = len(keywords & words) / max(len(keywords), 1)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:limit]]

    def get_all(self, user_id: str = "") -> list[dict]:
        if not user_id:
            return list(self._data)
        return [e for e in self._data if e.get("user_id") == user_id]

    def count(self) -> int:
        return len(self._data)

    def status(self) -> dict:
        return {
            "backend": "local",
            "file": str(self._file),
            "entries": len(self._data),
            "search": "keyword",
        }
