//! Coeur Rust optionnel de `grimoire.hosts.collect` et `grimoire.hosts.surface`.
//!
//! Troisieme port Rust du kit (issue #354 de Guilhem-Bonnet/Grimoire-kit,
//! apres `rust/grimoire-policies-core/` et `rust/grimoire-schema-core/`,
//! meme montage). Ce crate porte les fonctions **pures** de la lecture des
//! definitions d'agent et de la garde de distinction :
//!
//! - `parse_frontmatter(text)` (`collect.py`) : separation du frontmatter
//!   YAML et du corps d'un fichier d'agent.
//! - `_tool_verbs(raw)`/`_tool_verbs_with_rejects(raw)`, `_str_tuple(raw)`,
//!   `infer_tools(body, description)`, `_max_turns(value)` (`collect.py`) :
//!   lecture tolerante des cles de frontmatter et inference du perimetre
//!   d'outils. `_tool_verbs_with_rejects` retourne aussi les jetons hors de
//!   `ToolVerb` (perimetre inchange, rejet desormais visible — voir plus
//!   bas).
//! - La construction des champs purs d'un `AgentSpec` a partir de
//!   `(name, meta, body)` deja lus — sans toucher au disque (pas de
//!   resolution de `known_skills`, pas de verification d'existence de
//!   `context:`, laissees a `collect_agents` cote Python).
//! - `AgentSpec.fingerprint()` et `duplicate_agent_fingerprints()`
//!   (`surface.py`) : l'empreinte de faisceau et la detection de collision.
//! - La logique a deux regimes de `build_surface` (`collect.py`) : une
//!   collision impliquant un agent d'override leve, une collision purement
//!   cote kit devient une note — exposee ici sous forme pure
//!   (`partition_duplicate_pairs`), le formatage du message d'erreur/de la
//!   note restant cote Python (mêmes chaines qu'aujourd'hui, deja testees).
//!
//! Laisse en Python, deliberement : la decouverte des fichiers sur disque
//! (`_agent_files`, `_skill_files`, `_bundled`), `path.read_text`,
//! `entry_agent_name` (lit `project-context.yaml`), la resolution de
//! `layout.agent_identity`, la verification que `skills:`/`context:`
//! resolvent contre un inventaire ou le disque (deux verifications qui ont
//! besoin d'un etat externe a la fonction pure), les emetteurs, les
//! commandes, les hooks et les permissions. Ce module ne fait aucune E/S.
//!
//! ## Une dependance de plus que les deux premiers ports, et pourquoi
//!
//! `rust/grimoire-policies-core/` et `rust/grimoire-schema-core/` recevaient
//! deja une donnee analysee (un dict Python venu de ruamel). Ici,
//! `parse_frontmatter` lit lui-meme du texte YAML brut — il faut donc un
//! analyseur YAML cote Rust. `yaml-rust2` (fork maintenu de `yaml-rust`,
//! aucun `unsafe`, feature `encoding` desactivee car l'entree est deja une
//! `&str` UTF-8) a ete verifie empiriquement contre le comportement de
//! ruamel (`typ="safe"`) sur les points qui comptent pour ce module :
//! `yes`/`no`/`on`/`off` restent des chaines dans les deux (schema YAML 1.2,
//! pas le resolveur 1.1 de PyYAML), les entiers et booleens stricts
//! (`true`/`false`) sont identiques, et **les deux rejettent une cle
//! dupliquee** avec une erreur plutot que de garder silencieusement la
//! derniere valeur (`DuplicateKeyError` cote ruamel, `duplicated key in
//! mapping` cote yaml-rust2) — `parse_frontmatter` traite deja toute erreur
//! de ce genre comme un frontmatter absent (`({}, body)`, jamais une
//! exception qui remonte), donc ce point d'accord entre les deux moteurs
//! n'est meme pas visible a la frontiere.
//!
//! ## Ce que le compilateur/l'oracle Rust a trouve
//!
//! `_max_turns` (Python, `collect.py`) plante aujourd'hui sur une entree
//! reelle : `"max_turns: ²"` (un caractere Unicode que `str.isdigit()`
//! reconnait comme un chiffre mais que `int()` refuse) fait lever
//! `ValueError: invalid literal for int() with base 10: '²'`, non rattrapee,
//! jusqu'a faire planter `collect_agents`/`build_surface`/`grimoire host
//! sync` sur un fichier d'agent par ailleurs valide. Verifie empiriquement
//! (voir `tests/unit/test_hosts_rust_parity.py::test_max_turns_unicode_digit_no_longer_crashes_python`).
//! Corrige dans cette PR (trivial, sans risque : `str.isascii()` en plus de
//! `str.isdigit()` avant `int()`, `max_turns.py`/`collect.py`) — le coeur
//! Rust ne teste que des chiffres ASCII des le depart et n'a jamais ce
//! probleme, il est donc l'oracle qui a revele le defaut Python, exactement
//! le role attendu de ce port (cf. commit du port #392).
//!
//! `_tool_verbs` (Python) acceptait silencieusement un verbe d'outil hors de
//! `ToolVerb` : il etait ignore, sans etre rejete NI signale (`tools: [read,
//! bogus]` donnait `(ToolVerb.READ,)`, sans avertissement). Le perimetre
//! d'outils reste **volontairement** inchange par ce port — la fonction
//! elle-meme documente que "an over-granted boundary is a governance hole,
//! while a too-narrow one is a visible failure the operator can correct",
//! et un verbe inconnu *retrecit* le perimetre plutot que de l'elargir ;
//! rejeter au lieu d'ignorer modifierait silencieusement le perimetre
//! d'outils de tout agent existant dont le frontmatter contient deja une
//! coquille, au moment de la mise a jour du kit — pas un correctif trivial
//! et sans risque. Ce que ce port change, en revanche : le rejet n'est plus
//! invisible. `tool_verbs_with_rejects_core` retourne aussi les jetons
//! rejetes (dedupliques, ordre de premiere apparition) ; cote Python,
//! `collect_agents` transforme cette liste en une note de
//! `ProjectSurface.notes` nommant l'agent et les jetons inconnus — la meme
//! discipline que la garde de distinction (visible, jamais bloquant). Les
//! deux backends produisent exactement le meme ensemble de rejets et donc
//! la meme note (voir `tests/unit/test_hosts_rust_parity.py`).
//!
//! Un `tools:` qui n'est ni une liste ni une chaine (par ex. `tools: 3` ou
//! `tools: {a: b}`) est traite comme absent par les deux backends : `()`,
//! puis `infer_tools` prend le relais. Documente, pas un defaut — c'est
//! exactement ce que `_tool_verbs` fait deja pour toute valeur qui n'est ni
//! `str` ni `list`.

#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

use yaml_rust2::{Yaml, YamlLoader};

// ── Representation dynamique minimale ───────────────────────────────────────
//
// Meme philosophie que rust/grimoire-schema-core/ : une representation
// suffisante pour porter une valeur YAML/Python arbitraire (frontmatter d'un
// fichier d'agent, potentiellement malforme), convertible dans les deux sens
// a la frontiere PyO3.
#[derive(Debug, Clone, PartialEq)]
enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    /// Paires (cle, valeur) dans l'ordre d'insertion, comme un dict Python
    /// (3.7+) ou un mapping YAML analyse par ruamel/yaml-rust2.
    Map(Vec<(String, Value)>),
}

impl Value {
    /// Acces a une cle d'un mapping. `None` si absente OU si `self` n'est
    /// pas un mapping — a distinguer de `Some(&Value::Null)` (cle presente,
    /// valeur explicitement nulle). Mirroir de `dict.get(key)` (un seul
    /// argument) cote Python : les deux cas se confondent en `None` a cette
    /// frontiere, sauf ou le code Python appelle explicitement la forme a
    /// deux arguments (`dict.get(key, default)`), geree a part (voir
    /// `model_affinity_from_frontmatter_core`).
    fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Map(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    fn as_map(&self) -> Option<&[(String, Value)]> {
        match self {
            Value::Map(entries) => Some(entries),
            _ => None,
        }
    }

    /// Verite a la Python (`bool(x)`) : utilise pour reproduire a l'identique
    /// `meta.get("description") or f"Grimoire agent {name}"`.
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
}

/// `str(value)` a la Python, pour les types que ce module peut rencontrer.
/// Exact pour `None`/`bool`/`int`/`str` ; approche au mieux pour `float`
/// (repr le plus court n'est pas garanti bit-a-bit identique a CPython, mais
/// aucun champ porte ici n'attend un flottant en usage reel) et pour
/// `list`/`dict` (reproduit le style de `repr()` Python, mais ce chemin
/// n'est exerce par aucun frontmatter reel — `description:`/`skills:`/
/// `context:`/`tools:` sont toujours des scalaires ou des listes de
/// scalaires dans toute definition d'agent existante).
fn python_str(value: &Value) -> String {
    match value {
        Value::Str(s) => s.clone(),
        _ => python_repr(value),
    }
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Int(i) => i.to_string(),
        Value::Float(f) => python_float_repr(*f),
        Value::Str(s) => python_repr_string(s),
        Value::List(items) => {
            let inner: Vec<String> = items.iter().map(python_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Map(entries) => {
            let inner: Vec<String> = entries
                .iter()
                .map(|(k, v)| format!("{}: {}", python_repr_string(k), python_repr(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

fn python_float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".to_string();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    let rendered = format!("{f}");
    if rendered.contains(['.', 'e', 'E']) {
        rendered
    } else {
        format!("{rendered}.0")
    }
}

/// `str(value)` non quote quand `value` est deja une chaine (utilise par
/// `python_str`), quote a la Python quand c'est un element imbrique
/// (utilise par `python_repr`). Best-effort : gere le choix de guillemet
/// (simple par defaut, double si la chaine contient un guillemet simple et
/// aucun double) et l'echappement des cas courants, sans pretendre couvrir
/// tout l'espace Unicode que `repr()` gere cote CPython.
fn python_repr_string(s: &str) -> String {
    let use_double = s.contains('\'') && !s.contains('"');
    let quote = if use_double { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

// ── Conversion YAML -> Value ─────────────────────────────────────────────────

fn yaml_to_value(y: &Yaml) -> Value {
    match y {
        Yaml::Null | Yaml::BadValue => Value::Null,
        Yaml::Boolean(b) => Value::Bool(*b),
        Yaml::Integer(i) => Value::Int(*i),
        Yaml::Real(raw) => match raw.parse::<f64>() {
            Ok(f) => Value::Float(f),
            // `.inf`/`.nan` et consorts : yaml-rust2 garde le texte brut
            // pour ces formes speciales plutot que de les resoudre en f64.
            // Aucun champ de ce module ne consomme un flottant en usage
            // reel (voir `python_str`) ; conserver le texte brut suffit.
            Err(_) => Value::Str(raw.clone()),
        },
        Yaml::String(s) => Value::Str(s.clone()),
        Yaml::Array(items) => Value::List(items.iter().map(yaml_to_value).collect()),
        Yaml::Hash(map) => {
            let mut entries = Vec::with_capacity(map.len());
            for (k, v) in map {
                let key = match k {
                    Yaml::String(s) => s.clone(),
                    other => python_str(&yaml_to_value(other)),
                };
                entries.push((key, yaml_to_value(v)));
            }
            Value::Map(entries)
        }
        Yaml::Alias(_) => Value::Null,
    }
}

/// Analyse un texte YAML ; `None` sur toute erreur (mirroir du `except
/// Exception` generique de `parse_frontmatter` cote Python, qui ne
/// distingue pas les classes d'erreur de ruamel).
fn parse_yaml_text(text: &str) -> Option<Value> {
    let docs = YamlLoader::load_from_str(text).ok()?;
    match docs.first() {
        Some(y) => Some(yaml_to_value(y)),
        None => Some(Value::Null),
    }
}

// ── parse_frontmatter ────────────────────────────────────────────────────────
//
// Reproduit `_FRONTMATTER_RE = re.compile(r"\A(?:﻿)?(?:<!--.*?-->\s*)?
// ---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)` sans moteur de regex : le
// motif est ancre des deux cotes et suffisamment simple pour une analyse
// manuelle caractere par caractere, ce qui evite une dependance de plus.

fn strip_prefix_bom(text: &str) -> &str {
    text.strip_prefix('\u{feff}').unwrap_or(text)
}

/// Consomme un commentaire HTML optionnel (`<!--...-->`) suivi de tout
/// l'espace qui suit, non gourmand comme `.*?` sous `re.DOTALL` (s'arrete au
/// premier `-->`).
fn strip_leading_html_comment(text: &str) -> &str {
    let Some(rest) = text.strip_prefix("<!--") else {
        return text;
    };
    let Some(end) = rest.find("-->") else {
        // Commentaire jamais ferme : le motif Python entier echoue a
        // matcher (le groupe optionnel n'aide pas un `\A` qui doit ensuite
        // voir `---`) — retourner le texte original fait echouer la suite
        // (pas de `---` en tete) exactement pareil.
        return text;
    };
    let after_comment = &rest[end + "-->".len()..];
    after_comment.trim_start_matches(|c: char| c.is_whitespace())
}

/// Repere la fermeture du frontmatter : le premier `\n---` suivi de tout
/// l'espace qui suit (potentiellement zero caractere). Retourne
/// `(frontmatter_yaml, corps)` ou `None` si aucune fermeture n'existe —
/// dans ce cas `parse_frontmatter` renvoie le texte entier comme corps,
/// comme le ferait un `re.match` qui echoue completement.
fn split_frontmatter(text: &str) -> Option<(String, String)> {
    let after_bom = strip_prefix_bom(text);
    let after_comment = strip_leading_html_comment(after_bom);
    let after_open = after_comment.strip_prefix("---")?;
    // `\s*\n` : l'ouverture peut etre suivie d'espace puis d'un saut de
    // ligne obligatoire avant le contenu du frontmatter.
    let mut idx = 0usize;
    let bytes: Vec<char> = after_open.chars().collect();
    while idx < bytes.len() && bytes[idx] != '\n' && bytes[idx].is_whitespace() {
        idx += 1;
    }
    if idx >= bytes.len() || bytes[idx] != '\n' {
        return None;
    }
    let content_start: usize = bytes[..=idx].iter().collect::<String>().len();
    let after_open_line = &after_open[content_start..];

    // Premiere occurrence de "\n---" dans ce qui reste : fermeture du
    // frontmatter (motif non gourmand `(.*?)\n---`).
    let close_rel = find_closing_fence(after_open_line)?;
    let frontmatter_yaml = &after_open_line[..close_rel];
    let after_fence_marker = &after_open_line[close_rel + 1 + 3..]; // saute "\n---"

    // `\s*\n?` : consomme tout l'espace qui suit la fermeture (le moteur
    // regex de Python, greedy sur `\s*` avec rien a regagner en arriere
    // puisque `(.*)\Z` accepte n'importe quoi, avale tout l'espace
    // contigu — voir le docstring de module pour la verification empirique).
    let body_start = after_fence_marker
        .char_indices()
        .find(|(_, c)| !c.is_whitespace())
        .map(|(i, _)| i)
        .unwrap_or(after_fence_marker.len());
    let body = &after_fence_marker[body_start..];

    Some((frontmatter_yaml.to_string(), body.to_string()))
}

/// Position (en octets, relative a `text`) du `\n` qui precede la premiere
/// fermeture `---` de `text`, ou `None` si aucune n'existe.
fn find_closing_fence(text: &str) -> Option<usize> {
    text.find("\n---")
}

/// Reproduit `parse_frontmatter` : `({}, texte_original)` si aucune
/// fermeture, `({}, corps)` si le YAML du frontmatter est invalide ou ne
/// resout pas a un mapping, `(meta, corps)` sinon.
fn parse_frontmatter_core(text: &str) -> (Value, String) {
    let Some((frontmatter_yaml, body)) = split_frontmatter(text) else {
        return (Value::Map(Vec::new()), text.to_string());
    };
    let meta = match parse_yaml_text(&frontmatter_yaml) {
        Some(Value::Map(entries)) => Value::Map(entries),
        _ => Value::Map(Vec::new()),
    };
    (meta, body)
}

// ── ToolVerb ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ToolVerb {
    Read,
    Search,
    Edit,
    Execute,
    Web,
}

impl ToolVerb {
    fn value(self) -> &'static str {
        match self {
            ToolVerb::Read => "read",
            ToolVerb::Search => "search",
            ToolVerb::Edit => "edit",
            ToolVerb::Execute => "execute",
            ToolVerb::Web => "web",
        }
    }

    /// `ToolVerb(s)` cote Python : `None` reproduit le `ValueError` attrape
    /// par `_tool_verbs` (`continue`, verbe ignore).
    fn parse(s: &str) -> Option<ToolVerb> {
        match s {
            "read" => Some(ToolVerb::Read),
            "search" => Some(ToolVerb::Search),
            "edit" => Some(ToolVerb::Edit),
            "execute" => Some(ToolVerb::Execute),
            "web" => Some(ToolVerb::Web),
            _ => None,
        }
    }
}

/// Reproduit `_tool_verbs(raw)` : une chaine devient une liste par
/// eclatement sur virgules/espaces, une liste est prise telle quelle,
/// toute autre forme (mapping, scalaire non-chaine, `None`) donne `()`.
/// Ignore les rejets — voir [`tool_verbs_with_rejects_core`] pour le
/// contrat complet (le perimetre d'outils ne change pas, mais un verbe
/// inconnu n'est plus avale sans laisser de trace ailleurs dans le pipeline).
fn tool_verbs_core(raw: &Value) -> Vec<ToolVerb> {
    tool_verbs_with_rejects_core(raw).0
}

/// Reproduit `_tool_verbs_with_rejects(raw)` : meme derivation que
/// [`tool_verbs_core`] (chaine eclatee sur virgules/espaces, liste prise
/// telle quelle, toute autre forme donne `((), ())`), mais retourne en plus
/// les jetons qui n'ont resolu vers aucun `ToolVerb` — dedupliques en
/// preservant le premier ordre d'apparition, comme les verbes retenus.
///
/// Le perimetre d'outils lui-meme reste inchange (un verbe inconnu ne
/// devient jamais un outil) : ce que ce port change, c'est que le rejet
/// n'est plus invisible. `collect.py::collect_agents` transforme cette
/// liste en une note de `ProjectSurface.notes` nommant l'agent et les
/// jetons rejetes — la meme discipline que la garde de distinction
/// (une dette du kit reste visible, elle n'est simplement pas bloquante).
fn tool_verbs_with_rejects_core(raw: &Value) -> (Vec<ToolVerb>, Vec<String>) {
    let items: Vec<Value> = match raw {
        Value::Str(s) => s
            .replace(',', " ")
            .split_whitespace()
            .map(|part| Value::Str(part.to_string()))
            .collect(),
        Value::List(items) => items.clone(),
        _ => return (Vec::new(), Vec::new()),
    };
    let mut verbs: Vec<ToolVerb> = Vec::new();
    let mut rejected: Vec<String> = Vec::new();
    for item in &items {
        let candidate = python_str(item).trim().to_lowercase();
        match ToolVerb::parse(&candidate) {
            Some(verb) => {
                if !verbs.contains(&verb) {
                    verbs.push(verb);
                }
            }
            None => {
                if !rejected.contains(&candidate) {
                    rejected.push(candidate);
                }
            }
        }
    }
    (verbs, rejected)
}

/// Reproduit `_str_tuple(raw)` : une chaine devient `[raw]`, une liste est
/// prise telle quelle, toute autre forme donne `()`. Chaque element est
/// `str(item).strip()`, les chaines vides une fois strippees sont exclues.
fn str_tuple_core(raw: &Value) -> Vec<String> {
    let items: Vec<Value> = match raw {
        Value::Str(s) => vec![Value::Str(s.clone())],
        Value::List(items) => items.clone(),
        _ => return Vec::new(),
    };
    items
        .iter()
        .map(|item| python_str(item).trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Reproduit `_max_turns(value)`. `bool` est exclu explicitement (sous-type
/// de `int` cote Python — `max_turns: true` est un override malforme, pas
/// un budget de 1). Correctif inclus par rapport a la version Python
/// actuelle : `str.isdigit()` accepte des chiffres Unicode non-ASCII (`²`)
/// que `int()` refuse ensuite avec un `ValueError` non rattrape (voir le
/// docstring de module) — ce coeur ne teste que des chiffres ASCII des le
/// depart, donc ce plantage n'existe pas ici par construction, pas par un
/// `try`/`except` ajoute pour l'occasion.
fn max_turns_core(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(_) => None,
        Value::Int(i) if *i > 0 => Some(*i),
        Value::Str(s) => {
            let trimmed = s.trim();
            if !trimmed.is_empty() && trimmed.chars().all(|c| c.is_ascii_digit()) {
                trimmed.parse::<i64>().ok()
            } else {
                None
            }
        }
        _ => None,
    }
}

// ── infer_tools ──────────────────────────────────────────────────────────────

/// Copie exacte de `_EDIT_MARKERS` (`collect.py`).
const EDIT_MARKERS: &[&str] = &[
    "écrit",
    "écrire",
    "modifie",
    "modifier",
    "édite",
    "éditer",
    "implémente",
    "implémenter",
    "rédige",
    "rédiger",
    "génère",
    "générer",
    "refactor",
    "write",
    "edit",
    "implement",
    "generate",
];

/// Copie exacte de `_EXECUTE_MARKERS` (`collect.py`).
const EXECUTE_MARKERS: &[&str] = &[
    "exécute",
    "exécuter",
    "lance",
    "lancer",
    "commande",
    "terminal",
    "pytest",
    "build",
    "déploie",
    "déployer",
    "run ",
    "execute",
    "deploy",
    "test suite",
];

fn infer_tools_core(body: &str, description: &str) -> Vec<ToolVerb> {
    let haystack = format!("{description}\n{body}").to_lowercase();
    let mut verbs = vec![ToolVerb::Read, ToolVerb::Search];
    if EDIT_MARKERS.iter().any(|m| haystack.contains(m)) {
        verbs.push(ToolVerb::Edit);
    }
    if EXECUTE_MARKERS.iter().any(|m| haystack.contains(m)) {
        verbs.push(ToolVerb::Execute);
    }
    verbs
}

// ── ModelAffinity.from_frontmatter ──────────────────────────────────────────

/// Les 4 chaines de `ModelAffinity.from_frontmatter(data).to_dict()`, dans
/// l'ordre `(reasoning, context_window, speed, cost)`. Si `data` n'est pas
/// un mapping, les 4 valent `"medium"`. Sinon, chaque champ absent vaut
/// `"medium"` (defaut applique a l'ABSENCE de cle, `dict.get(key,
/// "medium")`) tandis qu'un champ present mais explicitement nul vaut la
/// chaine `"None"` (`str(None)`) — verifie empiriquement contre
/// `ModelAffinity.from_frontmatter({"reasoning": None})`, un ecart facile a
/// manquer entre "absent" et "present et nul" que ce port rend explicite.
fn model_affinity_from_frontmatter_core(data: &Value) -> (String, String, String, String) {
    let Some(_) = data.as_map() else {
        return (
            "medium".to_string(),
            "medium".to_string(),
            "medium".to_string(),
            "medium".to_string(),
        );
    };
    let field = |key: &str| match data.get(key) {
        Some(v) => python_str(v),
        None => "medium".to_string(),
    };
    (
        field("reasoning"),
        field("context_window"),
        field("speed"),
        field("cost"),
    )
}

// ── description ──────────────────────────────────────────────────────────────

/// Reproduit `str(meta.get("description") or f"Grimoire agent {name}").strip()`.
/// `meta.get("description")` (un seul argument) donne `None` aussi bien pour
/// une cle absente qu'une cle presente valant `null` ; combine a `or`, toute
/// valeur *falsy* Python (`None`, `""`, `0`, `False`, `[]`, `{}`) retombe sur
/// le nom par defaut — pas seulement l'absence de cle. D'ou l'usage de
/// `is_falsy()` plutot que `Option::is_none()`.
fn description_core(meta: &Value, name: &str) -> String {
    let raw = meta.get("description");
    let text = match raw {
        Some(v) if !v.is_falsy() => python_str(v),
        _ => format!("Grimoire agent {name}"),
    };
    text.trim().to_string()
}

// ── Construction des champs purs d'un AgentSpec ─────────────────────────────

struct AgentSpecFields {
    description: String,
    tools: Vec<ToolVerb>,
    tools_origin: &'static str,
    affinity: (String, String, String, String),
    max_turns: Option<i64>,
    skills: Vec<String>,
    context: Vec<String>,
}

const ABSENT: Value = Value::Null;

/// Reproduit la portion pure de la boucle de `collect_agents` (le corps du
/// `for path in _agent_files(...)`, une fois `text` deja lu et
/// `parse_frontmatter` deja applique) : tout ce qui derive `meta`/`body` en
/// champs d'`AgentSpec`, hors `entry_point` (decide par `entry_point ==
/// name`, externe) et la resolution `skills:`/`context:` contre
/// `known_skills`/le disque (qui ont besoin d'un etat que cette fonction ne
/// recoit pas — `collect_agents` les applique apres coup, avant de lever
/// `GrimoireAgentError` si necessaire).
fn build_agent_spec_core(name: &str, meta: &Value, body: &str) -> AgentSpecFields {
    let description = description_core(meta, name);
    let declared = tool_verbs_core(meta.get("tools").unwrap_or(&ABSENT));
    let (tools, tools_origin) = if !declared.is_empty() {
        (declared, "declared")
    } else {
        (infer_tools_core(body, &description), "inferred")
    };
    let affinity =
        model_affinity_from_frontmatter_core(meta.get("model_affinity").unwrap_or(&ABSENT));
    let max_turns = max_turns_core(meta.get("max_turns").unwrap_or(&ABSENT));
    let skills = str_tuple_core(meta.get("skills").unwrap_or(&ABSENT));
    let context = str_tuple_core(meta.get("context").unwrap_or(&ABSENT));
    AgentSpecFields {
        description,
        tools,
        tools_origin,
        affinity,
        max_turns,
        skills,
        context,
    }
}

// ── Empreinte de faisceau et garde de distinction ───────────────────────────

/// Reproduit `AgentSpec.fingerprint()` : outils/contexte/skills tries,
/// separes par des marqueurs `"|"` litteraux (comme le tuple Python).
fn fingerprint_core(tools: &[String], context: &[String], skills: &[String]) -> Vec<String> {
    let mut tools_sorted = tools.to_vec();
    tools_sorted.sort();
    let mut context_sorted = context.to_vec();
    context_sorted.sort();
    let mut skills_sorted = skills.to_vec();
    skills_sorted.sort();

    let mut out =
        Vec::with_capacity(tools_sorted.len() + context_sorted.len() + skills_sorted.len() + 2);
    out.extend(tools_sorted);
    out.push("|".to_string());
    out.extend(context_sorted);
    out.push("|".to_string());
    out.extend(skills_sorted);
    out
}

/// Un agent tel que la garde de distinction en a besoin : son nom, si sa
/// definition vit dans la couche d'override du projet (`_is_override`, deja
/// calcule cote Python a partir de `definition_ref` — ce module ne connait
/// pas la convention de chemin `layout.OVERRIDES_DIR`, il recoit le
/// verdict), et son faisceau (outils/contexte/skills, deja en chaines).
struct AgentRecord {
    name: String,
    is_override: bool,
    tools: Vec<String>,
    context: Vec<String>,
    skills: Vec<String>,
}

/// Reproduit `duplicate_agent_fingerprints(agents)` : les groupes d'agents
/// au meme faisceau, dans l'ordre de premiere apparition de chaque
/// empreinte, chaque groupe donnant toutes les paires (noms tries).
fn duplicate_pairs_core(agents: &[AgentRecord]) -> Vec<(String, String)> {
    let mut seen_fingerprints: Vec<Vec<String>> = Vec::new();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (i, agent) in agents.iter().enumerate() {
        let fp = fingerprint_core(&agent.tools, &agent.context, &agent.skills);
        match seen_fingerprints.iter().position(|k| k == &fp) {
            Some(pos) => groups[pos].push(i),
            None => {
                seen_fingerprints.push(fp);
                groups.push(vec![i]);
            }
        }
    }
    let mut duplicates = Vec::new();
    for idxs in &groups {
        if idxs.len() < 2 {
            continue;
        }
        let mut names: Vec<&str> = idxs.iter().map(|&i| agents[i].name.as_str()).collect();
        names.sort_unstable();
        for i in 0..names.len() {
            for j in (i + 1)..names.len() {
                duplicates.push((names[i].to_string(), names[j].to_string()));
            }
        }
    }
    duplicates
}

/// Reproduit le regime a deux niveaux de `build_surface` : `strict` (une
/// paire dont au moins un des deux noms vit dans la couche d'override —
/// erreur cote appelant) et `duplicates` (toutes les paires — utilisees
/// pour la note quand `strict` est vide). Verifie au niveau de la **paire**,
/// pas du groupe entier : un groupe de 3 agents dont un seul est un override
/// ne rend strictes que les paires qui le nomment, exactement comme
/// `[pair for pair in duplicates if any(_is_override(...) for n in pair)]`
/// cote Python.
fn partition_duplicate_pairs_core(
    agents: &[AgentRecord],
) -> (Vec<(String, String)>, Vec<(String, String)>) {
    let duplicates = duplicate_pairs_core(agents);
    let is_override: std::collections::HashMap<&str, bool> = agents
        .iter()
        .map(|a| (a.name.as_str(), a.is_override))
        .collect();
    let strict: Vec<(String, String)> = duplicates
        .iter()
        .filter(|(a, b)| {
            *is_override.get(a.as_str()).unwrap_or(&false)
                || *is_override.get(b.as_str()).unwrap_or(&false)
        })
        .cloned()
        .collect();
    (strict, duplicates)
}

// Tout ce qui suit touche a PyO3 et n'existe que sous la feature
// `extension-module` (cf. Cargo.toml) : la logique pure ci-dessus, seule
// couverte par `cargo test --no-default-features`, n'en depend pas.
#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{
        build_agent_spec_core, infer_tools_core, max_turns_core,
        model_affinity_from_frontmatter_core, parse_frontmatter_core,
        partition_duplicate_pairs_core, str_tuple_core, tool_verbs_core,
        tool_verbs_with_rejects_core, AgentRecord, Value,
    };
    use pyo3::exceptions::PyTypeError;
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

    /// Convertit une valeur Python arbitraire (le frontmatter deja analyse
    /// par ruamel cote appelant, ou une valeur passee directement par les
    /// tests de parite) vers la representation dynamique de ce crate.
    /// Jamais d'echec : la forme malformee doit rester visible pour que la
    /// logique pure (`tool_verbs_core`, etc.) puisse la traiter comme le
    /// fait deja son equivalent Python, pas etre rejetee des la frontiere.
    fn to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
        if obj.is_none() {
            return Ok(Value::Null);
        }
        // PyBool avant PyInt : `bool` est une sous-classe de `int` cote
        // Python (`isinstance(True, int)` est vrai).
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
        // Type Python non reconnu : coercion via str(), comme le ferait
        // n'importe quel `str(item)` cote Python sur une forme exotique.
        Ok(Value::Str(obj.str()?.to_string()))
    }

    fn value_to_py<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
        Ok(match value {
            Value::Null => py.None().into_bound(py),
            Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any(),
            Value::Int(i) => i.into_pyobject(py)?.into_any(),
            Value::Float(f) => f.into_pyobject(py)?.into_any(),
            Value::Str(s) => s.into_pyobject(py)?.into_any(),
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

    fn value_from_any(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
        to_value(obj).map_err(|e| {
            PyTypeError::new_err(format!("grimoire_hosts_core: conversion impossible: {e}"))
        })
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect.parse_frontmatter`.
    /// Retourne `(meta: dict, body: str)`, meme contrat que la fonction
    /// Python.
    #[pyfunction]
    fn parse_frontmatter(py: Python<'_>, text: &str) -> PyResult<(Py<PyAny>, String)> {
        let (meta, body) = parse_frontmatter_core(text);
        Ok((value_to_py(py, &meta)?.unbind(), body))
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect._tool_verbs`. Retourne
    /// la liste des valeurs `ToolVerb` retenues (`"read"`, `"edit"`, ...),
    /// dans l'ordre de premiere apparition. Ignore les rejets — voir
    /// `tool_verbs_with_rejects` pour le contrat complet.
    #[pyfunction]
    fn tool_verbs(raw: Bound<'_, PyAny>) -> PyResult<Vec<String>> {
        let value = value_from_any(&raw)?;
        Ok(tool_verbs_core(&value)
            .into_iter()
            .map(|v| v.value().to_string())
            .collect())
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect._tool_verbs_with_rejects`.
    /// Meme derivation que `tool_verbs`, plus les jetons qui n'ont resolu
    /// vers aucun `ToolVerb` (dedupliques, ordre de premiere apparition) —
    /// le perimetre d'outils ne change pas, mais le rejet n'est plus
    /// invisible : `collect_agents` en fait une note de
    /// `ProjectSurface.notes` nommant l'agent et les jetons rejetes.
    #[pyfunction]
    fn tool_verbs_with_rejects(raw: Bound<'_, PyAny>) -> PyResult<(Vec<String>, Vec<String>)> {
        let value = value_from_any(&raw)?;
        let (verbs, rejected) = tool_verbs_with_rejects_core(&value);
        Ok((
            verbs.into_iter().map(|v| v.value().to_string()).collect(),
            rejected,
        ))
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect._str_tuple`.
    #[pyfunction]
    fn str_tuple(raw: Bound<'_, PyAny>) -> PyResult<Vec<String>> {
        let value = value_from_any(&raw)?;
        Ok(str_tuple_core(&value))
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect.infer_tools`.
    #[pyfunction]
    fn infer_tools(body: &str, description: &str) -> Vec<String> {
        infer_tools_core(body, description)
            .into_iter()
            .map(|v| v.value().to_string())
            .collect()
    }

    /// Frontiere PyO3 pour `grimoire.hosts.collect._max_turns`.
    #[pyfunction]
    fn max_turns(raw: Bound<'_, PyAny>) -> PyResult<Option<i64>> {
        let value = value_from_any(&raw)?;
        Ok(max_turns_core(&value))
    }

    /// Frontiere PyO3 pour `ModelAffinity.from_frontmatter(data).to_dict()`.
    #[pyfunction]
    fn model_affinity_from_frontmatter(
        py: Python<'_>,
        raw: Bound<'_, PyAny>,
    ) -> PyResult<Py<PyDict>> {
        let value = value_from_any(&raw)?;
        let (reasoning, context_window, speed, cost) = model_affinity_from_frontmatter_core(&value);
        let dict = PyDict::new(py);
        dict.set_item("reasoning", reasoning)?;
        dict.set_item("context_window", context_window)?;
        dict.set_item("speed", speed)?;
        dict.set_item("cost", cost)?;
        Ok(dict.unbind())
    }

    /// Frontiere PyO3 pour la construction pure des champs d'`AgentSpec`
    /// (voir `build_agent_spec_core`). `meta` est le dict deja retourne par
    /// `parse_frontmatter` (Python ou Rust, peu importe — les deux doivent
    /// produire les memes champs). Ne fixe pas `entry_point` (toujours
    /// `False` ici ; decide par l'appelant) ni la validation
    /// `skills:`/`context:` contre un etat externe.
    #[pyfunction]
    fn build_agent_spec(
        py: Python<'_>,
        name: &str,
        meta: Bound<'_, PyAny>,
        body: &str,
    ) -> PyResult<Py<PyDict>> {
        let meta_value = value_from_any(&meta)?;
        let fields = build_agent_spec_core(name, &meta_value, body);
        let dict = PyDict::new(py);
        dict.set_item("name", name)?;
        dict.set_item("description", fields.description)?;
        dict.set_item(
            "tools",
            fields
                .tools
                .into_iter()
                .map(|v| v.value().to_string())
                .collect::<Vec<_>>(),
        )?;
        dict.set_item("tools_origin", fields.tools_origin)?;
        let affinity = PyDict::new(py);
        affinity.set_item("reasoning", fields.affinity.0)?;
        affinity.set_item("context_window", fields.affinity.1)?;
        affinity.set_item("speed", fields.affinity.2)?;
        affinity.set_item("cost", fields.affinity.3)?;
        dict.set_item("affinity", affinity)?;
        dict.set_item("max_turns", fields.max_turns)?;
        dict.set_item("skills", fields.skills)?;
        dict.set_item("context", fields.context)?;
        Ok(dict.unbind())
    }

    /// Frontiere PyO3 pour `AgentSpec.fingerprint()`.
    #[pyfunction]
    fn agent_fingerprint(
        tools: Vec<String>,
        context: Vec<String>,
        skills: Vec<String>,
    ) -> Vec<String> {
        super::fingerprint_core(&tools, &context, &skills)
    }

    /// Frontiere PyO3 pour le regime a deux niveaux de `build_surface`.
    /// `agents` est une liste de tuples `(name, is_override, tools,
    /// context, skills)` — `is_override` deja calcule cote Python
    /// (`_is_override(definition_ref)`, qui connait la convention de
    /// chemin `layout.OVERRIDES_DIR`). Retourne `(strict, duplicates)`.
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn partition_duplicate_pairs(
        agents: Vec<(String, bool, Vec<String>, Vec<String>, Vec<String>)>,
    ) -> (Vec<(String, String)>, Vec<(String, String)>) {
        let records: Vec<AgentRecord> = agents
            .into_iter()
            .map(|(name, is_override, tools, context, skills)| AgentRecord {
                name,
                is_override,
                tools,
                context,
                skills,
            })
            .collect();
        partition_duplicate_pairs_core(&records)
    }

    #[pymodule]
    fn grimoire_hosts_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(parse_frontmatter, m)?)?;
        m.add_function(wrap_pyfunction!(tool_verbs, m)?)?;
        m.add_function(wrap_pyfunction!(tool_verbs_with_rejects, m)?)?;
        m.add_function(wrap_pyfunction!(str_tuple, m)?)?;
        m.add_function(wrap_pyfunction!(infer_tools, m)?)?;
        m.add_function(wrap_pyfunction!(max_turns, m)?)?;
        m.add_function(wrap_pyfunction!(model_affinity_from_frontmatter, m)?)?;
        m.add_function(wrap_pyfunction!(build_agent_spec, m)?)?;
        m.add_function(wrap_pyfunction!(agent_fingerprint, m)?)?;
        m.add_function(wrap_pyfunction!(partition_duplicate_pairs, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(text: &str) -> Value {
        Value::Str(text.to_string())
    }

    fn list_str(items: &[&str]) -> Value {
        Value::List(items.iter().map(|i| s(i)).collect())
    }

    // ── parse_frontmatter ────────────────────────────────────────────────

    #[test]
    fn no_frontmatter_returns_original_text_as_body() {
        let (meta, body) = parse_frontmatter_core("pas de frontmatter du tout");
        assert_eq!(meta, Value::Map(vec![]));
        assert_eq!(body, "pas de frontmatter du tout");
    }

    #[test]
    fn unclosed_frontmatter_returns_original_text_as_body() {
        let (meta, body) = parse_frontmatter_core("---\nname: x\ncorps sans fermeture");
        assert_eq!(meta, Value::Map(vec![]));
        assert_eq!(body, "---\nname: x\ncorps sans fermeture");
    }

    #[test]
    fn well_formed_frontmatter_is_parsed() {
        let (meta, body) = parse_frontmatter_core("---\nname: x\n---\ncorps");
        assert_eq!(meta.get("name"), Some(&s("x")));
        assert_eq!(body, "corps");
    }

    #[test]
    fn leading_bom_and_comment_are_skipped() {
        let (meta, body) =
            parse_frontmatter_core("<!-- ARCHETYPE: meta -->\n---\nname: x\n---\ncorps");
        assert_eq!(meta.get("name"), Some(&s("x")));
        assert_eq!(body, "corps");

        let (meta2, body2) = parse_frontmatter_core("\u{feff}---\nname: x\n---\ncorps");
        assert_eq!(meta2.get("name"), Some(&s("x")));
        assert_eq!(body2, "corps");
    }

    #[test]
    fn duplicate_key_yaml_becomes_empty_meta_not_an_error() {
        let (meta, body) = parse_frontmatter_core("---\nname: x\nname: y\n---\ncorps");
        assert_eq!(meta, Value::Map(vec![]));
        assert_eq!(body, "corps");
    }

    #[test]
    fn non_mapping_yaml_becomes_empty_meta() {
        let (meta, body) = parse_frontmatter_core("---\n- a\n- b\n---\ncorps");
        assert_eq!(meta, Value::Map(vec![]));
        assert_eq!(body, "corps");

        let (meta2, body2) = parse_frontmatter_core("---\njust scalar\n---\ncorps");
        assert_eq!(meta2, Value::Map(vec![]));
        assert_eq!(body2, "corps");
    }

    #[test]
    fn frontmatter_with_no_trailing_newline_gives_empty_body() {
        let (meta, body) = parse_frontmatter_core("---\nname: x\n---");
        assert_eq!(meta.get("name"), Some(&s("x")));
        assert_eq!(body, "");
    }

    // ── tool_verbs_core ──────────────────────────────────────────────────

    #[test]
    fn tool_verbs_from_comma_or_space_separated_string() {
        assert_eq!(
            tool_verbs_core(&s("read, edit")),
            vec![ToolVerb::Read, ToolVerb::Edit]
        );
        assert_eq!(
            tool_verbs_core(&s("read edit,search")),
            vec![ToolVerb::Read, ToolVerb::Edit, ToolVerb::Search]
        );
    }

    #[test]
    fn tool_verbs_unknown_verb_is_ignored_but_reported() {
        // Le perimetre d'outils ne change pas (voir le docstring de
        // module) : un verbe hors de ToolVerb n'est ni une erreur ni un
        // outil. Mais il n'est plus invisible — tool_verbs_with_rejects_core
        // le retourne pour que collect_agents puisse en faire une note.
        let (verbs, rejects) = tool_verbs_with_rejects_core(&list_str(&["read", "bogus"]));
        assert_eq!(verbs, vec![ToolVerb::Read]);
        assert_eq!(rejects, vec!["bogus".to_string()]);
        assert_eq!(
            tool_verbs_core(&list_str(&["read", "bogus"])),
            vec![ToolVerb::Read]
        );
    }

    #[test]
    fn tool_verbs_with_rejects_deduplicates_rejects_preserving_first_order() {
        let (verbs, rejects) =
            tool_verbs_with_rejects_core(&list_str(&["bogus", "read", "bogus", "reed"]));
        assert_eq!(verbs, vec![ToolVerb::Read]);
        assert_eq!(rejects, vec!["bogus".to_string(), "reed".to_string()]);
    }

    #[test]
    fn tool_verbs_with_rejects_empty_on_all_known_verbs() {
        let (verbs, rejects) = tool_verbs_with_rejects_core(&list_str(&["read", "edit"]));
        assert_eq!(verbs, vec![ToolVerb::Read, ToolVerb::Edit]);
        assert!(rejects.is_empty());
    }

    #[test]
    fn tool_verbs_deduplicates_preserving_first_order() {
        assert_eq!(
            tool_verbs_core(&list_str(&["read", "read", "edit"])),
            vec![ToolVerb::Read, ToolVerb::Edit]
        );
    }

    #[test]
    fn tool_verbs_non_list_non_string_is_empty() {
        assert_eq!(tool_verbs_core(&Value::Int(123)), Vec::<ToolVerb>::new());
        assert_eq!(tool_verbs_core(&Value::Null), Vec::<ToolVerb>::new());
        assert_eq!(tool_verbs_core(&Value::Map(vec![])), Vec::<ToolVerb>::new());
    }

    // ── str_tuple_core ───────────────────────────────────────────────────

    #[test]
    fn str_tuple_from_string_or_list() {
        assert_eq!(str_tuple_core(&s("a")), vec!["a".to_string()]);
        assert_eq!(
            str_tuple_core(&list_str(&["a", "b", " c "])),
            vec!["a".to_string(), "b".to_string(), "c".to_string()]
        );
    }

    #[test]
    fn str_tuple_drops_blank_entries_and_non_list_non_string() {
        assert_eq!(str_tuple_core(&list_str(&["a", ""])), vec!["a".to_string()]);
        assert_eq!(str_tuple_core(&Value::Int(123)), Vec::<String>::new());
        assert_eq!(str_tuple_core(&Value::Bool(true)), Vec::<String>::new());
    }

    // ── max_turns_core ───────────────────────────────────────────────────

    #[test]
    fn max_turns_accepts_positive_int_and_digit_string() {
        assert_eq!(max_turns_core(&Value::Int(5)), Some(5));
        assert_eq!(max_turns_core(&s("5")), Some(5));
        assert_eq!(max_turns_core(&s("  7  ")), Some(7));
    }

    #[test]
    fn max_turns_rejects_bool_zero_negative_and_non_digit() {
        assert_eq!(max_turns_core(&Value::Bool(true)), None);
        assert_eq!(max_turns_core(&Value::Bool(false)), None);
        assert_eq!(max_turns_core(&Value::Int(0)), None);
        assert_eq!(max_turns_core(&Value::Int(-5)), None);
        assert_eq!(max_turns_core(&s("abc")), None);
        assert_eq!(max_turns_core(&Value::Null), None);
    }

    #[test]
    fn max_turns_unicode_digit_does_not_crash_and_is_rejected() {
        // '\u{00b2}' (²) : str.isdigit() vaut True cote Python mais int()
        // leve ValueError — le defaut trouve par ce port (voir le
        // docstring de module). Le coeur Rust n'a jamais ce probleme :
        // preuve directe qu'aucun panic ne se produit et que la valeur est
        // rejetee proprement.
        assert_eq!(max_turns_core(&s("\u{00b2}")), None);
    }

    // ── infer_tools_core ─────────────────────────────────────────────────

    #[test]
    fn infer_tools_grants_edit_only_on_explicit_signal() {
        assert_eq!(
            infer_tools_core("Tu observes et tu rapportes.", ""),
            vec![ToolVerb::Read, ToolVerb::Search]
        );
        assert_eq!(
            infer_tools_core("Tu rédiges la documentation.", ""),
            vec![ToolVerb::Read, ToolVerb::Search, ToolVerb::Edit]
        );
        assert_eq!(
            infer_tools_core("Tu lances les tests.", ""),
            vec![ToolVerb::Read, ToolVerb::Search, ToolVerb::Execute]
        );
    }

    // ── model_affinity_from_frontmatter_core ────────────────────────────

    #[test]
    fn model_affinity_defaults_to_medium_when_not_a_mapping() {
        assert_eq!(
            model_affinity_from_frontmatter_core(&Value::Null),
            (
                "medium".into(),
                "medium".into(),
                "medium".into(),
                "medium".into()
            )
        );
        assert_eq!(
            model_affinity_from_frontmatter_core(&list_str(&["a"])),
            (
                "medium".into(),
                "medium".into(),
                "medium".into(),
                "medium".into()
            )
        );
    }

    #[test]
    fn model_affinity_absent_key_is_medium_present_null_is_the_string_none() {
        let present_null = Value::Map(vec![("reasoning".to_string(), Value::Null)]);
        let (reasoning, context_window, _, _) = model_affinity_from_frontmatter_core(&present_null);
        assert_eq!(reasoning, "None");
        assert_eq!(context_window, "medium");
    }

    #[test]
    fn model_affinity_stringifies_non_string_values() {
        let data = Value::Map(vec![("reasoning".to_string(), Value::Bool(true))]);
        let (reasoning, ..) = model_affinity_from_frontmatter_core(&data);
        assert_eq!(reasoning, "True");
    }

    // ── description_core ─────────────────────────────────────────────────

    #[test]
    fn description_falls_back_on_any_falsy_value_not_just_absence() {
        assert_eq!(
            description_core(&Value::Map(vec![]), "agent-x"),
            "Grimoire agent agent-x"
        );
        let empty_string = Value::Map(vec![("description".to_string(), s(""))]);
        assert_eq!(
            description_core(&empty_string, "agent-x"),
            "Grimoire agent agent-x"
        );
        let falsy_int = Value::Map(vec![("description".to_string(), Value::Int(0))]);
        assert_eq!(
            description_core(&falsy_int, "agent-x"),
            "Grimoire agent agent-x"
        );
        let real = Value::Map(vec![("description".to_string(), s("Un rôle."))]);
        assert_eq!(description_core(&real, "agent-x"), "Un rôle.");
    }

    // ── fingerprint / duplicate detection ───────────────────────────────

    #[test]
    fn fingerprint_is_order_independent_on_tools_context_skills() {
        let a = fingerprint_core(&["edit".into(), "read".into()], &[], &[]);
        let b = fingerprint_core(&["read".into(), "edit".into()], &[], &[]);
        assert_eq!(a, b);
    }

    fn record(name: &str, is_override: bool, tools: &[&str]) -> AgentRecord {
        AgentRecord {
            name: name.to_string(),
            is_override,
            tools: tools.iter().map(|s| s.to_string()).collect(),
            context: vec![],
            skills: vec![],
        }
    }

    #[test]
    fn two_kit_agents_same_fingerprint_are_not_strict() {
        let agents = vec![
            record("kit-a", false, &["read", "edit"]),
            record("kit-b", false, &["read", "edit"]),
        ];
        let (strict, duplicates) = partition_duplicate_pairs_core(&agents);
        assert!(strict.is_empty());
        assert_eq!(duplicates, vec![("kit-a".to_string(), "kit-b".to_string())]);
    }

    #[test]
    fn an_override_colliding_with_a_kit_agent_is_strict() {
        let agents = vec![
            record("kit-a", false, &["read", "edit"]),
            record("mon-agent", true, &["read", "edit"]),
        ];
        let (strict, duplicates) = partition_duplicate_pairs_core(&agents);
        assert_eq!(strict, duplicates);
        assert_eq!(strict.len(), 1);
    }

    #[test]
    fn only_pairs_naming_the_override_are_strict_in_a_larger_group() {
        // Groupe de 3 au meme faisceau, un seul override : seules les
        // paires qui le nomment sont strictes (verifie au niveau de la
        // paire, pas du groupe entier — voir le docstring de la fonction).
        let agents = vec![
            record("kit-a", false, &["read"]),
            record("kit-b", false, &["read"]),
            record("override-c", true, &["read"]),
        ];
        let (strict, duplicates) = partition_duplicate_pairs_core(&agents);
        assert_eq!(duplicates.len(), 3);
        assert_eq!(strict.len(), 2);
        assert!(!strict.contains(&("kit-a".to_string(), "kit-b".to_string())));
    }

    #[test]
    fn distinct_fingerprints_give_no_duplicates() {
        let agents = vec![
            record("kit-a", false, &["read"]),
            record("kit-c", false, &["read", "edit"]),
        ];
        let (strict, duplicates) = partition_duplicate_pairs_core(&agents);
        assert!(strict.is_empty());
        assert!(duplicates.is_empty());
    }
}
