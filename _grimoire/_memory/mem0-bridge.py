#!/usr/bin/env python3
"""
mem0 Bridge pour Grimoire — Mémoire sémantique partagée entre agents.

Supporte 2 modes :
  - "local" (fallback) : Stockage JSON simple, recherche par mots-clés. Zéro dépendance.
  - "semantic" (défaut) : Recherche sémantique via sentence-transformers + Qdrant local. Zéro API key.

Configuration dynamique via project-context.yaml (USER_ID, APP_ID, AGENT_PROFILES).

Usage:
    python mem0-bridge.py add <agent> <memory_text>
    python mem0-bridge.py search <query> [--agent <agent>] [--limit <n>]
    python mem0-bridge.py dispatch <query> [--limit <n>]
    python mem0-bridge.py list [--agent <agent>]
    python mem0-bridge.py export [--output <file>]
    python mem0-bridge.py status
    python mem0-bridge.py stats
    python mem0-bridge.py migrate    # Migre mémoires JSON → Qdrant
    python mem0-bridge.py upgrade    # Guide d'installation

Exemples:
    python mem0-bridge.py add forge "Le module X nécessite Y"
    python mem0-bridge.py search "backend s3 configuration"
    python mem0-bridge.py list --agent forge
"""

import argparse
import json
import logging
import os
import sys
import warnings
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher

# Supprimer le bruit HF/safetensors AVANT tout import ML
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["SAFETENSORS_LOAD_REPORT"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
warnings.filterwarnings("ignore", message=".*unauthenticated.*")
warnings.filterwarnings("ignore", message=".*LOAD REPORT.*")
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)

# Configuration
Grimoire_ROOT = Path(__file__).parent.parent
MEMORY_DIR = Grimoire_ROOT / "_memory"
LOCAL_DB_PATH = MEMORY_DIR / "memories.json"
ACTIVITY_LOG_PATH = MEMORY_DIR / "activity.jsonl"


# ─── Configuration dynamique ────────────────────────────────────────────────

def _load_project_context() -> dict:
    """Charge project-context.yaml depuis la racine du projet."""
    try:
        import yaml
    except ImportError:
        return {}
    for parent in [Grimoire_ROOT.parent, Grimoire_ROOT.parent.parent]:
        ctx_file = parent / "project-context.yaml"
        if ctx_file.exists():
            with open(ctx_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def _get_config():
    """Retourne USER_ID et APP_ID depuis project-context.yaml."""
    ctx = _load_project_context()
    user = ctx.get("user", {})
    project = ctx.get("project", {})
    user_id = user.get("name", "user").lower().replace(" ", "-")
    project_name = project.get("name", "grimoire-project").lower().replace(" ", "-")
    return user_id, f"grimoire-{project_name}"


# Chargement au module-level
USER_ID, APP_ID = _get_config()


def _load_agent_profiles() -> dict:
    """Charge les profils d'agents dynamiquement.
    1. Cherche dans project-context.yaml → agents.custom_agents
    2. Sinon, scanne les fichiers agents dans _grimoire/_config/custom/agents/
    3. Fallback: profils meta par défaut
    """
    profiles = {}

    # Profils meta par défaut (toujours présents)
    profiles.update({
        "atlas": {
            "icon": "🗺️",
            "domain": "Navigation & Mémoire projet",
            "keywords": "project map navigate locate find search registry "
                        "service network topology dependency graph architecture "
                        "documentation adr shared-context session memory",
        },
        "sentinel": {
            "icon": "🔍",
            "domain": "Qualité & Optimisation agents",
            "keywords": "agent audit quality prompt optimize lint scope "
                        "protocol check overlap refactor token budget "
                        "learnings consolidate report agent-manifest",
        },
        "mnemo": {
            "icon": "🧠",
            "domain": "Mémoire & Qualité des connaissances",
            "keywords": "memory contradiction duplicate learnings consolidate "
                        "context drift audit quality score coverage pruning "
                        "semantic search qdrant embedding similarity "
                        "cercle-vertueux session-state shared-context",
        },
    })

    # Charger depuis project-context.yaml si disponible
    ctx = _load_project_context()
    custom_agents = ctx.get("agents", {}).get("custom_agents", [])
    for agent in custom_agents:
        name = agent.get("name", "").lower()
        if name and name not in profiles:
            profiles[name] = {
                "icon": agent.get("icon", "🤖"),
                "domain": agent.get("domain", "Custom"),
                "keywords": agent.get("keywords", name),
            }

    # Scanner les fichiers agents si le dossier existe
    agents_dir = Grimoire_ROOT / "_config" / "custom" / "agents"
    if agents_dir.exists():
        for f in agents_dir.glob("*.md"):
            name = f.stem
            if name not in profiles:
                profiles[name] = {
                    "icon": "🤖",
                    "domain": "Custom Agent",
                    "keywords": name,
                }

    return profiles


# Chargement au module-level
AGENT_PROFILES = _load_agent_profiles()


# ─── Activity Log ─────────────────────────────────────────────────────────────

def log_activity(cmd: str, agent: str = "", query: str = "", hits: int = 0,
                 top_score: float = 0.0, mode: str = "", extra: dict = None):
    """Logge un event d'activité en JSONL pour observabilité."""
    event = {
        "ts": datetime.now().isoformat(),
        "cmd": cmd,
        "agent": agent,
        "mode": mode,
    }
    if query:
        event["query"] = query
    if hits:
        event["hits"] = hits
    if top_score:
        event["top_score"] = round(top_score, 3)
    if extra:
        event.update(extra)
    try:
        with open(ACTIVITY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Ne jamais crasher le bridge pour un log


# ─── Mode Local (JSON) ───────────────────────────────────────────────────────

class LocalMemory:
    """Mémoire locale basée sur un fichier JSON. Zéro dépendance."""

    def __init__(self):
        self.db_path = LOCAL_DB_PATH
        self.memories = self._load()

    def _load(self):
        if self.db_path.exists():
            with open(self.db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save(self):
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(self.memories, f, indent=2, ensure_ascii=False)

    def add(self, text, user_id=None, metadata=None):
        entry = {
            "id": len(self.memories) + 1,
            "memory": text,
            "user_id": user_id or USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }
        self.memories.append(entry)
        self._save()
        return entry

    def search(self, query, user_id=None, limit=5):
        """Recherche par similarité de mots-clés (fuzzy matching)."""
        query_lower = query.lower()
        query_words = set(query_lower.split())
        scored = []
        for m in self.memories:
            text = m.get("memory", "").lower()
            text_words = set(text.split())
            word_overlap = len(query_words & text_words) / max(len(query_words), 1)
            seq_score = SequenceMatcher(None, query_lower, text).ratio()
            score = (word_overlap * 0.6) + (seq_score * 0.4)
            if score > 0.1:
                scored.append({**m, "score": round(score, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def get_all(self, user_id=None):
        return self.memories

    def count(self):
        return len(self.memories)


# ─── Mode sémantique (sentence-transformers + Qdrant) ────────────────────────

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "bmad_memories"
QDRANT_PATH = str(MEMORY_DIR / "qdrant_data")

# Types de mémoire structurée (BM-22 — Qdrant source de vérité)
MEMORY_TYPES = ["shared-context", "decisions", "agent-learnings", "failures", "stories"]


class SemanticMemory:
    """Mémoire sémantique locale via sentence-transformers + Qdrant. Zéro API key."""

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct

        self._PointStruct = PointStruct
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr_fd = os.dup(2)
        os.dup2(devnull_fd, 2)
        try:
            self.model = SentenceTransformer(EMBEDDING_MODEL)
        finally:
            os.dup2(old_stderr_fd, 2)
            os.close(old_stderr_fd)
            os.close(devnull_fd)
        self.dim = self.model.get_sentence_embedding_dimension()
        self.client = QdrantClient(path=QDRANT_PATH)

        collections = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME not in collections:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )

    def _next_id(self):
        info = self.client.get_collection(COLLECTION_NAME)
        return info.points_count + 1

    def add(self, text, user_id=None, metadata=None):
        vector = self.model.encode(text).tolist()
        point_id = self._next_id()
        payload = {
            "memory": text,
            "user_id": user_id or USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[self._PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return {"id": point_id, "memory": text}

    def search(self, query, user_id=None, limit=5):
        vector = self.model.encode(query).tolist()
        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            limit=limit,
        ).points
        items = []
        for r in results:
            entry = {
                "memory": r.payload.get("memory", ""),
                "score": round(r.score, 3),
                "metadata": r.payload.get("metadata", {}),
                "id": r.id,
            }
            items.append(entry)
        return items

    def get_all(self, user_id=None):
        info = self.client.get_collection(COLLECTION_NAME)
        if info.points_count == 0:
            return []
        result = self.client.scroll(collection_name=COLLECTION_NAME, limit=1000)
        points = result[0]
        return [
            {
                "id": p.id,
                "memory": p.payload.get("memory", ""),
                "metadata": p.payload.get("metadata", {}),
                "created_at": p.payload.get("created_at", ""),
            }
            for p in points
        ]

    def count(self):
        return self.client.get_collection(COLLECTION_NAME).points_count


# ─── Mémoire multi-collection structurée (BM-22) ─────────────────────────────

class StructuredMemory:
    """Mémoire multi-collection Qdrant — source de vérité pour tous les agents.

    Collections :
      {APP_ID}-shared-context   → contexte projet/infra
      {APP_ID}-decisions        → ADRs et décisions
      {APP_ID}-agent-learnings  → learnings par agent
      {APP_ID}-failures         → failure museum entries
      {APP_ID}-stories          → stories/tickets

    Interface principale :
      remember(type, agent, text, tags=[])   → upsert idempotent (UUID5)
      recall(query, type=None, agent=None)   → search sémantique cross-collection
      export_md(type)                        → export Markdown lisible
      import_md(type, content, agent)        → import depuis .md
    """

    TYPES = MEMORY_TYPES

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct

        self._PointStruct = PointStruct
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr_fd = os.dup(2)
        os.dup2(devnull_fd, 2)
        try:
            self.model = SentenceTransformer(EMBEDDING_MODEL)
        finally:
            os.dup2(old_stderr_fd, 2)
            os.close(old_stderr_fd)
            os.close(devnull_fd)
        self.dim = self.model.get_sentence_embedding_dimension()
        self.client = QdrantClient(path=QDRANT_PATH)
        self._ensure_collections()

    def _col(self, type_: str) -> str:
        """Nom de collection normalisé : {APP_ID}-{type}."""
        return f"{APP_ID}-{type_}"

    def _ensure_collections(self):
        from qdrant_client.models import Distance, VectorParams
        existing = {c.name for c in self.client.get_collections().collections}
        for t in self.TYPES:
            col = self._col(t)
            if col not in existing:
                self.client.create_collection(
                    collection_name=col,
                    vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
                )

    def _point_id(self, agent: str, text: str) -> str:
        """UUID5 déterministe — upsert idempotent sur (APP_ID, agent, text[:150])."""
        import uuid
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{APP_ID}:{agent}:{text[:150]}"))

    def remember(self, type_: str, agent: str, text: str, tags: list = None) -> dict:
        """Upsert une mémoire dans sa collection de type."""
        if type_ not in self.TYPES:
            raise ValueError(f"Type invalide: {type_!r}. Valides: {self.TYPES}")
        col = self._col(type_)
        vector = self.model.encode(text).tolist()
        point_id = self._point_id(agent, text)
        payload = {
            "text": text,
            "agent": agent,
            "type": type_,
            "tags": tags or [],
            "project": APP_ID,
            "created_at": datetime.now().isoformat(),
        }
        self.client.upsert(
            collection_name=col,
            points=[self._PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return {"id": point_id, "type": type_, "agent": agent, "text": text}

    def recall(self, query: str, type_: str = None, agent: str = None, limit: int = 5) -> list:
        """Recherche sémantique — une ou toutes les collections."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        vector = self.model.encode(query).tolist()
        collections = [self._col(type_)] if type_ else [self._col(t) for t in self.TYPES]
        filt = None
        if agent:
            filt = Filter(must=[FieldCondition(key="agent", match=MatchValue(value=agent))])
        results = []
        for col in collections:
            try:
                hits = self.client.query_points(
                    collection_name=col,
                    query=vector,
                    query_filter=filt,
                    limit=limit,
                ).points
                col_type = col[len(APP_ID) + 1:] if col.startswith(APP_ID + "-") else col
                for h in hits:
                    results.append({
                        "type": col_type,
                        "agent": h.payload.get("agent", ""),
                        "text": h.payload.get("text", ""),
                        "tags": h.payload.get("tags", []),
                        "score": round(h.score, 3),
                        "date": h.payload.get("created_at", "")[:10],
                        "id": h.id,
                    })
            except Exception:
                pass  # Collection vide ou inexistante
        results.sort(key=lambda x: -x["score"])
        return results[:limit]

    def export_md(self, type_: str) -> str:
        """Exporte une collection en Markdown lisible (format agent-learnings)."""
        col = self._col(type_)
        try:
            result = self.client.scroll(collection_name=col, limit=1000)
            points = result[0]
        except Exception:
            return f"# {type_}\n\n_Collection vide ou inexistante._\n"
        if not points:
            return f"# {type_}\n\n_Aucune entrée._\n"
        lines = [
            f"# {type_} — {APP_ID}",
            f"> Exporté : {datetime.now().isoformat()[:10]} | Entrées : {len(points)}",
            "",
        ]
        by_agent: dict = {}
        for p in points:
            ag = p.payload.get("agent", "unknown")
            by_agent.setdefault(ag, []).append(p)
        for ag, pts in sorted(by_agent.items()):
            lines.append(f"## {ag}")
            for p in sorted(pts, key=lambda x: x.payload.get("created_at", "")):
                date = p.payload.get("created_at", "")[:10]
                text = p.payload.get("text", "")
                tags = p.payload.get("tags", [])
                tag_str = f" `{'` `'.join(tags)}`" if tags else ""
                lines.append(f"- [{date}] {text}{tag_str}")
            lines.append("")
        return "\n".join(lines)

    def import_md(self, type_: str, content: str, agent: str = "import") -> int:
        """Importe un .md (lignes `- [YYYY-MM-DD] text` ou `## agent_name`) dans une collection."""
        count = 0
        current_agent = agent
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("## "):
                current_agent = line[3:].strip()
            elif line.startswith("- [") and "] " in line:
                bracket_content = line[3:line.index("]")] if "]" in line else ""
                # Valider format date YYYY-MM-DD
                if len(bracket_content) == 10 and bracket_content[4] == "-" and bracket_content[7] == "-":
                    text = line[line.index("] ") + 2:].strip()
                    if text:
                        self.remember(type_, current_agent, text)
                        count += 1
        return count

    def count(self, type_: str) -> int:
        try:
            return self.client.get_collection(self._col(type_)).points_count
        except Exception:
            return 0

    def init_collections(self) -> list:
        """Crée toutes les collections si inexistantes (idempotent)."""
        self._ensure_collections()
        return self.TYPES


def _load_memory_config() -> dict:
    """Lit la section 'memory:' de project-context.yaml."""
    ctx = _load_project_context()
    return ctx.get("memory", {})


def get_semantic_client():
    """Initialise le backend sémantique via la factory backends/."""
    # Essai 1 : import relatif (mode package)
    try:
        from .backends import get_backend
        backend, name = get_backend()
        if name == "local":
            return None
        return backend
    except ImportError:
        pass  # Exécuté directement (python mem0-bridge.py) — utiliser importlib
    except Exception as e:
        print(f"⚠️  Backend mémoire indisponible ({e})")
        return None

    # Essai 2 : chargement direct via importlib.util (mode script)
    try:
        import importlib.util, sys, os
        backends_dir = os.path.join(os.path.dirname(__file__), "backends")
        backends_init = os.path.join(backends_dir, "__init__.py")
        if os.path.exists(backends_init):
            spec = importlib.util.spec_from_file_location(
                "backends", backends_init,
                submodule_search_locations=[backends_dir]
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["backends"] = mod  # enregistrer AVANT exec pour les imports relatifs
            spec.loader.exec_module(mod)
            backend, name = mod.get_backend()
            if name == "local":
                return None
            return backend
    except Exception as e:
        print(f"⚠️  Backend sémantique indisponible ({e}), fallback mode local")
        print(f"   → Diagnostiquer : python mem0-bridge.py status")

    return None


def get_client(prefer_semantic=True):
    """Retourne (client, mode). Interface inchangée pour compatibilité."""
    if prefer_semantic:
        client = get_semantic_client()
        if client:
            return client, "semantic"
    return LocalMemory(), "local"


# ─── Mnemo auto-hook : détection contradictions ────────────────────────────────

def _auto_detect_contradictions(client, agent: str, new_text: str):
    """Mnemo hook — cherche des mémoires existantes du même agent avec
    score sémantique > 0.8 (= quasi-doublon). Si trouvé, marque l'ancienne
    comme superseded et log un warning."""
    try:
        results = client.search(new_text, user_id=USER_ID)
        if not isinstance(results, list):
            return
        for r in results:
            meta = r.get("metadata", {}) if isinstance(r, dict) else {}
            score = r.get("score", 0) if isinstance(r, dict) else 0
            mem_agent = meta.get("agent", "")
            mem_id = r.get("id", "")
            if mem_agent == agent and score > 0.8 and mem_id:
                print(f"   ⚠️  Mnemo: contradiction détectée (score={score:.2f}) "
                      f"avec mémoire {mem_id}… — marquée superseded")
                try:
                    client.update(mem_id, data=None, metadata={
                        **meta,
                        "superseded": True,
                        "superseded_by": "auto-mnemo",
                        "superseded_at": datetime.now().isoformat(),
                    })
                except Exception:
                    pass
    except Exception:
        pass


# ─── Commandes ────────────────────────────────────────────────────────────────

def cmd_add(args):
    client, mode = get_client()
    metadata = {
        "agent": args.agent,
        "app_id": APP_ID,
        "timestamp": datetime.now().isoformat(),
    }

    # Mnemo auto-hook: détection contradictions avant ajout
    if mode == "semantic":
        _auto_detect_contradictions(client, args.agent, args.memory_text)

    result = client.add(args.memory_text, user_id=USER_ID, metadata=metadata)
    print(f"✅ [{mode}] Mémoire ajoutée pour {args.agent}")
    if isinstance(result, dict):
        print(f"   ID: {result.get('id', 'N/A')}")
    log_activity("add", agent=args.agent, mode=mode,
                 extra={"memory_len": len(args.memory_text)})

    # Auto-consolidation : lancer le health-check (rate-limited à 1x/24h)
    try:
        import subprocess
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "maintenance.py"), "health-check"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass


def cmd_search(args):
    client, mode = get_client()
    results = client.search(args.query, user_id=USER_ID, limit=args.limit or 5)

    items = results.get("results", results) if isinstance(results, dict) else results
    if not items:
        print("🔍 Aucun résultat.")
        return

    if args.agent:
        items = [r for r in items if isinstance(r, dict) and r.get("metadata", {}).get("agent") == args.agent]

    top_score = 0.0
    if items and isinstance(items[0], dict):
        top_score = items[0].get("score", 0.0) or 0.0
    log_activity("search", agent=args.agent or "", query=args.query, mode=mode,
                 hits=len(items), top_score=float(top_score))

    print(f"🔍 [{mode}] {len(items)} résultat(s) :")
    for i, r in enumerate(items, 1):
        if isinstance(r, dict):
            memory = r.get("memory", r.get("text", str(r)))
            score = r.get("score", "")
            meta = r.get("metadata", {})
            agent = meta.get("agent", "—") if isinstance(meta, dict) else "—"
            score_str = f" (score: {score})" if score else ""
            print(f"  {i}. [{agent}]{score_str} {memory}")


def cmd_list(args):
    client, mode = get_client()
    items = client.get_all(user_id=USER_ID)
    if isinstance(items, dict):
        items = items.get("results", items)

    if args.agent:
        items = [r for r in items if isinstance(r, dict) and r.get("metadata", {}).get("agent") == args.agent]

    if not items:
        print("📋 Aucune mémoire.")
        return

    print(f"📋 [{mode}] {len(items)} mémoire(s) :")
    for i, r in enumerate(items, 1):
        if isinstance(r, dict):
            memory = r.get("memory", str(r))
            meta = r.get("metadata", {})
            agent = meta.get("agent", "—") if isinstance(meta, dict) else "—"
            print(f"  {i}. [{agent}] {memory}")


def cmd_export(args):
    client, mode = get_client()
    items = client.get_all(user_id=USER_ID)
    output_file = args.output or str(MEMORY_DIR / "memories-export.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False, default=str)
    print(f"📦 [{mode}] Mémoires exportées → {output_file}")


def cmd_status(args):
    print(f"🧠 mem0 Bridge — Grimoire")
    print(f"   User: {USER_ID}")
    print(f"   App: {APP_ID}")
    print(f"   DB locale: {LOCAL_DB_PATH}")

    local = LocalMemory()
    print(f"   Mode local (JSON): ✅ {local.count()} mémoires")

    semantic_ok = False
    try:
        client = get_semantic_client()
        if client:
            backend_type = type(client).__name__.replace("Backend", "").lower()
            count = client.count()
            st = client.status() if hasattr(client, "status") else {}
            model = st.get("model", st.get("embedding_model", getattr(client, "_model", "—")))
            qdrant_url = st.get("qdrant_url", getattr(client, "qdrant_url", None))
            print(f"   Backend: ✅ {backend_type}")
            print(f"   Modèle embeddings: {model}")
            if qdrant_url:
                print(f"   Qdrant: {qdrant_url}")
            print(f"   Mémoires sémantiques: {count}")
            semantic_ok = True
        else:
            import os as _os
            has_env = _os.environ.get("Grimoire_OLLAMA_URL") or _os.environ.get("Grimoire_QDRANT_URL")
            if has_env:
                print(f"   Sémantique: ⚠️  backend non disponible (voir logs ci-dessus)")
            else:
                print(f"   Sémantique: ⚠️  non configuré")
                print(f"              → Définir Grimoire_OLLAMA_URL ou Grimoire_QDRANT_URL")
                print(f"              → Ou configurer memory: dans project-context.yaml")
            print(f"              → Mode fallback JSON actif automatiquement")
    except Exception as e:
        print(f"   Sémantique: ❌ {e}")

    mode = "sémantique" if semantic_ok else "local JSON (fallback)"
    print(f"   Mode actif: {'🚀' if semantic_ok else '📁'} {mode}")
    if not semantic_ok:
        print(f"   ℹ️  Mode JSON fonctionnel — recherche sémantique non active.")
        print(f"      → Activer : Grimoire_OLLAMA_URL=http://localhost:11434 python mem0-bridge.py status")

    # Afficher les agents détectés
    print(f"\n   Agents configurés ({len(AGENT_PROFILES)}):")
    for name, profile in sorted(AGENT_PROFILES.items()):
        print(f"     {profile['icon']} {name}: {profile['domain']}")


def cmd_upgrade(args):
    """Guide pour passer en mode mem0 sémantique."""
    print("🔧 Upgrade vers mem0 avec embeddings locaux :")
    print()
    print("  1. Installer les dépendances :")
    print("     pip install sentence-transformers qdrant-client")
    print()
    print("  2. Le bridge utilisera automatiquement le mode sémantique")
    print("     avec le modèle all-MiniLM-L6-v2 (~80MB, tourne en CPU)")
    print()
    print("  3. Migrer les mémoires existantes :")
    print("     python mem0-bridge.py migrate")


def cmd_migrate(args):
    """Migre les mémoires JSON existantes vers Qdrant sémantique."""
    local = LocalMemory()
    if local.count() == 0:
        print("📋 Aucune mémoire locale à migrer.")
        return

    client = get_semantic_client()
    if not client:
        print("❌ Backend sémantique non disponible. Installer : pip install sentence-transformers qdrant-client")
        return

    print(f"🔄 Migration de {local.count()} mémoire(s) JSON → Qdrant...")
    migrated = 0
    for m in local.get_all():
        text = m.get("memory", "")
        metadata = m.get("metadata", {})
        if not metadata.get("app_id"):
            metadata["app_id"] = APP_ID
        metadata["migrated_from"] = "local_json"
        metadata["original_id"] = m.get("id")
        try:
            client.add(text, user_id=USER_ID, metadata=metadata)
            agent = metadata.get("agent", "—")
            print(f"  ✅ [{agent}] {text[:80]}")
            migrated += 1
        except Exception as e:
            print(f"  ❌ Erreur: {e} — {text[:60]}")

    print(f"\n🎉 Migration terminée: {migrated}/{local.count()} mémoires migrées.")
    print(f"   Les mémoires JSON sont conservées dans {LOCAL_DB_PATH} (backup).")


def cmd_stats(args):
    """Analyse le log d'activité JSONL — métriques du cercle vertueux."""
    if not ACTIVITY_LOG_PATH.exists():
        print("📊 Aucun log d'activité. Utilisez add/search pour générer des events.")
        return

    events = []
    with open(ACTIVITY_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not events:
        print("📊 Log vide.")
        return

    cmds = {}
    agents = {}
    searches = {"total": 0, "high_score": 0, "low_score": 0, "scores": []}

    for e in events:
        cmd = e.get("cmd", "?")
        cmds[cmd] = cmds.get(cmd, 0) + 1
        ag = e.get("agent", "")
        if ag:
            agents[ag] = agents.get(ag, 0) + 1
        if cmd == "search":
            searches["total"] += 1
            score = e.get("top_score", 0)
            searches["scores"].append(score)
            if score >= 0.3:
                searches["high_score"] += 1
            else:
                searches["low_score"] += 1

    first = events[0].get("ts", "?")[:10]
    last = events[-1].get("ts", "?")[:10]

    print(f"📊 Métriques d'activité mem0 Bridge")
    print(f"   Période: {first} → {last}")
    print(f"   Events: {len(events)}")
    print()

    print("   Commandes:")
    for cmd, count in sorted(cmds.items(), key=lambda x: -x[1]):
        print(f"     {cmd}: {count}")
    print()

    if agents:
        print("   Agents actifs:")
        for ag, count in sorted(agents.items(), key=lambda x: -x[1]):
            print(f"     {ag}: {count}")
        print()

    if searches["total"]:
        avg = sum(searches["scores"]) / len(searches["scores"]) if searches["scores"] else 0
        hit_rate = searches["high_score"] / searches["total"] * 100 if searches["total"] else 0
        print(f"   Recherche sémantique:")
        print(f"     Total: {searches['total']}")
        print(f"     Score moyen: {avg:.3f}")
        print(f"     Hit rate (score>=0.3): {hit_rate:.0f}% ({searches['high_score']}/{searches['total']})")
        print(f"     Misses (score<0.3): {searches['low_score']}")
        if hit_rate < 50 and searches["total"] >= 5:
            print(f"     ⚠️  Hit rate faible — enrichir la mémoire sémantique avec plus de 'add'")


# ─── Dispatch sémantique d'agents ────────────────────────────────────────────

def cmd_dispatch(args):
    """Dispatch sémantique — route une requête vers les agents les plus pertinents."""
    query = args.query
    limit = args.limit

    # 1. Score statique basé sur les mots-clés des profils
    query_lower = query.lower()
    query_words = set(query_lower.split())
    static_scores = {}
    for agent, profile in AGENT_PROFILES.items():
        kw_words = set(profile["keywords"].split())
        overlap = len(query_words & kw_words)
        substr_bonus = sum(1 for kw in kw_words if kw in query_lower and kw not in query_words) * 0.5
        static_scores[agent] = overlap + substr_bonus

    # 2. Score sémantique
    semantic_scores = {}
    client, mode = get_client()
    if mode == "semantic":
        results = client.search(query, limit=20)
        for r in results:
            agent = r.get("metadata", {}).get("agent", "")
            if agent:
                current = semantic_scores.get(agent, 0)
                semantic_scores[agent] = max(current, r.get("score", 0))

    # 3. Score hybride = 0.4 × statique_normalisé + 0.6 × sémantique
    max_static = max(static_scores.values()) if static_scores else 1
    combined = {}
    for agent in AGENT_PROFILES:
        s_norm = static_scores.get(agent, 0) / max(max_static, 1)
        sem = semantic_scores.get(agent, 0)
        if semantic_scores:
            combined[agent] = 0.4 * s_norm + 0.6 * sem
        else:
            combined[agent] = s_norm

    ranked = sorted(combined.items(), key=lambda x: -x[1])[:limit]

    top_agent = ranked[0][0] if ranked else ""
    top_score = ranked[0][1] if ranked else 0
    log_activity("dispatch", query=query, hits=len(ranked),
                 top_score=top_score, mode=mode,
                 extra={"top_agent": top_agent})

    print(f"🎯 Dispatch sémantique ({mode}) — \"{query}\"")
    print()
    print(f"  {'#':<3} {'Agent':<12} {'Score':<8} {'Domaine'}")
    print(f"  {'─'*3} {'─'*12} {'─'*8} {'─'*30}")
    for i, (agent, score) in enumerate(ranked, 1):
        profile = AGENT_PROFILES[agent]
        bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
        print(f"  {i:<3} {profile['icon']} {agent:<10} {score:.3f}  {bar}  {profile['domain']}")

    if ranked:
        best = ranked[0]
        print(f"\n  → Recommandation : {AGENT_PROFILES[best[0]]['icon']} {best[0].upper()}")


# ─── Commandes structurées (BM-22) ───────────────────────────────────────────

def cmd_remember(args):
    """Mémoriser dans une collection typée Qdrant (source de vérité)."""
    type_ = args.type
    agent = args.agent
    text = args.memory_text
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    try:
        sm = StructuredMemory()
    except Exception as e:
        warn_msg = f"⚠️  StructuredMemory indisponible ({e}), fallback add classique"
        print(warn_msg)
        client, _ = get_client()
        client.add(text, metadata={"agent": agent, "type": type_, "tags": tags})
        print(f"✅ Mémorisé (local) [{type_}] @{agent}: {text[:80]}")
        return
    result = sm.remember(type_, agent, text, tags)
    display = text[:80] + ("..." if len(text) > 80 else "")
    print(f"✅ Mémorisé [{type_}] @{agent}: {display}")
    if tags:
        print(f"   tags: {', '.join(tags)}")
    log_activity("remember", agent=agent, query=text[:80], extra={"type": type_})


def cmd_recall(args):
    """Recherche sémantique dans les collections typées Qdrant."""
    query = args.query
    if not query.strip():
        print("⚠️  Query vide — rien à rechercher")
        return
    type_ = getattr(args, "type", None)
    agent = getattr(args, "agent", None)
    limit = args.limit
    try:
        sm = StructuredMemory()
    except Exception as e:
        print(f"⚠️  StructuredMemory indisponible ({e}), fallback search classique")
        client, _ = get_client()
        results = client.search(query, limit=limit)
        for i, r in enumerate(results, 1):
            print(f"  {i}. [{r.get('score', 0):.3f}] {r.get('memory', '')[:100]}")
        return
    results = sm.recall(query, type_=type_, agent=agent, limit=limit)
    if not results:
        print(f"🔍 Recall — \"{query}\" → aucun résultat")
        return
    tl = f" [{type_}]" if type_ else ""
    al = f" @{agent}" if agent else ""
    print(f"🔍 Recall{tl}{al} — \"{query}\"")
    print()
    for i, r in enumerate(results, 1):
        filled = int(r["score"] * 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"  {i}. [{r['score']:.3f}] {bar}  [{r['type']}] @{r['agent']}")
        print(f"     {r['text'][:120]}{'...' if len(r['text']) > 120 else ''}")
        if r["tags"]:
            print(f"     tags: {', '.join(r['tags'])}")
    log_activity("recall", query=query, hits=len(results),
                 top_score=results[0]["score"] if results else 0,
                 extra={"type": type_, "agent": agent})


def cmd_export_md(args):
    """Exporter une collection Qdrant en Markdown lisible."""
    type_ = args.type
    output = getattr(args, "output", None)
    try:
        sm = StructuredMemory()
    except Exception as e:
        print(f"❌ StructuredMemory indisponible: {e}")
        return
    content = sm.export_md(type_)
    if output:
        from pathlib import Path as _Path
        _Path(output).write_text(content, encoding="utf-8")
        print(f"✅ Exporté → {output} ({sm.count(type_)} entrées)")
    else:
        print(content)


def cmd_import_md(args):
    """Importer un fichier Markdown dans une collection Qdrant."""
    file_path = args.file
    type_ = args.type
    agent = getattr(args, "agent", "import")
    try:
        sm = StructuredMemory()
    except Exception as e:
        print(f"❌ StructuredMemory indisponible: {e}")
        return
    content = Path(file_path).read_text(encoding="utf-8")
    count = sm.import_md(type_, content, agent=agent)
    print(f"✅ Importé {count} entrées depuis {file_path} → [{type_}]")


def cmd_init_collections(args):
    """Initialise toutes les collections Qdrant (idempotent, appelé par grimoire-init.sh)."""
    try:
        sm = StructuredMemory()
        types = sm.init_collections()
        print(f"✅ Collections Qdrant initialisées : {', '.join(types)}")
    except Exception as e:
        print(f"⚠️  Qdrant indisponible ({e}) — collections non créées (fallback local actif)")


def main():
    parser = argparse.ArgumentParser(description="mem0 Bridge — Mémoire partagée Grimoire")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("add", help="Ajouter une mémoire")
    p.add_argument("agent", help="Agent tag")
    p.add_argument("memory_text", help="Texte à mémoriser")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("search", help="Rechercher")
    p.add_argument("query")
    p.add_argument("--agent")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("list", help="Lister")
    p.add_argument("--agent")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", help="Exporter en JSON")
    p.add_argument("--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("status", help="Statut")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stats", help="Métriques d'activité (cercle vertueux)")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("dispatch", help="Dispatch sémantique — route une requête vers les agents")
    p.add_argument("query", help="Requête en langage naturel")
    p.add_argument("--limit", type=int, default=5, help="Nombre d'agents à afficher")
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("upgrade", help="Guide upgrade mem0 sémantique")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("migrate", help="Migrer mémoires JSON → Qdrant")
    p.set_defaults(func=cmd_migrate)

    # ── Mémoire structurée multi-collection (BM-22) ──────────────────────────
    _types = ["shared-context", "decisions", "agent-learnings", "failures", "stories"]

    p = sub.add_parser("remember", help="Mémoriser dans une collection typée Qdrant")
    p.add_argument("--type", required=True, choices=_types, help="Type de mémoire")
    p.add_argument("--agent", required=True, help="Agent tag (ex: forge, atlas)")
    p.add_argument("memory_text", help="Contenu à mémoriser")
    p.add_argument("--tags", help="Tags séparés par virgules (ex: terraform,infra)")
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("recall", help="Recherche sémantique multi-collection Qdrant")
    p.add_argument("query", help="Requête en langage naturel")
    p.add_argument("--type", choices=_types, help="Filtrer par type de mémoire")
    p.add_argument("--agent", help="Filtrer par agent")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("export-md", help="Exporter une collection Qdrant en Markdown lisible")
    p.add_argument("--type", required=True, choices=_types)
    p.add_argument("--output", help="Fichier de sortie (sinon stdout)")
    p.set_defaults(func=cmd_export_md)

    p = sub.add_parser("import-md", help="Importer un .md dans une collection Qdrant")
    p.add_argument("file", help="Fichier .md à importer")
    p.add_argument("--type", required=True, choices=_types)
    p.add_argument("--agent", default="import", help="Agent tag par défaut")
    p.set_defaults(func=cmd_import_md)

    p = sub.add_parser("init-collections", help="Initialiser toutes les collections Qdrant (idempotent)")
    p.set_defaults(func=cmd_init_collections)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
