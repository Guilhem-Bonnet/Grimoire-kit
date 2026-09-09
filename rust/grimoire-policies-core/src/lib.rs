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

// Tout ce qui suit touche a PyO3 et n'existe que sous la feature
// `extension-module` (cf. Cargo.toml) : `evaluate_core` ci-dessus, seule
// logique couverte par `cargo test --no-default-features`, n'en depend pas.
#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{evaluate_core, ActionKind, MutationClass, PolicyMode, Rule, VerdictKind};
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

    #[pymodule]
    fn grimoire_policies_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(evaluate, m)?)?;
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
