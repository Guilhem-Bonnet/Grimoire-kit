//! Coeur Rust optionnel des agregations du journal de traces, de la regle de
//! fraicheur et du declencheur de propositions d'artefact.
//!
//! Sixieme port Rust du kit (issue #354 de Guilhem-Bonnet/Grimoire-kit,
//! apres `rust/grimoire-policies-core/`, `rust/grimoire-schema-core/`,
//! `rust/grimoire-hosts-core/`, `rust/grimoire-flows-core/` et
//! `rust/grimoire-dispatch-core/`, meme montage). Ce crate porte les parties
//! **pures** de trois modules qui forment le coeur du systeme d'artefact
//! emergent (issues #389, #394, #395, #396, #402) :
//!
//! - `grimoire.traces.ledger` : `TraceLedger.agent_dispatch_counts`,
//!   `TraceLedger.agent_miss_counts`, `TraceLedger.oldest_started_at`
//!   (agregations pures sur une sequence de `TraceRecord` deja decodes) et
//!   `compute_agent_freshness` (la regle de fraicheur de l'issue #396).
//! - `grimoire.core.agent_freshness` : rien de plus a porter — ce module
//!   est presque entierement de l'E/S (`collect_agents`, `stat()` de
//!   fichiers, formatage) autour de la meme `compute_agent_freshness`.
//! - `grimoire.proposals` : le nommage mecanique (`_slugify`, `_agent_slug`,
//!   `_skill_slug`, `_guess_tools`, `_employment_clause`), la resolution du
//!   porteur (`_category_carrier`, `_resolve_carrier`, issue #402) et la
//!   decision de synchronisation d'une proposition face au journal
//!   (creer / rafraichir / garder / rouvrir, issue #395).
//!
//! Laisse en Python, deliberement : toute lecture/ecriture de fichiers
//! (`traces.jsonl`, les YAML de proposition, `project-context.yaml`, les
//! definitions d'agent), `collect_agents`/`collect_skills`,
//! `accept_proposal`/`reject_proposal` (qui ecrivent de vrais artefacts) et
//! l'export OTel/Langfuse. Ce module ne fait aucune E/S et ne connait aucun
//! fichier ni horloge (`now` est toujours fourni par l'appelant).
//!
//! ## Le defaut trouve par l'oracle Rust — corrige dans cette PR
//!
//! `grimoire.traces.ledger._parse_iso` attrapait `ValueError` mais pas ce
//! qui suit : `compute_agent_freshness` soustrait ensuite ce datetime de
//! `now` (toujours *aware*, `datetime.now(tz=UTC)`). Un horodatage ISO-8601
//! *naif* (sans decalage — jamais ecrit par `_now_iso()`, mais un journal
//! est un fichier JSONL ordinaire qu'un contributeur ou un outil externe
//! peut editer a la main) fait lever `TypeError: can't subtract
//! offset-naive and offset-aware datetimes`, non rattrape, au beau milieu
//! du calcul de fraicheur — exactement la garantie que ce module revendique
//! ("jamais d'exception sur un journal arbitraire", voir le docstring de
//! `compute_agent_freshness`). Reproduit avant correctif :
//!
//! ```text
//! >>> from datetime import datetime, UTC
//! >>> now = datetime.now(tz=UTC)
//! >>> naive = datetime.fromisoformat("2026-01-01T00:00:00")
//! >>> now - naive
//! TypeError: can't subtract offset-naive and offset-aware datetimes
//! ```
//!
//! Corrige ici (trivial, sans risque) : un horodatage sans decalage est
//! traite comme UTC, exactement comme le fait deja `_now_iso()` pour tout ce
//! que le kit ecrit lui-meme — jamais une exception remontee a l'appelant.
//! `grimoire.traces.ledger._parse_iso` recoit le meme correctif dans cette
//! PR (`parsed.replace(tzinfo=UTC)` quand `parsed.tzinfo is None`), pour que
//! les deux backends s'accordent plutot que de laisser Rust seul tolerant.
//! Voir `tests/unit/test_traces_rust_parity.py::test_naive_timestamp_never_raises_on_either_backend`.
//!
//! ## Les invariants de la doctrine que ce port rend impossibles a violer
//!
//! - `sync_decision_core` clampe tout seuil a 2 (`clamp_threshold`) — aucun
//!   appelant ne peut obtenir "proposition au premier non-choix" en passant
//!   1 ou 0 : la fonction qui decide ne recoit jamais un seuil non clampe,
//!   il n'existe pas de chemin de code qui contourne l'appel.
//! - Une proposition refusee ne redevient `Reopen` que si le compte observe
//!   a au moins double depuis le refus (`reopen_at = 2 * max(rejected_at ou
//!   seuil, 1)`) — encode dans la meme fonction que le clamp de seuil,
//!   jamais reimplemente ailleurs avec un facteur different.
//! - `resolve_carrier_core`/`category_carrier_core` excluent la persona
//!   d'entree de la recherche de porteur avant meme de regarder son
//!   `use_when` (issue #402) : le nom est retire de la liste des candidats
//!   en tete de fonction, il ne peut donc jamais matcher par accident plus
//!   loin dans la meme fonction.
//! - `category_carrier_core` fait un match par mot entier (limites de mot
//!   Unicode, pas une sous-chaine) sur le `use_when` — voir le module
//!   `word_match` plus bas et son commentaire sur pourquoi une sous-chaine
//!   serait fausse (« ci » dans « spécifique »).

#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

use std::collections::HashMap;

// ── Constantes miroir de grimoire.traces.ledger / grimoire.proposals ───────

/// Miroir de `grimoire.traces.ledger.AGENT_DISPATCH_TAG`.
const AGENT_DISPATCH_TAG: &str = "agent.dispatch";
/// Miroir de `grimoire.traces.ledger.AGENT_MISS_TAG`.
const AGENT_MISS_TAG: &str = "agent.miss";
/// Miroir de `grimoire.traces.ledger.UNNAMED_SPECIALTY`.
const UNNAMED_SPECIALTY: &str = "(non nommée)";
/// Miroir de `grimoire.proposals.DEFAULT_THRESHOLD` / `_MIN_THRESHOLD` —
/// les deux valent 2 cote Python ; ce crate ne distingue pas "seuil par
/// defaut" de "seuil plancher", il n'a besoin que du plancher.
const MIN_THRESHOLD: i64 = 2;
/// Miroir de `grimoire.proposals._EXECUTION_HINTS`. Substrings, pas des mots
/// entiers — meme comportement que la reference Python (`_guess_tools` et
/// le volet structurel de `_category_carrier` font tous deux un test `in`
/// simple, jamais un match par mot).
const EXECUTION_HINTS: &[&str] = &[
    "infra",
    "ops",
    "terraform",
    "ansible",
    "deploy",
    "ci",
    "cd",
    "pipeline",
    "docker",
    "kubernetes",
    "k8s",
    "build",
    "test",
    "script",
    "release",
];

// ── iso8601 : un analyseur minimal, jamais paniquant ────────────────────────
//
// `grimoire.traces.ledger` ne manipule des horodatages que sous forme de
// chaines ISO-8601 produites par `datetime.isoformat()` (le format habituel
// du kit) — mais un journal est un fichier JSONL ordinaire, et
// `compute_agent_freshness` doit rester tolerant a n'importe quelle chaine
// qu'un contributeur ou un outil externe y aurait ecrite a la main. Ce
// module ne vise donc pas a reproduire l'integralite de
// `datetime.fromisoformat` (formats ordinaux, semaines ISO...) — seulement
// le sous-ensemble que ce format produit reellement (date, heure optionnelle
// avec fraction de seconde optionnelle, decalage optionnel `Z`/`+HH:MM`/
// `-HH:MM`/`+HHMM`/`-HHMM`/`+HH`) — et, surtout, ne jamais paniquer sur quoi
// que ce soit d'autre : toute forme non reconnue rend `None`, jamais un
// index hors-limites ni une conversion qui echoue en dehors d'un chemin
// controle.
mod iso8601 {
    /// Un instant UTC, en microsecondes depuis l'epoque — suffisant pour un
    /// calcul de difference de jours, jamais utilise pour re-formater une
    /// date.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct Instant {
        micros_since_epoch: i128,
    }

    impl Instant {
        /// Miroir de `timedelta.days` — division entiere vers le bas
        /// (Euclidienne, diviseur positif), jamais une troncature vers zero :
        /// c'est ce qui rend un horodatage *futur* soustrait de `now`
        /// negatif de facon coherente avec Python plutot que d'inverser le
        /// signe d'un arrondi.
        pub fn days_since(&self, other: &Instant) -> i64 {
            const DAY_MICROS: i128 = 86_400_000_000;
            let delta = self.micros_since_epoch - other.micros_since_epoch;
            delta.div_euclid(DAY_MICROS) as i64
        }
    }

    fn is_ascii_digit(c: char) -> bool {
        c.is_ascii_digit()
    }

    /// Consomme exactement `n` chiffres ASCII en tete de `chars`, renvoie
    /// leur valeur entiere. `None` si moins de `n` chiffres sont disponibles
    /// ou qu'un caractere non-chiffre apparait avant.
    fn take_digits(chars: &mut std::iter::Peekable<std::str::Chars<'_>>, n: usize) -> Option<i64> {
        let mut value: i64 = 0;
        for _ in 0..n {
            let c = *chars.peek()?;
            if !is_ascii_digit(c) {
                return None;
            }
            value = value * 10 + i64::from(c as u32 - '0' as u32);
            chars.next();
        }
        Some(value)
    }

    fn is_leap(year: i64) -> bool {
        (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
    }

    fn days_in_month(year: i64, month: i64) -> i64 {
        const DAYS: [i64; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        if month == 2 && is_leap(year) {
            29
        } else {
            DAYS[(month - 1) as usize]
        }
    }

    /// Jours ecoules depuis l'epoque civile (1970-01-01) pour une date
    /// (annee, mois, jour) valide — algorithme "days from civil" de Howard
    /// Hinnant, proleptique gregorien, valable sur toute la plage i64 sans
    /// dependance externe.
    fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
        let y = if month <= 2 { year - 1 } else { year };
        let era = if y >= 0 { y } else { y - 399 } / 400;
        let yoe = y - era * 400; // [0, 399]
        let mp = (month + 9) % 12; // [0, 11], Mar=0 .. Feb=11
        let doy = (153 * mp + 2) / 5 + day - 1; // [0, 365]
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
        era * 146097 + doe - 719468
    }

    /// Analyse une date/heure ISO-8601 dans le sous-ensemble que le kit
    /// produit et tolere (voir le commentaire de module). Un horodatage sans
    /// decalage est traite comme UTC — le correctif de cette PR (voir le
    /// docstring de `lib.rs`) : la reference Python, avant correctif,
    /// laissait echapper une `TypeError` non rattrapee sur ce cas au moment
    /// de la soustraction, jamais a l'analyse elle-meme.
    pub fn parse(value: &str) -> Option<Instant> {
        let mut chars = value.chars().peekable();

        let year = take_digits(&mut chars, 4)?;
        if chars.next()? != '-' {
            return None;
        }
        let month = take_digits(&mut chars, 2)?;
        if !(1..=12).contains(&month) {
            return None;
        }
        if chars.next()? != '-' {
            return None;
        }
        let day = take_digits(&mut chars, 2)?;
        if day < 1 || day > days_in_month(year, month) {
            return None;
        }

        let mut hour = 0i64;
        let mut minute = 0i64;
        let mut second = 0i64;
        let mut micros = 0i64;
        let mut tz_offset_seconds = 0i64;
        let mut has_offset = false;

        if let Some(&sep) = chars.peek() {
            if sep == 'T' || sep == ' ' || sep == 't' {
                chars.next();
                hour = take_digits(&mut chars, 2)?;
                if hour > 23 {
                    return None;
                }
                if chars.next()? != ':' {
                    return None;
                }
                minute = take_digits(&mut chars, 2)?;
                if minute > 59 {
                    return None;
                }
                // Secondes optionnelles (":SS"), comme `datetime.fromisoformat`.
                if chars.peek() == Some(&':') {
                    chars.next();
                    second = take_digits(&mut chars, 2)?;
                    if second > 60 {
                        // tolere la seconde intercalaire 60, jamais rejetee
                        // en dur ailleurs dans ce module.
                        return None;
                    }
                    // Fraction de seconde optionnelle, longueur libre — ne
                    // garde que les 6 premiers chiffres (microsecondes),
                    // consomme le reste sans le compter (jamais de panique
                    // sur une precision inhabituelle).
                    if chars.peek() == Some(&'.') || chars.peek() == Some(&',') {
                        chars.next();
                        let mut digits: Vec<u32> = Vec::new();
                        while let Some(&c) = chars.peek() {
                            if !is_ascii_digit(c) {
                                break;
                            }
                            digits.push(c as u32 - '0' as u32);
                            chars.next();
                        }
                        if digits.is_empty() {
                            return None;
                        }
                        let mut micro_digits = [0u32; 6];
                        for (i, slot) in micro_digits.iter_mut().enumerate() {
                            *slot = *digits.get(i).unwrap_or(&0);
                        }
                        micros = micro_digits
                            .iter()
                            .fold(0i64, |acc, d| acc * 10 + i64::from(*d));
                    }
                }

                // Decalage optionnel.
                match chars.peek().copied() {
                    Some('Z') | Some('z') => {
                        chars.next();
                        has_offset = true;
                    }
                    Some(sign @ ('+' | '-')) => {
                        chars.next();
                        let tz_hour = take_digits(&mut chars, 2)?;
                        let mut tz_minute = 0i64;
                        if chars.peek() == Some(&':') {
                            chars.next();
                            tz_minute = take_digits(&mut chars, 2)?;
                        } else if chars.peek().map(|c| is_ascii_digit(*c)).unwrap_or(false) {
                            tz_minute = take_digits(&mut chars, 2)?;
                        }
                        if tz_hour > 23 || tz_minute > 59 {
                            return None;
                        }
                        let magnitude = tz_hour * 3600 + tz_minute * 60;
                        tz_offset_seconds = if sign == '-' { -magnitude } else { magnitude };
                        has_offset = true;
                    }
                    _ => {}
                }
            } else if sep != '\0' && chars.peek().is_some() {
                // Un caractere de fin de chaine attendu (rien), sinon une
                // forme non reconnue : jamais une panique, un simple refus.
                return None;
            }
        }

        // Rien ne doit rester non consomme : une chaine avec de la traine
        // (par ex. du texte apres une date valide) n'est pas une date ISO
        // valide, exactement comme `datetime.fromisoformat` la rejette.
        if chars.next().is_some() {
            return None;
        }

        let days = days_from_civil(year, month, day);
        let seconds_of_day = hour * 3600 + minute * 60 + second;
        // Horodatage naif (`has_offset == false`) : traite comme UTC (voir
        // le docstring de module — c'est le correctif de cette PR).
        let total_seconds =
            days * 86_400 + seconds_of_day - if has_offset { tz_offset_seconds } else { 0 };
        let micros_since_epoch = i128::from(total_seconds) * 1_000_000 + i128::from(micros);
        Some(Instant { micros_since_epoch })
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn parses_full_offset_form() {
            assert!(parse("2026-09-11T12:34:56.789012+00:00").is_some());
        }

        #[test]
        fn parses_naive_form_as_utc() {
            let naive = parse("2026-01-01T00:00:00").unwrap();
            let with_utc_offset = parse("2026-01-01T00:00:00+00:00").unwrap();
            assert_eq!(naive, with_utc_offset);
        }

        #[test]
        fn parses_z_suffix() {
            let z = parse("2026-01-01T00:00:00Z").unwrap();
            let offset = parse("2026-01-01T00:00:00+00:00").unwrap();
            assert_eq!(z, offset);
        }

        #[test]
        fn parses_date_only_as_midnight() {
            let date_only = parse("2026-01-01").unwrap();
            let midnight = parse("2026-01-01T00:00:00").unwrap();
            assert_eq!(date_only, midnight);
        }

        #[test]
        fn rejects_garbage_without_panicking() {
            for candidate in [
                "",
                "not-a-date",
                "2026",
                "2026-13-01",
                "2026-02-30",
                "2026-01-01T25:00:00",
                "2026-01-01Xgarbage",
                "🎉",
                "2026-01-01T00:00:00+99:99",
            ] {
                assert!(parse(candidate).is_none(), "should reject {candidate:?}");
            }
        }

        #[test]
        fn mixed_offsets_compare_correctly() {
            // 12:00+02:00 == 10:00Z
            let a = parse("2026-01-01T12:00:00+02:00").unwrap();
            let b = parse("2026-01-01T10:00:00Z").unwrap();
            assert_eq!(a, b);
        }

        #[test]
        fn days_since_floors_towards_negative_infinity_for_future_instant() {
            let now = parse("2026-01-10T00:00:00Z").unwrap();
            let future = parse("2026-01-10T12:00:00Z").unwrap();
            // now - future = -12h => floor(-0.5) = -1 jour, jamais 0.
            assert_eq!(now.days_since(&future), -1);
        }

        #[test]
        fn days_since_exact_multiple_of_a_day() {
            let now = parse("2026-04-10T00:00:00Z").unwrap();
            let past = parse("2026-01-01T00:00:00Z").unwrap();
            assert_eq!(now.days_since(&past), 99);
        }
    }
}

// ── word_match : limite de mot Unicode, sans dependance regex ──────────────
//
// Miroir de `re.compile(rf"\b{re.escape(category_lower)}\b")` cote Python
// (`grimoire.proposals._category_carrier`). Une sous-chaine ne suffit pas :
// la categorie "ci" est une sous-chaine de "spécifique" (positions 3-4,
// c/i), mais aucune des deux limites n'y est une frontiere de mot puisque
// les deux caracteres voisins ('é' et 'f') sont eux-memes des caracteres de
// mot Unicode — exactement le cas que
// `tests/unit/test_proposals.py::test_category_matching_is_by_whole_word_not_substring`
// fige deja cote Python. `char::is_alphanumeric()` (Unicode) plus `_`
// reproduit le `\w` de Python d'assez pres pour ce besoin (categories et
// `use_when` sont du texte humain ordinaire, jamais de la ponctuation
// exotique).
mod word_match {
    fn is_word_char(c: char) -> bool {
        c.is_alphanumeric() || c == '_'
    }

    /// `needle` apparait-il dans `haystack` comme mot entier ? Les deux
    /// doivent deja etre dans la meme casse (la reference Python compare
    /// `category_lower` a `use_when.lower()` — la mise en minuscule reste a
    /// la charge de l'appelant, ce module ne fait que la comparaison).
    /// `needle` vide ne matche jamais (une categorie vide est deja exclue
    /// avant d'atteindre ce module cote Python).
    pub fn contains_whole_word(haystack: &str, needle: &str) -> bool {
        if needle.is_empty() {
            return false;
        }
        let hay: Vec<char> = haystack.chars().collect();
        let need: Vec<char> = needle.chars().collect();
        if need.len() > hay.len() {
            return false;
        }
        for start in 0..=(hay.len() - need.len()) {
            if hay[start..start + need.len()] != need[..] {
                continue;
            }
            let before_ok = start == 0 || !is_word_char(hay[start - 1]);
            let end = start + need.len();
            let after_ok = end == hay.len() || !is_word_char(hay[end]);
            if before_ok && after_ok {
                return true;
            }
        }
        false
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn whole_word_match_succeeds() {
            assert!(contains_whole_word("une demande infra ou ops", "infra"));
        }

        #[test]
        fn substring_inside_accented_word_never_matches() {
            // "ci" est une sous-chaine de "spécifique", mais jamais un mot entier.
            assert!(!contains_whole_word(
                "une demande spécifique au produit",
                "ci"
            ));
        }

        #[test]
        fn matches_at_start_and_end_of_string() {
            assert!(contains_whole_word("ci", "ci"));
            assert!(contains_whole_word("build ci", "ci"));
            assert!(contains_whole_word("ci build", "ci"));
        }

        #[test]
        fn empty_needle_never_matches() {
            assert!(!contains_whole_word("quoi que ce soit", ""));
        }

        #[test]
        fn needle_longer_than_haystack_never_panics_or_matches() {
            assert!(!contains_whole_word("ci", "cicicici"));
        }
    }
}

// ── Agregations du journal (TraceLedger) ────────────────────────────────────

/// Projection minimale d'un `TraceRecord` deja decode — les trois seuls
/// champs dont les agregations ci-dessous ont besoin. Construite cote
/// Python a partir de `TraceLedger._load_all()`, jamais un contenu de
/// requete/prompt (voir le docstring de `lib.rs` : ce crate ne recoit que
/// cette projection, un champ de contenu libre ecrit a la main dans le
/// JSONL n'a tout simplement pas de champ correspondant ici pour le porter).
#[derive(Debug, Clone)]
pub struct TraceProjection {
    pub agent_id: String,
    pub started_at: String,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DispatchStat {
    pub count: i64,
    pub last_seen: String,
}

/// Miroir de `TraceLedger.agent_dispatch_counts`. Filtre sur
/// `AGENT_DISPATCH_TAG` et `agent_id` non vide, `last_seen` gagne par
/// comparaison de chaine (pas d'analyse de date — identique a la reference
/// Python, y compris sur un horodatage malforme : une comparaison de chaine
/// ne leve jamais).
pub fn agent_dispatch_counts_core(records: &[TraceProjection]) -> Vec<(String, DispatchStat)> {
    let mut order: Vec<String> = Vec::new();
    let mut map: HashMap<String, DispatchStat> = HashMap::new();
    for record in records {
        if record.agent_id.is_empty() {
            continue;
        }
        if !record.tags.iter().any(|t| t == AGENT_DISPATCH_TAG) {
            continue;
        }
        let entry = map.entry(record.agent_id.clone()).or_insert_with(|| {
            order.push(record.agent_id.clone());
            DispatchStat {
                count: 0,
                last_seen: String::new(),
            }
        });
        entry.count += 1;
        if record.started_at.as_str() > entry.last_seen.as_str() {
            entry.last_seen = record.started_at.clone();
        }
    }
    order
        .into_iter()
        .map(|name| {
            let stat = map.remove(&name).expect("just inserted");
            (name, stat)
        })
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissStat {
    pub count: i64,
    pub last_seen: String,
    pub category: String,
    pub fallback_agent: String,
}

/// Miroir de `TraceLedger.agent_miss_counts`. Une entree sans specialite
/// nommable est comptee sous `UNNAMED_SPECIALTY`, jamais ignoree — le
/// non-choix reste un signal. Quand plusieurs tags `specialty:`/`category:`
/// figurent sur le meme enregistrement (un journal edite a la main peut en
/// avoir plusieurs), le dernier de la liste des tags gagne — meme ordre
/// d'iteration que la boucle Python `for tag in trace.tags`.
pub fn agent_miss_counts_core(records: &[TraceProjection]) -> Vec<(String, MissStat)> {
    let mut order: Vec<String> = Vec::new();
    let mut map: HashMap<String, MissStat> = HashMap::new();
    for record in records {
        if !record.tags.iter().any(|t| t == AGENT_MISS_TAG) {
            continue;
        }
        let mut specialty = UNNAMED_SPECIALTY.to_string();
        let mut category = String::new();
        for tag in &record.tags {
            if let Some(rest) = tag.strip_prefix("specialty:") {
                specialty = if rest.is_empty() {
                    UNNAMED_SPECIALTY.to_string()
                } else {
                    rest.to_string()
                };
            } else if let Some(rest) = tag.strip_prefix("category:") {
                category = rest.to_string();
            }
        }
        let entry = map.entry(specialty.clone()).or_insert_with(|| {
            order.push(specialty.clone());
            MissStat {
                count: 0,
                last_seen: String::new(),
                category: String::new(),
                fallback_agent: String::new(),
            }
        });
        entry.count += 1;
        if record.started_at.as_str() > entry.last_seen.as_str() {
            entry.category = category.clone();
            entry.fallback_agent = record.agent_id.clone();
            entry.last_seen = record.started_at.clone();
        }
    }
    order
        .into_iter()
        .map(|specialty| {
            let stat = map.remove(&specialty).expect("just inserted");
            (specialty, stat)
        })
        .collect()
}

/// Miroir de `TraceLedger.oldest_started_at` : le plus petit `started_at`
/// (comparaison de chaine) parmi les enregistrements dont il est non vide.
/// `None` sur un journal vide ou dont aucun enregistrement n'a de
/// `started_at` — jamais une erreur.
pub fn oldest_started_at_core(started_ats: &[String]) -> Option<String> {
    started_ats.iter().filter(|s| !s.is_empty()).min().cloned()
}

// ── Comptabilite continue du dispatch : cout par tache resolue et pass^k
//    (issue #442, point 5 de l'audit de positionnement du 2026-09-12) ───────

const DISPATCH_OUTCOME_TAG: &str = "dispatch.outcome";

/// Projection minimale d'un enregistrement `dispatch.outcome` deja decode —
/// les seuls deux champs dont l'agregation ci-dessous a besoin. Construite
/// cote Python a partir de `TraceLedger._load_all()`
/// (`(list(trace.tags), trace.token_usage.estimated_cost_usd)`), jamais un
/// contenu de prompt ou de sortie de commande : ce crate ne recoit que des
/// etiquettes mecaniques (voir `missions.dispatch._record_dispatch_outcome`
/// cote Python pour ce qu'elles portent).
#[derive(Debug, Clone)]
pub struct DispatchOutcomeProjection {
    pub tags: Vec<String>,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DispatchGroupStats {
    pub total: i64,
    pub resolved: i64,
    pub inexecutable: i64,
    pub escalated: i64,
    pub total_cost_usd: f64,
}

impl DispatchGroupStats {
    fn empty() -> Self {
        Self {
            total: 0,
            resolved: 0,
            inexecutable: 0,
            escalated: 0,
            total_cost_usd: 0.0,
        }
    }

    fn accumulate(&mut self, resolved: bool, inexecutable: bool, escalated: bool, cost_usd: f64) {
        self.total += 1;
        self.total_cost_usd += cost_usd;
        if resolved {
            self.resolved += 1;
        }
        if inexecutable {
            self.inexecutable += 1;
        }
        if escalated {
            self.escalated += 1;
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct DispatchOutcomeStatsCore {
    pub overall: DispatchGroupStats,
    pub by_class: Vec<(String, DispatchGroupStats)>,
    pub by_provider: Vec<(String, DispatchGroupStats)>,
    pub pass_k_observations: i64,
    pub pass_k_fully_green: i64,
}

/// La valeur du dernier tag `prefix`-prefixe de `tags`, `""` si aucun — meme
/// regle « le dernier gagne » que `agent_miss_counts_core` pour
/// `specialty:`/`category:` : un journal edite a la main peut porter
/// plusieurs tags du meme prefixe, le plus recemment ajoute prime.
fn last_tag_value(tags: &[String], prefix: &str) -> String {
    let mut value = String::new();
    for tag in tags {
        if let Some(rest) = tag.strip_prefix(prefix) {
            value = rest.to_string();
        }
    }
    value
}

/// Miroir de `compute_dispatch_outcome_stats` (cote Python,
/// `grimoire.traces.ledger`). Filtre sur `DISPATCH_OUTCOME_TAG` ; `by_class`
/// et `by_provider` sont deux ventilations independantes du meme `overall`
/// (pas une matrice croisee), dans l'ordre de premiere apparition — meme
/// convention que `agent_dispatch_counts_core`. pass^k : deux
/// enregistrements ou plus partageant le meme tag `replay:` forment une
/// serie rejouee, comptee « toute au vert » quand tous ses enregistrements
/// sont `resolved:true`.
pub fn dispatch_outcome_stats_core(
    records: &[DispatchOutcomeProjection],
) -> DispatchOutcomeStatsCore {
    let mut overall = DispatchGroupStats::empty();
    let mut class_order: Vec<String> = Vec::new();
    let mut by_class: HashMap<String, DispatchGroupStats> = HashMap::new();
    let mut provider_order: Vec<String> = Vec::new();
    let mut by_provider: HashMap<String, DispatchGroupStats> = HashMap::new();
    let mut replay_total: HashMap<String, i64> = HashMap::new();
    let mut replay_resolved: HashMap<String, i64> = HashMap::new();

    for record in records {
        if !record.tags.iter().any(|t| t == DISPATCH_OUTCOME_TAG) {
            continue;
        }
        let class_ = last_tag_value(&record.tags, "class:");
        let mut tiers: Vec<&str> = record
            .tags
            .iter()
            .filter_map(|t| t.strip_prefix("tier:"))
            .collect();
        tiers.sort_unstable();
        tiers.dedup();
        let acceptance = last_tag_value(&record.tags, "acceptance:");
        let resolved = last_tag_value(&record.tags, "resolved:") == "true";
        let provider = last_tag_value(&record.tags, "provider:");
        let replay_key = last_tag_value(&record.tags, "replay:");
        let escalated = tiers.len() > 1;
        let inexecutable = acceptance == "unrunnable";

        overall.accumulate(resolved, inexecutable, escalated, record.cost_usd);
        if !class_.is_empty() {
            let entry = by_class.entry(class_.clone()).or_insert_with(|| {
                class_order.push(class_.clone());
                DispatchGroupStats::empty()
            });
            entry.accumulate(resolved, inexecutable, escalated, record.cost_usd);
        }
        if !provider.is_empty() {
            let entry = by_provider.entry(provider.clone()).or_insert_with(|| {
                provider_order.push(provider.clone());
                DispatchGroupStats::empty()
            });
            entry.accumulate(resolved, inexecutable, escalated, record.cost_usd);
        }
        if !replay_key.is_empty() {
            *replay_total.entry(replay_key.clone()).or_insert(0) += 1;
            if resolved {
                *replay_resolved.entry(replay_key).or_insert(0) += 1;
            }
        }
    }

    let mut pass_k_observations: i64 = 0;
    let mut pass_k_fully_green: i64 = 0;
    for (key, total) in &replay_total {
        if *total < 2 {
            continue;
        }
        pass_k_observations += 1;
        if replay_resolved.get(key).copied().unwrap_or(0) == *total {
            pass_k_fully_green += 1;
        }
    }

    DispatchOutcomeStatsCore {
        overall,
        by_class: class_order
            .into_iter()
            .map(|name| {
                let stats = by_class.remove(&name).expect("just inserted");
                (name, stats)
            })
            .collect(),
        by_provider: provider_order
            .into_iter()
            .map(|name| {
                let stats = by_provider.remove(&name).expect("just inserted");
                (name, stats)
            })
            .collect(),
        pass_k_observations,
        pass_k_fully_green,
    }
}

// ── Fraicheur (issue #396) ──────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct FreshnessEntryCore {
    pub name: String,
    pub last_seen: Option<String>,
    pub days_since: Option<i64>,
    pub stale: bool,
    pub too_recent: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FreshnessResultCore {
    pub judged: bool,
    pub journal_span_days: Option<i64>,
    pub entries: Vec<FreshnessEntryCore>,
}

/// Miroir corrige de `grimoire.traces.ledger.compute_agent_freshness` (voir
/// le docstring de `lib.rs` pour le defaut corrige). `dispatch_last_seen` ne
/// porte que le `last_seen` de chaque agent (le `count` d'
/// `agent_dispatch_counts` n'entre jamais dans ce calcul, cote Python non
/// plus). `now` est toujours fourni par l'appelant (jamais d'horloge lue
/// ici) — un `now` non analysable est un defaut d'appel, pas une entree de
/// journal arbitraire, donc `None` plutot qu'un panic : l'appelant PyO3
/// leve une erreur nommee dans ce cas, ca ne devrait jamais arriver en
/// pratique puisque Python construit toujours `now` depuis
/// `datetime.now(tz=UTC).isoformat()`.
pub fn compute_agent_freshness_core(
    agent_names: &[String],
    dispatch_last_seen: &HashMap<String, String>,
    threshold_days: i64,
    oldest_started_at: Option<&str>,
    agent_ages: &HashMap<String, Option<i64>>,
    now: &iso8601::Instant,
) -> FreshnessResultCore {
    let journal_span_days = oldest_started_at
        .and_then(iso8601::parse)
        .map(|oldest| now.days_since(&oldest));
    let judged = journal_span_days.is_some_and(|span| span >= threshold_days);

    let mut names: Vec<String> = agent_names.to_vec();
    names.sort();
    names.dedup();

    let mut entries = Vec::with_capacity(names.len());
    for name in names {
        let last_seen_raw = dispatch_last_seen.get(&name).filter(|s| !s.is_empty());
        let days_since = last_seen_raw
            .and_then(|s| iso8601::parse(s))
            .map(|seen| now.days_since(&seen));
        let agent_age = agent_ages.get(&name).copied().flatten();
        let too_recent = agent_age.is_some_and(|age| age < threshold_days);
        let stale = judged
            && !too_recent
            && (last_seen_raw.is_none() || days_since.is_some_and(|d| d >= threshold_days));
        entries.push(FreshnessEntryCore {
            name,
            last_seen: last_seen_raw.cloned(),
            days_since,
            stale,
            too_recent,
        });
    }

    FreshnessResultCore {
        judged,
        journal_span_days,
        entries,
    }
}

// ── Nommage mecanique (grimoire.proposals) ──────────────────────────────────

/// Miroir de `grimoire.proposals._slugify`. Seuls `a-z0-9` (ASCII) survivent
/// — tout le reste (espaces, ponctuation, lettres accentuees Unicode) est
/// collapse en un seul `-` par sequence, jamais un `-` par caractere : meme
/// comportement que `re.sub(r"[^a-z0-9]+", "-", ...)`.
pub fn slugify_core(text: &str) -> String {
    let lowered = text.trim().to_lowercase();
    let mut out = String::with_capacity(lowered.len());
    let mut in_run = false;
    for c in lowered.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            in_run = false;
        } else if !in_run {
            out.push('-');
            in_run = true;
        }
    }
    let trimmed = out.trim_matches('-');
    if trimmed.is_empty() {
        "specialite".to_string()
    } else {
        trimmed.to_string()
    }
}

pub fn agent_slug_core(specialty: &str) -> String {
    format!("{}-specialist", slugify_core(specialty))
}

pub fn skill_slug_core(specialty: &str) -> String {
    slugify_core(specialty)
}

/// Miroir de `grimoire.proposals._guess_tools`. Substring, pas mot entier —
/// meme heuristique volontairement grossiere que la reference Python.
pub fn guess_tools_core(category: &str, specialty: &str) -> &'static str {
    let haystack = format!("{category} {specialty}").to_lowercase();
    if EXECUTION_HINTS.iter().any(|hint| haystack.contains(hint)) {
        "read, search, execute"
    } else {
        "read, search"
    }
}

/// Miroir de `grimoire.proposals._employment_clause`. Le texte francais est
/// reproduit mot pour mot — plusieurs tests golden Python
/// (`tests/unit/test_proposals.py`) comparent des sous-chaines de ce
/// resultat.
pub fn employment_clause_core(specialty: &str, category: &str, carrier: &str) -> (String, String) {
    let use_when = if category.is_empty() {
        format!(
            "Une demande cherche une compétence « {specialty} » qu'aucun agent déclaré ne couvre."
        )
    } else {
        format!(
            "Une demande classée « {category} » cherche une compétence « {specialty} » qu'aucun agent déclaré ne couvre."
        )
    };
    let dont_use_when = if carrier.is_empty() {
        format!(
            "Toute demande déjà couverte par un agent déclaré — ce spécialiste n'existe que pour « {specialty} »."
        )
    } else {
        format!(
            "Toute demande déjà couverte par {carrier} ou un autre agent déclaré — ce spécialiste n'existe que pour « {specialty} »."
        )
    };
    (use_when, dont_use_when)
}

// ── Porteur (issue #402) ─────────────────────────────────────────────────────

/// Un agent declare candidat a etre porteur : son nom, son `use_when`
/// declare (chaine vide si absent/illisible — deja best-effort cote Python
/// via `parse_frontmatter`), et s'il porte l'outil `execute` dans son
/// faisceau.
#[derive(Debug, Clone)]
pub struct CarrierCandidate {
    pub name: String,
    pub use_when: String,
    pub has_execute_tool: bool,
}

/// Miroir de `grimoire.proposals._category_carrier`. `entry_name` est
/// exclu de la recherche *avant* tout test de `use_when` — meme si son
/// `use_when` nomme la categorie, il ne peut jamais matcher (issue #402).
/// Une categorie vide ne matche jamais rien (deja garde cote appelant, mais
/// defensif ici aussi). Exactement un candidat -> porteur ; zero ou
/// plusieurs -> aucun (une ambiguite vaut une absence).
pub fn category_carrier_core(
    category: &str,
    entry_name: &str,
    candidates: &[CarrierCandidate],
) -> Option<String> {
    if category.is_empty() {
        return None;
    }
    let category_lower = category.to_lowercase();
    let is_execution_category = EXECUTION_HINTS
        .iter()
        .any(|hint| category_lower.contains(hint));

    let mut matches: Vec<&str> = Vec::new();
    for candidate in candidates {
        if !entry_name.is_empty() && candidate.name == entry_name {
            continue;
        }
        let matches_use_when =
            word_match::contains_whole_word(&candidate.use_when.to_lowercase(), &category_lower);
        let matches_execute = is_execution_category && candidate.has_execute_tool;
        if matches_use_when || matches_execute {
            matches.push(candidate.name.as_str());
        }
    }

    if matches.len() == 1 {
        Some(matches[0].to_string())
    } else {
        None
    }
}

/// Miroir de `grimoire.proposals._resolve_carrier`. `category_candidate`
/// est deja le resultat de `category_carrier_core` — cette fonction ne fait
/// que la partie "repli observe vs recherche par categorie" qui ne depend
/// d'aucune lecture de fichier.
pub fn resolve_carrier_core(
    fallback_agent: &str,
    entry_name: &str,
    category_candidate: Option<&str>,
) -> (String, String) {
    if !fallback_agent.is_empty() && fallback_agent != entry_name {
        return (fallback_agent.to_string(), "repli observé".to_string());
    }
    if let Some(carrier) = category_candidate {
        return (
            carrier.to_string(),
            format!("porteur par catégorie : {carrier}"),
        );
    }
    (
        String::new(),
        "persona d'entrée exclue, aucun porteur : agent".to_string(),
    )
}

// ── Gabarit d'une proposition (hors horodatages/statut, laisses a Python) ──

#[derive(Debug, Clone, PartialEq)]
pub struct ProposalFieldsCore {
    pub slug: String,
    pub artifact_type: &'static str,
    pub agent_role: String,
    pub use_when: String,
    pub dont_use_when: String,
    pub tool_boundary: String,
    pub tools: String,
    pub target_agent: String,
}

/// Miroir de `grimoire.proposals._build_proposal`, moins `status`,
/// `created_at`, `first_seen`/`last_seen`/`count` (des faits observes ou de
/// l'horodatage, pas du gabarit mecanique) — Python les assemble autour de
/// ce resultat.
pub fn build_proposal_fields_core(
    specialty: &str,
    category: &str,
    carrier: &str,
) -> ProposalFieldsCore {
    let (use_when, dont_use_when) = employment_clause_core(specialty, category, carrier);
    let agent_role = format!(
        "Spécialiste {specialty} pour les demandes {}",
        if category.is_empty() {
            "sans catégorie"
        } else {
            category
        }
    );
    if !carrier.is_empty() {
        ProposalFieldsCore {
            slug: skill_slug_core(specialty),
            artifact_type: "skill",
            agent_role,
            use_when,
            dont_use_when,
            tool_boundary: String::new(),
            tools: String::new(),
            target_agent: carrier.to_string(),
        }
    } else {
        ProposalFieldsCore {
            slug: agent_slug_core(specialty),
            artifact_type: "agent",
            agent_role,
            use_when,
            dont_use_when,
            tool_boundary: format!(
                "Lecture et recherche circonscrites au périmètre « {specialty} », distinct du périmètre générique de l'agent de repli."
            ),
            tools: guess_tools_core(category, specialty).to_string(),
            target_agent: String::new(),
        }
    }
}

// ── Decision de synchronisation (issue #395) ────────────────────────────────

/// Miroir de `grimoire.proposals.DEFAULT_THRESHOLD`/`_MIN_THRESHOLD` : un
/// seuil configure jamais applique sous sa forme brute — clampe a 2 des les
/// deux backends, sans exception pour "l'appelant a explicitement demande
/// 1". La doctrine (#389/#395) est explicite : jamais de proposition au
/// premier non-choix.
pub fn clamp_threshold_core(raw: i64) -> i64 {
    raw.max(MIN_THRESHOLD)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SyncAction {
    /// Aucune proposition existante, compte encore sous le seuil — rien a
    /// creer, rien a afficher.
    Skip,
    /// Aucune proposition existante, compte au seuil ou au-dessus — un
    /// gabarit doit etre construit et ecrit.
    Create,
    /// Proposition deja acceptee — jamais reevaluee ni retype.
    KeepAccepted,
    /// Toujours en attente — rafraichir les faits observes, jamais le type.
    RefreshPending,
    /// Refusee, compte encore sous le seuil de reouverture — rafraichir les
    /// faits observes, rester refusee.
    KeepRejected,
    /// Refusee, compte au moins double depuis le refus — reevaluer le type
    /// (le porteur a pu changer) et rouvrir en attente.
    Reopen,
}

impl SyncAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            SyncAction::Skip => "skip",
            SyncAction::Create => "create",
            SyncAction::KeepAccepted => "keep_accepted",
            SyncAction::RefreshPending => "refresh_pending",
            SyncAction::KeepRejected => "keep_rejected",
            SyncAction::Reopen => "reopen",
        }
    }
}

/// Miroir exhaustif de la branche centrale de `grimoire.proposals.sync_proposals`
/// (le `for specialty, stats in misses.items(): ...` — sans l'E/S de lecture/
/// ecriture des fichiers YAML autour). `existing` est `None` quand aucun
/// fichier de proposition n'existe encore pour cette specialite (ni
/// `<slug>-specialist.yaml` ni `<slug>.yaml`).
///
/// Le clamp de seuil (`clamp_threshold_core`) est applique *dans* cette
/// fonction, jamais delegue a l'appelant : aucun chemin de code ne peut
/// obtenir une decision fondee sur un seuil non clampe.
pub fn sync_decision_core(
    threshold_raw: i64,
    observed_count: i64,
    existing_status: Option<&str>,
    existing_count: i64,
    existing_rejected_at_count: Option<i64>,
) -> (SyncAction, i64, Option<i64>) {
    let threshold = clamp_threshold_core(threshold_raw);
    match existing_status {
        None => {
            if observed_count < threshold {
                (SyncAction::Skip, threshold, None)
            } else {
                (SyncAction::Create, threshold, None)
            }
        }
        Some("accepted") => (SyncAction::KeepAccepted, threshold, None),
        Some("rejected") => {
            // `existing.rejected_at_count or effective_threshold` cote
            // Python : `0` est aussi falsy que `None` en Python — reproduit
            // ici a l'identique (voir le test de parite dedie) plutot que
            // "corrige" silencieusement, ce comportement n'etant pas dans
            // le perimetre des invariants de doctrine de cette PR.
            let base = match existing_rejected_at_count {
                Some(c) if c != 0 => c,
                _ => threshold,
            };
            let reopen_at = 2 * base.max(1);
            if observed_count < reopen_at {
                (SyncAction::KeepRejected, threshold, Some(reopen_at))
            } else {
                (SyncAction::Reopen, threshold, Some(reopen_at))
            }
        }
        // "pending", ou tout statut non reconnu — la reference Python
        // (`_load_proposal`) ne charge jamais un statut hors
        // `{pending, accepted, rejected}` (`data.get("status") not in
        // _STATUSES` -> `None`), donc `existing_status` ne devrait jamais
        // valoir autre chose ici. Le traiter comme "pending" (rafraichir
        // sans reevaluer le type) est le repli le moins surprenant plutot
        // qu'un panic sur une valeur qui ne devrait jamais arriver.
        _ => {
            let _ = existing_count; // conserve pour la forme de la frontiere PyO3 ; pas utilise ici.
            (SyncAction::RefreshPending, threshold, None)
        }
    }
}

// ── Frontiere PyO3 ───────────────────────────────────────────────────────

#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{
        agent_dispatch_counts_core, agent_miss_counts_core, agent_slug_core,
        build_proposal_fields_core, category_carrier_core, clamp_threshold_core,
        compute_agent_freshness_core, dispatch_outcome_stats_core, employment_clause_core,
        guess_tools_core, iso8601, oldest_started_at_core, resolve_carrier_core, skill_slug_core,
        slugify_core, sync_decision_core, CarrierCandidate, DispatchGroupStats,
        DispatchOutcomeProjection, TraceProjection,
    };
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use std::collections::HashMap;

    fn parse_now(now: &str) -> PyResult<iso8601::Instant> {
        iso8601::parse(now).ok_or_else(|| {
            PyValueError::new_err(format!(
                "grimoire_traces_core: 'now' invalide (attendu ISO-8601, recu {now:?}) — devrait toujours venir de datetime.now(tz=UTC).isoformat()"
            ))
        })
    }

    fn to_projections(records: Vec<(String, String, Vec<String>)>) -> Vec<TraceProjection> {
        records
            .into_iter()
            .map(|(agent_id, started_at, tags)| TraceProjection {
                agent_id,
                started_at,
                tags,
            })
            .collect()
    }

    /// Frontiere PyO3 pour `TraceLedger.agent_dispatch_counts`. `records`
    /// est `[(agent_id, started_at, tags), ...]` — la projection minimale
    /// construite par `TraceLedger._load_all()` cote Python, jamais le
    /// `TraceRecord` complet. Retourne `[(agent_id, count, last_seen), ...]`.
    #[pyfunction]
    fn agent_dispatch_counts(
        records: Vec<(String, String, Vec<String>)>,
    ) -> Vec<(String, i64, String)> {
        agent_dispatch_counts_core(&to_projections(records))
            .into_iter()
            .map(|(name, stat)| (name, stat.count, stat.last_seen))
            .collect()
    }

    /// Frontiere PyO3 pour `TraceLedger.agent_miss_counts`. Retourne
    /// `[(specialty, count, last_seen, category, fallback_agent), ...]`.
    #[pyfunction]
    fn agent_miss_counts(
        records: Vec<(String, String, Vec<String>)>,
    ) -> Vec<(String, i64, String, String, String)> {
        agent_miss_counts_core(&to_projections(records))
            .into_iter()
            .map(|(specialty, stat)| {
                (
                    specialty,
                    stat.count,
                    stat.last_seen,
                    stat.category,
                    stat.fallback_agent,
                )
            })
            .collect()
    }

    /// Frontiere PyO3 pour `TraceLedger.oldest_started_at`.
    #[pyfunction]
    fn oldest_started_at(started_ats: Vec<String>) -> Option<String> {
        oldest_started_at_core(&started_ats)
    }

    /// Frontiere PyO3 pour `compute_agent_freshness`. `dispatch_last_seen`
    /// est deja projete par l'appelant a `{agent_id: last_seen}` (le
    /// `count` d'`agent_dispatch_counts` n'entre jamais dans ce calcul).
    /// Retourne `(judged, journal_span_days, entries)` avec `entries` sous
    /// forme `[(name, last_seen, days_since, stale, too_recent), ...]`,
    /// triees par nom (meme ordre que `sorted(set(agent_names))` cote
    /// Python).
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn compute_agent_freshness(
        agent_names: Vec<String>,
        dispatch_last_seen: HashMap<String, String>,
        threshold_days: i64,
        oldest_started_at: Option<String>,
        agent_ages: HashMap<String, Option<i64>>,
        now: &str,
    ) -> PyResult<(
        bool,
        Option<i64>,
        Vec<(String, Option<String>, Option<i64>, bool, bool)>,
    )> {
        let now_instant = parse_now(now)?;
        let result = compute_agent_freshness_core(
            &agent_names,
            &dispatch_last_seen,
            threshold_days,
            oldest_started_at.as_deref(),
            &agent_ages,
            &now_instant,
        );
        let entries = result
            .entries
            .into_iter()
            .map(|e| (e.name, e.last_seen, e.days_since, e.stale, e.too_recent))
            .collect();
        Ok((result.judged, result.journal_span_days, entries))
    }

    /// Frontiere PyO3 pour `grimoire.proposals._slugify`.
    #[pyfunction]
    fn slugify(text: &str) -> String {
        slugify_core(text)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._agent_slug`.
    #[pyfunction]
    fn agent_slug(specialty: &str) -> String {
        agent_slug_core(specialty)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._skill_slug`.
    #[pyfunction]
    fn skill_slug(specialty: &str) -> String {
        skill_slug_core(specialty)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._guess_tools`.
    #[pyfunction]
    fn guess_tools(category: &str, specialty: &str) -> String {
        guess_tools_core(category, specialty).to_string()
    }

    /// Frontiere PyO3 pour `grimoire.proposals._employment_clause`.
    #[pyfunction]
    fn employment_clause(specialty: &str, category: &str, carrier: &str) -> (String, String) {
        employment_clause_core(specialty, category, carrier)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._category_carrier`.
    /// `candidates` est `[(name, use_when, has_execute_tool), ...]` pour
    /// **tous** les agents declares (la persona d'entree incluse : c'est
    /// cette fonction qui l'exclut, exactement comme la reference Python).
    #[pyfunction]
    fn category_carrier(
        category: &str,
        entry_name: &str,
        candidates: Vec<(String, String, bool)>,
    ) -> Option<String> {
        let candidates: Vec<CarrierCandidate> = candidates
            .into_iter()
            .map(|(name, use_when, has_execute_tool)| CarrierCandidate {
                name,
                use_when,
                has_execute_tool,
            })
            .collect();
        category_carrier_core(category, entry_name, &candidates)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._resolve_carrier`.
    #[pyfunction]
    fn resolve_carrier(
        fallback_agent: &str,
        entry_name: &str,
        category_candidate: Option<&str>,
    ) -> (String, String) {
        resolve_carrier_core(fallback_agent, entry_name, category_candidate)
    }

    /// Frontiere PyO3 pour `grimoire.proposals._build_proposal` (sans
    /// horodatages/statut — voir `ProposalFieldsCore`). Retourne
    /// `(slug, artifact_type, agent_role, use_when, dont_use_when,
    /// tool_boundary, tools, target_agent)`.
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn build_proposal_fields(
        specialty: &str,
        category: &str,
        carrier: &str,
    ) -> (
        String,
        String,
        String,
        String,
        String,
        String,
        String,
        String,
    ) {
        let fields = build_proposal_fields_core(specialty, category, carrier);
        (
            fields.slug,
            fields.artifact_type.to_string(),
            fields.agent_role,
            fields.use_when,
            fields.dont_use_when,
            fields.tool_boundary,
            fields.tools,
            fields.target_agent,
        )
    }

    /// Frontiere PyO3 pour le clamp de seuil (`_MIN_THRESHOLD`).
    #[pyfunction]
    fn clamp_threshold(raw: i64) -> i64 {
        clamp_threshold_core(raw)
    }

    /// Frontiere PyO3 pour la decision centrale de `sync_proposals` (issue
    /// #395). `existing_status` est `None` quand aucun fichier de
    /// proposition n'existe encore pour cette specialite. Retourne
    /// `(action, effective_threshold, reopen_at)` — `action` est l'une des
    /// chaines `SyncAction::as_str()`.
    #[pyfunction]
    fn sync_decision(
        threshold_raw: i64,
        observed_count: i64,
        existing_status: Option<&str>,
        existing_count: i64,
        existing_rejected_at_count: Option<i64>,
    ) -> (String, i64, Option<i64>) {
        let (action, threshold, reopen_at) = sync_decision_core(
            threshold_raw,
            observed_count,
            existing_status,
            existing_count,
            existing_rejected_at_count,
        );
        (action.as_str().to_string(), threshold, reopen_at)
    }

    type GroupStatsTuple = (i64, i64, i64, i64, f64);

    fn group_stats_to_tuple(stats: DispatchGroupStats) -> GroupStatsTuple {
        (
            stats.total,
            stats.resolved,
            stats.inexecutable,
            stats.escalated,
            stats.total_cost_usd,
        )
    }

    /// Frontiere PyO3 pour `TraceLedger.dispatch_outcome_stats`
    /// (`compute_dispatch_outcome_stats`, issue #442). `records` est
    /// `[(tags, cost_usd), ...]` — la projection minimale construite par
    /// `TraceLedger._load_all()` cote Python. Retourne `(overall, by_class,
    /// by_provider, pass_k_observations, pass_k_fully_green)` ou chaque
    /// groupe est `(total, resolved, inexecutable, escalated,
    /// total_cost_usd)` et `by_class`/`by_provider` sont
    /// `[(nom, groupe), ...]`.
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn dispatch_outcome_stats(
        records: Vec<(Vec<String>, f64)>,
    ) -> (
        GroupStatsTuple,
        Vec<(String, GroupStatsTuple)>,
        Vec<(String, GroupStatsTuple)>,
        i64,
        i64,
    ) {
        let projections: Vec<DispatchOutcomeProjection> = records
            .into_iter()
            .map(|(tags, cost_usd)| DispatchOutcomeProjection { tags, cost_usd })
            .collect();
        let result = dispatch_outcome_stats_core(&projections);
        (
            group_stats_to_tuple(result.overall),
            result
                .by_class
                .into_iter()
                .map(|(name, stats)| (name, group_stats_to_tuple(stats)))
                .collect(),
            result
                .by_provider
                .into_iter()
                .map(|(name, stats)| (name, group_stats_to_tuple(stats)))
                .collect(),
            result.pass_k_observations,
            result.pass_k_fully_green,
        )
    }

    #[pymodule]
    fn grimoire_traces_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(agent_dispatch_counts, m)?)?;
        m.add_function(wrap_pyfunction!(agent_miss_counts, m)?)?;
        m.add_function(wrap_pyfunction!(dispatch_outcome_stats, m)?)?;
        m.add_function(wrap_pyfunction!(oldest_started_at, m)?)?;
        m.add_function(wrap_pyfunction!(compute_agent_freshness, m)?)?;
        m.add_function(wrap_pyfunction!(slugify, m)?)?;
        m.add_function(wrap_pyfunction!(agent_slug, m)?)?;
        m.add_function(wrap_pyfunction!(skill_slug, m)?)?;
        m.add_function(wrap_pyfunction!(guess_tools, m)?)?;
        m.add_function(wrap_pyfunction!(employment_clause, m)?)?;
        m.add_function(wrap_pyfunction!(category_carrier, m)?)?;
        m.add_function(wrap_pyfunction!(resolve_carrier, m)?)?;
        m.add_function(wrap_pyfunction!(build_proposal_fields, m)?)?;
        m.add_function(wrap_pyfunction!(clamp_threshold, m)?)?;
        m.add_function(wrap_pyfunction!(sync_decision, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

// ── Tests unitaires purs (aucun interprete Python requis) ──────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(agent_id: &str, started_at: &str, tags: &[&str]) -> TraceProjection {
        TraceProjection {
            agent_id: agent_id.to_string(),
            started_at: started_at.to_string(),
            tags: tags.iter().map(|s| s.to_string()).collect(),
        }
    }

    // ── agent_dispatch_counts_core ───────────────────────────────────────

    #[test]
    fn dispatch_counts_ignores_untagged_records() {
        let records = [rec("dev", "2026-01-01T00:00:00+00:00", &[])];
        assert!(agent_dispatch_counts_core(&records).is_empty());
    }

    #[test]
    fn dispatch_counts_ignores_empty_agent_id() {
        let records = [rec("", "2026-01-01T00:00:00+00:00", &[AGENT_DISPATCH_TAG])];
        assert!(agent_dispatch_counts_core(&records).is_empty());
    }

    #[test]
    fn dispatch_counts_counts_and_keeps_most_recent() {
        let records = [
            rec(
                "concierge",
                "2026-01-01T00:00:00+00:00",
                &[AGENT_DISPATCH_TAG],
            ),
            rec(
                "concierge",
                "2026-01-02T00:00:00+00:00",
                &[AGENT_DISPATCH_TAG],
            ),
            rec("scribe", "2026-01-03T00:00:00+00:00", &[AGENT_DISPATCH_TAG]),
        ];
        let counts: HashMap<_, _> = agent_dispatch_counts_core(&records).into_iter().collect();
        assert_eq!(counts["concierge"].count, 2);
        assert_eq!(counts["concierge"].last_seen, "2026-01-02T00:00:00+00:00");
        assert_eq!(counts["scribe"].count, 1);
    }

    // ── agent_miss_counts_core ────────────────────────────────────────────

    #[test]
    fn miss_counts_ignores_untagged_records() {
        let records = [rec("dev", "2026-01-01T00:00:00+00:00", &[])];
        assert!(agent_miss_counts_core(&records).is_empty());
    }

    #[test]
    fn miss_counts_unnamed_specialty_is_counted_not_dropped() {
        let records = [rec(
            "",
            "2026-01-01T00:00:00+00:00",
            &[AGENT_MISS_TAG, "category:design"],
        )];
        let counts: HashMap<_, _> = agent_miss_counts_core(&records).into_iter().collect();
        assert_eq!(counts[UNNAMED_SPECIALTY].count, 1);
        assert_eq!(counts[UNNAMED_SPECIALTY].category, "design");
        assert_eq!(counts[UNNAMED_SPECIALTY].fallback_agent, "");
    }

    #[test]
    fn miss_counts_groups_by_specialty_with_latest_category_and_fallback() {
        let records = [
            rec(
                "generic-dev",
                "2026-01-01T00:00:00+00:00",
                &[AGENT_MISS_TAG, "category:infra", "specialty:terraform"],
            ),
            rec(
                "generic-dev",
                "2026-01-02T00:00:00+00:00",
                &[AGENT_MISS_TAG, "category:infra", "specialty:terraform"],
            ),
        ];
        let counts: HashMap<_, _> = agent_miss_counts_core(&records).into_iter().collect();
        let terraform = &counts["terraform"];
        assert_eq!(terraform.count, 2);
        assert_eq!(terraform.last_seen, "2026-01-02T00:00:00+00:00");
        assert_eq!(terraform.category, "infra");
        assert_eq!(terraform.fallback_agent, "generic-dev");
    }

    #[test]
    fn miss_counts_a_free_content_field_is_never_aggregated() {
        // Un enregistrement edite a la main peut porter n'importe quel
        // champ ; la projection `TraceProjection` n'a tout simplement pas
        // de place pour un `prompt`/`request` libre — impossible de le
        // faire fuiter dans une agregation par construction du type, pas
        // par une garde explicite. Ce test fige juste que les seuls champs
        // lus restent agent_id/started_at/tags.
        let records = [rec(
            "generic-dev",
            "2026-01-01T00:00:00+00:00",
            &[AGENT_MISS_TAG, "specialty:terraform"],
        )];
        let counts: HashMap<_, _> = agent_miss_counts_core(&records).into_iter().collect();
        assert_eq!(counts.len(), 1);
        assert!(counts.contains_key("terraform"));
    }

    #[test]
    fn miss_counts_last_tag_wins_on_duplicate_specialty_tags() {
        let records = [rec(
            "dev",
            "2026-01-01T00:00:00+00:00",
            &[AGENT_MISS_TAG, "specialty:a", "specialty:b"],
        )];
        let counts: HashMap<_, _> = agent_miss_counts_core(&records).into_iter().collect();
        assert!(counts.contains_key("b"));
        assert!(!counts.contains_key("a"));
    }

    // ── oldest_started_at_core ────────────────────────────────────────────

    #[test]
    fn oldest_started_at_empty_is_none() {
        assert_eq!(oldest_started_at_core(&[]), None);
    }

    #[test]
    fn oldest_started_at_ignores_empty_strings() {
        let values = vec!["".to_string(), "2026-01-01T00:00:00+00:00".to_string()];
        assert_eq!(
            oldest_started_at_core(&values),
            Some("2026-01-01T00:00:00+00:00".to_string())
        );
    }

    #[test]
    fn oldest_started_at_all_empty_is_none() {
        let values = vec!["".to_string(), "".to_string()];
        assert_eq!(oldest_started_at_core(&values), None);
    }

    // ── compute_agent_freshness_core ─────────────────────────────────────

    fn now() -> iso8601::Instant {
        iso8601::parse("2026-09-11T00:00:00Z").unwrap()
    }

    #[test]
    fn empty_journal_is_not_judged() {
        let names = vec!["concierge".to_string()];
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            90,
            None,
            &HashMap::new(),
            &now(),
        );
        assert!(!result.judged);
        assert_eq!(result.journal_span_days, None);
        assert!(result.entries.iter().all(|e| !e.stale));
    }

    #[test]
    fn journal_younger_than_threshold_judges_nothing_stale() {
        let names = vec!["concierge".to_string()];
        let oldest = "2026-09-10T00:00:00Z"; // 1 jour, seuil 90
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(!result.judged);
        assert_eq!(result.journal_span_days, Some(1));
    }

    #[test]
    fn agent_never_dispatched_in_a_judged_journal_is_stale() {
        let names = vec!["security-auditor".to_string()];
        let oldest = "2026-01-01T00:00:00Z"; // largement > 90 jours
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(result.judged);
        assert!(result.entries[0].stale);
        assert_eq!(result.entries[0].last_seen, None);
    }

    #[test]
    fn agent_seen_recently_is_not_stale() {
        let names = vec!["concierge".to_string()];
        let mut last_seen = HashMap::new();
        last_seen.insert("concierge".to_string(), "2026-09-01T00:00:00Z".to_string()); // 10 jours
        let oldest = "2026-01-01T00:00:00Z";
        let result = compute_agent_freshness_core(
            &names,
            &last_seen,
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(result.judged);
        assert!(!result.entries[0].stale);
        assert_eq!(result.entries[0].days_since, Some(10));
    }

    #[test]
    fn agent_at_exact_threshold_boundary_is_stale_inclusive() {
        let names = vec!["concierge".to_string()];
        let mut last_seen = HashMap::new();
        // Exactement 90 jours avant `now()`.
        last_seen.insert("concierge".to_string(), "2026-06-13T00:00:00Z".to_string());
        let oldest = "2026-01-01T00:00:00Z";
        let result = compute_agent_freshness_core(
            &names,
            &last_seen,
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert_eq!(result.entries[0].days_since, Some(90));
        assert!(
            result.entries[0].stale,
            "la borne est inclusive : 90 jours pile doit etre perime"
        );
    }

    #[test]
    fn too_recent_agent_is_never_stale_even_with_huge_journal() {
        let names = vec!["fresh-agent".to_string()];
        let mut ages = HashMap::new();
        ages.insert("fresh-agent".to_string(), Some(1i64)); // fichier d'hier
        let oldest = "2020-01-01T00:00:00Z"; // journal enorme
        let result =
            compute_agent_freshness_core(&names, &HashMap::new(), 90, Some(oldest), &ages, &now());
        assert!(result.judged);
        assert!(result.entries[0].too_recent);
        assert!(!result.entries[0].stale);
    }

    #[test]
    fn unknown_agent_age_is_judged_normally() {
        let names = vec!["security-auditor".to_string()];
        let oldest = "2026-01-01T00:00:00Z";
        let mut ages: HashMap<String, Option<i64>> = HashMap::new();
        ages.insert("security-auditor".to_string(), None);
        let result =
            compute_agent_freshness_core(&names, &HashMap::new(), 90, Some(oldest), &ages, &now());
        assert!(result.entries[0].stale);
        assert!(!result.entries[0].too_recent);
    }

    #[test]
    fn huge_threshold_never_judges() {
        let names = vec!["concierge".to_string()];
        let oldest = "2000-01-01T00:00:00Z";
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            1_000_000,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(!result.judged);
    }

    #[test]
    fn malformed_oldest_started_at_never_panics_and_is_not_judged() {
        let names = vec!["concierge".to_string()];
        for garbage in ["", "not-a-date", "2026-99-99", "🎉"] {
            let result = compute_agent_freshness_core(
                &names,
                &HashMap::new(),
                90,
                Some(garbage),
                &HashMap::new(),
                &now(),
            );
            assert!(!result.judged, "garbage={garbage:?}");
        }
    }

    #[test]
    fn malformed_last_seen_never_panics_and_is_not_stale_nor_never_seen() {
        // Un last_seen present mais illisible n'est ni "jamais vu" ni
        // "vu recemment" du point de vue du calcul de jours — reproduit la
        // reference Python a l'identique (voir le commentaire de
        // `compute_agent_freshness_core`), jamais une exception.
        let names = vec!["concierge".to_string()];
        let mut last_seen = HashMap::new();
        last_seen.insert("concierge".to_string(), "garbage-timestamp".to_string());
        let oldest = "2020-01-01T00:00:00Z";
        let result = compute_agent_freshness_core(
            &names,
            &last_seen,
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(result.judged);
        assert_eq!(result.entries[0].days_since, None);
        assert!(!result.entries[0].stale);
    }

    #[test]
    fn naive_timestamp_treated_as_utc_never_panics() {
        // Le defaut corrige dans cette PR (voir le docstring de lib.rs) :
        // un horodatage sans decalage ne doit jamais faire lever ce calcul.
        let names = vec!["concierge".to_string()];
        let oldest = "2026-01-01T00:00:00"; // naif, pas de decalage
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            90,
            Some(oldest),
            &HashMap::new(),
            &now(),
        );
        assert!(result.judged);
        assert_eq!(result.journal_span_days, Some(253));
    }

    #[test]
    fn duplicate_agent_names_are_deduplicated_and_sorted() {
        let names = vec!["b".to_string(), "a".to_string(), "a".to_string()];
        let result = compute_agent_freshness_core(
            &names,
            &HashMap::new(),
            90,
            None,
            &HashMap::new(),
            &now(),
        );
        let names_out: Vec<&str> = result.entries.iter().map(|e| e.name.as_str()).collect();
        assert_eq!(names_out, vec!["a", "b"]);
    }

    // ── slugify_core / agent_slug_core / skill_slug_core ─────────────────

    #[test]
    fn slugify_collapses_runs_and_strips_accents() {
        assert_eq!(slugify_core("Chaos Engineering !!"), "chaos-engineering");
        assert_eq!(slugify_core("Terraform/Ansible"), "terraform-ansible");
        assert_eq!(slugify_core("Résilience réseau"), "r-silience-r-seau");
    }

    #[test]
    fn slugify_empty_or_all_punctuation_falls_back() {
        assert_eq!(slugify_core(""), "specialite");
        assert_eq!(slugify_core("   "), "specialite");
        assert_eq!(slugify_core("!!!"), "specialite");
    }

    #[test]
    fn agent_and_skill_slug_mirror_python_suffix_convention() {
        assert_eq!(agent_slug_core("terraform"), "terraform-specialist");
        assert_eq!(skill_slug_core("chaos-engineering"), "chaos-engineering");
    }

    // ── guess_tools_core ──────────────────────────────────────────────────

    #[test]
    fn guess_tools_execution_hint_grants_execute() {
        assert_eq!(
            guess_tools_core("infra", "ansible-homelab"),
            "read, search, execute"
        );
        assert_eq!(guess_tools_core("design", "figma-review"), "read, search");
    }

    // ── category_carrier_core / resolve_carrier_core ─────────────────────

    fn candidate(name: &str, use_when: &str, has_execute: bool) -> CarrierCandidate {
        CarrierCandidate {
            name: name.to_string(),
            use_when: use_when.to_string(),
            has_execute_tool: has_execute,
        }
    }

    #[test]
    fn category_carrier_excludes_entry_persona_even_if_use_when_matches() {
        let candidates = [candidate("concierge", "Une demande infra ou ops.", false)];
        assert_eq!(
            category_carrier_core("infra", "concierge", &candidates),
            None
        );
    }

    #[test]
    fn category_carrier_single_match_wins() {
        let candidates = [
            candidate("infra-ops", "Une demande infra ou d'exploitation.", false),
            candidate("designer", "Une demande de design.", false),
        ];
        assert_eq!(
            category_carrier_core("infra", "concierge", &candidates),
            Some("infra-ops".to_string())
        );
    }

    #[test]
    fn category_carrier_two_matches_is_none() {
        let candidates = [
            candidate("infra-ops", "Une demande infra ou d'exploitation.", false),
            candidate("infra-support", "Support infra de premier niveau.", false),
        ];
        assert_eq!(
            category_carrier_core("infra", "concierge", &candidates),
            None
        );
    }

    #[test]
    fn category_carrier_whole_word_not_substring() {
        let candidates = [candidate(
            "produit-generaliste",
            "Une demande spécifique au produit.",
            false,
        )];
        assert_eq!(category_carrier_core("ci", "concierge", &candidates), None);
    }

    #[test]
    fn category_carrier_structural_match_via_execute_tool() {
        let candidates = [candidate("worker", "", true)];
        assert_eq!(
            category_carrier_core("terraform", "concierge", &candidates),
            Some("worker".to_string())
        );
    }

    #[test]
    fn category_carrier_empty_category_never_matches() {
        let candidates = [candidate("anyone", "tout", true)];
        assert_eq!(category_carrier_core("", "concierge", &candidates), None);
    }

    #[test]
    fn resolve_carrier_prefers_observed_fallback_over_entry_persona() {
        let (carrier, reason) = resolve_carrier_core("generic-dev", "concierge", None);
        assert_eq!(carrier, "generic-dev");
        assert_eq!(reason, "repli observé");
    }

    #[test]
    fn resolve_carrier_fallback_equal_to_entry_persona_is_ignored() {
        let (carrier, reason) = resolve_carrier_core("concierge", "concierge", Some("infra-ops"));
        assert_eq!(carrier, "infra-ops");
        assert_eq!(reason, "porteur par catégorie : infra-ops");
    }

    #[test]
    fn resolve_carrier_no_fallback_no_category_candidate_is_agent() {
        let (carrier, reason) = resolve_carrier_core("", "concierge", None);
        assert_eq!(carrier, "");
        assert_eq!(reason, "persona d'entrée exclue, aucun porteur : agent");
    }

    // ── build_proposal_fields_core ────────────────────────────────────────

    #[test]
    fn build_proposal_fields_no_carrier_is_an_agent() {
        let fields = build_proposal_fields_core("terraform", "infra", "");
        assert_eq!(fields.artifact_type, "agent");
        assert_eq!(fields.slug, "terraform-specialist");
        assert_eq!(fields.target_agent, "");
        assert!(fields.tools.contains("execute"));
    }

    #[test]
    fn build_proposal_fields_with_carrier_is_a_skill() {
        let fields = build_proposal_fields_core("chaos-engineering", "infra", "generic-dev");
        assert_eq!(fields.artifact_type, "skill");
        assert_eq!(fields.slug, "chaos-engineering");
        assert_eq!(fields.target_agent, "generic-dev");
        assert_eq!(fields.tools, "");
        assert_eq!(fields.tool_boundary, "");
    }

    // ── clamp_threshold_core / sync_decision_core (issue #395) ───────────

    #[test]
    fn clamp_threshold_never_goes_below_two() {
        assert_eq!(clamp_threshold_core(0), 2);
        assert_eq!(clamp_threshold_core(1), 2);
        assert_eq!(clamp_threshold_core(-5), 2);
        assert_eq!(clamp_threshold_core(2), 2);
        assert_eq!(clamp_threshold_core(5), 5);
    }

    #[test]
    fn sync_decision_single_miss_is_never_a_proposal() {
        let (action, threshold, _) = sync_decision_core(2, 1, None, 0, None);
        assert_eq!(action, SyncAction::Skip);
        assert_eq!(threshold, 2);
    }

    #[test]
    fn sync_decision_count_equal_to_threshold_creates() {
        let (action, _, _) = sync_decision_core(2, 2, None, 0, None);
        assert_eq!(action, SyncAction::Create);
    }

    #[test]
    fn sync_decision_threshold_one_or_zero_is_clamped_to_two_on_both_sides() {
        // Seuil configure a 1 ou 0 : un compte de 1 doit rester "skip" —
        // jamais de proposition au premier non-choix, quel que soit ce que
        // l'appelant a configure.
        assert_eq!(sync_decision_core(1, 1, None, 0, None).0, SyncAction::Skip);
        assert_eq!(sync_decision_core(0, 1, None, 0, None).0, SyncAction::Skip);
        assert_eq!(
            sync_decision_core(1, 2, None, 0, None).0,
            SyncAction::Create
        );
    }

    #[test]
    fn sync_decision_accepted_is_always_kept() {
        let (action, _, _) = sync_decision_core(2, 100, Some("accepted"), 50, None);
        assert_eq!(action, SyncAction::KeepAccepted);
    }

    #[test]
    fn sync_decision_pending_refreshes_never_recreates() {
        let (action, _, _) = sync_decision_core(2, 5, Some("pending"), 3, None);
        assert_eq!(action, SyncAction::RefreshPending);
    }

    #[test]
    fn sync_decision_rejected_at_n_stays_rejected_until_double() {
        // Rejetee au compte 2 (seuil 2) -> reopen_at = 4. Un troisieme
        // non-choix (compte 3) ne doit pas la faire revenir.
        let (action, _, reopen_at) = sync_decision_core(2, 3, Some("rejected"), 2, Some(2));
        assert_eq!(action, SyncAction::KeepRejected);
        assert_eq!(reopen_at, Some(4));
    }

    #[test]
    fn sync_decision_rejected_reopens_exactly_at_double() {
        let (action, _, reopen_at) = sync_decision_core(2, 4, Some("rejected"), 2, Some(2));
        assert_eq!(action, SyncAction::Reopen);
        assert_eq!(reopen_at, Some(4));
    }

    #[test]
    fn sync_decision_rejected_without_recorded_count_falls_back_to_threshold() {
        // `rejected_at_count` absent (proposition ecrite avant l'ajout du
        // champ, ou editee a la main) -> base = seuil effectif.
        let (_, _, reopen_at) = sync_decision_core(2, 1, Some("rejected"), 2, None);
        assert_eq!(reopen_at, Some(4));
    }

    #[test]
    fn sync_decision_rejected_at_count_zero_falls_back_to_threshold_matching_python_falsy() {
        // Reproduit `existing.rejected_at_count or effective_threshold` :
        // `0` est falsy en Python, donc traite comme "absent" — voir le
        // commentaire de `sync_decision_core`.
        let (_, _, reopen_at) = sync_decision_core(2, 1, Some("rejected"), 2, Some(0));
        assert_eq!(reopen_at, Some(4));
    }

    #[test]
    fn sync_decision_unknown_status_falls_back_to_refresh_pending_never_panics() {
        let (action, _, _) = sync_decision_core(2, 5, Some("bogus"), 3, None);
        assert_eq!(action, SyncAction::RefreshPending);
    }

    // ── dispatch_outcome_stats_core (issue #442) ─────────────────────────

    fn outcome(tags: &[&str], cost_usd: f64) -> DispatchOutcomeProjection {
        DispatchOutcomeProjection {
            tags: tags.iter().map(|s| s.to_string()).collect(),
            cost_usd,
        }
    }

    #[test]
    fn dispatch_stats_ignores_records_without_the_outcome_tag() {
        let records = [outcome(&["class:V0", "resolved:true"], 1.0)];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.overall, DispatchGroupStats::empty());
    }

    #[test]
    fn dispatch_stats_cost_per_resolved_task_sums_across_unresolved_attempts() {
        let records = [
            outcome(
                &[
                    DISPATCH_OUTCOME_TAG,
                    "class:V0",
                    "tier:cheap",
                    "resolved:false",
                    "acceptance:judged",
                ],
                0.01,
            ),
            outcome(
                &[
                    DISPATCH_OUTCOME_TAG,
                    "class:V0",
                    "tier:mid",
                    "resolved:true",
                    "acceptance:judged",
                ],
                0.02,
            ),
        ];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.overall.total, 2);
        assert_eq!(stats.overall.resolved, 1);
        assert!((stats.overall.total_cost_usd - 0.03).abs() < 1e-9);
    }

    #[test]
    fn dispatch_stats_group_with_zero_resolved_has_no_cost_signal() {
        // La division par zero est laissee au cote Python (`cost_per_resolved_task_usd`
        // property) — le coeur Rust ne rend que les compteurs bruts, jamais un
        // ratio, justement pour ne jamais avoir a coder cette garde deux fois.
        let records = [outcome(
            &[DISPATCH_OUTCOME_TAG, "class:V0", "resolved:false"],
            5.0,
        )];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.overall.resolved, 0);
        assert_eq!(stats.overall.total_cost_usd, 5.0);
    }

    #[test]
    fn dispatch_stats_escalation_counts_distinct_tiers_only() {
        let single_tier = [outcome(
            &[
                DISPATCH_OUTCOME_TAG,
                "tier:cheap",
                "tier:cheap",
                "resolved:true",
            ],
            0.0,
        )];
        assert_eq!(
            dispatch_outcome_stats_core(&single_tier).overall.escalated,
            0
        );

        let two_tiers = [outcome(
            &[
                DISPATCH_OUTCOME_TAG,
                "tier:cheap",
                "tier:mid",
                "resolved:true",
            ],
            0.0,
        )];
        assert_eq!(dispatch_outcome_stats_core(&two_tiers).overall.escalated, 1);
    }

    #[test]
    fn dispatch_stats_inexecutable_share_from_acceptance_tag() {
        let records = [
            outcome(
                &[
                    DISPATCH_OUTCOME_TAG,
                    "acceptance:unrunnable",
                    "resolved:false",
                ],
                0.0,
            ),
            outcome(
                &[DISPATCH_OUTCOME_TAG, "acceptance:judged", "resolved:true"],
                0.0,
            ),
        ];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.overall.inexecutable, 1);
        assert_eq!(stats.overall.total, 2);
    }

    #[test]
    fn dispatch_stats_by_class_and_by_provider_are_independent_breakdowns() {
        let records = [
            outcome(
                &[
                    DISPATCH_OUTCOME_TAG,
                    "class:V0",
                    "provider:openai",
                    "resolved:true",
                ],
                1.0,
            ),
            outcome(
                &[
                    DISPATCH_OUTCOME_TAG,
                    "class:V1",
                    "provider:openai",
                    "resolved:false",
                ],
                2.0,
            ),
        ];
        let stats = dispatch_outcome_stats_core(&records);
        let by_class: HashMap<_, _> = stats.by_class.into_iter().collect();
        let by_provider: HashMap<_, _> = stats.by_provider.into_iter().collect();
        assert_eq!(by_class["V0"].resolved, 1);
        assert_eq!(by_class["V1"].resolved, 0);
        assert_eq!(by_provider["openai"].total, 2); // ventilation independante, pas une matrice croisee
    }

    #[test]
    fn dispatch_stats_records_without_provider_are_absorbed_by_overall_only() {
        let records = [outcome(&[DISPATCH_OUTCOME_TAG, "resolved:true"], 0.0)];
        let stats = dispatch_outcome_stats_core(&records);
        assert!(stats.by_provider.is_empty());
        assert_eq!(stats.overall.total, 1);
    }

    #[test]
    fn dispatch_stats_pass_k_requires_at_least_two_observations_of_the_same_replay_key() {
        let records = [outcome(
            &[DISPATCH_OUTCOME_TAG, "replay:node-a", "resolved:true"],
            0.0,
        )];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.pass_k_observations, 0); // une seule observation n'est pas une serie
        assert_eq!(stats.pass_k_fully_green, 0);
    }

    #[test]
    fn dispatch_stats_pass_k_fully_green_requires_every_replay_resolved() {
        let all_green = [
            outcome(
                &[DISPATCH_OUTCOME_TAG, "replay:node-a", "resolved:true"],
                0.0,
            ),
            outcome(
                &[DISPATCH_OUTCOME_TAG, "replay:node-a", "resolved:true"],
                0.0,
            ),
        ];
        let mixed = dispatch_outcome_stats_core(&[
            outcome(
                &[DISPATCH_OUTCOME_TAG, "replay:node-b", "resolved:true"],
                0.0,
            ),
            outcome(
                &[DISPATCH_OUTCOME_TAG, "replay:node-b", "resolved:false"],
                0.0,
            ),
        ]);
        let green = dispatch_outcome_stats_core(&all_green);
        assert_eq!(green.pass_k_observations, 1);
        assert_eq!(green.pass_k_fully_green, 1);
        assert_eq!(mixed.pass_k_observations, 1);
        assert_eq!(mixed.pass_k_fully_green, 0); // une seule tentative rouge suffit a casser la serie
    }

    #[test]
    fn dispatch_stats_last_tag_wins_on_duplicate_prefixes() {
        let records = [outcome(
            &[DISPATCH_OUTCOME_TAG, "resolved:true", "resolved:false"],
            0.0,
        )];
        let stats = dispatch_outcome_stats_core(&records);
        assert_eq!(stats.overall.resolved, 0); // "resolved:false" est le dernier tag
    }

    // ── Fuzz leger : jamais de panique sur des enregistrements arbitraires ─

    #[test]
    fn fuzz_never_panics_on_arbitrary_records() {
        // Generateur pseudo-aleatoire minimal, sans dependance externe —
        // deterministe (graine fixe) pour un test reproductible.
        let mut seed: u64 = 20260911;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let alphabet: Vec<char> =
            "abc{}[]\"'`\\\n \t#-!*?.,:;grimoireuncertaintiesNaInfy0123456789é🎉"
                .chars()
                .collect();
        let tag_pool = [
            AGENT_DISPATCH_TAG,
            AGENT_MISS_TAG,
            "category:infra",
            "category:",
            "specialty:",
            "specialty:terraform",
            "unrelated-tag",
            "",
        ];

        let mut records: Vec<TraceProjection> = Vec::new();
        for _ in 0..200 {
            let agent_len = (next() % 8) as usize;
            let agent_id: String = (0..agent_len)
                .map(|_| alphabet[(next() as usize) % alphabet.len()])
                .collect();
            let started_len = (next() % 24) as usize;
            let started_at: String = (0..started_len)
                .map(|_| alphabet[(next() as usize) % alphabet.len()])
                .collect();
            let tag_count = (next() % 4) as usize;
            let tags: Vec<String> = (0..tag_count)
                .map(|_| tag_pool[(next() as usize) % tag_pool.len()].to_string())
                .collect();
            records.push(TraceProjection {
                agent_id,
                started_at,
                tags,
            });
        }

        // Ne doit jamais paniquer, quel que soit le contenu.
        let dispatch = agent_dispatch_counts_core(&records);
        let misses = agent_miss_counts_core(&records);
        let started_ats: Vec<String> = records.iter().map(|r| r.started_at.clone()).collect();
        let oldest = oldest_started_at_core(&started_ats);

        let names: Vec<String> = records
            .iter()
            .map(|r| r.agent_id.clone())
            .filter(|s| !s.is_empty())
            .collect();
        let mut last_seen_map = HashMap::new();
        for (name, stat) in &dispatch {
            last_seen_map.insert(name.clone(), stat.last_seen.clone());
        }
        let freshness = compute_agent_freshness_core(
            &names,
            &last_seen_map,
            90,
            oldest.as_deref(),
            &HashMap::new(),
            &now(),
        );

        // Determinisme : rejouer la meme entree rend la meme sortie.
        let dispatch2 = agent_dispatch_counts_core(&records);
        let misses2 = agent_miss_counts_core(&records);
        let freshness2 = compute_agent_freshness_core(
            &names,
            &last_seen_map,
            90,
            oldest.as_deref(),
            &HashMap::new(),
            &now(),
        );
        assert_eq!(format!("{dispatch:?}"), format!("{dispatch2:?}"));
        assert_eq!(format!("{misses:?}"), format!("{misses2:?}"));
        assert_eq!(freshness, freshness2);

        // Le nommage mecanique et le porteur ne doivent jamais paniquer non
        // plus sur ces memes chaines arbitraires.
        for r in &records {
            let _ = slugify_core(&r.agent_id);
            let _ = guess_tools_core(&r.started_at, &r.agent_id);
            let candidates = [CarrierCandidate {
                name: r.agent_id.clone(),
                use_when: r.started_at.clone(),
                has_execute_tool: false,
            }];
            let _ = category_carrier_core(&r.started_at, &r.agent_id, &candidates);
        }
    }
}
