"""Embedded Tantivy memory backend — BM25 with French + English stemming.

Tantivy is a Rust full-text search library (Lucene-class); the ``tantivy``
PyPI package ships prebuilt bindings.  Compared to the FTS5 lexical backend
this adds language stemming (``harmonisé`` matches ``harmonisation``) and
scales to much larger corpora — the intended tier for code/docs indexing.

Install with::

    pip install grimoire-kit[search]
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.taxonomy import build_taxonomy, entry_matches_filters

_TEXT_FIELDS = ["text", "text_en", "text_fr", "text_folded"]


def _fold(text: str) -> str:
    """Strip diacritics (NFD, drop combining marks) for accent-insensitive matching."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def _require_tantivy() -> Any:
    try:
        import tantivy

        return tantivy
    except ImportError:
        raise ImportError(
            "tantivy is not installed. Run:\n  pip install grimoire-kit[search]"
        ) from None


def _first(values: Any) -> str:
    """Tantivy stores every field as a list — unwrap the first value."""
    if isinstance(values, list):
        return str(values[0]) if values else ""
    return str(values or "")


class TantivyMemoryBackend(MemoryBackend):
    """Tantivy-based memory backend with BM25 and FR+EN stemming.

    Usage::

        backend = TantivyMemoryBackend(Path("/tmp/tantivy-index"))
        entry = backend.store("fait important")
    """

    def __init__(self, index_dir: Path) -> None:
        tantivy = _require_tantivy()
        self._tantivy = tantivy
        self._dir = index_dir
        self._lock = threading.RLock()
        index_dir.mkdir(parents=True, exist_ok=True)
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("id", stored=True, tokenizer_name="raw")
        builder.add_text_field("text", stored=True)
        builder.add_text_field("text_en", stored=False, tokenizer_name="en_stem")
        builder.add_text_field("text_fr", stored=False, tokenizer_name="fr_stem")
        builder.add_text_field("text_folded", stored=False)
        builder.add_text_field("user_id", stored=True, tokenizer_name="raw")
        builder.add_text_field("payload", stored=True, tokenizer_name="raw")
        self._schema = builder.build()
        self._index = tantivy.Index(self._schema, path=str(index_dir))

    # ── Internal helpers ──────────────────────────────────────────────────

    def _writer(self) -> Any:
        """Acquire the single-writer directory lock; caller must drop the ref after commit."""
        return self._index.writer()

    def _add_document(self, writer: Any, entry_id: str, text: str, user_id: str, payload: dict[str, Any]) -> None:
        writer.add_document(self._tantivy.Document(
            id=entry_id,
            text=text,
            text_en=text,
            text_fr=text,
            text_folded=_fold(text),
            user_id=user_id,
            payload=json.dumps(payload, ensure_ascii=False),
        ))

    def _commit(self, writer: Any) -> None:
        """Commit and release the writer lock, then refresh the searcher."""
        writer.commit()
        del writer
        self._index.reload()

    def _doc_to_entry(self, doc: dict[str, Any], score: float = 0.0) -> MemoryEntry:
        try:
            payload = json.loads(_first(doc.get("payload")) or "{}")
        except json.JSONDecodeError:
            payload = {}
        return MemoryEntry(
            id=_first(doc.get("id")),
            text=_first(doc.get("text")),
            user_id=_first(doc.get("user_id")) or "global",
            tags=tuple(payload.get("tags") or ()),
            metadata=payload.get("metadata") or {},
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            score=score,
        )

    def _match_all_entries(self) -> list[MemoryEntry]:
        searcher = self._index.searcher()
        query = self._tantivy.Query.all_query()
        hits = searcher.search(query, max(searcher.num_docs, 1)).hits
        entries = [self._doc_to_entry(searcher.doc(address).to_dict()) for _, address in hits]
        entries.sort(key=lambda e: (e.created_at, e.id))
        return entries

    # ── Contract ──────────────────────────────────────────────────────────

    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry_id = str(uuid.uuid4())
        uid = user_id or "global"
        payload = {"tags": list(tags), "metadata": metadata or {}, "created_at": ts, "updated_at": ""}
        with self._lock:
            writer = self._writer()
            self._add_document(writer, entry_id, text, uid, payload)
            self._commit(writer)
        return MemoryEntry(id=entry_id, text=text, user_id=uid, tags=tags, metadata=metadata or {}, created_at=ts)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        with self._lock:
            searcher = self._index.searcher()
            query = self._tantivy.Query.term_query(self._schema, "id", entry_id)
            hits = searcher.search(query, 1).hits
            if not hits:
                return None
            return self._doc_to_entry(searcher.doc(hits[0][1]).to_dict())

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        tokens = re.findall(r"\w+", query.lower())
        if not tokens:
            return []
        # Add folded variants so accented and unaccented queries both match.
        folded = [_fold(token) for token in tokens]
        expression = " OR ".join(dict.fromkeys(tokens + folded))
        with self._lock:
            searcher = self._index.searcher()
            parsed = self._index.parse_query(expression, _TEXT_FIELDS)
            # Over-fetch so post-hoc user filtering can still fill the limit.
            fetch = limit if not user_id else max(limit * 10, limit)
            hits = searcher.search(parsed, fetch).hits
            results = [
                self._doc_to_entry(searcher.doc(address).to_dict(), score=float(score))
                for score, address in hits
            ]
        if user_id:
            results = [entry for entry in results if entry.user_id == user_id]
        return results[:limit]

    def search_filtered(
        self,
        query: str,
        *,
        user_id: str = "",
        limit: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        candidates = self.search(query, user_id=user_id, limit=max(limit * 10, limit))
        if not filters:
            return candidates[:limit]
        return [
            entry
            for entry in candidates
            if entry_matches_filters(
                entry,
                wing=filters.get("wing", ""),
                hall=filters.get("hall", ""),
                room=filters.get("room", ""),
            )
        ][:limit]

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        with self._lock:
            entries = self._match_all_entries()
        if user_id:
            entries = [entry for entry in entries if entry.user_id == user_id]
        return entries[offset:] if limit is None else entries[offset:offset + limit]

    def get_all_filtered(
        self,
        *,
        user_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        entries = self.get_all(user_id=user_id, offset=0, limit=None)
        if filters:
            entries = [
                entry
                for entry in entries
                if entry_matches_filters(
                    entry,
                    wing=filters.get("wing", ""),
                    hall=filters.get("hall", ""),
                    room=filters.get("room", ""),
                )
            ]
        return entries[offset:] if limit is None else entries[offset:offset + limit]

    def count(self) -> int:
        with self._lock:
            return int(self._index.searcher().num_docs)

    def health_check(self) -> BackendStatus:
        return BackendStatus(
            backend="tantivy-local",
            healthy=True,
            entries=self.count(),
            detail={"path": str(self._dir), "search": "tantivy-bm25", "stemming": "fr+en"},
        )

    def consolidate(self) -> int:
        """Remove exact duplicates (same text + user_id), keeping the oldest."""
        with self._lock:
            seen: set[tuple[str, str]] = set()
            duplicates: list[str] = []
            for entry in self._match_all_entries():
                key = (entry.text, entry.user_id)
                if key in seen:
                    duplicates.append(entry.id)
                else:
                    seen.add(key)
            if duplicates:
                writer = self._writer()
                for entry_id in duplicates:
                    writer.delete_documents("id", entry_id)
                self._commit(writer)
        return len(duplicates)

    def taxonomy(self, *, user_id: str = "", filters: dict[str, str] | None = None) -> dict[str, Any]:
        return build_taxonomy(self.get_all_filtered(user_id=user_id, filters=filters))

    # ── CRUD extensions ───────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            if self.recall(entry_id) is None:
                return False
            writer = self._writer()
            writer.delete_documents("id", entry_id)
            self._commit(writer)
        return True

    def update(self, entry_id: str, *, text: str | None = None, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry | None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            existing = self.recall(entry_id)
            if existing is None:
                return None
            new_text = text if text is not None else existing.text
            new_tags = tags if tags is not None else existing.tags
            new_metadata = metadata if metadata is not None else existing.metadata
            payload = {
                "tags": list(new_tags),
                "metadata": new_metadata,
                "created_at": existing.created_at,
                "updated_at": ts,
            }
            writer = self._writer()
            writer.delete_documents("id", entry_id)
            self._add_document(writer, entry_id, new_text, existing.user_id, payload)
            self._commit(writer)
        return MemoryEntry(
            id=entry_id, text=new_text, user_id=existing.user_id, tags=tuple(new_tags),
            metadata=new_metadata, created_at=existing.created_at, updated_at=ts,
        )

    def upsert(
        self,
        entry_id: str,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        uid = user_id or "global"
        with self._lock:
            existing = self.recall(entry_id)
            created_at = existing.created_at if existing is not None else ts
            updated_at = ts if existing is not None else ""
            payload = {"tags": list(tags), "metadata": metadata or {}, "created_at": created_at, "updated_at": updated_at}
            writer = self._writer()
            if existing is not None:
                writer.delete_documents("id", entry_id)
            self._add_document(writer, entry_id, text, uid, payload)
            self._commit(writer)
        return MemoryEntry(
            id=entry_id, text=text, user_id=uid, tags=tags, metadata=metadata or {},
            created_at=created_at, updated_at=updated_at,
        )
