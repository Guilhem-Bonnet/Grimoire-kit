//! Coeur Rust optionnel de `grimoire.policies`.
//!
//! Ce crate porte la logique d'evaluation de `grimoire.policies.engine.PolicyEngine`
//! (`src/grimoire/policies/engine.py` cote Python) : etant donne un ensemble de
//! regles et une requete, quelle est la decision (allow / warn / block) ?
//!
//! Ce qui est porte et ce qui ne l'est pas :
//! - Porte : la boucle de correspondance des regles, l'escalade de severite
//!   (block > warn > allow) et le repli du mode shadow (un block redescend en
//!   warn). C'est la totalite de la logique metier de `PolicyEngine.evaluate`.
//! - Pas porte : la generation de l'identifiant de verdict (uuid) et de
//!   l'horodatage, laissees cote Python (`engine.py`) pour que les deux
//!   implementations restent triviales a comparer champ a champ dans les
//!   tests — seule la partie deterministe traverse la frontiere PyO3.
//!
//! Ce module est optionnel par construction (voir la decision sur l'issue
//! #354 de Guilhem-Bonnet/Grimoire-kit) : `grimoire.policies.engine` ne
//! l'importe que si il est present, et retombe sinon sur l'implementation
//! Python pure. Rien ici n'est publie sur PyPI ; ce crate se construit en
//! local (`maturin develop`, voir CONTRIBUTING.md) ou dans le job CI dedie.
//!
//! ## La propriete que ce port doit demontrer
//!
//! Les quatre enums ci-dessous (`ActionKind`, `MutationClass`, `VerdictKind`,
//! `PolicyMode`) miroitent les `StrEnum` Python de
//! `grimoire.policies.schemas`. Chaque fonction qui les consomme (`parse`,
//! `as_str`, `severity`) est un `match` exhaustif, sans branche `_ =>` : si
//! une variante est ajoutee cote Python sans etre reportee ici, ce crate ne
//! compile plus. Cote Python, une valeur de `StrEnum` non geree dans une
//! comparaison ne casse rien a la compilation — elle se contente de rendre
//! une comparaison `False` a l'execution, potentiellement au milieu d'un
//! audit. C'est exactement le defaut que l'audit de l'issue #354 pointait
//! comme argument pour ce premier module.

// Sous `--no-default-features` (le profil de `cargo test`, voir Cargo.toml),
// tout ce qui suit n'est consomme que par le module `#[cfg(test)]` en bas de
// fichier — le pont PyO3 qui les utilise en dehors des tests est absent de
// cette configuration. C'est le compromis attendu de la separation feature
// par feature, pas du code mort a corriger.
#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

/// Miroir de `grimoire.policies.schemas.ActionKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ActionKind {
    ToolUse,
    FileWrite,
    Network,
    SecretAccess,
    PackActivation,
    TaskClose,
    MissionClose,
}

impl ActionKind {
    /// `Err` porte un message pret a l'affichage, pas encore une exception
    /// Python — cette fonction est aussi appelee depuis les tests `cargo
    /// test`, qui ne lient pas pyo3 (cf. feature `extension-module`).
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "tool_use" => ActionKind::ToolUse,
            "file_write" => ActionKind::FileWrite,
            "network" => ActionKind::Network,
            "secret_access" => ActionKind::SecretAccess,
            "pack_activation" => ActionKind::PackActivation,
            "task_close" => ActionKind::TaskClose,
            "mission_close" => ActionKind::MissionClose,
            other => return Err(format!("ActionKind inconnu: {other}")),
        })
    }
}

/// Miroir de `grimoire.policies.schemas.MutationClass`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MutationClass {
    ReadOnly,
    MutationControlled,
    PackActivation,
    Destructive,
}

impl MutationClass {
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "read_only" => MutationClass::ReadOnly,
            "mutation_controlled" => MutationClass::MutationControlled,
            "pack_activation" => MutationClass::PackActivation,
            "destructive" => MutationClass::Destructive,
            other => return Err(format!("MutationClass inconnu: {other}")),
        })
    }
}

/// Miroir de `grimoire.policies.schemas.VerdictKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum VerdictKind {
    Allow,
    Warn,
    Block,
}

impl VerdictKind {
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "allow" => VerdictKind::Allow,
            "warn" => VerdictKind::Warn,
            "block" => VerdictKind::Block,
            other => return Err(format!("VerdictKind inconnu: {other}")),
        })
    }

    fn as_str(self) -> &'static str {
        match self {
            VerdictKind::Allow => "allow",
            VerdictKind::Warn => "warn",
            VerdictKind::Block => "block",
        }
    }

    /// Ordre de severite pour l'escalade : block > warn > allow.
    fn severity(self) -> u8 {
        match self {
            VerdictKind::Allow => 0,
            VerdictKind::Warn => 1,
            VerdictKind::Block => 2,
        }
    }
}

/// Miroir de `grimoire.policies.schemas.PolicyMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PolicyMode {
    Shadow,
    Canary,
    Enforced,
}

impl PolicyMode {
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "shadow" => PolicyMode::Shadow,
            "canary" => PolicyMode::Canary,
            "enforced" => PolicyMode::Enforced,
            other => return Err(format!("PolicyMode inconnu: {other}")),
        })
    }
}

/// Une regle deja resolue (les chaines cote Python ont ete parsees en enums).
///
/// Miroir de `grimoire.policies.schemas.PolicyRule`, moins `description` et
/// `reason_template` verbatim qui n'entrent pas dans la logique de
/// correspondance elle-meme.
struct Rule {
    id: String,
    action_kinds: Vec<ActionKind>,
    mutation_classes: Vec<MutationClass>,
    risk_profiles: Vec<String>,
    verdict_on_match: VerdictKind,
    reason_template: String,
}

impl Rule {
    /// Miroir exact de `PolicyRule.matches` (schemas.py) : une liste vide
    /// sur une dimension signifie "pas de contrainte sur cette dimension".
    fn matches(
        &self,
        action_kind: ActionKind,
        mutation_class: MutationClass,
        risk_profile: &str,
    ) -> bool {
        let kind_ok = self.action_kinds.is_empty() || self.action_kinds.contains(&action_kind);
        let mutation_ok =
            self.mutation_classes.is_empty() || self.mutation_classes.contains(&mutation_class);
        let profile_ok =
            self.risk_profiles.is_empty() || self.risk_profiles.iter().any(|p| p == risk_profile);
        kind_ok && mutation_ok && profile_ok
    }
}

struct MatchedRule {
    rule_id: String,
    verdict: VerdictKind,
    reason: String,
}

struct EvaluationResult {
    verdict: VerdictKind,
    reason: String,
    matched: Vec<MatchedRule>,
    retry_hints: Vec<String>,
}

/// Coeur pur de l'evaluation — aucun type PyO3, testable directement par
/// `cargo test` sans interprete Python. Miroir exact de la boucle
/// `PolicyEngine.evaluate` (engine.py) : memes regles, meme escalade de
/// severite, meme repli shadow.
fn evaluate_core(
    rules: &[Rule],
    mode: PolicyMode,
    action_kind: ActionKind,
    mutation_class: MutationClass,
    risk_profile: &str,
) -> EvaluationResult {
    let mut matched = Vec::new();
    let mut effective = VerdictKind::Allow;
    let mut reason = "No rules matched — allowed by default".to_string();

    for rule in rules {
        if !rule.matches(action_kind, mutation_class, risk_profile) {
            continue;
        }
        matched.push(MatchedRule {
            rule_id: rule.id.clone(),
            verdict: rule.verdict_on_match,
            reason: rule.reason_template.clone(),
        });
        if rule.verdict_on_match.severity() > effective.severity() {
            effective = rule.verdict_on_match;
            reason = rule.reason_template.clone();
        }
    }

    // Mode shadow : un block redescend en warn, jamais l'inverse.
    if mode == PolicyMode::Shadow && effective == VerdictKind::Block {
        effective = VerdictKind::Warn;
        reason = format!("[shadow] {reason}");
    }

    // Indices de retry : les motifs des regles bloquantes parmi celles
    // matchees — calcules sur le verdict *individuel* de chaque regle,
    // donc non affectes par le repli shadow ci-dessus (miroir de
    // `retry_hints` dans engine.py, calcule apres le bloc shadow mais a
    // partir de `matched`, qui garde le verdict d'origine de chaque regle).
    let retry_hints = matched
        .iter()
        .filter(|m| m.verdict == VerdictKind::Block)
        .map(|m| m.reason.clone())
        .collect();

    EvaluationResult {
        verdict: effective,
        reason,
        matched,
        retry_hints,
    }
}

// ---------------------------------------------------------------------------
// Politiques temporelles (issue #429, point 3 de l'audit de positionnement
// 2026-09-12) : budgets par session, approbation prealable, refroidissement.
//
// Miroir de `grimoire.policies.temporal` (Python) : meme boucle sur les
// regles temporelles, meme ordre de priorite par regle (refroidissement >
// budget > approbation prealable > allow), memes compteurs incrementes. Pas
// de dependance a une bibliotheque de dates : chaque horodatage traverse la
// frontiere PyO3 comme un flottant "secondes depuis epoch" — la conversion
// ISO<->epoch reste cote Python (`temporal.py`), qui l'a deja pour
// `datetime.fromisoformat`/`isoformat`.
// ---------------------------------------------------------------------------

/// Correspondance de motif : `*` est le seul joker, miroir exact de
/// `grimoire.policies.temporal.glob_match`. Sensible a la casse.
fn glob_match(pattern: &str, text: &str) -> bool {
    if pattern == "*" || pattern == text {
        return true;
    }
    if !pattern.contains('*') {
        return false;
    }
    let parts: Vec<&str> = pattern.split('*').collect();
    let first = parts[0];
    let last = parts[parts.len() - 1];
    if !text.starts_with(first) || !text.ends_with(last) {
        return false;
    }
    let mut cursor = first.len();
    let end = text.len() - last.len();
    for part in &parts[1..parts.len() - 1] {
        if part.is_empty() {
            continue;
        }
        match text.get(cursor..end).and_then(|hay| hay.find(part)) {
            Some(idx) => cursor += idx + part.len(),
            None => return false,
        }
    }
    cursor <= end
}

/// Miroir de la partie temporelle de `grimoire.policies.schemas.PolicyRule` :
/// `tool_pattern`, `require_approval`, `per_session` (aplati) et
/// `cooldown_after` (aplati). Une regle sans aucune de ces contraintes
/// n'entre jamais dans cette liste — filtree cote Python par
/// `PolicyRule.is_temporal` avant l'appel PyO3.
struct TemporalRule {
    id: String,
    tool_pattern: String,
    require_approval: bool,
    max_tool_calls: Option<i64>,
    max_writes: Option<i64>,
    max_cost_usd: Option<f64>,
    max_duration_min: Option<f64>,
    cooldown_pattern: String,
    cooldown_count: i64,
    cooldown_minutes: f64,
    estimated_cost_usd: f64,
}

impl TemporalRule {
    fn has_cooldown(&self) -> bool {
        self.cooldown_count > 0
    }
}

/// Miroir de `grimoire.policies.session_state.RuleState` — les compteurs
/// d'une seule regle pour la session courante.
#[derive(Clone)]
struct RuleState {
    calls: i64,
    writes: i64,
    cost_usd: f64,
    approved: bool,
    hits: Vec<f64>,
}

struct TemporalMatch {
    rule_id: String,
    verdict: VerdictKind,
    reason: String,
}

struct TemporalResult {
    verdict: VerdictKind,
    reason: String,
    matched: Vec<TemporalMatch>,
    states: Vec<(String, RuleState)>,
}

/// Nombre de `hits` dans la fenetre `[now - minutes*60, now]`.
fn hits_in_window(hits: &[f64], now_epoch_s: f64, minutes: f64) -> i64 {
    let threshold = now_epoch_s - minutes * 60.0;
    hits.iter().filter(|&&h| h >= threshold).count() as i64
}

/// Une seule regle temporelle contre ses propres compteurs. Miroir exact de
/// `grimoire.policies.temporal._evaluate_one_rule` : meme ordre de priorite,
/// memes conditions de refus, memes compteurs mutes uniquement quand l'appel
/// n'est pas refuse par un budget ou n'est pas deja en refroidissement actif.
/// Retourne `(verdict, raison, faut_il_enregistrer_un_hit)`.
fn evaluate_one_temporal_rule(
    rule: &TemporalRule,
    state: &mut RuleState,
    tool_name: &str,
    is_write: bool,
    now_epoch_s: f64,
    session_started_epoch_s: f64,
) -> (VerdictKind, String, bool) {
    // 1) Refroidissement — le refus le plus immediat, en forme de rate-limit.
    if rule.has_cooldown() && glob_match(&rule.cooldown_pattern, tool_name) {
        let hits = hits_in_window(&state.hits, now_epoch_s, rule.cooldown_minutes);
        if hits >= rule.cooldown_count {
            return (
                VerdictKind::Block,
                format!(
                    "Refroidissement actif pour '{}' ({} appels en {} min) — réessaie plus tard",
                    rule.cooldown_pattern, rule.cooldown_count, rule.cooldown_minutes
                ),
                true,
            );
        }
    }

    // 2) Budgets par session — le plafond deja atteint, pas celui sur le point de l'etre.
    if let Some(max_calls) = rule.max_tool_calls {
        if state.calls >= max_calls {
            return (
                VerdictKind::Block,
                format!("Budget de {max_calls} appels d'outil atteint pour cette session"),
                false,
            );
        }
    }
    if is_write {
        if let Some(max_writes) = rule.max_writes {
            if state.writes >= max_writes {
                return (
                    VerdictKind::Block,
                    format!("Budget de {max_writes} écritures atteint pour cette session"),
                    false,
                );
            }
        }
    }
    if let Some(max_cost) = rule.max_cost_usd {
        if state.cost_usd >= max_cost {
            return (
                VerdictKind::Block,
                format!("Budget de {max_cost} $ atteint pour cette session"),
                false,
            );
        }
    }
    if let Some(max_duration) = rule.max_duration_min {
        let elapsed_min = ((now_epoch_s - session_started_epoch_s).max(0.0)) / 60.0;
        if elapsed_min >= max_duration {
            return (
                VerdictKind::Block,
                format!("Fenêtre de {max_duration} min dépassée pour cette session"),
                false,
            );
        }
    }

    // 3) Approbation prealable — premiere occurrence dans la session seulement.
    let mut verdict = VerdictKind::Allow;
    let mut reason = String::new();
    if rule.require_approval && !state.approved {
        verdict = VerdictKind::Warn;
        reason = format!(
            "Approbation requise pour '{tool_name}' — première occurrence dans cette session"
        );
        state.approved = true;
    }

    state.calls += 1;
    if is_write {
        state.writes += 1;
    }
    state.cost_usd += rule.estimated_cost_usd;
    (verdict, reason, true)
}

/// Coeur pur de l'evaluation temporelle — aucun type PyO3, testable
/// directement par `cargo test`. Miroir exact de
/// `grimoire.policies.temporal._evaluate_python`.
fn evaluate_temporal_core(
    rules: &[TemporalRule],
    mut states: Vec<(String, RuleState)>,
    tool_name: &str,
    is_write: bool,
    now_epoch_s: f64,
    session_started_epoch_s: f64,
) -> TemporalResult {
    let mut matched = Vec::new();
    let mut effective = VerdictKind::Allow;
    let mut reason = String::new();

    for rule in rules {
        if !glob_match(&rule.tool_pattern, tool_name) {
            continue;
        }
        let entry = states.iter_mut().find(|(id, _)| id == &rule.id).expect(
            "every temporal rule has a matching state entry (see py_bridge::evaluate_temporal)",
        );
        let (verdict, rule_reason, record_hit) = evaluate_one_temporal_rule(
            rule,
            &mut entry.1,
            tool_name,
            is_write,
            now_epoch_s,
            session_started_epoch_s,
        );
        if record_hit {
            entry.1.hits.push(now_epoch_s);
        }
        if verdict != VerdictKind::Allow || !rule_reason.is_empty() {
            matched.push(TemporalMatch {
                rule_id: rule.id.clone(),
                verdict,
                reason: rule_reason.clone(),
            });
        }
        if verdict.severity() > effective.severity() {
            effective = verdict;
            reason = rule_reason;
        }
    }

    TemporalResult {
        verdict: effective,
        reason,
        matched,
        states,
    }
}

// Tout ce qui suit touche a PyO3 et n'existe que sous la feature
// `extension-module` (cf. Cargo.toml) : `evaluate_core` ci-dessus, seule
// logique couverte par `cargo test --no-default-features`, n'en depend pas.
#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{
        evaluate_core, evaluate_temporal_core, ActionKind, MutationClass, PolicyMode, Rule,
        RuleState, TemporalRule, VerdictKind,
    };
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    type PyRuleTuple = (
        String,
        Vec<String>,
        Vec<String>,
        Vec<String>,
        String,
        String,
    );
    type PyMatchedTuple = (String, String, String);
    type PyEvaluateResult = (String, String, Vec<PyMatchedTuple>, Vec<String>);

    /// Frontiere PyO3 : convertit les chaines recues depuis `engine.py` en
    /// enums via `parse` (rejet explicite d'une valeur inconnue, plutot
    /// qu'un mauvais classement silencieux), delegue a `evaluate_core`, puis
    /// reconvertit le resultat en types Python natifs (tuples de chaines et
    /// de listes).
    ///
    /// Signature de `rules` : `(id, action_kinds, mutation_classes,
    /// risk_profiles, verdict_on_match, reason_template)`, un tuple par
    /// regle enregistree dans `PolicyEngine._rules` — cf.
    /// `PolicyRule.to_dict()`.
    #[pyfunction]
    fn evaluate(
        rules: Vec<PyRuleTuple>,
        mode: String,
        action_kind: String,
        mutation_class: String,
        risk_profile: String,
    ) -> PyResult<PyEvaluateResult> {
        let mode = PolicyMode::parse(&mode).map_err(PyValueError::new_err)?;
        let action_kind = ActionKind::parse(&action_kind).map_err(PyValueError::new_err)?;
        let mutation_class =
            MutationClass::parse(&mutation_class).map_err(PyValueError::new_err)?;

        let mut parsed_rules = Vec::with_capacity(rules.len());
        for (id, kinds, mutations, profiles, verdict, reason_template) in rules {
            let action_kinds = kinds
                .iter()
                .map(|s| ActionKind::parse(s))
                .collect::<Result<Vec<_>, _>>()
                .map_err(PyValueError::new_err)?;
            let mutation_classes = mutations
                .iter()
                .map(|s| MutationClass::parse(s))
                .collect::<Result<Vec<_>, _>>()
                .map_err(PyValueError::new_err)?;
            let verdict_on_match = VerdictKind::parse(&verdict).map_err(PyValueError::new_err)?;
            parsed_rules.push(Rule {
                id,
                action_kinds,
                mutation_classes,
                risk_profiles: profiles,
                verdict_on_match,
                reason_template,
            });
        }

        let result = evaluate_core(
            &parsed_rules,
            mode,
            action_kind,
            mutation_class,
            &risk_profile,
        );

        let matched: Vec<PyMatchedTuple> = result
            .matched
            .into_iter()
            .map(|m| (m.rule_id, m.verdict.as_str().to_string(), m.reason))
            .collect();

        Ok((
            result.verdict.as_str().to_string(),
            result.reason,
            matched,
            result.retry_hints,
        ))
    }

    type PyTemporalRuleTuple = (
        String,      // id
        String,      // tool_pattern
        bool,        // require_approval
        Option<i64>, // per_session.max_tool_calls
        Option<i64>, // per_session.max_writes
        Option<f64>, // per_session.max_cost_usd
        Option<f64>, // per_session.max_duration_min
        String,      // cooldown_after.pattern ("" if unset)
        i64,         // cooldown_after.count (0 if unset)
        f64,         // cooldown_after.minutes (0.0 if unset)
        f64,         // estimated_cost_usd
    );
    type PyRuleStateTuple = (String, i64, i64, f64, bool, Vec<f64>);
    type PyTemporalMatchedTuple = (String, String, String);
    type PyTemporalDelta = (String, i64, i64, f64, bool, Vec<f64>);
    type PyTemporalResult = (
        String,
        String,
        Vec<PyTemporalMatchedTuple>,
        Vec<PyTemporalDelta>,
    );

    /// Frontiere PyO3 de la partie temporelle (issue #429, point 3) —
    /// contrepartie de `evaluate` ci-dessus pour
    /// `grimoire.policies.temporal.evaluate_temporal`. Chaque horodatage
    /// (``now_epoch_s``, ``session_started_epoch_s``, les ``hits`` dans
    /// chaque tuple d'etat) est deja en secondes-epoch : voir le commentaire
    /// en tete de la section "Politiques temporelles" plus haut dans ce
    /// fichier pour pourquoi aucune conversion ISO n'a lieu ici.
    #[pyfunction]
    fn evaluate_temporal(
        rules: Vec<PyTemporalRuleTuple>,
        states: Vec<PyRuleStateTuple>,
        tool_name: String,
        is_write: bool,
        now_epoch_s: f64,
        session_started_epoch_s: f64,
    ) -> PyResult<PyTemporalResult> {
        let parsed_rules: Vec<TemporalRule> = rules
            .into_iter()
            .map(
                |(
                    id,
                    tool_pattern,
                    require_approval,
                    max_tool_calls,
                    max_writes,
                    max_cost_usd,
                    max_duration_min,
                    cooldown_pattern,
                    cooldown_count,
                    cooldown_minutes,
                    estimated_cost_usd,
                )| TemporalRule {
                    id,
                    tool_pattern,
                    require_approval,
                    max_tool_calls,
                    max_writes,
                    max_cost_usd,
                    max_duration_min,
                    cooldown_pattern,
                    cooldown_count,
                    cooldown_minutes,
                    estimated_cost_usd,
                },
            )
            .collect();

        let parsed_states: Vec<(String, RuleState)> = states
            .into_iter()
            .map(|(id, calls, writes, cost_usd, approved, hits)| {
                (
                    id,
                    RuleState {
                        calls,
                        writes,
                        cost_usd,
                        approved,
                        hits,
                    },
                )
            })
            .collect();

        let result = evaluate_temporal_core(
            &parsed_rules,
            parsed_states,
            &tool_name,
            is_write,
            now_epoch_s,
            session_started_epoch_s,
        );

        let matched: Vec<PyTemporalMatchedTuple> = result
            .matched
            .into_iter()
            .map(|m| (m.rule_id, m.verdict.as_str().to_string(), m.reason))
            .collect();
        let deltas: Vec<PyTemporalDelta> = result
            .states
            .into_iter()
            .map(|(id, state)| {
                (
                    id,
                    state.calls,
                    state.writes,
                    state.cost_usd,
                    state.approved,
                    state.hits,
                )
            })
            .collect();

        Ok((
            result.verdict.as_str().to_string(),
            result.reason,
            matched,
            deltas,
        ))
    }

    #[pymodule]
    fn grimoire_policies_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(evaluate, m)?)?;
        m.add_function(wrap_pyfunction!(evaluate_temporal, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(
        id: &str,
        action_kinds: &[ActionKind],
        mutation_classes: &[MutationClass],
        risk_profiles: &[&str],
        verdict: VerdictKind,
        reason: &str,
    ) -> Rule {
        Rule {
            id: id.to_string(),
            action_kinds: action_kinds.to_vec(),
            mutation_classes: mutation_classes.to_vec(),
            risk_profiles: risk_profiles.iter().map(|s| s.to_string()).collect(),
            verdict_on_match: verdict,
            reason_template: reason.to_string(),
        }
    }

    /// Les quatre regles integrees de `_BUILTIN_RULES` (engine.py), reprises
    /// a l'identique pour que ces tests exercent le meme scenario que
    /// `tests/unit/test_policies.py` cote Python.
    fn builtin_rules() -> Vec<Rule> {
        vec![
            rule(
                "no-destructive-without-strict",
                &[],
                &[MutationClass::Destructive],
                &["light", "standard"],
                VerdictKind::Block,
                "Destructive mutation requires strict risk profile",
            ),
            rule(
                "pack-activation-requires-evidence",
                &[ActionKind::PackActivation],
                &[],
                &[],
                VerdictKind::Block,
                "Pack activation requires pack.lock and doctor success evidence",
            ),
            rule(
                "task-close-requires-verification",
                &[ActionKind::TaskClose],
                &[],
                &[],
                VerdictKind::Warn,
                "Closing a task requires verified evidence; transition to needs_verification first",
            ),
            rule(
                "secret-access-always-block",
                &[ActionKind::SecretAccess],
                &[],
                &[],
                VerdictKind::Block,
                "Secret access must be explicitly authorised via host capability manifest",
            ),
        ]
    }

    #[test]
    fn allow_by_default() {
        let result = evaluate_core(
            &[],
            PolicyMode::Enforced,
            ActionKind::ToolUse,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Allow);
    }

    #[test]
    fn allow_read_only_tool() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Enforced,
            ActionKind::ToolUse,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Allow);
    }

    #[test]
    fn block_destructive_on_standard() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Enforced,
            ActionKind::ToolUse,
            MutationClass::Destructive,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Block);
    }

    #[test]
    fn block_secret_access() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Enforced,
            ActionKind::SecretAccess,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Block);
    }

    #[test]
    fn block_pack_activation_builtin() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Enforced,
            ActionKind::PackActivation,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Block);
    }

    #[test]
    fn shadow_mode_downgrades_block_to_warn() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Shadow,
            ActionKind::ToolUse,
            MutationClass::Destructive,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Warn);
        assert!(result.reason.starts_with("[shadow]"));
    }

    #[test]
    fn most_restrictive_verdict_wins() {
        let rules = vec![
            rule(
                "warn-rule",
                &[ActionKind::ToolUse],
                &[],
                &[],
                VerdictKind::Warn,
                "",
            ),
            rule(
                "block-rule",
                &[ActionKind::ToolUse],
                &[],
                &[],
                VerdictKind::Block,
                "",
            ),
        ];
        let result = evaluate_core(
            &rules,
            PolicyMode::Enforced,
            ActionKind::ToolUse,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Block);
    }

    #[test]
    fn remove_rule_restores_allow() {
        // Miroir de test_remove_rule (Python) : une fois la seule regle
        // bloquante retiree de la liste passee a evaluate_core, le verdict
        // redevient allow.
        let result = evaluate_core(
            &[],
            PolicyMode::Enforced,
            ActionKind::Network,
            MutationClass::ReadOnly,
            "standard",
        );
        assert_eq!(result.verdict, VerdictKind::Allow);
    }

    #[test]
    fn retry_hints_collect_blocking_reasons_unaffected_by_shadow_downgrade() {
        let result = evaluate_core(
            &builtin_rules(),
            PolicyMode::Shadow,
            ActionKind::ToolUse,
            MutationClass::Destructive,
            "standard",
        );
        // Le verdict agrege est redescendu en warn par le mode shadow, mais
        // l'indice de retry vient de la regle bloquante individuelle, qui
        // garde son propre verdict d'origine.
        assert_eq!(result.verdict, VerdictKind::Warn);
        assert_eq!(
            result.retry_hints,
            vec!["Destructive mutation requires strict risk profile".to_string()]
        );
    }

    #[test]
    fn unknown_action_kind_is_rejected_explicitly() {
        // La propriete que ce port doit demontrer : une valeur hors
        // enumeration a la frontiere est un rejet explicite (erreur), pas
        // une comparaison qui echoue silencieusement.
        assert!(ActionKind::parse("not_a_real_kind").is_err());
        assert!(MutationClass::parse("not_a_real_class").is_err());
        assert!(VerdictKind::parse("not_a_real_verdict").is_err());
        assert!(PolicyMode::parse("not_a_real_mode").is_err());
    }
}

#[cfg(test)]
mod temporal_tests {
    use super::*;

    fn budget_rule(id: &str, max_writes: Option<i64>, max_tool_calls: Option<i64>) -> TemporalRule {
        TemporalRule {
            id: id.to_string(),
            tool_pattern: "*".to_string(),
            require_approval: false,
            max_tool_calls,
            max_writes,
            max_cost_usd: None,
            max_duration_min: None,
            cooldown_pattern: String::new(),
            cooldown_count: 0,
            cooldown_minutes: 0.0,
            estimated_cost_usd: 0.0,
        }
    }

    fn approval_rule(id: &str, pattern: &str) -> TemporalRule {
        TemporalRule {
            id: id.to_string(),
            tool_pattern: pattern.to_string(),
            require_approval: true,
            max_tool_calls: None,
            max_writes: None,
            max_cost_usd: None,
            max_duration_min: None,
            cooldown_pattern: String::new(),
            cooldown_count: 0,
            cooldown_minutes: 0.0,
            estimated_cost_usd: 0.0,
        }
    }

    fn cooldown_rule(id: &str, pattern: &str, count: i64, minutes: f64) -> TemporalRule {
        TemporalRule {
            id: id.to_string(),
            tool_pattern: pattern.to_string(),
            require_approval: false,
            max_tool_calls: None,
            max_writes: None,
            max_cost_usd: None,
            max_duration_min: None,
            cooldown_pattern: pattern.to_string(),
            cooldown_count: count,
            cooldown_minutes: minutes,
            estimated_cost_usd: 0.0,
        }
    }

    fn empty_state(id: &str) -> (String, RuleState) {
        (
            id.to_string(),
            RuleState {
                calls: 0,
                writes: 0,
                cost_usd: 0.0,
                approved: false,
                hits: Vec::new(),
            },
        )
    }

    #[test]
    fn glob_match_star_only() {
        assert!(glob_match("*", "anything"));
        assert!(glob_match("Bash", "Bash"));
        assert!(!glob_match("Bash", "bash"));
        assert!(glob_match("mcp__*__write*", "mcp__grimoire__write_file"));
        assert!(!glob_match("mcp__*__write*", "mcp__grimoire__read_file"));
    }

    #[test]
    fn budget_allows_up_to_the_limit_then_blocks() {
        let rules = vec![budget_rule("writes-cap", Some(2), None)];
        let mut states = vec![empty_state("writes-cap")];

        // Deux ecritures autorisees : le compteur monte a 2.
        for _ in 0..2 {
            let result = evaluate_temporal_core(&rules, states, "Write", true, 0.0, 0.0);
            assert_eq!(result.verdict, VerdictKind::Allow);
            states = result.states;
        }
        assert_eq!(states[0].1.writes, 2);

        // La 3e est refusee, nommement, et le compteur ne bouge plus.
        let result = evaluate_temporal_core(&rules, states, "Write", true, 0.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Block);
        assert!(result.reason.contains("Budget de 2 écritures"));
        assert_eq!(result.states[0].1.writes, 2);
    }

    #[test]
    fn require_approval_asks_once_then_allows() {
        let rules = vec![approval_rule("rm-approval", "Bash(rm:*)")];
        let states = vec![empty_state("rm-approval")];

        let first = evaluate_temporal_core(&rules, states, "Bash(rm:*)", true, 0.0, 0.0);
        assert_eq!(first.verdict, VerdictKind::Warn);
        assert!(first.states[0].1.approved);

        let second = evaluate_temporal_core(&rules, first.states, "Bash(rm:*)", true, 0.0, 0.0);
        assert_eq!(second.verdict, VerdictKind::Allow);
    }

    #[test]
    fn fresh_session_asks_again() {
        // Miroir du test Python `test_require_approval_asks_again_in_a_new_session` :
        // un etat neuf (approved=false) redemande, meme pour la meme regle.
        let rules = vec![approval_rule("rm-approval", "*")];
        let states = vec![empty_state("rm-approval")];
        let result = evaluate_temporal_core(&rules, states, "Bash", false, 0.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Warn);
    }

    #[test]
    fn cooldown_blocks_after_count_hits_in_window() {
        let rules = vec![cooldown_rule("burst-guard", "*", 3, 10.0)];
        let mut states = vec![empty_state("burst-guard")];

        for _ in 0..3 {
            let result = evaluate_temporal_core(&rules, states, "Bash", false, 0.0, 0.0);
            assert_eq!(result.verdict, VerdictKind::Allow);
            states = result.states;
        }
        // Les 3 hits sont dans la fenetre (meme instant) : le 4e est bloque.
        let result = evaluate_temporal_core(&rules, states, "Bash", false, 5.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Block);
        assert!(result.reason.contains("Refroidissement"));
    }

    #[test]
    fn cooldown_clears_once_hits_age_out_of_the_window() {
        let rules = vec![cooldown_rule("burst-guard", "*", 3, 10.0)];
        let mut states = vec![empty_state("burst-guard")];
        for _ in 0..3 {
            let result = evaluate_temporal_core(&rules, states, "Bash", false, 0.0, 0.0);
            states = result.states;
        }
        // 700s later (> 10 min), the three hits have aged out of the window.
        let result = evaluate_temporal_core(&rules, states, "Bash", false, 700.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Allow);
    }

    #[test]
    fn max_duration_blocks_past_the_session_window() {
        let rules = vec![TemporalRule {
            id: "short-session".to_string(),
            tool_pattern: "*".to_string(),
            require_approval: false,
            max_tool_calls: None,
            max_writes: None,
            max_cost_usd: None,
            max_duration_min: Some(5.0),
            cooldown_pattern: String::new(),
            cooldown_count: 0,
            cooldown_minutes: 0.0,
            estimated_cost_usd: 0.0,
        }];
        let states = vec![empty_state("short-session")];
        // 6 minutes (360s) after session start > 5 min budget.
        let result = evaluate_temporal_core(&rules, states, "Bash", false, 360.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Block);
        assert!(result.reason.contains("Fenêtre"));
    }

    #[test]
    fn non_matching_tool_pattern_is_untouched() {
        let rules = vec![budget_rule("writes-cap", Some(1), None)];
        let mut r = rules;
        r[0].tool_pattern = "Write".to_string();
        let states = vec![empty_state("writes-cap")];
        let result = evaluate_temporal_core(&r, states, "Bash", true, 0.0, 0.0);
        assert_eq!(result.verdict, VerdictKind::Allow);
        assert_eq!(result.states[0].1.writes, 0);
    }
}
