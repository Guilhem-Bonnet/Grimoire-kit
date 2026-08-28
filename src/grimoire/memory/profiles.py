"""Memory OS layer profiles — the composed stack, chosen as one unit.

A project never runs *one* memory.  It runs a short-term layer, a semantic
store, a structured sidecar, optionally a graph projection and a hot cache.
Those layers were each configurable in ``project-context.yaml``, but the setup
only ever asked one question — which backend? — so every project landed on the
same composition and the other six layers kept their defaults forever.

A profile names a whole composition.  It is what the setup asks for, what
``layer_profile`` records, and what :mod:`grimoire.memory.architecture` reports
against.

The backend axis stays separate on purpose: ``backend=""`` means *keep whatever
detection found* (``qdrant-local``, ``ollama``, ``mempalace``…), so choosing a
composition never overrides a store the machine already runs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Capability tokens a profile can require from the host machine.
REQ_EGRESS = "egress"
REQ_DOCKER = "docker"
REQ_REDIS = "redis"

#: Semantic backends that can carry a lexical companion index, so that a
#: profile asking for fusion actually gets two rankings to fuse.
VECTOR_BACKENDS: frozenset[str] = frozenset({
    "qdrant-local", "qdrant-server", "weaviate-server", "ollama",
})

#: Backends that are lexical by construction — fusing them with a BM25
#: companion would fuse an index with itself.
LEXICAL_BACKENDS: frozenset[str] = frozenset({"local", "lexical", "tantivy-local"})

#: Layer keys emitted into the ``memory:`` block, in a stable order.
_LAYER_KEYS = (
    "layer_profile",
    "vector_database",
    "retrieval_mode",
    "short_term_backend",
    "redis_url",
    "knowledge_graph",
    "memory_graph",
    "code_graph",
    "task_memory",
    "visualization",
)


@dataclass(frozen=True, slots=True)
class MemoryProfile:
    """One named composition of the seven Memory OS layers."""

    id: str
    label: str
    summary: str
    #: Semantic backend to force, or ``""`` to keep the detected one.
    backend: str = ""
    vector_database: bool = True
    retrieval_mode: str = "hybrid"
    short_term_backend: str = "sqlite"
    redis_url: str = ""
    knowledge_graph: str = "sqlite-sidecar"
    memory_graph: str = "sqlite-sidecar"
    code_graph: str = "planned"
    task_memory: str = "planned"
    visualization: str = "runtime-dashboard"
    requires: tuple[str, ...] = ()
    #: Extra ``memory:`` keys (connection settings) this profile implies.
    connection: tuple[tuple[str, str], ...] = ()

    def layer_values(self) -> dict[str, str]:
        """The layer fields as YAML-ready scalars, in emission order."""
        raw: dict[str, object] = {
            "layer_profile": self.id,
            "vector_database": self.vector_database,
            "retrieval_mode": self.retrieval_mode,
            "short_term_backend": self.short_term_backend,
            "redis_url": self.redis_url,
            "knowledge_graph": self.knowledge_graph,
            "memory_graph": self.memory_graph,
            "code_graph": self.code_graph,
            "task_memory": self.task_memory,
            "visualization": self.visualization,
        }
        return {key: _scalar(raw[key]) for key in _LAYER_KEYS}

    def layers_block(self, *, indent: str = "  ") -> str:
        """The ``memory:`` layer lines, newline-terminated like the template."""
        lines = [f"{indent}{key}: {value}" for key, value in self.layer_values().items()]
        return "\n".join(lines) + "\n"

    def connection_block(self, backend: str = "", *, indent: str = "  ") -> str:
        """Connection lines for this composition on *backend*.

        Merges what the store needs to be reachable with what the layers need,
        the store first and never twice. Empty when nothing is required — the
        caller concatenates it right after ``backend: "..."`` on the same line.
        """
        merged: dict[str, str] = dict(BACKEND_CONNECTION.get(backend, ()))
        for key, value in self.connection:
            merged.setdefault(key, value)
        return "".join(f'\n{indent}{key}: "{value}"' for key, value in merged.items())

    def resolve_backend(self, detected: str) -> str:
        """The backend this profile runs on, given what detection found."""
        return self.backend or detected

    def for_backend(self, backend: str) -> MemoryProfile:
        """This profile, made honest about the store it will actually run on.

        ``standard`` asks for fusion, but a machine with no vector service
        resolves to a lexical primary — and a config claiming ``hybrid`` there
        would describe a semantic layer that does not exist.  The layers are
        narrowed to what the backend can back; nothing is ever widened.
        """
        if backend in ("", "auto"):
            # Resolution is deferred to runtime; the declared intent stands.
            return self
        if backend in LEXICAL_BACKENDS:
            return replace(self, vector_database=False, retrieval_mode="lexical")
        if backend not in VECTOR_BACKENDS and self.retrieval_mode == "hybrid":
            # Semantic (mempalace) but with no companion to fuse against.
            return replace(self, retrieval_mode="vector")
        return self

    def unmet(self, available: frozenset[str]) -> tuple[str, ...]:
        """Requirements this machine does not satisfy."""
        return tuple(req for req in self.requires if req not in available)


def _scalar(value: object) -> str:
    """Render a Python value as the YAML scalar the template expects."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return f'"{value}"'


#: Settings a backend needs to be reachable at all. A property of the store,
#: not of the composition: a `standard` project that lands on a detected
#: Weaviate needs its URL just as much as a `graphe` one does.
BACKEND_CONNECTION: dict[str, tuple[tuple[str, str], ...]] = {
    "ollama": (("ollama_url", "http://localhost:11434"),),
    "qdrant-server": (("qdrant_url", "http://localhost:6333"),),
    "weaviate-server": (
        ("qdrant_url", "http://localhost:6333"),
        ("weaviate_url", "http://localhost:8080"),
        ("weaviate_collection", "GrimoireMemory"),
    ),
}

#: Settings the *layers* need — the graph projection and the migration bundle
#: that carries it. Only profiles that declare Neo4j layers carry these.
_GRAPH_CONNECTION: tuple[tuple[str, str], ...] = (
    ("neo4j_uri", "bolt://localhost:7687"),
    ("neo4j_user", "neo4j"),
    ("neo4j_password_env", "GRIMOIRE_NEO4J_PASSWORD"),
    ("neo4j_database", "neo4j"),
    ("migration_source_backend", "qdrant-server"),
    ("migration_target_backend", "weaviate-server"),
    ("migration_bundle_path", "_grimoire/_memory/migration/weaviate-neo4j"),
)


PROFILES: dict[str, MemoryProfile] = {
    "lexical": MemoryProfile(
        id="lexical",
        label="Lexical",
        summary="BM25 SQLite seul — aucun modèle, aucun service, aucun réseau.",
        backend="lexical",
        vector_database=False,
        retrieval_mode="lexical",
    ),
    "standard": MemoryProfile(
        id="standard",
        label="Standard",
        summary="Sémantique + BM25 fusionnés (RRF) et sidecar structuré.",
        retrieval_mode="hybrid",
    ),
    "graphe": MemoryProfile(
        id="graphe",
        label="Graphe",
        summary="Standard + graphes Neo4j : connaissances, souvenirs, code, tâches.",
        backend="weaviate-server",
        retrieval_mode="hybrid",
        knowledge_graph="neo4j",
        memory_graph="neo4j",
        code_graph="neo4j",
        task_memory="neo4j",
        requires=(REQ_EGRESS, REQ_DOCKER),
        connection=_GRAPH_CONNECTION,
    ),
    "complet": MemoryProfile(
        id="complet",
        label="Complet",
        summary="Graphe + mémoire chaude Redis (TTL, baux, coordination multi-agents).",
        backend="weaviate-server",
        retrieval_mode="hybrid",
        short_term_backend="redis",
        redis_url="redis://localhost:6379/0",
        knowledge_graph="neo4j",
        memory_graph="neo4j",
        code_graph="neo4j",
        task_memory="neo4j",
        requires=(REQ_EGRESS, REQ_DOCKER, REQ_REDIS),
        connection=_GRAPH_CONNECTION,
    ),
}

#: Profile ids written by earlier versions, mapped to their current profile.
#: A project on the old id keeps working and keeps its layers.
ALIASES: dict[str, str] = {"weaviate-neo4j": "graphe"}

#: Order used by every listing surface — cheapest composition first.
PROFILE_ORDER: tuple[str, ...] = ("lexical", "standard", "graphe", "complet")

DEFAULT_PROFILE = "standard"


def resolve(profile_id: str) -> MemoryProfile:
    """Return the profile for *profile_id*, following aliases.

    Unknown ids fall back to :data:`DEFAULT_PROFILE` rather than raising: a
    profile name is descriptive metadata, and a project must never fail to
    load its memory because someone typed a name this version does not know.
    """
    key = (profile_id or "").strip()
    key = ALIASES.get(key, key)
    return PROFILES.get(key, PROFILES[DEFAULT_PROFILE])


def is_known(profile_id: str) -> bool:
    """Whether *profile_id* names a profile this version ships."""
    key = (profile_id or "").strip()
    return key in PROFILES or key in ALIASES


def ordered() -> list[MemoryProfile]:
    """Every profile, cheapest composition first."""
    return [PROFILES[key] for key in PROFILE_ORDER]


def feasible(available: frozenset[str]) -> list[MemoryProfile]:
    """Profiles whose requirements *available* satisfies, cheapest first."""
    return [profile for profile in ordered() if not profile.unmet(available)]


#: Backends a profile pins. Asking for one of these *is* asking for that
#: composition, which keeps ``grimoire init -b weaviate-server`` meaning what
#: it meant before profiles existed.
_BACKEND_IMPLIES: dict[str, str] = {"weaviate-server": "graphe"}


def infer(backend: str, *, offline: bool) -> MemoryProfile:
    """The composition implied by a backend and an egress verdict.

    Used when no profile was named — by an express ``init``, by an upgrade, or
    by any caller that only knows which store it found.
    """
    if backend in _BACKEND_IMPLIES:
        # A backend that pins its own composition is the deliberate gesture of
        # a site that runs its own services: it survives the offline verdict,
        # and gets its embedding model through a bundle instead of the network.
        return PROFILES[_BACKEND_IMPLIES[backend]]
    if offline:
        # No egress: an embedding model cannot be fetched, so any semantic
        # layer declared here would be a collection that can never be filled.
        return PROFILES["lexical"]
    return PROFILES[DEFAULT_PROFILE]
