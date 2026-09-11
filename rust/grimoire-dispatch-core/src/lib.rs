//! Coeur Rust optionnel de la cascade de dispatch (`grimoire.missions.dispatch`)
//! et du routage de fournisseurs par palier de cout (`grimoire.providers.routing`).
//!
//! Cinquieme port Rust optionnel du kit (issue #354 de Guilhem-Bonnet/Grimoire-kit),
//! meme montage que `rust/grimoire-policies-core/`, `rust/grimoire-schema-core/`,
//! `rust/grimoire-hosts-core/` et `rust/grimoire-flows-core/`.
//!
//! ## Ce qui est porte
//!
//! - `start_tier_for` / `_tier_chain` (dispatch.py) : le plancher de palier par
//!   classe de verifiabilite et la chaine de paliers a essayer.
//! - La resolution `--start-tier` explicite contre le plancher de la classe
//!   (`run_dispatch`, lignes ~714-725 de dispatch.py) : un palier explicite ne
//!   peut jamais descendre sous le plancher.
//! - `render_invocation` : le rendu d'un gabarit d'invocation en argv
//!   (`shlex.split` + substitution token par token de `{prompt}`/`{model}`).
//! - `_looks_rate_limited`, `_extract_cost_usd`, `_uncertainties_search_text`
//!   et `_extract_uncertainties` : l'analyse de la sortie brute d'un ouvrier
//!   delegue.
//! - `_matches_review_surface` et la classification de revue
//!   (`_classify_review`, partie pure : matching de globs contre une liste de
//!   fichiers deja obtenue par `git diff --name-only`).
//! - `_candidate_order` et le filtre de refroidissement/disponibilite de
//!   `providers.routing.candidates()` : quel fournisseur, dans quel ordre,
//!   pour un palier donne.
//! - `DispatchReport.succeeded` / `.exit_code` / `.refusal_message` : la
//!   derivation du verdict final d'une cascade depuis la liste des tentatives.
//!
//! ## Ce qui reste en Python, et pourquoi
//!
//! Aucun sous-processus, aucune lecture disque, aucun appel reseau ne
//! traverse cette frontiere : `_run_provider_call` (subprocess), `_run_checks`
//! (subprocess), `_classify_review`'s `git diff` et la lecture de
//! `orchestration-policy.yaml`, `providers.registry.read_registry` (lecture du
//! YAML du registre) et `providers.state.load_state`/`save_state` (lecture et
//! ecriture du JSON d'etat runtime) restent cote Python — ce module ne recoit
//! que des donnees deja lues, sous forme de types Python natifs (tuples,
//! listes, chaines), jamais un chemin de fichier.
//!
//! ## La propriete que ce port doit demontrer
//!
//! `Tier`/`Verifiability` miroitent `SUPPORTED_MODEL_TIERS`/`Verifiability`
//! (registry.py/verifiability.py) : chaque fonction qui les consomme est un
//! `match` exhaustif sans branche `_ =>` — une valeur non reconnue a la
//! frontiere (nouveau palier ajoute cote Python sans etre reporte ici) est un
//! rejet explicite (`Err`), jamais une comparaison qui echoue en silence.
//!
//! ## JSON strict (`serde_json`) : Rust est l'oracle, pas Python
//!
//! `_extract_cost_usd` et `_extract_uncertainties` analysent du texte JSON
//! brut potentiellement produit par un fournisseur headless quelconque —
//! la surface la plus exposee de ce port : un texte non maitrise, pas une
//! structure deja validee. `serde_json` (RFC 8259 strict) est la dependance
//! JSON de ce crate. Une premiere version de ce port ecrivait son propre
//! analyseur pour reproduire une extension non standard de `json.loads` de
//! CPython — l'acceptation par defaut des jetons hors RFC 8259 `NaN`,
//! `Infinity`, `-Infinity`. C'etait l'inverse de ce que ce port doit
//! demontrer : la regle est que Rust, strict, fait foi, et c'est Python qui
//! s'aligne dessus, jamais l'inverse. Corrige : `dispatch.py` passe desormais
//! `parse_constant=_reject_non_standard` a `json.loads`, qui leve
//! `ValueError` sur ces trois jetons — rattrapee comme n'importe quel JSON
//! invalide, donc identique aux deux backends (cout absent, aucune
//! incertitude extraite). Voir `test_json_rejects_nan_and_infinity_strict_rfc8259`
//! ci-dessous et `tests/unit/test_dispatch_rust_parity.py` cote Python.

#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

use serde_json::Value;

// ── Palier de cout / classe de verifiabilite ───────────────────────────────

/// Miroir de `SUPPORTED_MODEL_TIERS` (registry.py).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Tier {
    Cheap,
    Mid,
    Strong,
}

impl Tier {
    const ORDER: [Tier; 3] = [Tier::Cheap, Tier::Mid, Tier::Strong];

    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "cheap" => Tier::Cheap,
            "mid" => Tier::Mid,
            "strong" => Tier::Strong,
            other => return Err(format!("palier de cout inconnu: {other}")),
        })
    }

    fn as_str(self) -> &'static str {
        match self {
            Tier::Cheap => "cheap",
            Tier::Mid => "mid",
            Tier::Strong => "strong",
        }
    }

    fn index(self) -> usize {
        match self {
            Tier::Cheap => 0,
            Tier::Mid => 1,
            Tier::Strong => 2,
        }
    }
}

/// Miroir de `grimoire.missions.verifiability.Verifiability`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Verifiability {
    V0,
    V1,
    V2,
}

impl Verifiability {
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "V0" => Verifiability::V0,
            "V1" => Verifiability::V1,
            "V2" => Verifiability::V2,
            other => return Err(format!("classe de verifiabilite inconnue: {other}")),
        })
    }
}

/// Miroir de `dispatch.start_tier_for` : le premier palier autorise pour
/// cette classe, `None` pour V2 (aucune delegation possible).
fn start_tier_for(verifiability: Verifiability) -> Option<Tier> {
    match verifiability {
        Verifiability::V0 => Some(Tier::Cheap),
        Verifiability::V1 => Some(Tier::Mid),
        Verifiability::V2 => None,
    }
}

/// Miroir de `dispatch._tier_chain` : les paliers entre *start_tier* et
/// *max_tier* (inclus), vide si *max_tier* est strictement en dessous.
fn tier_chain(start_tier: Tier, max_tier: Option<Tier>) -> Vec<Tier> {
    let start_idx = start_tier.index();
    let end_idx = max_tier.map(Tier::index).unwrap_or(Tier::ORDER.len() - 1);
    if end_idx < start_idx {
        return Vec::new();
    }
    Tier::ORDER[start_idx..=end_idx].to_vec()
}

/// Miroir de la resolution `--start-tier` explicite de `run_dispatch`
/// (dispatch.py, ~lignes 714-725) : un palier explicite ne peut que monter,
/// jamais descendre sous le plancher de la classe. Rend
/// `(chosen_tier, was_raised)` — `was_raised` distingue "palier explicite
/// respecte tel quel" de "palier explicite relevé au plancher", pour que
/// l'appelant Python compose le message d'explication (`start_tier_reason`)
/// sans dupliquer cette decision.
fn resolve_explicit_start_tier(floor: Tier, requested: Tier) -> (Tier, bool) {
    if requested.index() < floor.index() {
        (floor, true)
    } else {
        (requested, false)
    }
}

// ── Rendu d'un gabarit d'invocation (module `shlex`) ───────────────────────
//
// Miroir de `shlex.split` de CPython en mode POSIX par defaut
// (`comments=False`) : espaces/tabulations/CR/LF separent les tokens, les
// guillemets simples preservent tout litteralement (aucun echappement),
// les guillemets doubles n'accordent une signification speciale au
// backslash que devant `"` ou `\` lui-meme, et hors guillemets le backslash
// echappe litteralement le caractere suivant, quel qu'il soit. Verifie
// empiriquement contre `shlex.split` de CPython (voir les cas de test
// ci-dessous) plutot que suppose depuis la documentation.
mod shlex {
    pub fn split(input: &str) -> Result<Vec<String>, String> {
        let mut tokens = Vec::new();
        let mut current: Option<String> = None;
        let mut chars = input.chars();
        while let Some(c) = chars.next() {
            match c {
                ' ' | '\t' | '\r' | '\n' => {
                    if let Some(tok) = current.take() {
                        tokens.push(tok);
                    }
                }
                '\\' => {
                    let cur = current.get_or_insert_with(String::new);
                    match chars.next() {
                        Some(next_c) => cur.push(next_c),
                        None => return Err("backslash final sans caractere echappe".to_string()),
                    }
                }
                '\'' => {
                    let cur = current.get_or_insert_with(String::new);
                    loop {
                        match chars.next() {
                            Some('\'') => break,
                            Some(ch) => cur.push(ch),
                            None => return Err("guillemet simple non ferme".to_string()),
                        }
                    }
                }
                '"' => {
                    let cur = current.get_or_insert_with(String::new);
                    loop {
                        match chars.next() {
                            Some('"') => break,
                            Some('\\') => match chars.next() {
                                Some(nc) if nc == '"' || nc == '\\' => cur.push(nc),
                                Some(nc) => {
                                    cur.push('\\');
                                    cur.push(nc);
                                }
                                None => return Err("guillemet double non ferme".to_string()),
                            },
                            Some(ch) => cur.push(ch),
                            None => return Err("guillemet double non ferme".to_string()),
                        }
                    }
                }
                other => {
                    let cur = current.get_or_insert_with(String::new);
                    cur.push(other);
                }
            }
        }
        if let Some(tok) = current.take() {
            tokens.push(tok);
        }
        Ok(tokens)
    }
}

/// Miroir de `dispatch.render_invocation` : `shlex.split` puis substitution
/// litterale, token par token, de `{prompt}` d'abord et `{model}` ensuite —
/// dans cet ordre precis, comme cote Python (`tok.replace("{prompt}",
/// prompt).replace("{model}", model)`), y compris l'effet de bord partage
/// par les deux implementations si *prompt* contient lui-meme le texte
/// litteral `{model}` : il serait alors substitue une seconde fois. Ce
/// comportement n'est pas corrige ici — c'est un partage intentionnel du
/// meme calcul, pas une divergence entre backends.
fn render_invocation(template: &str, prompt: &str, model: &str) -> Result<Vec<String>, String> {
    let tokens = shlex::split(template)?;
    Ok(tokens
        .into_iter()
        .map(|t| t.replace("{prompt}", prompt).replace("{model}", model))
        .collect())
}

// ── Analyse de la sortie d'un ouvrier delegue ──────────────────────────────

/// Miroir de `dispatch._RATE_LIMIT_MARKERS`.
const RATE_LIMIT_MARKERS: [&str; 5] = ["429", "rate limit", "rate_limit", "overloaded", "quota"];

/// Miroir de `dispatch._looks_rate_limited`.
fn looks_rate_limited(text: &str) -> bool {
    let lowered = text.to_lowercase();
    RATE_LIMIT_MARKERS
        .iter()
        .any(|marker| lowered.contains(marker))
}

/// `float(x)` si *value* est un nombre JSON fini — jamais un booleen (voir
/// le docstring de `extract_cost_usd` : `isinstance(x, bool)` est vrai en
/// Python pour `True`/`False`, un defaut trouve et corrige cote Python dans
/// cette meme PR) ni `NaN`/`Infinity` (RFC 8259 ne les representant pas,
/// `serde_json` ne les produit jamais depuis un JSON valide ; le controle
/// `is_finite()` reste une defense en profondeur contre un litteral valide
/// mais hors bornes de `f64`, ex. `1e400`, qui existe des deux cotes).
fn as_finite_cost(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64().filter(|f| f.is_finite()),
        _ => None,
    }
}

/// Miroir de `dispatch._extract_cost_usd` : `None` si *stdout* n'est pas un
/// JSON valide (RFC 8259 strict, `serde_json`), si la valeur racine n'est
/// pas un objet, si `total_cost_usd` est absent, ou si sa valeur n'est pas
/// un nombre fini hors booleen.
fn extract_cost_usd(stdout: &str) -> Option<f64> {
    let value: Value = serde_json::from_str(stdout).ok()?;
    as_finite_cost(value.as_object()?.get("total_cost_usd")?)
}

/// Miroir de `dispatch._uncertainties_search_text`.
fn uncertainties_search_text(stdout: &str) -> String {
    match serde_json::from_str::<Value>(stdout) {
        Ok(value) => match value.get("result").and_then(Value::as_str) {
            Some(s) => s.to_string(),
            None => stdout.to_string(),
        },
        Err(_) => stdout.to_string(),
    }
}

const UNCERTAINTIES_MARKER: &str = "```grimoire-uncertainties";
const FENCE: &str = "```";

fn is_py_whitespace(c: char) -> bool {
    c.is_whitespace()
}

fn find_char_substr(chars: &[char], from: usize, needle: &str) -> Option<usize> {
    let needle_chars: Vec<char> = needle.chars().collect();
    let nlen = needle_chars.len();
    if nlen == 0 || from + nlen > chars.len() {
        return None;
    }
    let mut i = from;
    while i + nlen <= chars.len() {
        if chars[i..i + nlen] == needle_chars[..] {
            return Some(i);
        }
        i += 1;
    }
    None
}

/// Trouve le corps du bloc ``` ```grimoire-uncertainties\n...``` ``` dans
/// *haystack*, miroir de `dispatch._UNCERTAINTIES_BLOCK_RE`
/// (``r"```grimoire-uncertainties\s*\n(.*?)```"``, `re.DOTALL`) : le corps
/// capture n'est jamais strip() ici (fait par l'appelant, comme cote
/// Python), la fermeture est la PREMIERE occurrence de ``` ``` ``` apres le
/// debut du corps (correspondance non-gourmande). Si le marqueur est suivi
/// d'un caractere non blanc avant tout saut de ligne, ou si aucune
/// fermeture n'existe, cette occurrence du marqueur ne compte pas — la
/// recherche continue sur une occurrence suivante du marqueur, s'il y en a
/// une.
fn find_uncertainties_block(haystack: &str) -> Option<String> {
    let chars: Vec<char> = haystack.chars().collect();
    let marker_chars: Vec<char> = UNCERTAINTIES_MARKER.chars().collect();
    let mut search_from = 0usize;
    loop {
        let marker_pos = find_char_substr(&chars, search_from, UNCERTAINTIES_MARKER)?;
        let mut scan = marker_pos + marker_chars.len();
        let mut newline_at = None;
        let mut ok = true;
        while scan < chars.len() {
            let c = chars[scan];
            if is_py_whitespace(c) {
                scan += 1;
                if c == '\n' {
                    newline_at = Some(scan - 1);
                    break;
                }
            } else {
                ok = false;
                break;
            }
        }
        if ok {
            if let Some(nl) = newline_at {
                let body_start = nl + 1;
                if let Some(close_idx) = find_char_substr(&chars, body_start, FENCE) {
                    let body: String = chars[body_start..close_idx].iter().collect();
                    return Some(body);
                }
            }
        }
        search_from = marker_pos + 1;
    }
}

/// Miroir de `dispatch._extract_uncertainties`. Rend
/// `(uncertainties, warnings)` — `uncertainties` en triplets
/// `(where, what, why)`.
fn extract_uncertainties(stdout: &str) -> (Vec<(String, String, String)>, Vec<String>) {
    let haystack = uncertainties_search_text(stdout);
    let body = match find_uncertainties_block(&haystack) {
        Some(b) => b,
        None => return (Vec::new(), Vec::new()),
    };
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let payload: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => {
            let preview: String = trimmed.chars().take(200).collect();
            return (
                Vec::new(),
                vec![format!(
                    "bloc grimoire-uncertainties illisible (JSON invalide) : {preview:?}"
                )],
            );
        }
    };
    let items = match payload {
        Value::Array(items) => items,
        _ => {
            let preview: String = trimmed.chars().take(200).collect();
            return (
                Vec::new(),
                vec![format!(
                    "bloc grimoire-uncertainties illisible (attendu une liste JSON) : {preview:?}"
                )],
            );
        }
    };
    let mut uncertainties = Vec::new();
    let mut warnings = Vec::new();
    for item in items {
        let where_ = item.get("where").and_then(Value::as_str);
        let what_ = item.get("what").and_then(Value::as_str);
        let why_ = item.get("why").and_then(Value::as_str);
        match (where_, what_, why_) {
            (Some(w), Some(wh), Some(y)) => {
                uncertainties.push((w.to_string(), wh.to_string(), y.to_string()))
            }
            _ => warnings.push(format!(
                "incertitude ignoree (cles where/what/why manquantes ou non textuelles) : {item:?}"
            )),
        }
    }
    (uncertainties, warnings)
}

// ── Correspondance de glob (module `glob`) — `_matches_review_surface` ────
//
// Miroir de `fnmatch.fnmatchcase` de CPython : `*` (toute sequence, y
// compris vide), `?` (un caractere), `[seq]`/`[!seq]` (classe de
// caracteres, negation, plages `a-z`, `]` litteral en premiere position),
// et un `[` sans fermeture correspondante traite comme litteral — jamais
// de repli sur la casse (contrairement a `fnmatch.fnmatch`, dependant de
// l'OS). Verifie empiriquement contre `fnmatch.fnmatchcase`/`fnmatch.translate`
// de CPython (voir les cas de test ci-dessous).
mod glob {
    #[derive(Debug, Clone)]
    enum ClassMember {
        Single(char),
        Range(char, char),
    }

    #[derive(Debug, Clone)]
    enum Token {
        Literal(char),
        AnyChar,
        AnyRun,
        Class {
            negate: bool,
            members: Vec<ClassMember>,
        },
    }

    fn parse_class_members(chars: &[char]) -> Vec<ClassMember> {
        let mut members = Vec::new();
        let mut idx = 0;
        let n = chars.len();
        while idx < n {
            if idx + 2 < n && chars[idx + 1] == '-' {
                members.push(ClassMember::Range(chars[idx], chars[idx + 2]));
                idx += 3;
            } else {
                members.push(ClassMember::Single(chars[idx]));
                idx += 1;
            }
        }
        members
    }

    fn parse_pattern(pattern: &str) -> Vec<Token> {
        let chars: Vec<char> = pattern.chars().collect();
        let n = chars.len();
        let mut tokens = Vec::new();
        let mut i = 0;
        while i < n {
            let c = chars[i];
            i += 1;
            match c {
                '*' => tokens.push(Token::AnyRun),
                '?' => tokens.push(Token::AnyChar),
                '[' => {
                    let mut k = i;
                    let negate = if k < n && chars[k] == '!' {
                        k += 1;
                        true
                    } else {
                        false
                    };
                    let members_start = k;
                    if k < n && chars[k] == ']' {
                        k += 1;
                    }
                    while k < n && chars[k] != ']' {
                        k += 1;
                    }
                    if k >= n {
                        // Pas de fermeture : '[' est litteral, on ne
                        // consomme rien de plus (miroir de
                        // `fnmatch.translate`).
                        tokens.push(Token::Literal('['));
                    } else {
                        let members = parse_class_members(&chars[members_start..k]);
                        tokens.push(Token::Class { negate, members });
                        i = k + 1;
                    }
                }
                other => tokens.push(Token::Literal(other)),
            }
        }
        tokens
    }

    fn class_matches(c: char, members: &[ClassMember]) -> bool {
        members.iter().any(|m| match m {
            ClassMember::Single(s) => *s == c,
            ClassMember::Range(a, b) => *a <= c && c <= *b,
        })
    }

    fn token_matches_char(tok: &Token, c: char) -> bool {
        match tok {
            Token::Literal(l) => *l == c,
            Token::AnyChar => true,
            Token::Class { negate, members } => {
                let m = class_matches(c, members);
                if *negate {
                    !m
                } else {
                    m
                }
            }
            Token::AnyRun => false,
        }
    }

    /// Algorithme classique de "wildcard matching" a deux pointeurs avec
    /// retour arriere sur le dernier `*` rencontre.
    fn tokens_match(path: &[char], tokens: &[Token]) -> bool {
        let (mut pi, mut ti) = (0usize, 0usize);
        let mut star: Option<(usize, usize)> = None; // (position token, position path)
        while pi < path.len() {
            if ti < tokens.len()
                && !matches!(tokens[ti], Token::AnyRun)
                && token_matches_char(&tokens[ti], path[pi])
            {
                pi += 1;
                ti += 1;
            } else if ti < tokens.len() && matches!(tokens[ti], Token::AnyRun) {
                star = Some((ti, pi));
                ti += 1;
            } else if let Some((sti, spi)) = star {
                ti = sti + 1;
                pi = spi + 1;
                star = Some((sti, pi));
            } else {
                return false;
            }
        }
        while ti < tokens.len() && matches!(tokens[ti], Token::AnyRun) {
            ti += 1;
        }
        ti == tokens.len()
    }

    pub fn fnmatch_case(path: &str, pattern: &str) -> bool {
        let tokens = parse_pattern(pattern);
        let path_chars: Vec<char> = path.chars().collect();
        tokens_match(&path_chars, &tokens)
    }
}

/// Miroir de `dispatch._matches_review_surface`.
fn matches_review_surface(path: &str, surfaces: &[String]) -> bool {
    surfaces
        .iter()
        .any(|pattern| glob::fnmatch_case(path, pattern))
}

/// Partie pure de `dispatch._classify_review` : etant donne la liste des
/// fichiers modifies (deja obtenue par `git diff --name-only`, cote Python)
/// et les globs de surfaces sensibles (deja resolus, `orchestration-policy.yaml`
/// lu ou defaut), rend `(review_required, matched_files)`. Le cas "projet non
/// versionne" (git indisponible) reste entierement cote Python : il ne
/// depend d'aucune des deux listes ici, seulement de l'echec de la commande
/// `git` elle-meme.
fn classify_review_core(files: &[String], surfaces: &[String]) -> (bool, Vec<String>) {
    let matched: Vec<String> = files
        .iter()
        .filter(|f| matches_review_surface(f, surfaces))
        .cloned()
        .collect();
    let required = !matched.is_empty();
    (required, matched)
}

// ── Routage de fournisseurs par palier (module `providers/routing.py`) ────

struct ProviderRow {
    id: String,
    enabled: bool,
    fallback_order: Vec<String>,
    has_tier_model: bool,
}

struct CooldownRow {
    id: String,
    cooldown_until_epoch: Option<f64>,
    available: bool,
}

/// Miroir de `ProviderRuntimeState.is_cooling_down`.
fn is_cooling_down(cooldown_until_epoch: Option<f64>, now_epoch: f64) -> bool {
    match cooldown_until_epoch {
        Some(c) => c > now_epoch,
        None => false,
    }
}

/// Miroir exact de `routing._candidate_order` : l'ordre du registre,
/// complete par le `fallback_order` de chaque fournisseur puis par
/// `default_fallback_chain`, sans jamais reordonner ni dupliquer.
fn candidate_order(providers: &[ProviderRow], default_fallback_chain: &[String]) -> Vec<String> {
    let mut order = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for p in providers {
        if seen.insert(p.id.clone()) {
            order.push(p.id.clone());
        }
    }
    for p in providers {
        for candidate_id in &p.fallback_order {
            if seen.insert(candidate_id.clone()) {
                order.push(candidate_id.clone());
            }
        }
    }
    for candidate_id in default_fallback_chain {
        if seen.insert(candidate_id.clone()) {
            order.push(candidate_id.clone());
        }
    }
    order
}

/// Miroir de `routing.candidates` (partie pure : le filtrage
/// active/palier/refroidissement/disponibilite une fois le registre et
/// l'etat runtime deja lus cote Python). Un fournisseur refroidi ou
/// indisponible est absent du resultat — jamais un index hors borne, jamais
/// `None` silencieux : l'appelant qui trouve une liste vide sait qu'aucun
/// fournisseur n'est invocable et doit refuser explicitement (`no_provider`),
/// exactement comme `run_dispatch` le fait deja.
fn candidate_provider_ids(
    providers: &[ProviderRow],
    default_fallback_chain: &[String],
    cooldowns: &[CooldownRow],
    now_epoch: f64,
) -> Vec<String> {
    let order = candidate_order(providers, default_fallback_chain);
    let by_id: std::collections::HashMap<&str, &ProviderRow> =
        providers.iter().map(|p| (p.id.as_str(), p)).collect();
    let cooldown_by_id: std::collections::HashMap<&str, &CooldownRow> =
        cooldowns.iter().map(|c| (c.id.as_str(), c)).collect();
    let mut out = Vec::new();
    for provider_id in order {
        let provider = match by_id.get(provider_id.as_str()) {
            Some(p) => *p,
            None => continue,
        };
        if !provider.enabled || !provider.has_tier_model {
            continue;
        }
        if let Some(state) = cooldown_by_id.get(provider_id.as_str()) {
            if is_cooling_down(state.cooldown_until_epoch, now_epoch) || !state.available {
                continue;
            }
        }
        out.push(provider_id);
    }
    out
}

// ── Verdict final d'une cascade (`DispatchReport`) ─────────────────────────

/// Miroir de `DispatchReport.succeeded`.
fn dispatch_succeeded(verdicts: &[String]) -> bool {
    verdicts.iter().any(|v| v == "green")
}

/// Miroir de `DispatchReport.exit_code` : 0 vert (ou dry-run), 1 chaine
/// epuisee, 2 refus.
fn dispatch_exit_code(dry_run: bool, refusal: Option<&str>, succeeded: bool) -> i64 {
    if dry_run {
        return 0;
    }
    if refusal.is_some() {
        return 2;
    }
    if succeeded {
        0
    } else {
        1
    }
}

/// Miroir de `dispatch._REFUSAL_MESSAGES`.
const REFUSAL_MESSAGES: [(&str, &str); 4] = [
    ("v2", "classe de verifiabilite V2 : aucun verdict ne peut juger ce travail, pas de delegation"),
    (
        "no_check",
        "au moins un `--check` est requis : la classe dit que le verdict est mecanique, la commande dit lequel",
    ),
    ("no_tier", "aucun palier disponible entre le plancher de la classe et --max-tier"),
    ("no_provider", "aucun fournisseur invocable sur la chaine de paliers prevue"),
];

/// Miroir de `DispatchReport.refusal_message` : le libelle du code de
/// refus, ou le code lui-meme si non reconnu (meme repli que
/// `_REFUSAL_MESSAGES.get(self.refusal, self.refusal)`).
fn refusal_message(code: &str) -> String {
    REFUSAL_MESSAGES
        .iter()
        .find(|(k, _)| *k == code)
        .map(|(_, v)| (*v).to_string())
        .unwrap_or_else(|| code.to_string())
}

// Tout ce qui suit touche a PyO3 et n'existe que sous la feature
// `extension-module` (cf. Cargo.toml) : rien de la logique pure ci-dessus
// n'en depend, d'ou `cargo test --no-default-features`.
#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{
        candidate_provider_ids, classify_review_core, dispatch_exit_code, dispatch_succeeded,
        extract_cost_usd, extract_uncertainties, is_cooling_down, looks_rate_limited,
        matches_review_surface, refusal_message, render_invocation, resolve_explicit_start_tier,
        start_tier_for, tier_chain, CooldownRow, ProviderRow, Tier, Verifiability,
    };
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    fn parse_tier(value: &str) -> PyResult<Tier> {
        Tier::parse(value).map_err(PyValueError::new_err)
    }

    fn parse_verifiability(value: &str) -> PyResult<Verifiability> {
        Verifiability::parse(value).map_err(PyValueError::new_err)
    }

    /// `dispatch.start_tier_for` : `None` pour V2.
    #[pyfunction]
    fn start_tier_for_py(verifiability: String) -> PyResult<Option<String>> {
        let v = parse_verifiability(&verifiability)?;
        Ok(start_tier_for(v).map(Tier::as_str).map(str::to_string))
    }

    /// `dispatch._tier_chain`.
    #[pyfunction]
    fn tier_chain_py(start_tier: String, max_tier: Option<String>) -> PyResult<Vec<String>> {
        let start = parse_tier(&start_tier)?;
        let max = max_tier.as_deref().map(parse_tier).transpose()?;
        Ok(tier_chain(start, max)
            .into_iter()
            .map(Tier::as_str)
            .map(str::to_string)
            .collect())
    }

    /// Resolution `--start-tier` explicite contre le plancher de la classe.
    /// Rend `(chosen_tier, was_raised)`.
    #[pyfunction]
    fn resolve_explicit_start_tier_py(
        floor: String,
        requested: String,
    ) -> PyResult<(String, bool)> {
        let floor_t = parse_tier(&floor)?;
        let requested_t = parse_tier(&requested)?;
        let (chosen, raised) = resolve_explicit_start_tier(floor_t, requested_t);
        Ok((chosen.as_str().to_string(), raised))
    }

    /// `dispatch.render_invocation`.
    #[pyfunction]
    fn render_invocation_py(
        template: String,
        prompt: String,
        model: String,
    ) -> PyResult<Vec<String>> {
        render_invocation(&template, &prompt, &model).map_err(PyValueError::new_err)
    }

    /// `dispatch._looks_rate_limited`.
    #[pyfunction]
    fn looks_rate_limited_py(text: String) -> bool {
        looks_rate_limited(&text)
    }

    /// `dispatch._extract_cost_usd`.
    #[pyfunction]
    fn extract_cost_usd_py(stdout: String) -> Option<f64> {
        extract_cost_usd(&stdout)
    }

    /// `dispatch._uncertainties_search_text`.
    #[pyfunction]
    fn uncertainties_search_text_py(stdout: String) -> String {
        super::uncertainties_search_text(&stdout)
    }

    /// `dispatch._extract_uncertainties`. Rend `(uncertainties, warnings)`
    /// avec `uncertainties` en triplets `(where, what, why)`.
    #[pyfunction]
    fn extract_uncertainties_py(stdout: String) -> (Vec<(String, String, String)>, Vec<String>) {
        extract_uncertainties(&stdout)
    }

    /// `dispatch._matches_review_surface`.
    #[pyfunction]
    fn matches_review_surface_py(path: String, surfaces: Vec<String>) -> bool {
        matches_review_surface(&path, &surfaces)
    }

    /// Partie pure de `dispatch._classify_review`. Rend
    /// `(review_required, matched_files)`.
    #[pyfunction]
    fn classify_review_py(files: Vec<String>, surfaces: Vec<String>) -> (bool, Vec<String>) {
        classify_review_core(&files, &surfaces)
    }

    /// `providers.state.ProviderRuntimeState.is_cooling_down`, epoch
    /// secondes des deux cotes (le parsing ISO 8601 de
    /// `cooldown_until`/`now` reste cote Python).
    #[pyfunction]
    fn is_cooling_down_py(cooldown_until_epoch: Option<f64>, now_epoch: f64) -> bool {
        is_cooling_down(cooldown_until_epoch, now_epoch)
    }

    type PyProviderRow = (String, bool, Vec<String>, bool);
    type PyCooldownRow = (String, Option<f64>, bool);

    /// Partie pure de `providers.routing.candidates` : *providers* est
    /// `(id, enabled, fallback_order, has_tier_model)` par fournisseur dans
    /// l'ordre du registre ; *cooldowns* est `(id, cooldown_until_epoch,
    /// available)` pour les fournisseurs qui ont un etat runtime connu.
    #[pyfunction]
    fn candidate_provider_ids_py(
        providers: Vec<PyProviderRow>,
        default_fallback_chain: Vec<String>,
        cooldowns: Vec<PyCooldownRow>,
        now_epoch: f64,
    ) -> Vec<String> {
        let rows: Vec<ProviderRow> = providers
            .into_iter()
            .map(
                |(id, enabled, fallback_order, has_tier_model)| ProviderRow {
                    id,
                    enabled,
                    fallback_order,
                    has_tier_model,
                },
            )
            .collect();
        let cooldown_rows: Vec<CooldownRow> = cooldowns
            .into_iter()
            .map(|(id, cooldown_until_epoch, available)| CooldownRow {
                id,
                cooldown_until_epoch,
                available,
            })
            .collect();
        candidate_provider_ids(&rows, &default_fallback_chain, &cooldown_rows, now_epoch)
    }

    /// `DispatchReport.succeeded`.
    #[pyfunction]
    fn dispatch_succeeded_py(verdicts: Vec<String>) -> bool {
        dispatch_succeeded(&verdicts)
    }

    /// `DispatchReport.exit_code`.
    #[pyfunction]
    fn dispatch_exit_code_py(dry_run: bool, refusal: Option<String>, succeeded: bool) -> i64 {
        dispatch_exit_code(dry_run, refusal.as_deref(), succeeded)
    }

    /// `DispatchReport.refusal_message`.
    #[pyfunction]
    fn refusal_message_py(code: String) -> String {
        refusal_message(&code)
    }

    #[pymodule]
    fn grimoire_dispatch_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(start_tier_for_py, m)?)?;
        m.add_function(wrap_pyfunction!(tier_chain_py, m)?)?;
        m.add_function(wrap_pyfunction!(resolve_explicit_start_tier_py, m)?)?;
        m.add_function(wrap_pyfunction!(render_invocation_py, m)?)?;
        m.add_function(wrap_pyfunction!(looks_rate_limited_py, m)?)?;
        m.add_function(wrap_pyfunction!(extract_cost_usd_py, m)?)?;
        m.add_function(wrap_pyfunction!(uncertainties_search_text_py, m)?)?;
        m.add_function(wrap_pyfunction!(extract_uncertainties_py, m)?)?;
        m.add_function(wrap_pyfunction!(matches_review_surface_py, m)?)?;
        m.add_function(wrap_pyfunction!(classify_review_py, m)?)?;
        m.add_function(wrap_pyfunction!(is_cooling_down_py, m)?)?;
        m.add_function(wrap_pyfunction!(candidate_provider_ids_py, m)?)?;
        m.add_function(wrap_pyfunction!(dispatch_succeeded_py, m)?)?;
        m.add_function(wrap_pyfunction!(dispatch_exit_code_py, m)?)?;
        m.add_function(wrap_pyfunction!(refusal_message_py, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── start_tier_for / tier_chain / resolve_explicit_start_tier ────────

    #[test]
    fn start_tier_for_v0_is_cheap() {
        assert_eq!(start_tier_for(Verifiability::V0), Some(Tier::Cheap));
    }

    #[test]
    fn start_tier_for_v1_is_mid() {
        assert_eq!(start_tier_for(Verifiability::V1), Some(Tier::Mid));
    }

    #[test]
    fn start_tier_for_v2_is_none() {
        assert_eq!(start_tier_for(Verifiability::V2), None);
    }

    #[test]
    fn tier_chain_full_range() {
        assert_eq!(
            tier_chain(Tier::Cheap, None),
            vec![Tier::Cheap, Tier::Mid, Tier::Strong]
        );
    }

    #[test]
    fn tier_chain_bounded_by_max_tier() {
        assert_eq!(
            tier_chain(Tier::Cheap, Some(Tier::Mid)),
            vec![Tier::Cheap, Tier::Mid]
        );
    }

    #[test]
    fn tier_chain_single_tier() {
        assert_eq!(
            tier_chain(Tier::Strong, Some(Tier::Strong)),
            vec![Tier::Strong]
        );
    }

    #[test]
    fn tier_chain_empty_when_max_below_start() {
        // Le cas que le cadrage demande de verifier cote Python : une
        // chaine vide silencieuse, jamais une exception — a charge de
        // l'appelant de la traduire en refus nomme (`no_tier`).
        assert_eq!(tier_chain(Tier::Mid, Some(Tier::Cheap)), Vec::<Tier>::new());
    }

    #[test]
    fn tier_chain_never_repeats_a_tier() {
        for start in Tier::ORDER {
            for max in [None, Some(Tier::Cheap), Some(Tier::Mid), Some(Tier::Strong)] {
                let chain = tier_chain(start, max);
                let mut seen = std::collections::HashSet::new();
                for t in &chain {
                    assert!(seen.insert(*t), "palier duplique dans la chaine: {chain:?}");
                }
            }
        }
    }

    #[test]
    fn tier_chain_never_descends_below_start() {
        for start in Tier::ORDER {
            for max in [None, Some(Tier::Cheap), Some(Tier::Mid), Some(Tier::Strong)] {
                let chain = tier_chain(start, max);
                for t in &chain {
                    assert!(t.index() >= start.index());
                }
            }
        }
    }

    #[test]
    fn unknown_tier_and_verifiability_are_rejected_explicitly() {
        assert!(Tier::parse("ultra").is_err());
        assert!(Verifiability::parse("V3").is_err());
    }

    #[test]
    fn resolve_explicit_start_tier_raises_below_floor() {
        assert_eq!(
            resolve_explicit_start_tier(Tier::Mid, Tier::Cheap),
            (Tier::Mid, true)
        );
    }

    #[test]
    fn resolve_explicit_start_tier_keeps_at_or_above_floor() {
        assert_eq!(
            resolve_explicit_start_tier(Tier::Mid, Tier::Mid),
            (Tier::Mid, false)
        );
        assert_eq!(
            resolve_explicit_start_tier(Tier::Mid, Tier::Strong),
            (Tier::Strong, false)
        );
    }

    // ── render_invocation / shlex ──────────────────────────────────────

    #[test]
    fn render_invocation_basic() {
        let out =
            render_invocation("claude -p {prompt} --model {model}", "hello", "sonnet").unwrap();
        assert_eq!(out, vec!["claude", "-p", "hello", "--model", "sonnet"]);
    }

    #[test]
    fn render_invocation_double_quoted_prompt() {
        let out = render_invocation(
            r#"claude -p "{prompt}" --model {model}"#,
            "hello world",
            "sonnet",
        )
        .unwrap();
        assert_eq!(
            out,
            vec!["claude", "-p", "hello world", "--model", "sonnet"]
        );
    }

    #[test]
    fn render_invocation_single_quoted_prompt() {
        let out = render_invocation("claude -p '{prompt}'", "hello", "sonnet").unwrap();
        assert_eq!(out, vec!["claude", "-p", "hello"]);
    }

    #[test]
    fn render_invocation_missing_prompt_placeholder_is_not_an_error() {
        // Cadrage : "gabarit sans {prompt}" -> le rendu reussit tel quel,
        // c'est l'appel fournisseur qui echouera plus tard (dispatch.py ne
        // valide pas la presence du placeholder ici).
        let out = render_invocation("claude --model {model}", "hello", "sonnet").unwrap();
        assert_eq!(out, vec!["claude", "--model", "sonnet"]);
    }

    #[test]
    fn render_invocation_model_twice() {
        let out = render_invocation("claude {model} --tag {model}", "p", "sonnet").unwrap();
        assert_eq!(out, vec!["claude", "sonnet", "--tag", "sonnet"]);
    }

    #[test]
    fn render_invocation_literal_braces_untouched() {
        let out = render_invocation("claude --json '{}' {prompt}", "p", "sonnet").unwrap();
        assert_eq!(out, vec!["claude", "--json", "{}", "p"]);
    }

    #[test]
    fn render_invocation_unterminated_quote_is_an_error() {
        assert!(render_invocation(r#"claude -p "unterminated"#, "p", "m").is_err());
        assert!(render_invocation("claude -p 'unterminated", "p", "m").is_err());
    }

    #[test]
    fn shlex_matches_cpython_reference_cases() {
        // Cas verifies empiriquement contre `shlex.split` de CPython.
        assert_eq!(shlex::split("a\\ b").unwrap(), vec!["a b"]);
        assert_eq!(shlex::split("a\\\\b").unwrap(), vec!["a\\b"]);
        assert_eq!(shlex::split(r#"a"b"c"#).unwrap(), vec!["abc"]);
        assert_eq!(shlex::split("a'b'c").unwrap(), vec!["abc"]);
        assert_eq!(shlex::split("ab#comment").unwrap(), vec!["ab#comment"]);
        assert_eq!(shlex::split(r#""a\nb""#).unwrap(), vec!["a\\nb"]);
        assert_eq!(shlex::split(r#""a\$b""#).unwrap(), vec!["a\\$b"]);
        assert_eq!(shlex::split(r#""a\\b""#).unwrap(), vec!["a\\b"]);
        assert_eq!(shlex::split(r#""a\"b""#).unwrap(), vec!["a\"b"]);
        assert_eq!(shlex::split("'a\\nb'").unwrap(), vec!["a\\nb"]);
        assert_eq!(shlex::split(r#"""  "a""#).unwrap(), vec!["", "a"]);
        assert_eq!(shlex::split("").unwrap(), Vec::<String>::new());
        assert_eq!(shlex::split("   ").unwrap(), Vec::<String>::new());
        assert!(shlex::split("end\\").is_err());
    }

    // ── looks_rate_limited ─────────────────────────────────────────────

    #[test]
    fn looks_rate_limited_recognises_markers() {
        assert!(looks_rate_limited("HTTP 429 Too Many Requests"));
        assert!(looks_rate_limited("Rate Limit exceeded"));
        assert!(looks_rate_limited("rate_limit_error"));
        assert!(looks_rate_limited("service overloaded"));
        assert!(looks_rate_limited("quota exceeded"));
        assert!(!looks_rate_limited("all good, 200 OK"));
    }

    // ── extract_cost_usd ───────────────────────────────────────────────

    #[test]
    fn extract_cost_usd_plain_json() {
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 0.0512}"#),
            Some(0.0512)
        );
    }

    #[test]
    fn extract_cost_usd_integer_json() {
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": 2}"#), Some(2.0));
    }

    #[test]
    fn extract_cost_usd_scientific_notation() {
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 1.5e-2}"#),
            Some(0.015)
        );
    }

    #[test]
    fn extract_cost_usd_negative_cost_is_accepted_as_is() {
        // dispatch.py ne valide pas le signe : un cout negatif traverse tel
        // quel des les deux backends.
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": -1.5}"#), Some(-1.5));
    }

    #[test]
    fn extract_cost_usd_plain_text_stdout_is_none() {
        assert_eq!(
            extract_cost_usd("Voici le resultat de la tache, sans JSON."),
            None
        );
    }

    #[test]
    fn extract_cost_usd_missing_field_is_none() {
        assert_eq!(extract_cost_usd(r#"{"other_field": 1}"#), None);
    }

    #[test]
    fn extract_cost_usd_string_decimal_comma_is_none() {
        // Une valeur textuelle avec virgule decimale n'est pas un nombre
        // JSON valide pour ce champ : `isinstance(cost, (int, float))` est
        // faux en Python, `Value::Number` ne matche pas ici non plus.
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": "0,05"}"#), None);
    }

    #[test]
    fn extract_cost_usd_boolean_is_none_defect_fixed() {
        // Defaut trouve en portant cette fonction : `isinstance(True, int)`
        // est vrai en Python, donc `_extract_cost_usd` renvoyait 1.0 pour un
        // cout JSON `true` avant correctif (corrige dans cette PR, voir
        // dispatch.py). Les deux backends rendent desormais `None`.
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": true}"#), None);
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": false}"#), None);
    }

    #[test]
    fn extract_cost_usd_non_finite_is_none() {
        // `NaN`/`Infinity`/`-Infinity` sont hors RFC 8259 : `serde_json`
        // (strict) rejette le document entier, donc `extract_cost_usd`
        // rend `None` par l'echec de l'analyse elle-meme. Cote Python,
        // `_reject_non_standard` (parse_constant) leve desormais sur ces
        // memes jetons, rattrapee comme un JSON invalide — meme verdict,
        // pour la meme raison, des deux cotes (voir
        // `json_rejects_nan_and_infinity_strict_rfc8259` ci-dessus).
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": NaN}"#), None);
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": Infinity}"#), None);
        assert_eq!(extract_cost_usd(r#"{"total_cost_usd": -Infinity}"#), None);
    }

    #[test]
    fn extract_cost_usd_multiple_numbers_only_top_level_field_counts() {
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 0.3, "nested": {"total_cost_usd": 99.0}}"#),
            Some(0.3)
        );
    }

    #[test]
    fn extract_cost_usd_duplicate_key_last_wins() {
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 1, "total_cost_usd": 2}"#),
            Some(2.0)
        );
    }

    #[test]
    fn json_rejects_nan_and_infinity_strict_rfc8259() {
        // La propriete que ce port doit demontrer : Rust est l'oracle,
        // strict RFC 8259, et c'est Python qui s'aligne dessus
        // (`_reject_non_standard` dans dispatch.py) — jamais l'inverse.
        // Une premiere version de ce port faisait le contraire (analyseur
        // maison reproduisant l'extension non standard de `json.loads` de
        // CPython) ; corrige.
        assert!(serde_json::from_str::<Value>("NaN").is_err());
        assert!(serde_json::from_str::<Value>("Infinity").is_err());
        assert!(serde_json::from_str::<Value>("-Infinity").is_err());
    }

    #[test]
    fn extract_cost_usd_nan_elsewhere_in_document_is_none_not_just_the_field() {
        // Un document qui porte NaN/Infinity ailleurs que dans le champ lu
        // est rejete en bloc par le parseur strict — pas seulement le champ
        // concerne. C'est le comportement que Python doit desormais imiter
        // via `parse_constant` plutot que de reussir la ou Rust echoue.
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 0.5, "other": NaN}"#),
            None
        );
        assert_eq!(
            extract_cost_usd(r#"{"total_cost_usd": 0.5, "other": Infinity}"#),
            None
        );
    }

    #[test]
    fn json_parses_surrogate_pair_and_escapes() {
        let v: Value = serde_json::from_str(r#""😀""#).unwrap();
        assert_eq!(v.as_str(), Some("\u{1F600}"));
        let v2: Value = serde_json::from_str(r#""a\nb\t\"c""#).unwrap();
        assert_eq!(v2.as_str(), Some("a\nb\t\"c"));
    }

    // ── uncertainties_search_text / extract_uncertainties ───────────────

    #[test]
    fn uncertainties_search_text_plain_stdout() {
        assert_eq!(
            uncertainties_search_text("juste du texte"),
            "juste du texte"
        );
    }

    #[test]
    fn uncertainties_search_text_json_result_field() {
        let stdout = r#"{"total_cost_usd": 0.1, "result": "voici la reponse"}"#;
        assert_eq!(uncertainties_search_text(stdout), "voici la reponse");
    }

    #[test]
    fn uncertainties_search_text_json_without_result_falls_back_to_stdout() {
        let stdout = r#"{"total_cost_usd": 0.1}"#;
        assert_eq!(uncertainties_search_text(stdout), stdout);
    }

    fn block(body: &str) -> String {
        format!("Reponse.\n```grimoire-uncertainties\n{body}\n```\nFin.")
    }

    #[test]
    fn extract_uncertainties_absent_section() {
        assert_eq!(
            extract_uncertainties("aucun bloc ici"),
            (Vec::new(), Vec::new())
        );
    }

    #[test]
    fn extract_uncertainties_empty_list() {
        let stdout = block("[]");
        assert_eq!(extract_uncertainties(&stdout), (Vec::new(), Vec::new()));
    }

    #[test]
    fn extract_uncertainties_blank_body_treated_as_empty_list() {
        let stdout = "```grimoire-uncertainties\n\n```";
        assert_eq!(extract_uncertainties(stdout), (Vec::new(), Vec::new()));
    }

    #[test]
    fn extract_uncertainties_well_formed_entries() {
        let stdout = block(r#"[{"where": "a.py", "what": "doute", "why": "pas teste"}]"#);
        let (uncertainties, warnings) = extract_uncertainties(&stdout);
        assert_eq!(
            uncertainties,
            vec![(
                "a.py".to_string(),
                "doute".to_string(),
                "pas teste".to_string()
            )]
        );
        assert!(warnings.is_empty());
    }

    #[test]
    fn extract_uncertainties_missing_keys_are_warned_and_skipped() {
        let stdout = block(r#"[{"where": "a.py", "what": "doute"}]"#);
        let (uncertainties, warnings) = extract_uncertainties(&stdout);
        assert!(uncertainties.is_empty());
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn extract_uncertainties_invalid_json_body_is_warned_never_raises() {
        let stdout = "```grimoire-uncertainties\nnot json at all {{{\n```";
        let (uncertainties, warnings) = extract_uncertainties(stdout);
        assert!(uncertainties.is_empty());
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn extract_uncertainties_non_list_json_is_warned() {
        let stdout = block(r#"{"where": "a.py"}"#);
        let (uncertainties, warnings) = extract_uncertainties(&stdout);
        assert!(uncertainties.is_empty());
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn extract_uncertainties_trailing_prose_after_section_ignored() {
        let stdout = format!(
            "{}\n\nEt voila d'autres explications non structurees.",
            block(r#"[]"#)
        );
        assert_eq!(extract_uncertainties(&stdout), (Vec::new(), Vec::new()));
    }

    #[test]
    fn extract_uncertainties_nested_strings_are_opaque() {
        let stdout = block(
            r#"[{"where": "a.py", "what": "- item imbrique\n- autre", "why": "voir plus haut"}]"#,
        );
        let (uncertainties, _warnings) = extract_uncertainties(&stdout);
        assert_eq!(uncertainties.len(), 1);
        assert_eq!(uncertainties[0].1, "- item imbrique\n- autre");
    }

    #[test]
    fn extract_uncertainties_result_wrapped_in_json_envelope() {
        let inner = block(r#"[{"where": "x", "what": "y", "why": "z"}]"#);
        let stdout = serde_json_ish_wrap(&inner);
        let (uncertainties, _warnings) = extract_uncertainties(&stdout);
        assert_eq!(
            uncertainties,
            vec![("x".to_string(), "y".to_string(), "z".to_string())]
        );
    }

    /// Petit encodeur JSON minimal pour construire un cas de test — pas le
    /// coeur du crate, juste le strict necessaire pour envelopper un texte
    /// dans `{"result": "..."}` avec un echappement correct des sauts de
    /// ligne et guillemets.
    fn serde_json_ish_wrap(inner: &str) -> String {
        let mut escaped = String::new();
        for c in inner.chars() {
            match c {
                '"' => escaped.push_str("\\\""),
                '\\' => escaped.push_str("\\\\"),
                '\n' => escaped.push_str("\\n"),
                other => escaped.push(other),
            }
        }
        format!(r#"{{"result": "{escaped}"}}"#)
    }

    #[test]
    fn extract_uncertainties_fuzz_never_panics() {
        // Fuzz leger demande par le cadrage : 200 chaines aleatoires (texte
        // ASCII imprevisible, incluant des fragments de marqueur et de JSON
        // malforme), aucune des deux fonctions ne doit jamais paniquer.
        let alphabet: Vec<char> =
            "abc{}[]\"'`\\n \t\n#-!*?.,:;grimoireuncertaintiesNaInfy0123456789"
                .chars()
                .collect();
        let mut state: u64 = 0x2545F4914F6CDD1D;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        for _ in 0..200 {
            let len = (next() % 120) as usize;
            let s: String = (0..len)
                .map(|_| alphabet[(next() as usize) % alphabet.len()])
                .collect();
            let _ = extract_cost_usd(&s);
            let _ = uncertainties_search_text(&s);
            let _ = extract_uncertainties(&s);
            let _ = looks_rate_limited(&s);
            let _ = render_invocation(&s, "prompt", "model");
            let _ = matches_review_surface(&s, &["*/cli/*".to_string(), "*schema*".to_string()]);
        }
    }

    // ── matches_review_surface / classify_review_core ───────────────────

    #[test]
    fn fnmatch_matches_cpython_reference_cases() {
        assert!(glob::fnmatch_case("src/cli/foo.py", "*/cli/*"));
        assert!(glob::fnmatch_case("src/mcp/tool.py", "*/mcp/*"));
        assert!(glob::fnmatch_case("pkg/__init__.py", "*/__init__.py"));
        assert!(!glob::fnmatch_case("__init__.py", "*/__init__.py"));
        assert!(glob::fnmatch_case(
            "a/b/verifiability.py",
            "*/verifiability.py"
        ));
        assert!(glob::fnmatch_case("schema_thing.py", "*schema*"));
        assert!(!glob::fnmatch_case("a.txt", "a[bc].txt"));
        assert!(glob::fnmatch_case("ab.txt", "a[bc].txt"));
        assert!(glob::fnmatch_case("a].txt", "a[]].txt"));
        assert!(!glob::fnmatch_case("ax.txt", "a[!bc]x.txt"));
        assert!(glob::fnmatch_case("a[b.txt", "a[b.txt"));
        assert!(glob::fnmatch_case("a1.txt", "a[0-9].txt"));
        assert!(!glob::fnmatch_case("aA.txt", "a[a-z].txt"));
    }

    #[test]
    fn matches_review_surface_default_surfaces() {
        let surfaces = vec![
            "*/cli/*".to_string(),
            "*schema*".to_string(),
            "*/hooks/*".to_string(),
        ];
        assert!(matches_review_surface("src/grimoire/cli/app.py", &surfaces));
        assert!(matches_review_surface("core/schema_extra.py", &surfaces));
        assert!(!matches_review_surface(
            "src/grimoire/tools/lint.py",
            &surfaces
        ));
    }

    #[test]
    fn classify_review_required_when_a_file_matches() {
        let files = vec![
            "src/grimoire/cli/app.py".to_string(),
            "README.md".to_string(),
        ];
        let surfaces = vec!["*/cli/*".to_string()];
        let (required, matched) = classify_review_core(&files, &surfaces);
        assert!(required);
        assert_eq!(matched, vec!["src/grimoire/cli/app.py".to_string()]);
    }

    #[test]
    fn classify_review_optional_when_nothing_matches() {
        let files = vec!["README.md".to_string()];
        let surfaces = vec!["*/cli/*".to_string()];
        let (required, matched) = classify_review_core(&files, &surfaces);
        assert!(!required);
        assert!(matched.is_empty());
    }

    #[test]
    fn classify_review_optional_when_no_files_changed() {
        let (required, matched) = classify_review_core(&[], &["*/cli/*".to_string()]);
        assert!(!required);
        assert!(matched.is_empty());
    }

    // ── providers/routing : is_cooling_down / candidate_provider_ids ────

    #[test]
    fn is_cooling_down_true_when_cooldown_in_future() {
        assert!(is_cooling_down(Some(200.0), 100.0));
    }

    #[test]
    fn is_cooling_down_false_when_cooldown_in_past_or_absent() {
        assert!(!is_cooling_down(Some(50.0), 100.0));
        assert!(!is_cooling_down(None, 100.0));
    }

    fn provider(
        id: &str,
        enabled: bool,
        fallback_order: &[&str],
        has_tier_model: bool,
    ) -> ProviderRow {
        ProviderRow {
            id: id.to_string(),
            enabled,
            fallback_order: fallback_order.iter().map(|s| s.to_string()).collect(),
            has_tier_model,
        }
    }

    #[test]
    fn candidate_order_registry_order_then_fallbacks() {
        let providers = vec![
            provider("a", true, &["c"], true),
            provider("b", true, &["d"], true),
        ];
        let order = candidate_order(&providers, &["e".to_string()]);
        assert_eq!(order, vec!["a", "b", "c", "d", "e"]);
    }

    #[test]
    fn candidate_order_never_duplicates() {
        let providers = vec![
            provider("a", true, &["a", "b"], true),
            provider("b", true, &["a"], true),
        ];
        let order = candidate_order(&providers, &["a".to_string(), "b".to_string()]);
        assert_eq!(order, vec!["a", "b"]);
    }

    #[test]
    fn candidate_provider_ids_excludes_disabled_and_wrong_tier() {
        let providers = vec![
            provider("disabled", false, &[], true),
            provider("wrong-tier", true, &[], false),
            provider("ok", true, &[], true),
        ];
        let ids = candidate_provider_ids(&providers, &[], &[], 0.0);
        assert_eq!(ids, vec!["ok".to_string()]);
    }

    #[test]
    fn candidate_provider_ids_excludes_cooling_down_provider() {
        let providers = vec![
            provider("p1", true, &[], true),
            provider("p2", true, &[], true),
        ];
        let cooldowns = vec![CooldownRow {
            id: "p1".to_string(),
            cooldown_until_epoch: Some(500.0),
            available: true,
        }];
        let ids = candidate_provider_ids(&providers, &[], &cooldowns, 100.0);
        assert_eq!(ids, vec!["p2".to_string()]);
    }

    #[test]
    fn candidate_provider_ids_excludes_unavailable_provider() {
        let providers = vec![provider("p1", true, &[], true)];
        let cooldowns = vec![CooldownRow {
            id: "p1".to_string(),
            cooldown_until_epoch: None,
            available: false,
        }];
        let ids = candidate_provider_ids(&providers, &[], &cooldowns, 100.0);
        assert!(ids.is_empty());
    }

    #[test]
    fn candidate_provider_ids_all_cooled_down_is_empty_never_panics() {
        let providers = vec![
            provider("p1", true, &[], true),
            provider("p2", true, &[], true),
        ];
        let cooldowns = vec![
            CooldownRow {
                id: "p1".to_string(),
                cooldown_until_epoch: Some(999.0),
                available: true,
            },
            CooldownRow {
                id: "p2".to_string(),
                cooldown_until_epoch: Some(999.0),
                available: true,
            },
        ];
        let ids = candidate_provider_ids(&providers, &[], &cooldowns, 0.0);
        assert!(ids.is_empty());
    }

    #[test]
    fn candidate_provider_ids_unknown_default_fallback_id_is_ignored() {
        // Un id de `default_fallback_chain` qui ne correspond a aucun
        // fournisseur du registre est simplement ignore (`by_id.get` rend
        // `None`) — jamais un index hors borne ni un fournisseur fantome.
        let providers = vec![provider("p1", true, &[], true)];
        let ids = candidate_provider_ids(&providers, &["ghost".to_string()], &[], 0.0);
        assert_eq!(ids, vec!["p1".to_string()]);
    }

    // ── DispatchReport.succeeded / exit_code / refusal_message ─────────

    #[test]
    fn dispatch_succeeded_true_if_any_green() {
        assert!(dispatch_succeeded(&[
            "red".to_string(),
            "green".to_string()
        ]));
        assert!(!dispatch_succeeded(&[
            "red".to_string(),
            "timeout".to_string()
        ]));
        assert!(!dispatch_succeeded(&[]));
    }

    #[test]
    fn dispatch_exit_code_dry_run_always_zero() {
        assert_eq!(
            dispatch_exit_code(true, Some("v2".to_string()).as_deref(), false),
            0
        );
    }

    #[test]
    fn dispatch_exit_code_refusal_is_two() {
        assert_eq!(dispatch_exit_code(false, Some("no_provider"), false), 2);
    }

    #[test]
    fn dispatch_exit_code_success_is_zero_failure_is_one() {
        assert_eq!(dispatch_exit_code(false, None, true), 0);
        assert_eq!(dispatch_exit_code(false, None, false), 1);
    }

    #[test]
    fn refusal_message_known_codes() {
        assert!(refusal_message("v2").contains("V2"));
        assert!(refusal_message("no_check").contains("--check"));
        assert!(refusal_message("no_tier").contains("--max-tier"));
        assert!(refusal_message("no_provider").contains("fournisseur"));
    }

    #[test]
    fn refusal_message_unknown_code_falls_back_to_the_code_itself() {
        assert_eq!(refusal_message("mystere"), "mystere");
    }
}
