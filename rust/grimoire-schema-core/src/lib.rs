//! Coeur Rust optionnel de `grimoire.core.schema` et `grimoire.core.validator`.
//!
//! Second port Rust du kit (issue #354 de Guilhem-Bonnet/Grimoire-kit, apres
//! `rust/grimoire-policies-core/`, meme montage). Ce crate porte deux
//! fonctions pures de `grimoire.core` :
//!
//! - `generate_schema()` (`src/grimoire/core/schema.py`) : le document JSON
//!   Schema statique decrivant `project-context.yaml`. Aucune entree, sortie
//!   deterministe — porte ici essentiellement en echauffement avant
//!   `validate_config`, comme demande par l'audit de l'issue #354.
//! - `validate_config()` (`src/grimoire/core/validator.py`) : le validateur
//!   structurel de `project-context.yaml` (types, contraintes, cles connues).
//!   C'est le module qui justifie le port : son entree est `Any` cote Python
//!   (une donnee YAML arbitraire, potentiellement malformee), exactement le
//!   genre de frontiere ou un type mal forme ne se voit aujourd'hui qu'a
//!   l'execution.
//!
//! Ce qui est porte et ce qui ne l'est pas :
//! - Porte : la totalite de la logique structurelle de `validate_config`
//!   (sections requises/optionnelles, types, enumerations, cles inconnues,
//!   doublons) et la totalite de la structure statique de `generate_schema`.
//! - Pas porte : le calcul des suggestions « did you mean ? » sur une cle
//!   inconnue (`_suggest_key`, base sur `difflib.SequenceMatcher` cote
//!   Python). Ce n'est pas de la logique de validation — c'est un
//!   embellissement d'experience editeur — et le reimplementer fidelement en
//!   Rust (le meme algorithme de similarite, au caractere pres, pour rester
//!   compatible avec les suggestions deja testees) n'aurait ajoute aucune
//!   garantie de type nouvelle. Ce crate renvoie donc, pour chaque cle
//!   inconnue, un identifiant du jeu de cles concerne (`"top"`, `"project"`,
//!   `"user"`, `"memory"`, `"agents"`) ; `grimoire.core.validator` recalcule
//!   la suggestion avec sa fonction `_suggest_key` existante, inchangee. Voir
//!   le docstring de `validate_config` (`validator.py`) pour le detail de ce
//!   pont.
//!
//! Ce module est optionnel par construction (meme decision que le premier
//! port) : `grimoire.core.schema` et `grimoire.core.validator` ne l'importent
//! que s'il est present, et retombent sinon sur leur implementation Python
//! pure. Rien ici n'est publie sur PyPI ; ce crate se construit en local
//! (`maturin develop`, voir CONTRIBUTING.md) ou dans le job CI dedie.
//!
//! ## La propriete que ce port doit demontrer
//!
//! Le premier port (`grimoire-policies-core`) demontrait qu'un `match`
//! exhaustif en Rust attrape a la compilation un cas d'enum oublie, la ou
//! Python rend juste une comparaison `False` a l'execution. Ce second port
//! demontre une propriete complementaire, plus proche du coeur de l'audit
//! (« les analyseurs et validateurs d'abord ») : `grimoire.core.validator`
//! est expose a une entree `Any`, c'est-a-dire potentiellement n'importe
//! quelle structure YAML — y compris une malformee. Verifie empiriquement
//! sur le validateur Python actuel (voir les tests de ce fichier et
//! `tests/unit/test_schema_validator_rust_parity.py`) :
//!
//! - `{"project": {"name": "x", "type": ["webapp"]}}` fait lever
//!   `TypeError: cannot use 'list' as a set element (unhashable type: 'list')`
//!   depuis `validate_config` — un plantage non gere, pas une erreur de
//!   validation propre. La meme entree, cote Rust, produit un
//!   `ValidationError` explicite (« doit etre une chaine ») parce que le
//!   type de la valeur est verifie avant toute comparaison d'ensemble.
//! - `{"project": {"name": "x", "repos": [{"name": 123}]}}` et
//!   `{"project": {"name": "x"}, "installed_archetypes": [1, 2, 3]}`
//!   passent aujourd'hui la validation Python sans la moindre erreur (le
//!   nom de depot n'est jamais type-verifie au-dela de sa verite, et les
//!   elements d'`installed_archetypes` ne sont jamais type-verifies du
//!   tout) — alors que `schema.py` declare explicitement ces deux champs
//!   comme des chaines. Rust ferme ces deux trous.
//!
//! Ces divergences ne touchent aucune des entrees exercees par la suite de
//! tests existante (`tests/unit/core/test_validator.py`,
//! `tests/unit/core/test_schema.py`) : elles restent vertes, sans
//! modification, quel que soit le backend actif — c'est le contrat que ce
//! port ne s'autorise pas a assouplir. Les cas ci-dessus ne sont exerces que
//! par le nouveau test de parite, sur des entrees deliberement absentes de
//! la suite existante.

// Sous `--no-default-features` (le profil de `cargo test`, voir Cargo.toml),
// seul le module `#[cfg(test)]` en bas de fichier consomme ce qui suit — le
// pont PyO3 qui utilise les fonctions publiques hors tests est absent de
// cette configuration. Compromis attendu de la separation feature par
// feature (identique a rust/grimoire-policies-core/), pas du code mort a
// corriger.
#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

// ── Representation dynamique minimale ───────────────────────────────────────
//
// `validate_config` recoit une donnee YAML arbitraire (n'importe quelle
// forme : mapping, liste, scalaire, y compris malformee) et `generate_schema`
// produit un document JSON Schema tout aussi arbitraire en forme. Les deux
// partagent donc la meme representation minimale, sans dependance externe
// (meme philosophie zero-dependance que rust/grimoire-policies-core/) :
// suffisante pour representer YAML/JSON, convertible dans les deux sens avec
// les types Python natifs a la frontiere PyO3.
#[derive(Debug, Clone, PartialEq)]
enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    /// Paires (cle, valeur) dans l'ordre d'insertion — un dict Python
    /// preserve cet ordre depuis 3.7, tout comme un mapping YAML analyse par
    /// ruamel ; reproduit ici pour que l'ordre des erreurs de cles
    /// inconnues (`_check_unknown_keys` en Python itere `for key in
    /// section`) reste previsible, meme si aucune assertion existante n'en
    /// depend.
    Map(Vec<(String, Value)>),
}

impl Value {
    fn is_map(&self) -> bool {
        matches!(self, Value::Map(_))
    }

    /// Acces a une cle d'un mapping. `None` si absente OU si `self` n'est
    /// pas un mapping — a distinguer de `Some(&Value::Null)`, qui signifie
    /// "cle presente, valeur explicitement nulle" (mirroir de `dict.get`
    /// cote Python, qui ne distingue pas non plus "absente" de "presente a
    /// None" par la seule valeur de retour — la distinction se fait par
    /// l'appelant via `"cle" in dict` quand c'est ce qui compte, voir
    /// `validate_core`).
    fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Map(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    /// Verite a la Python (`bool(x)` / `not x`) : utilise pour reproduire a
    /// l'identique les branches `if not repo.get("name")` du validateur
    /// Python.
    fn is_falsy(&self) -> bool {
        match self {
            Value::Null => true,
            Value::Bool(b) => !b,
            Value::Int(i) => *i == 0,
            Value::Float(f) => *f == 0.0,
            Value::Str(s) => s.is_empty(),
            Value::List(items) => items.is_empty(),
            Value::Map(entries) => entries.is_empty(),
        }
    }

    /// Description humaine du type, utilisee dans les messages "doit etre
    /// une chaine (obtenu <description>)".
    fn describe_kind(&self) -> &'static str {
        match self {
            Value::Null => "null",
            // En anglais comme le reste des messages de ValidationError
            // (voir validator.py) — seuls les commentaires et docstrings de
            // ce crate sont en francais, jamais les messages utilisateur.
            Value::Bool(_) => "a boolean",
            Value::Int(_) | Value::Float(_) => "a number",
            Value::Str(_) => "a string",
            Value::List(_) => "a list",
            Value::Map(_) => "a mapping",
        }
    }

    /// Mirroir de l'interpolation `f"{value}"` de Python pour un scalaire
    /// hachable (str/int/float/bool) — jamais appelee pour Liste/Table, qui
    /// prennent le message "doit etre une chaine" dedie (voir
    /// `check_enum_field`) : Python plante justement sur ces deux cas avant
    /// d'atteindre son f-string (`TypeError: unhashable type`), donc aucun
    /// texte de reference n'existe a reproduire pour eux.
    fn as_display(&self) -> String {
        match self {
            Value::Str(s) => s.clone(),
            Value::Int(i) => i.to_string(),
            Value::Float(f) => f.to_string(),
            Value::Bool(b) => (if *b { "True" } else { "False" }).to_string(),
            Value::Null => "None".to_string(),
            Value::List(_) | Value::Map(_) => String::new(),
        }
    }
}

fn sorted_copy(values: &[&'static str]) -> Vec<&'static str> {
    let mut v = values.to_vec();
    v.sort_unstable();
    v
}

/// Mirroir de `', '.join(sorted(known))` cote Python.
fn sorted_join(values: &[&'static str]) -> String {
    sorted_copy(values).join(", ")
}

/// Mirroir de `sorted([...])` place tel quel dans une valeur `enum` du
/// schema JSON.
fn value_str_sorted(values: &[&'static str]) -> Value {
    Value::List(
        sorted_copy(values)
            .into_iter()
            .map(|s| Value::Str(s.to_string()))
            .collect(),
    )
}

fn value_str(values: &[&'static str]) -> Value {
    Value::List(
        values
            .iter()
            .map(|s| Value::Str((*s).to_string()))
            .collect(),
    )
}

// ── Constantes miroirs de grimoire.core.{schema,validator} ─────────────────
//
// Ordre de declaration identique aux tuples/listes Python source — certains
// champs schema.py utilisent l'ordre d'insertion tel quel (`_VALID_TYPES`),
// d'autres le trient (`_VALID_SKILL_LEVELS` et le reste) : voir l'usage de
// `value_str` (ordre brut) vs `value_str_sorted` (trie) ci-dessous, qui
// reproduit cette distinction terme a terme.

const VALID_TYPES: &[&str] = &[
    "webapp",
    "api",
    "service",
    "infrastructure",
    "library",
    "framework",
    "cli",
    "generic",
    "meta",
];
const VALID_SKILL_LEVELS: &[&str] = &["beginner", "intermediate", "expert"];
const VALID_BACKENDS: &[&str] = &[
    "auto",
    "local",
    "lexical",
    "tantivy-local",
    "qdrant-local",
    "qdrant-server",
    "weaviate-server",
    "mempalace",
    "ollama",
];
const VALID_SHORT_TERM_BACKENDS: &[&str] = &["sqlite", "redis", "none"];
const VALID_LAYER_MODES: &[&str] = &[
    "disabled",
    "planned",
    "sqlite-sidecar",
    "qdrant",
    "weaviate",
    "neo4j",
    "runtime-dashboard",
];
const KNOWN_ARCHETYPES: &[&str] = &[
    "minimal",
    "web-app",
    "creative-studio",
    "fix-loop",
    "infra-ops",
    "meta",
    "stack",
    "features",
    "platform-engineering",
];

const KNOWN_TOP_KEYS: &[&str] = &[
    "project",
    "user",
    "memory",
    "agents",
    "installed_archetypes",
];
const KNOWN_PROJECT_KEYS: &[&str] = &["name", "description", "type", "metaphor", "stack", "repos"];
const KNOWN_USER_KEYS: &[&str] = &["name", "language", "document_language", "skill_level"];
const KNOWN_MEMORY_KEYS: &[&str] = &[
    "backend",
    "vector_database",
    "retrieval_mode",
    "collection_prefix",
    "embedding_model",
    "qdrant_url",
    "weaviate_url",
    "weaviate_api_key_env",
    "weaviate_collection",
    "neo4j_uri",
    "neo4j_user",
    "neo4j_password_env",
    "neo4j_database",
    "migration_source_backend",
    "migration_target_backend",
    "migration_bundle_path",
    "mempalace_path",
    "ollama_url",
    "layer_profile",
    "short_term_backend",
    "redis_url",
    "knowledge_graph",
    "memory_graph",
    "code_graph",
    "task_memory",
    "visualization",
];
const KNOWN_AGENTS_KEYS: &[&str] = &[
    "archetype",
    "custom_agents",
    "entry",
    "freshness_threshold_days",
];

// ── generate_schema ──────────────────────────────────────────────────────────

fn obj(entries: Vec<(&str, Value)>) -> Value {
    Value::Map(
        entries
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect(),
    )
}

fn s(v: &str) -> Value {
    Value::Str(v.to_string())
}

/// Coeur pur de `generate_schema` (schema.py) — aucun type PyO3, testable
/// directement par `cargo test`. Reproduit terme a terme la structure
/// construite par `generate_schema`/`_project_schema`/`_user_schema`/
/// `_memory_schema`/`_agents_schema`.
fn schema_core() -> Value {
    obj(vec![
        ("$schema", s("https://json-schema.org/draft/2020-12/schema")),
        (
            "$id",
            s("https://grimoire-kit.dev/schemas/project-context.json"),
        ),
        ("title", s("Grimoire project-context.yaml")),
        (
            "description",
            s("Configuration schema for a Grimoire Kit project."),
        ),
        ("type", s("object")),
        ("required", value_str(&["project"])),
        ("additionalProperties", Value::Bool(true)),
        (
            "properties",
            obj(vec![
                ("project", project_schema()),
                ("user", user_schema()),
                ("memory", memory_schema()),
                ("agents", agents_schema()),
                (
                    "installed_archetypes",
                    obj(vec![
                        ("type", s("array")),
                        ("items", obj(vec![("type", s("string"))])),
                        ("description", s("List of installed archetype identifiers.")),
                    ]),
                ),
            ]),
        ),
    ])
}

fn project_schema() -> Value {
    obj(vec![
        ("type", s("object")),
        ("description", s("Project metadata.")),
        ("required", value_str(&["name"])),
        ("additionalProperties", Value::Bool(false)),
        (
            "properties",
            obj(vec![
                (
                    "name",
                    obj(vec![
                        ("type", s("string")),
                        ("minLength", Value::Int(1)),
                        ("description", s("Project name.")),
                    ]),
                ),
                (
                    "description",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Short project description.")),
                    ]),
                ),
                (
                    "type",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str(VALID_TYPES)),
                        ("default", s("webapp")),
                        ("description", s("Project type.")),
                    ]),
                ),
                (
                    "metaphor",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Project metaphor for agents.")),
                    ]),
                ),
                (
                    "stack",
                    obj(vec![
                        ("type", s("array")),
                        ("items", obj(vec![("type", s("string"))])),
                        ("default", Value::List(vec![])),
                        (
                            "description",
                            s("Technology stack entries (e.g. python, docker)."),
                        ),
                    ]),
                ),
                (
                    "repos",
                    obj(vec![
                        ("type", s("array")),
                        (
                            "items",
                            obj(vec![
                                ("type", s("object")),
                                ("required", value_str(&["name"])),
                                (
                                    "properties",
                                    obj(vec![
                                        (
                                            "name",
                                            obj(vec![
                                                ("type", s("string")),
                                                ("description", s("Repository name.")),
                                            ]),
                                        ),
                                        (
                                            "path",
                                            obj(vec![
                                                ("type", s("string")),
                                                ("default", s(".")),
                                                ("description", s("Relative path.")),
                                            ]),
                                        ),
                                        (
                                            "default_branch",
                                            obj(vec![
                                                ("type", s("string")),
                                                ("default", s("main")),
                                                ("description", s("Default branch.")),
                                            ]),
                                        ),
                                    ]),
                                ),
                                ("additionalProperties", Value::Bool(false)),
                            ]),
                        ),
                        ("description", s("Linked repositories.")),
                    ]),
                ),
            ]),
        ),
    ])
}

fn user_schema() -> Value {
    obj(vec![
        ("type", s("object")),
        ("description", s("User preferences.")),
        ("additionalProperties", Value::Bool(false)),
        (
            "properties",
            obj(vec![
                (
                    "name",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("User name.")),
                    ]),
                ),
                (
                    "language",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("Français")),
                        ("description", s("Communication language.")),
                    ]),
                ),
                (
                    "document_language",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("Français")),
                        ("description", s("Document output language.")),
                    ]),
                ),
                (
                    "skill_level",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str_sorted(VALID_SKILL_LEVELS)),
                        ("default", s("intermediate")),
                        ("description", s("User skill level.")),
                    ]),
                ),
            ]),
        ),
    ])
}

fn memory_schema() -> Value {
    let layer_field = |default: &str, description: &str| -> Value {
        obj(vec![
            ("type", s("string")),
            ("enum", value_str_sorted(VALID_LAYER_MODES)),
            ("default", s(default)),
            ("description", s(description)),
        ])
    };
    obj(vec![
        ("type", s("object")),
        ("description", s("Memory backend configuration.")),
        ("additionalProperties", Value::Bool(false)),
        (
            "properties",
            obj(vec![
                (
                    "backend",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str_sorted(VALID_BACKENDS)),
                        ("default", s("auto")),
                        ("description", s("Memory storage backend.")),
                    ]),
                ),
                (
                    "vector_database",
                    obj(vec![
                        ("type", s("boolean")),
                        ("default", Value::Bool(true)),
                        (
                            "description",
                            s(
                                "Enable a vector database for semantic retrieval. Set false for lexical-only \
                                 (sqlite FTS5, no vector DB, no service) — for environments forbidding a local vector database.",
                            ),
                        ),
                    ]),
                ),
                (
                    "retrieval_mode",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str(&["hybrid", "vector", "lexical", "none"])),
                        ("default", s("vector")),
                        (
                            "description",
                            s(
                                "Retrieval strategy. 'hybrid' fuses the vector ranking with a sqlite FTS5 BM25 \
                                 companion (RRF) and is what the composed profiles ask for. 'vector' queries the \
                                 semantic backend alone; 'lexical' forces BM25 without any vector backend.",
                            ),
                        ),
                    ]),
                ),
                (
                    "collection_prefix",
                    obj(vec![("type", s("string")), ("default", s("grimoire")), ("description", s("Collection name prefix."))]),
                ),
                (
                    "embedding_model",
                    obj(vec![("type", s("string")), ("default", s("")), ("description", s("Embedding model name."))]),
                ),
                ("qdrant_url", obj(vec![("type", s("string")), ("default", s("")), ("description", s("Qdrant server URL."))])),
                ("weaviate_url", obj(vec![("type", s("string")), ("default", s("")), ("description", s("Weaviate server URL."))])),
                (
                    "weaviate_api_key_env",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("GRIMOIRE_WEAVIATE_API_KEY")),
                        ("description", s("Environment variable that contains the Weaviate API key.")),
                    ]),
                ),
                (
                    "weaviate_collection",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Optional Weaviate collection name. Defaults to a normalized collection_prefix.")),
                    ]),
                ),
                (
                    "neo4j_uri",
                    obj(vec![("type", s("string")), ("default", s("")), ("description", s("Neo4j Bolt URI for graph memory layers."))]),
                ),
                ("neo4j_user", obj(vec![("type", s("string")), ("default", s("neo4j")), ("description", s("Neo4j user name."))])),
                (
                    "neo4j_password_env",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("GRIMOIRE_NEO4J_PASSWORD")),
                        ("description", s("Environment variable that contains the Neo4j password.")),
                    ]),
                ),
                ("neo4j_database", obj(vec![("type", s("string")), ("default", s("neo4j")), ("description", s("Neo4j database name."))])),
                (
                    "migration_source_backend",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Backend used as the source while migrating Memory OS data.")),
                    ]),
                ),
                (
                    "migration_target_backend",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Backend targeted by the current Memory OS migration.")),
                    ]),
                ),
                (
                    "migration_bundle_path",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        (
                            "description",
                            s("Portable migration bundle path used to preserve vectors, payloads, and graph projections."),
                        ),
                    ]),
                ),
                (
                    "mempalace_path",
                    obj(vec![("type", s("string")), ("default", s("")), ("description", s("Optional MemPalace / Chroma palace path."))]),
                ),
                ("ollama_url", obj(vec![("type", s("string")), ("default", s("")), ("description", s("Ollama server URL."))])),
                (
                    "layer_profile",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("standard")),
                        (
                            "description",
                            s(
                                "Named composition of the seven Memory OS layers: lexical | standard | graphe | \
                                 complet (see grimoire.memory.profiles). Chosen as one unit at setup; drives the layer fields \
                                 below and what `grimoire memory status` reports against.",
                            ),
                        ),
                    ]),
                ),
                (
                    "short_term_backend",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str_sorted(VALID_SHORT_TERM_BACKENDS)),
                        ("default", s("sqlite")),
                        ("description", s("Hot short-term memory backend. Use redis for distributed sessions.")),
                    ]),
                ),
                (
                    "redis_url",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("")),
                        ("description", s("Redis URL for short-term memory when enabled.")),
                    ]),
                ),
                ("knowledge_graph", layer_field("sqlite-sidecar", "Structured semantic knowledge graph layer.")),
                (
                    "memory_graph",
                    layer_field("sqlite-sidecar", "Semantic memory graph layer linking entities, facts, agents, and events."),
                ),
                ("code_graph", layer_field("planned", "Semantic code graph layer for symbols, files, tests, and ownership.")),
                ("task_memory", layer_field("planned", "Kanban and task lifecycle memory layer.")),
                ("visualization", layer_field("runtime-dashboard", "Visualization surface for memory layers.")),
            ]),
        ),
    ])
}

fn agents_schema() -> Value {
    obj(vec![
        ("type", s("object")),
        ("description", s("Agent configuration.")),
        ("additionalProperties", Value::Bool(false)),
        (
            "properties",
            obj(vec![
                (
                    "archetype",
                    obj(vec![
                        ("type", s("string")),
                        ("enum", value_str_sorted(KNOWN_ARCHETYPES)),
                        ("default", s("minimal")),
                        ("description", s("Agent archetype to use.")),
                    ]),
                ),
                (
                    "entry",
                    obj(vec![
                        ("type", s("string")),
                        ("default", s("concierge")),
                        (
                            "description",
                            s(
                                "Persona that answers when a request names no role. Injected into the main loop \
                                 at session start, since no host can open a session inside an agent. Empty string: none.",
                            ),
                        ),
                    ]),
                ),
                (
                    "custom_agents",
                    obj(vec![
                        ("type", s("array")),
                        ("items", obj(vec![("type", s("string"))])),
                        ("uniqueItems", Value::Bool(true)),
                        ("default", Value::List(vec![])),
                        ("description", s("Custom agent identifiers.")),
                    ]),
                ),
                (
                    "freshness_threshold_days",
                    obj(vec![
                        ("type", s("integer")),
                        ("minimum", Value::Int(1)),
                        ("default", Value::Int(90)),
                        (
                            "description",
                            s(
                                "Days without an agent.dispatch trace entry before `grimoire doctor` and the \
                                 cockpit flag a delivered or overridden agent as stale. Signal only — never \
                                 automatic removal or deprecation.",
                            ),
                        ),
                    ]),
                ),
            ]),
        ),
    ])
}

// ── validate_config ──────────────────────────────────────────────────────────

/// Une erreur de validation avant reconstruction cote Python. `suggestion`
/// est deja calculee pour tout sauf les cles inconnues (voir le docstring de
/// module) : dans ce cas `unknown_key`/`keyset_id` sont renseignes et
/// `suggestion` reste vide — `grimoire.core.validator` la recalcule avec
/// `_suggest_key(unknown_key, KEYSETS[keyset_id])`.
#[derive(Debug, Clone, PartialEq)]
struct RawError {
    path: String,
    message: String,
    suggestion: String,
    unknown_key: String,
    keyset_id: String,
}

fn err(path: impl Into<String>, message: impl Into<String>) -> RawError {
    RawError {
        path: path.into(),
        message: message.into(),
        suggestion: String::new(),
        unknown_key: String::new(),
        keyset_id: String::new(),
    }
}

fn err_sugg(
    path: impl Into<String>,
    message: impl Into<String>,
    suggestion: impl Into<String>,
) -> RawError {
    RawError {
        path: path.into(),
        message: message.into(),
        suggestion: suggestion.into(),
        unknown_key: String::new(),
        keyset_id: String::new(),
    }
}

/// Mirroir de `_check_unknown_keys` (validator.py).
fn check_unknown_keys(
    section: &Value,
    known: &[&str],
    path: &str,
    keyset_id: &str,
    errors: &mut Vec<RawError>,
) {
    let Value::Map(entries) = section else { return };
    for (key, _) in entries {
        if !known.contains(&key.as_str()) {
            let full_path = if path.is_empty() {
                key.clone()
            } else {
                format!("{path}.{key}")
            };
            errors.push(RawError {
                path: full_path,
                message: format!("Unknown key '{key}'."),
                suggestion: String::new(),
                unknown_key: key.clone(),
                keyset_id: keyset_id.to_string(),
            });
        }
    }
}

/// Verifie un champ scalaire de type "enumeration de chaines" (project.type,
/// user.skill_level, memory.backend, memory.short_term_backend, les cinq
/// modes de couche memoire, agents.archetype). Mirroir de
/// `if x is not None and x not in KNOWN: error(...)` — sauf que Python plante
/// (`TypeError: unhashable type`) des que `x` est une liste ou une table,
/// avant meme d'atteindre ce test ; voir le docstring de module.
fn check_enum_field(
    value: &Value,
    valid: &[&'static str],
    path: &str,
    unknown_label: &str,
    valid_label: &str,
    errors: &mut Vec<RawError>,
) {
    match value {
        Value::Str(s) => {
            if !valid.contains(&s.as_str()) {
                errors.push(err_sugg(
                    path,
                    format!("{unknown_label} '{s}'."),
                    format!("{valid_label}: {}", sorted_join(valid)),
                ));
            }
        }
        Value::List(_) | Value::Map(_) => {
            errors.push(err(
                path,
                format!("'{path}' must be a string (got {}).", value.describe_kind()),
            ));
        }
        // Bool/Int/Float : hachables, Python ne plante pas ici — meme
        // comportement (message identique, base sur la representation
        // Python-style de la valeur) que la branche "valeur inconnue".
        _ => {
            let display = value.as_display();
            if !valid.contains(&display.as_str()) {
                errors.push(err_sugg(
                    path,
                    format!("{unknown_label} '{display}'."),
                    format!("{valid_label}: {}", sorted_join(valid)),
                ));
            }
        }
    }
}

/// Acces "avec saut sur null" — mirroir de `x = section.get(key); if x is
/// not None: ...`, le motif utilise par la quasi-totalite des champs
/// optionnels du validateur (a l'exception de la selection des sections de
/// premier niveau, qui utilise la presence brute — voir `validate_core`).
fn field<'a>(section: &'a Value, key: &str) -> Option<&'a Value> {
    match section.get(key) {
        None | Some(Value::Null) => None,
        some => some,
    }
}

fn validate_project(section: &Value, errors: &mut Vec<RawError>) {
    if !section.is_map() {
        errors.push(err("project", "'project' must be a mapping."));
        return;
    }

    let name_ok = matches!(section.get("name"), Some(Value::Str(s)) if !s.is_empty());
    if !name_ok {
        errors.push(err(
            "project.name",
            "'project.name' is required and must be a non-empty string.",
        ));
    }

    if let Some(ptype) = field(section, "type") {
        check_enum_field(
            ptype,
            VALID_TYPES,
            "project.type",
            "Unknown project type",
            "Valid types",
            errors,
        );
    }

    if let Some(stack) = field(section, "stack") {
        match stack {
            Value::List(items) => {
                if !items.iter().all(|v| matches!(v, Value::Str(_))) {
                    errors.push(err("project.stack", "All stack entries must be strings."));
                }
            }
            _ => errors.push(err(
                "project.stack",
                "'project.stack' must be a list of strings.",
            )),
        }
    }

    if let Some(repos) = field(section, "repos") {
        match repos {
            Value::List(items) => {
                for (i, repo) in items.iter().enumerate() {
                    if !repo.is_map() {
                        errors.push(err(
                            format!("project.repos[{i}]"),
                            "Each repo must be a mapping with 'name'.",
                        ));
                        continue;
                    }
                    let name_val = repo.get("name");
                    let truthy = name_val.map(|v| !v.is_falsy()).unwrap_or(false);
                    if !truthy {
                        // Mirroir exact de `elif not repo.get("name")` — absente
                        // OU fausse (None, "", 0, [], {}...) : meme message
                        // qu'aujourd'hui, dans les deux cas.
                        errors.push(err(
                            format!("project.repos[{i}].name"),
                            "Repo must have a 'name' field.",
                        ));
                    } else if !matches!(name_val, Some(Value::Str(_))) {
                        // NOUVEAU : presente et vraie, mais pas une chaine (ex.
                        // `name: 123`) — passe aujourd'hui silencieusement cote
                        // Python (verifie empiriquement), alors que schema.py
                        // declare "name" comme une chaine.
                        errors.push(err(
                            format!("project.repos[{i}].name"),
                            "Repo 'name' must be a string.",
                        ));
                    }
                    for (key, message) in [
                        ("path", "Repo 'path' must be a string."),
                        ("default_branch", "Repo 'default_branch' must be a string."),
                    ] {
                        if let Some(v) = field(repo, key) {
                            if !matches!(v, Value::Str(_)) {
                                errors.push(err(format!("project.repos[{i}].{key}"), message));
                            }
                        }
                    }
                }
            }
            _ => errors.push(err("project.repos", "'project.repos' must be a list.")),
        }
    }

    check_unknown_keys(section, KNOWN_PROJECT_KEYS, "project", "project", errors);
}

fn validate_user(section: &Value, errors: &mut Vec<RawError>) {
    if !section.is_map() {
        errors.push(err("user", "'user' must be a mapping."));
        return;
    }

    if let Some(skill) = field(section, "skill_level") {
        check_enum_field(
            skill,
            VALID_SKILL_LEVELS,
            "user.skill_level",
            "Invalid skill level",
            "Valid levels",
            errors,
        );
    }

    // NOUVEAU : `_validate_user` (Python) ne type-verifie que skill_level.
    // name/language/document_language ne sont jamais verifies, alors que
    // schema.py les declare comme des chaines.
    for key in ["name", "language", "document_language"] {
        if let Some(v) = field(section, key) {
            if !matches!(v, Value::Str(_)) {
                errors.push(err(
                    format!("user.{key}"),
                    format!("'user.{key}' must be a string."),
                ));
            }
        }
    }

    check_unknown_keys(section, KNOWN_USER_KEYS, "user", "user", errors);
}

fn validate_memory(section: &Value, errors: &mut Vec<RawError>) {
    if !section.is_map() {
        errors.push(err("memory", "'memory' must be a mapping."));
        return;
    }

    if let Some(v) = field(section, "backend") {
        check_enum_field(
            v,
            VALID_BACKENDS,
            "memory.backend",
            "Unknown memory backend",
            "Valid backends",
            errors,
        );
    }
    if let Some(v) = field(section, "short_term_backend") {
        check_enum_field(
            v,
            VALID_SHORT_TERM_BACKENDS,
            "memory.short_term_backend",
            "Unknown short-term memory backend",
            "Valid short-term backends",
            errors,
        );
    }
    for key in [
        "knowledge_graph",
        "memory_graph",
        "code_graph",
        "task_memory",
        "visualization",
    ] {
        if let Some(v) = field(section, key) {
            check_enum_field(
                v,
                VALID_LAYER_MODES,
                &format!("memory.{key}"),
                "Unknown memory layer mode",
                "Valid modes",
                errors,
            );
        }
    }

    check_unknown_keys(section, KNOWN_MEMORY_KEYS, "memory", "memory", errors);
}

fn validate_agents(section: &Value, errors: &mut Vec<RawError>) {
    if !section.is_map() {
        errors.push(err("agents", "'agents' must be a mapping."));
        return;
    }

    if let Some(v) = field(section, "archetype") {
        check_enum_field(
            v,
            KNOWN_ARCHETYPES,
            "agents.archetype",
            "Unknown archetype",
            "Valid archetypes",
            errors,
        );
    }

    if let Some(custom) = field(section, "custom_agents") {
        match custom {
            Value::List(items) => {
                let mut seen: Vec<&str> = Vec::new();
                for (i, item) in items.iter().enumerate() {
                    match item {
                        Value::Str(s) => {
                            if seen.contains(&s.as_str()) {
                                errors.push(err_sugg(
                                    format!("agents.custom_agents[{i}]"),
                                    format!("Duplicate agent ID '{s}'."),
                                    "Remove the duplicate entry.",
                                ));
                            } else {
                                seen.push(s.as_str());
                            }
                        }
                        _ => errors.push(err(
                            format!("agents.custom_agents[{i}]"),
                            "Agent ID must be a string.",
                        )),
                    }
                }
            }
            _ => errors.push(err(
                "agents.custom_agents",
                "'agents.custom_agents' must be a list of strings.",
            )),
        }
    }

    if let Some(v) = field(section, "freshness_threshold_days") {
        let valid = matches!(v, Value::Int(i) if *i >= 1);
        if !valid {
            errors.push(err(
                "agents.freshness_threshold_days",
                "'agents.freshness_threshold_days' must be a positive integer (days).",
            ));
        }
    }

    check_unknown_keys(section, KNOWN_AGENTS_KEYS, "agents", "agents", errors);
}

fn validate_installed_archetypes(section: &Value, errors: &mut Vec<RawError>) {
    match section {
        Value::List(items) => {
            // NOUVEAU : `_validate_installed_archetypes` (Python) ne
            // verifie que le type du conteneur, jamais celui des elements —
            // `installed_archetypes: [1, 2, 3]` passe aujourd'hui sans la
            // moindre erreur (verifie empiriquement), alors que schema.py
            // declare les items comme des chaines.
            for (i, item) in items.iter().enumerate() {
                if !matches!(item, Value::Str(_)) {
                    errors.push(err(
                        format!("installed_archetypes[{i}]"),
                        "Archetype identifier must be a string.",
                    ));
                }
            }
        }
        _ => errors.push(err(
            "installed_archetypes",
            "'installed_archetypes' must be a list of strings.",
        )),
    }
}

/// Coeur pur de `validate_config` (validator.py) — aucun type PyO3, testable
/// directement par `cargo test`. Mirroir exact de l'ordre des verifications
/// Python : section `project` (requise), puis `user`/`memory`/`agents`/
/// `installed_archetypes` (optionnelles, seulement si presentes — presence
/// brute, pas "presente et non nulle", voir le commentaire sur `field`
/// ci-dessus), puis les cles inconnues de premier niveau.
fn validate_core(data: &Value) -> Vec<RawError> {
    let mut errors = Vec::new();

    if !data.is_map() {
        errors.push(err_sugg(
            "(root)",
            "Config must be a YAML mapping.",
            "Ensure the file starts with key-value pairs.",
        ));
        return errors;
    }

    match data.get("project") {
        None => errors.push(err_sugg(
            "project",
            "Missing required 'project' section.",
            "Add: project:\\n  name: \"my-project\"",
        )),
        Some(project) => validate_project(project, &mut errors),
    }

    if let Some(user) = data.get("user") {
        validate_user(user, &mut errors);
    }
    if let Some(memory) = data.get("memory") {
        validate_memory(memory, &mut errors);
    }
    if let Some(agents) = data.get("agents") {
        validate_agents(agents, &mut errors);
    }
    if let Some(installed) = data.get("installed_archetypes") {
        validate_installed_archetypes(installed, &mut errors);
    }

    check_unknown_keys(data, KNOWN_TOP_KEYS, "", "top", &mut errors);

    errors
}

// Tout ce qui suit touche a PyO3 et n'existe que sous la feature
// `extension-module` (cf. Cargo.toml) : `schema_core`/`validate_core`
// ci-dessus, seule logique couverte par `cargo test --no-default-features`,
// n'en dependent pas.
#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{schema_core, validate_core, Value};
    use pyo3::exceptions::PyTypeError;
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

    /// Convertit une valeur Python arbitraire (une donnee YAML deja
    /// analysee par ruamel, potentiellement malformee) vers la
    /// representation dynamique pure de ce crate. Aucune de ces conversions
    /// ne peut echouer : contrairement a la frontiere du premier port (qui
    /// rejetait explicitement toute valeur d'enum non reconnue), ici c'est
    /// `validate_core` lui-meme qui doit voir la forme malformee pour
    /// pouvoir la rejeter proprement — la convertir en erreur PyO3 des ce
    /// stade reviendrait a recreer, cote frontiere, exactement le plantage
    /// non gere que ce port est cense remplacer par un `ValidationError`
    /// explicite.
    fn to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
        if obj.is_none() {
            return Ok(Value::Null);
        }
        // PyBool avant PyInt : en Python, bool est une sous-classe de int
        // (`isinstance(True, int)` est vrai), donc l'ordre des `downcast`
        // compte pour ne pas confondre `True`/`False` avec `1`/`0`.
        if let Ok(b) = obj.cast::<PyBool>() {
            return Ok(Value::Bool(b.is_true()));
        }
        if let Ok(d) = obj.cast::<PyDict>() {
            let mut entries = Vec::with_capacity(d.len());
            for (k, v) in d.iter() {
                entries.push((k.str()?.to_string(), to_value(&v)?));
            }
            return Ok(Value::Map(entries));
        }
        if let Ok(l) = obj.cast::<PyList>() {
            let mut items = Vec::with_capacity(l.len());
            for item in l.iter() {
                items.push(to_value(&item)?);
            }
            return Ok(Value::List(items));
        }
        if let Ok(t) = obj.cast::<PyTuple>() {
            let mut items = Vec::with_capacity(t.len());
            for item in t.iter() {
                items.push(to_value(&item)?);
            }
            return Ok(Value::List(items));
        }
        if let Ok(st) = obj.cast::<PyString>() {
            return Ok(Value::Str(st.to_string()));
        }
        if let Ok(i) = obj.extract::<i64>() {
            return Ok(Value::Int(i));
        }
        if let Ok(f) = obj.extract::<f64>() {
            return Ok(Value::Float(f));
        }
        // Type Python non reconnu (objet personnalise, etc.) : reproduit la
        // coercion implicite d'un f-string via `str()`, pour que les
        // messages restent lisibles plutot que d'echouer completement sur
        // une forme exotique que `validate_config` n'a pas a rejeter elle-meme.
        Ok(Value::Str(obj.str()?.to_string()))
    }

    /// Convertit la representation dynamique de ce crate vers un objet
    /// Python natif (`None`/`bool`/`int`/`float`/`str`/`list`/`dict`).
    /// Utilisee a la fois par `generate_schema` (structure statique) et
    /// pourrait l'etre par `validate_config` si un jour il fallait renvoyer
    /// la donnee elle-meme — ce n'est pas le cas aujourd'hui (voir la
    /// signature des tuples d'erreur).
    fn value_to_py<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
        Ok(match value {
            Value::Null => py.None().into_bound(py),
            Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any(),
            Value::Int(i) => i.into_pyobject(py)?.into_any(),
            Value::Float(f) => f.into_pyobject(py)?.into_any(),
            Value::Str(st) => st.into_pyobject(py)?.into_any(),
            Value::List(items) => {
                let list = PyList::empty(py);
                for item in items {
                    list.append(value_to_py(py, item)?)?;
                }
                list.into_any()
            }
            Value::Map(entries) => {
                let dict = PyDict::new(py);
                for (k, v) in entries {
                    dict.set_item(k, value_to_py(py, v)?)?;
                }
                dict.into_any()
            }
        })
    }

    /// Frontiere PyO3 pour `grimoire.core.schema.generate_schema` — aucune
    /// entree, sortie deterministe. Voir `schema_core` pour la structure.
    #[pyfunction]
    fn generate_schema(py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(value_to_py(py, &schema_core())?.unbind())
    }

    /// Frontiere PyO3 pour `grimoire.core.validator.validate_config`.
    /// Retourne un tuple par erreur : `(path, message, suggestion,
    /// unknown_key, keyset_id)` — voir le docstring de module pour pourquoi
    /// `unknown_key`/`keyset_id` existent (suggestions "did you mean ?"
    /// recalculees cote Python).
    #[pyfunction]
    fn validate_config(
        data: Bound<'_, PyAny>,
    ) -> PyResult<Vec<(String, String, String, String, String)>> {
        let value = to_value(&data).map_err(|e| {
            PyTypeError::new_err(format!("grimoire_schema_core: conversion impossible: {e}"))
        })?;
        Ok(validate_core(&value)
            .into_iter()
            .map(|e| (e.path, e.message, e.suggestion, e.unknown_key, e.keyset_id))
            .collect())
    }

    #[pymodule]
    fn grimoire_schema_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(generate_schema, m)?)?;
        m.add_function(wrap_pyfunction!(validate_config, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(entries: Vec<(&str, Value)>) -> Value {
        Value::Map(
            entries
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        )
    }

    fn list_str(items: &[&str]) -> Value {
        Value::List(items.iter().map(|s| Value::Str((*s).to_string())).collect())
    }

    fn minimal() -> Value {
        map(vec![(
            "project",
            map(vec![("name", Value::Str("test".to_string()))]),
        )])
    }

    // ── schema_core : mirroir de tests/unit/core/test_schema.py ────────────

    #[test]
    fn schema_has_expected_top_level_shape() {
        let schema = schema_core();
        assert_eq!(
            schema.get("$schema"),
            Some(&Value::Str(
                "https://json-schema.org/draft/2020-12/schema".to_string()
            ))
        );
        assert_eq!(schema.get("type"), Some(&Value::Str("object".to_string())));
        assert_eq!(schema.get("additionalProperties"), Some(&Value::Bool(true)));
        let props = schema.get("properties").expect("properties");
        for key in [
            "project",
            "user",
            "memory",
            "agents",
            "installed_archetypes",
        ] {
            assert!(props.get(key).is_some(), "missing property {key}");
        }
    }

    #[test]
    fn schema_project_requires_name() {
        let project = project_schema();
        assert_eq!(project.get("required"), Some(&list_str(&["name"])));
    }

    #[test]
    fn schema_project_type_enum_matches_source_order() {
        // schema.py utilise `list(VALID_PROJECT_TYPES)`, non trie.
        let project = project_schema();
        let type_prop = project.get("properties").unwrap().get("type").unwrap();
        assert_eq!(type_prop.get("enum"), Some(&list_str(VALID_TYPES)));
    }

    #[test]
    fn schema_memory_backend_enum_is_sorted() {
        let memory = memory_schema();
        let backend = memory.get("properties").unwrap().get("backend").unwrap();
        assert_eq!(
            backend.get("enum"),
            Some(&list_str(&sorted_copy(VALID_BACKENDS)))
        );
    }

    #[test]
    fn schema_declares_agents_entry() {
        // Mirroir de test_schema_declares_agents_entry (test_validator.py).
        let agents = agents_schema();
        assert!(agents.get("properties").unwrap().get("entry").is_some());
    }

    #[test]
    fn schema_declares_agents_freshness_threshold_days() {
        // Mirroir de test_schema.py (issue #396).
        let agents = agents_schema();
        let field = agents
            .get("properties")
            .unwrap()
            .get("freshness_threshold_days")
            .expect("freshness_threshold_days");
        assert_eq!(field.get("default"), Some(&Value::Int(90)));
    }

    #[test]
    fn negative_freshness_threshold_is_flagged() {
        // Mirroir de test_validator.py (issue #396).
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            (
                "agents",
                map(vec![("freshness_threshold_days", Value::Int(0))]),
            ),
        ]);
        let errors = validate_core(&data);
        assert!(errors
            .iter()
            .any(|e| e.path == "agents.freshness_threshold_days"));
    }

    #[test]
    fn valid_freshness_threshold_has_no_error() {
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            (
                "agents",
                map(vec![("freshness_threshold_days", Value::Int(200))]),
            ),
        ]);
        let errors = validate_core(&data);
        assert!(!errors
            .iter()
            .any(|e| e.path == "agents.freshness_threshold_days"));
    }

    #[test]
    fn schema_is_deterministic() {
        assert_eq!(schema_core(), schema_core());
    }

    // ── validate_core : mirroir de tests/unit/core/test_validator.py ───────

    #[test]
    fn valid_minimal_has_no_errors() {
        assert!(validate_core(&minimal()).is_empty());
    }

    #[test]
    fn not_a_mapping_reports_root_error() {
        let errors = validate_core(&Value::Str("string".to_string()));
        assert_eq!(errors.len(), 1);
        assert!(errors[0].message.contains("mapping"));
    }

    #[test]
    fn missing_project_is_flagged() {
        let data = map(vec![(
            "user",
            map(vec![("name", Value::Str("x".to_string()))]),
        )]);
        let errors = validate_core(&data);
        assert!(errors.iter().any(|e| e.path == "project"));
    }

    #[test]
    fn empty_project_name_is_flagged() {
        let data = map(vec![(
            "project",
            map(vec![("name", Value::Str(String::new()))]),
        )]);
        let errors = validate_core(&data);
        assert!(errors.iter().any(|e| e.path == "project.name"));
    }

    #[test]
    fn unknown_project_type_is_flagged_with_suggestion() {
        let data = map(vec![(
            "project",
            map(vec![
                ("name", Value::Str("x".to_string())),
                ("type", Value::Str("notatype".to_string())),
            ]),
        )]);
        let errors = validate_core(&data);
        let e = errors
            .iter()
            .find(|e| e.path == "project.type")
            .expect("project.type error");
        assert!(e.message.contains("notatype"));
        assert!(e.suggestion.starts_with("Valid types:"));
    }

    #[test]
    fn duplicate_custom_agent_is_flagged() {
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            (
                "agents",
                map(vec![("custom_agents", list_str(&["a", "a"]))]),
            ),
        ]);
        let errors = validate_core(&data);
        assert!(errors.iter().any(|e| e.message.contains("Duplicate")));
    }

    #[test]
    fn unknown_top_level_key_is_flagged_for_python_side_suggestion() {
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            ("projct", Value::Bool(true)),
        ]);
        let errors = validate_core(&data);
        let e = errors
            .iter()
            .find(|e| e.unknown_key == "projct")
            .expect("unknown key error");
        assert_eq!(e.keyset_id, "top");
        assert_eq!(e.suggestion, ""); // recalculee cote Python via _suggest_key
    }

    // ── Entrees malformees : la propriete que ce port doit demontrer ───────

    #[test]
    fn list_in_place_of_enum_scalar_is_rejected_explicitly_not_crashed() {
        // Cote Python : `{"project": {"name": "x", "type": ["webapp"]}}` fait
        // lever `TypeError: unhashable type: 'list'` depuis validate_config
        // (verifie empiriquement, voir le docstring de module). Rust ne
        // plante jamais : la forme est verifiee avant toute comparaison
        // d'ensemble.
        let data = map(vec![(
            "project",
            map(vec![
                ("name", Value::Str("x".to_string())),
                ("type", list_str(&["webapp"])),
            ]),
        )]);
        let errors = validate_core(&data);
        let e = errors
            .iter()
            .find(|e| e.path == "project.type")
            .expect("project.type error");
        assert!(e.message.contains("must be a string"));
    }

    #[test]
    fn mapping_in_place_of_enum_scalar_is_rejected_explicitly() {
        let data = map(vec![(
            "memory",
            map(vec![("backend", map(vec![("nested", Value::Bool(true))]))]),
        )]);
        let mut base = minimal();
        if let Value::Map(entries) = &mut base {
            if let Value::Map(data_entries) = data {
                entries.extend(data_entries);
            }
        }
        let errors = validate_core(&base);
        let e = errors
            .iter()
            .find(|e| e.path == "memory.backend")
            .expect("memory.backend error");
        assert!(e.message.contains("must be a string"));
    }

    #[test]
    fn non_string_repo_name_is_rejected_where_python_accepts_it() {
        // Cote Python : `not repo.get("name")` est faux pour `123` (verite
        // non nulle), donc aucune erreur n'est levee — verifie
        // empiriquement. schema.py declare pourtant "name" comme une
        // chaine.
        let data = map(vec![(
            "project",
            map(vec![
                ("name", Value::Str("x".to_string())),
                (
                    "repos",
                    Value::List(vec![map(vec![("name", Value::Int(123))])]),
                ),
            ]),
        )]);
        let errors = validate_core(&data);
        assert!(errors
            .iter()
            .any(|e| e.path == "project.repos[0].name" && e.message.contains("must be a string")));
    }

    #[test]
    fn non_string_installed_archetype_item_is_rejected_where_python_accepts_it() {
        // Cote Python : seul le type du conteneur (list) est verifie, jamais
        // celui des elements — verifie empiriquement.
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            (
                "installed_archetypes",
                Value::List(vec![Value::Int(1), Value::Str("minimal".to_string())]),
            ),
        ]);
        let errors = validate_core(&data);
        assert!(errors.iter().any(|e| e.path == "installed_archetypes[0]"));
        assert!(!errors.iter().any(|e| e.path == "installed_archetypes[1]"));
    }

    #[test]
    fn non_string_user_name_is_rejected_where_python_accepts_it() {
        let data = map(vec![
            ("project", map(vec![("name", Value::Str("x".to_string()))])),
            ("user", map(vec![("name", Value::Int(123))])),
        ]);
        let errors = validate_core(&data);
        assert!(errors
            .iter()
            .any(|e| e.path == "user.name" && e.message.contains("must be a string")));
    }

    #[test]
    fn well_typed_optional_fields_never_trigger_the_new_checks() {
        // Garde-fou : les verifications ajoutees ne doivent jamais se
        // declencher sur une configuration entierement bien typee — sans
        // quoi la suite de tests Python existante, rejouee sous le backend
        // rust, cesserait de passer sans modification.
        let data = map(vec![
            (
                "project",
                map(vec![
                    ("name", Value::Str("my-app".to_string())),
                    ("type", Value::Str("webapp".to_string())),
                    ("stack", list_str(&["python", "docker"])),
                    (
                        "repos",
                        Value::List(vec![map(vec![
                            ("name", Value::Str("my-app".to_string())),
                            ("path", Value::Str(".".to_string())),
                        ])]),
                    ),
                ]),
            ),
            (
                "user",
                map(vec![
                    ("name", Value::Str("Guilhem".to_string())),
                    ("language", Value::Str("Français".to_string())),
                    ("skill_level", Value::Str("expert".to_string())),
                ]),
            ),
            (
                "memory",
                map(vec![("backend", Value::Str("local".to_string()))]),
            ),
            (
                "agents",
                map(vec![
                    ("archetype", Value::Str("minimal".to_string())),
                    ("custom_agents", list_str(&["my-agent"])),
                ]),
            ),
            ("installed_archetypes", list_str(&["minimal"])),
        ]);
        assert!(validate_core(&data).is_empty());
    }
}
