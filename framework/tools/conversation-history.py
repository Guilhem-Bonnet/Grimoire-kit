#!/usr/bin/env python3
"""
conversation-history.py — Conversation History Vectorization BMAD (BM-44 Story 5.4).
============================================================

Vectorise automatiquement l'historique conversationnel dans Qdrant
pour le retrieval cross-session. Au démarrage d'une session, les
conversations passées pertinentes sont retrouvées.

Modes :
  index   — Indexe une conversation terminée
  search  — Recherche des conversations passées pertinentes
  forget  — Supprime les vectors liés à un topic (RGPD)
  stats   — Statistiques de la collection conversations
  export  — Exporte l'historique en JSON

Usage :
  python3 conversation-history.py --project-root . index \\
    --session-id "sess-001" --agents "architect,dev" \\
    --summary "Discussion architecture microservices"
  python3 conversation-history.py --project-root . search \\
    --query "microservices architecture"
  python3 conversation-history.py --project-root . forget --topic "auth credentials"
  python3 conversation-history.py --project-root . stats

Qdrant optionnel (fallback JSON).

Références :
  - ChatGPT Memory: https://help.openai.com/en/articles/8590148-memory-in-chatgpt-faq
  - mem0 conversation memory: https://github.com/mem0ai/mem0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.conversation_history")

# ── Version ──────────────────────────────────────────────────────────────────

CONVERSATION_HISTORY_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

HISTORY_DIR = "_bmad-output/.conversation-history"
INDEX_FILE = "conversations.json"
COLLECTION_PREFIX = "conversations"
MAX_CONVERSATIONS = 50
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
DEFAULT_TOP_K = 5
SIMILARITY_THRESHOLD = 0.5


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ConversationEntry:
    """Une entrée de conversation indexée."""

    conversation_id: str = ""
    session_id: str = ""
    summary: str = ""
    agents: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    branch: str = "main"
    timestamp: str = ""
    token_count: int = 0
    decisions: list[str] = field(default_factory=list)
    key_insights: list[str] = field(default_factory=list)
    files_referenced: list[str] = field(default_factory=list)
    embedding_id: str = ""

    def __post_init__(self):
        if not self.conversation_id:
            self.conversation_id = f"conv-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ConversationEntry:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class SearchResult:
    """Résultat de recherche dans l'historique."""

    conversation: ConversationEntry
    score: float = 0.0
    match_method: str = "keyword"  # keyword | semantic


@dataclass
class HistoryStats:
    """Statistiques de l'historique."""

    total_conversations: int = 0
    total_tokens: int = 0
    agents_active: list[str] = field(default_factory=list)
    topics_count: dict[str, int] = field(default_factory=dict)
    branches_count: dict[str, int] = field(default_factory=dict)
    qdrant_available: bool = False
    oldest_conversation: str = ""
    newest_conversation: str = ""


# ── Embedding Provider ──────────────────────────────────────────────────────


class EmbeddingProvider:
    """Gère les embeddings pour la vectorisation."""

    def __init__(self):
        self._model = None
        self._available = False
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(EMBEDDING_MODEL)
            self._available = True
        except ImportError as _exc:
            _log.debug("ImportError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    @property
    def available(self) -> bool:
        return self._available

    def embed(self, text: str) -> list[float]:
        if not self._available or not self._model:
            # Fallback: simple hash-based pseudo-embedding for keyword search
            return self._hash_embed(text)
        return self._model.encode(text).tolist()

    def _hash_embed(self, text: str) -> list[float]:
        """Pseudo-embedding basé sur hash pour le fallback."""
        h = hashlib.sha256(text.encode()).hexdigest()
        return [int(h[i:i + 2], 16) / 255.0 for i in range(0, min(EMBEDDING_DIM * 2, len(h)), 2)]


# ── Qdrant Backend ──────────────────────────────────────────────────────────


class QdrantHistoryBackend:
    """Backend Qdrant pour le stockage vectoriel des conversations."""

    def __init__(self, project_name: str = "bmad", qdrant_path: str = ""):
        self._client = None
        self._available = False
        self._collection = f"{project_name}-{COLLECTION_PREFIX}"
        self._qdrant_path = qdrant_path

        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            if qdrant_path:
                self._client = QdrantClient(path=qdrant_path)
            else:
                self._client = QdrantClient(host="localhost", port=6333, timeout=5)

            # Ensure collection exists
            collections = [c.name for c in self._client.get_collections().collections]
            if self._collection not in collections:
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(
                        size=EMBEDDING_DIM,
                        distance=Distance.COSINE,
                    ),
                )
            self._available = True
        except Exception as _exc:
            _log.debug("Exception suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    @property
    def available(self) -> bool:
        return self._available

    def store(self, conversation_id: str, vector: list[float], metadata: dict) -> bool:
        if not self._available or not self._client:
            return False
        try:
            from qdrant_client.models import PointStruct
            point_id = uuid.uuid5(uuid.NAMESPACE_DNS, conversation_id).hex
            self._client.upsert(
                collection_name=self._collection,
                points=[PointStruct(
                    id=point_id,
                    vector=vector[:EMBEDDING_DIM],
                    payload=metadata,
                )],
            )
            return True
        except Exception:
            return False

    def search(self, vector: list[float], top_k: int = DEFAULT_TOP_K) -> list[dict]:
        if not self._available or not self._client:
            return []
        try:
            results = self._client.search(
                collection_name=self._collection,
                query_vector=vector[:EMBEDDING_DIM],
                limit=top_k,
                score_threshold=SIMILARITY_THRESHOLD,
            )
            return [{"payload": r.payload, "score": r.score} for r in results]
        except Exception:
            return []

    def delete_by_topic(self, topic: str) -> int:
        if not self._available or not self._client:
            return 0
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            result = self._client.delete(
                collection_name=self._collection,
                points_selector=Filter(
                    must=[FieldCondition(key="topics", match=MatchValue(value=topic))]
                ),
            )
            return 1 if result else 0
        except Exception:
            return 0

    def count(self) -> int:
        if not self._available or not self._client:
            return 0
        try:
            info = self._client.get_collection(self._collection)
            return info.points_count
        except Exception:
            return 0


# ── JSON Fallback Backend ──────────────────────────────────────────────────


class JSONHistoryBackend:
    """Backend JSON local pour le stockage d'historique (fallback)."""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = history_dir / INDEX_FILE

    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _save_index(self, entries: list[dict]) -> None:
        self.index_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def store(self, entry: ConversationEntry) -> bool:
        entries = self._load_index()
        # Deduplicate
        entries = [e for e in entries if e.get("conversation_id") != entry.conversation_id]
        entries.append(entry.to_dict())
        # Cap
        if len(entries) > MAX_CONVERSATIONS:
            entries = entries[-MAX_CONVERSATIONS:]
        self._save_index(entries)
        return True

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[SearchResult]:
        """Keyword search in JSON index."""
        entries = self._load_index()
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for data in entries:
            entry = ConversationEntry.from_dict(data)
            # Score based on keyword overlap
            text = f"{entry.summary} {' '.join(entry.topics)} {' '.join(entry.decisions)}"
            text_words = set(text.lower().split())
            overlap = len(query_words & text_words)
            if overlap > 0:
                score = overlap / max(len(query_words), 1)
                results.append(SearchResult(
                    conversation=entry,
                    score=round(score, 3),
                    match_method="keyword",
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def forget(self, topic: str) -> int:
        entries = self._load_index()
        original_count = len(entries)
        entries = [
            e for e in entries
            if topic.lower() not in " ".join(e.get("topics", [])).lower()
            and topic.lower() not in e.get("summary", "").lower()
        ]
        self._save_index(entries)
        return original_count - len(entries)

    def get_all(self) -> list[ConversationEntry]:
        return [ConversationEntry.from_dict(e) for e in self._load_index()]


# ── Conversation History Manager ─────────────────────────────────────────────


class ConversationHistoryManager:
    """
    Gère l'historique conversationnel BMAD.

    Utilise Qdrant si disponible, sinon fallback JSON.
    """

    def __init__(
        self,
        project_root: Path,
        project_name: str = "bmad",
        qdrant_path: str = "",
    ):
        self.project_root = project_root
        self.history_dir = project_root / HISTORY_DIR
        self.json_backend = JSONHistoryBackend(self.history_dir)
        self.embedding_provider = EmbeddingProvider()
        self.qdrant_backend = QdrantHistoryBackend(
            project_name=project_name,
            qdrant_path=qdrant_path,
        )

    def index(self, entry: ConversationEntry) -> bool:
        """Indexe une conversation."""
        # Always store in JSON
        self.json_backend.store(entry)

        # If Qdrant available, also vectorize
        if self.qdrant_backend.available and self.embedding_provider.available:
            text = f"{entry.summary} {' '.join(entry.topics)} {' '.join(entry.decisions)}"
            vector = self.embedding_provider.embed(text)
            self.qdrant_backend.store(
                entry.conversation_id,
                vector,
                entry.to_dict(),
            )
            entry.embedding_id = entry.conversation_id

        return True

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[SearchResult]:
        """Recherche des conversations pertinentes."""
        # Try semantic search first
        if self.qdrant_backend.available and self.embedding_provider.available:
            vector = self.embedding_provider.embed(query)
            qdrant_results = self.qdrant_backend.search(vector, top_k)
            if qdrant_results:
                results = []
                for r in qdrant_results:
                    entry = ConversationEntry.from_dict(r["payload"])
                    results.append(SearchResult(
                        conversation=entry,
                        score=round(r["score"], 3),
                        match_method="semantic",
                    ))
                return results

        # Fallback to keyword search
        return self.json_backend.search(query, top_k)

    def forget(self, topic: str) -> int:
        """Supprime les conversations liées à un topic (RGPD)."""
        count = self.json_backend.forget(topic)
        if self.qdrant_backend.available:
            count += self.qdrant_backend.delete_by_topic(topic)
        return count

    def stats(self) -> HistoryStats:
        """Retourne les statistiques."""
        entries = self.json_backend.get_all()

        agents: set[str] = set()
        topics_count: dict[str, int] = {}
        branches_count: dict[str, int] = {}
        total_tokens = 0

        for e in entries:
            agents.update(e.agents)
            for t in e.topics:
                topics_count[t] = topics_count.get(t, 0) + 1
            branches_count[e.branch] = branches_count.get(e.branch, 0) + 1
            total_tokens += e.token_count

        timestamps = [e.timestamp for e in entries if e.timestamp]

        return HistoryStats(
            total_conversations=len(entries),
            total_tokens=total_tokens,
            agents_active=sorted(agents),
            topics_count=topics_count,
            branches_count=branches_count,
            qdrant_available=self.qdrant_backend.available,
            oldest_conversation=min(timestamps) if timestamps else "",
            newest_conversation=max(timestamps) if timestamps else "",
        )

    def export(self) -> list[dict]:
        """Exporte tout l'historique en JSON."""
        entries = self.json_backend.get_all()
        return [e.to_dict() for e in entries]


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_conversation_history(
    project_root: str,
    action: str = "search",
    query: str = "",
    session_id: str = "",
    summary: str = "",
    agents: str = "",
    topic: str = "",
    top_k: int = DEFAULT_TOP_K,
) -> dict:
    """
    MCP tool `bmad_conversation_history` — historique conversationnel.
    """
    root = Path(project_root).resolve()
    manager = ConversationHistoryManager(root)

    if action == "search":
        if not query:
            return {"error": "Le paramètre 'query' est requis"}
        results = manager.search(query, top_k)
        return {
            "results": [
                {"conversation": r.conversation.to_dict(), "score": r.score, "method": r.match_method}
                for r in results
            ],
            "total": len(results),
        }
    elif action == "index":
        agent_list = [a.strip() for a in agents.split(",") if a.strip()] if agents else []
        entry = ConversationEntry(
            session_id=session_id,
            summary=summary,
            agents=agent_list,
        )
        manager.index(entry)
        return {"success": True, "conversation_id": entry.conversation_id}
    elif action == "forget":
        if not topic:
            return {"error": "Le paramètre 'topic' est requis"}
        count = manager.forget(topic)
        return {"forgotten": count, "topic": topic}
    elif action == "stats":
        s = manager.stats()
        return asdict(s)
    else:
        return {"error": f"Action inconnue: {action}"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conversation History — Historique conversationnel vectorisé BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"conversation-history {CONVERSATION_HISTORY_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # index
    idx_p = sub.add_parser("index", help="Indexer une conversation")
    idx_p.add_argument("--session-id", default="", help="ID de session")
    idx_p.add_argument("--summary", required=True, help="Résumé de la conversation")
    idx_p.add_argument("--agents", default="", help="Agents impliqués (séparés par virgule)")
    idx_p.add_argument("--topics", default="", help="Topics (séparés par virgule)")
    idx_p.add_argument("--branch", default="main", help="Branche de conversation")
    idx_p.add_argument("--decisions", default="", help="Décisions prises (séparées par virgule)")
    idx_p.add_argument("--json", action="store_true", help="Output JSON")

    # search
    search_p = sub.add_parser("search", help="Rechercher des conversations")
    search_p.add_argument("--query", required=True, help="Requête de recherche")
    search_p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Nombre de résultats")
    search_p.add_argument("--json", action="store_true", help="Output JSON")

    # forget
    forget_p = sub.add_parser("forget", help="Oublier un topic (RGPD)")
    forget_p.add_argument("--topic", required=True, help="Topic à oublier")

    # stats
    sub.add_parser("stats", help="Statistiques de l'historique")

    # export
    export_p = sub.add_parser("export", help="Exporter l'historique")
    export_p.add_argument("--output", type=Path, default=None, help="Fichier de sortie")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    manager = ConversationHistoryManager(project_root)

    if args.command == "index":
        agent_list = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else []
        topic_list = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else []
        decision_list = [d.strip() for d in args.decisions.split(",") if d.strip()] if args.decisions else []

        entry = ConversationEntry(
            session_id=args.session_id,
            summary=args.summary,
            agents=agent_list,
            topics=topic_list,
            branch=args.branch,
            decisions=decision_list,
        )
        manager.index(entry)

        if getattr(args, "json", False):
            print(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"\n  ✅ Conversation indexée : {entry.conversation_id}")
            print(f"    Summary : {entry.summary[:80]}")
            print(f"    Agents  : {', '.join(entry.agents) or '-'}")
            print(f"    Topics  : {', '.join(entry.topics) or '-'}\n")

    elif args.command == "search":
        results = manager.search(args.query, args.top_k)
        if getattr(args, "json", False):
            out = [{"conversation": r.conversation.to_dict(), "score": r.score, "method": r.match_method}
                   for r in results]
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"\n  🔍 Résultats pour : \"{args.query}\" ({len(results)})")
            print(f"  {'─' * 55}")
            if not results:
                print("  (aucun résultat)")
            for r in results:
                method_icon = "🧠" if r.match_method == "semantic" else "🔤"
                print(f"    {method_icon} [{r.score:.2f}] {r.conversation.summary[:60]}")
                if r.conversation.agents:
                    print(f"           Agents: {', '.join(r.conversation.agents)}")
            print()

    elif args.command == "forget":
        count = manager.forget(args.topic)
        print(f"\n  🗑️  {count} conversations oubliées pour le topic \"{args.topic}\"\n")

    elif args.command == "stats":
        s = manager.stats()
        print("\n  📊 Conversation History Stats")
        print(f"  {'─' * 40}")
        print(f"  Total conversations : {s.total_conversations}")
        print(f"  Total tokens        : {s.total_tokens:,}")
        print(f"  Qdrant disponible   : {'✅' if s.qdrant_available else '❌'}")
        print(f"  Agents actifs       : {', '.join(s.agents_active) or '-'}")
        if s.oldest_conversation:
            print(f"  Plus ancienne       : {s.oldest_conversation}")
        if s.newest_conversation:
            print(f"  Plus récente        : {s.newest_conversation}")
        if s.topics_count:
            print("\n  Topics :")
            for topic, count in sorted(s.topics_count.items(), key=lambda x: -x[1])[:10]:
                print(f"    {topic:30s} : {count}")
        print()

    elif args.command == "export":
        data = manager.export()
        output = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
            print(f"\n  ✅ Exporté : {args.output} ({len(data)} conversations)\n")
        else:
            print(output)


if __name__ == "__main__":
    main()
